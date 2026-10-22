"""Bounded synthetic selector; production membership and PIT claims are excluded."""
from collections import defaultdict
from fractions import Fraction
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ingestion.sec.contracts import Sha256, explicit_time
from .identity import Contract, Evidence, Exact, Id, IdentityCatalog, Time, content_hash
from .ranking import RankFraction, RankingInput, SelectionPolicy, earnings_yield, midrank_percentiles
from .snapshot import (Exclusion, HistoricalCoverage, IndustryCount, Member,
                       UniverseSnapshot, eligibility_reason)


class SelectionError(Exception):
    def __init__(self, code): self.code = code; super().__init__(code)


class SelectorLimits(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    max_candidates: int = Field(default=1000,ge=1,le=1000)
    max_industries: int = Field(default=100,ge=1,le=100)
    max_bytes: int = Field(default=8388608,ge=1,le=8388608)


class IndustrySignal(Contract):
    sic_code: str = Field(pattern=r'^[0-9]{4}$')
    momentum: Exact
    volume: Exact = Field(ge=0)
    momentum_sessions: int = Field(strict=True,gt=0)
    volume_sessions: int = Field(strict=True,gt=0)
    asof: Time
    evidence: Evidence


class Selection(Contract):
    source_sha256: Sha256
    snapshot: UniverseSnapshot
    ignored_listings: tuple[Id,...] = ()


def _allocation(capacities, weights):
    """Exact capacity-constrained largest remainder, with one initial seat each."""
    if len(capacities) != 10 or sum(capacities.values()) < 100:
        return None
    seats = {k:1 for k in capacities}
    while sum(seats.values()) < 100:
        remaining = 100-sum(seats.values())
        active = sorted(k for k in seats if seats[k] < capacities[k])
        total = sum(weights[k] for k in active)
        quotas = {k:Fraction(remaining)*weights[k]/total for k in active}
        for k in active:
            seats[k] += min(capacities[k]-seats[k],quotas[k].numerator//quotas[k].denominator)
        if sum(seats.values()) == 100: break
        for k in sorted(active,key=lambda k:(-(quotas[k] % 1),k)):
            if seats[k] < capacities[k]: seats[k] += 1
            if sum(seats.values()) == 100: break
    return seats


def _select(catalog,policy,candidates,signals,asof,published_at,effective_at,limits):
    limits = SelectorLimits.model_validate(limits or {})
    if not isinstance(candidates,(tuple,list)) or len(candidates) > limits.max_candidates:
        raise SelectionError('limit_candidates')
    if not isinstance(signals,(tuple,list)) or len(signals) > limits.max_industries:
        raise SelectionError('limit_industries')
    catalog = IdentityCatalog.model_validate(catalog); policy = SelectionPolicy.model_validate(policy)
    if policy.review_status != 'fixture_only': raise SelectionError('synthetic_policy_required')
    rows = tuple(RankingInput.model_validate(r) for r in candidates)
    signals = tuple(IndustrySignal.model_validate(s) for s in signals)
    if len({(r.issuer_id,r.instrument_id) for r in rows}) != len(rows): raise SelectionError('duplicate_listing')
    if len({s.sic_code for s in signals}) != len(signals): raise SelectionError('duplicate_industry')
    if len({(r.liquidity_provider,r.liquidity_feed,r.volume_scope,r.liquidity_metric) for r in rows}) > 1:
        raise SelectionError('ambiguous_source')
    asof,published_at,effective_at = map(explicit_time,(asof,published_at,effective_at))
    if None in (asof,published_at,effective_at): raise SelectionError('invalid_time')
    source = dict(catalog=catalog,policy=policy,candidates=sorted(rows,key=lambda r:(r.issuer_id,r.instrument_id)),
                  signals=sorted(signals,key=lambda s:s.sic_code),asof=asof,published_at=published_at,
                  effective_at=effective_at,limits=limits.model_dump())
    # Same canonical serializer as existing contracts; no floating-point scoring.
    from .identity import _canonical
    if len(json.dumps(_canonical(source),sort_keys=True,separators=(',',':')).encode()) > limits.max_bytes: raise SelectionError('limit_bytes')
    source_sha = content_hash(source)
    maps = {m.mapping_id:m for m in catalog.mappings}
    assignments = {a.assignment_id:a for a in catalog.assignments}
    byissuer = defaultdict(list)
    for row in rows: byissuer[row.issuer_id].append(row)
    chosen,ignored = [],[]
    for issuer,listings in sorted(byissuer.items()):
        # Do not let share classes duplicate issuer cap or invent conflicting fundamentals.
        values = {(r.market_cap,r.market_cap_asof,r.common_earnings_ttm,r.fundamental_period_end,r.assignment_id,
                   r.cap_evidence.content_hash if r.cap_evidence else None,
                   r.earnings_evidence.content_hash if r.earnings_evidence else None) for r in listings}
        if len(values) != 1: raise SelectionError('conflicting_issuer_inputs')
        ordered = sorted(listings,key=lambda r:(eligibility_reason(r,catalog,policy,asof) is not None,
            -r.dollar_volume,maps[r.mapping_id].mic or '' if r.mapping_id in maps else '',r.instrument_id))
        chosen.append(ordered[0]); ignored.extend(r.instrument_id for r in ordered[1:])
    inputs = tuple(sorted(chosen,key=lambda r:r.issuer_id))
    reasons = {r.issuer_id:eligibility_reason(r,catalog,policy,asof) for r in inputs}
    eligible = [r for r in inputs if reasons[r.issuer_id] is None]
    groups = defaultdict(list)
    for row in eligible: groups[assignments[row.assignment_id].code].append(row)
    valid_signals = {s.sic_code:s for s in signals if s.asof <= asof and s.evidence.eligible(asof)
        and s.asof <= s.evidence.ingested_at and s.momentum_sessions == policy.momentum_sessions
        and s.volume_sessions == policy.volume_window_sessions}
    qualified = {k:v for k,v in groups.items() if len(v) >= policy.minimum_industry_candidates and k in valid_signals}
    momentum = midrank_percentiles({k:valid_signals[k].momentum for k in qualified})
    volume = midrank_percentiles({k:valid_signals[k].volume for k in qualified})
    priority = sorted(qualified,key=lambda k:(-(momentum[k]*Fraction(policy.momentum_weight)+
                         volume[k]*Fraction(policy.volume_weight)),k))[:10]
    capacities = {k:len(groups[k]) for k in priority}
    weights = {k:sum(Fraction(r.market_cap) for r in groups[k]) for k in priority}
    seats = _allocation(capacities,weights)
    members = []
    scores = {}
    for code,group in groups.items():
        cap = midrank_percentiles({r.issuer_id:r.market_cap for r in group})
        yields = {r.issuer_id:earnings_yield(r.common_earnings_ttm,r.market_cap) for r in group}
        levels = {v:i for i,v in enumerate(sorted(set(yields.values())))}
        valuation = midrank_percentiles({k:levels[v] for k,v in yields.items()})
        liquidity = midrank_percentiles({r.issuer_id:r.dollar_volume for r in group})
        for r in group:
            k = r.issuer_id
            score = cap[k]*Fraction(policy.weights.market_cap)+valuation[k]*Fraction(policy.weights.earnings_yield)+liquidity[k]*Fraction(policy.weights.liquidity)
            scores[k] = (score,cap[k],valuation[k],liquidity[k])
        if seats:
            ordered = sorted(group,key=lambda r:(-scores[r.issuer_id][0],-r.market_cap,-r.dollar_volume,r.issuer_id))
            for r in ordered[:seats.get(code,0)]:
                score,cap_rank,val_rank,liq_rank = scores[r.issuer_id]
                members.append(Member(issuer_id=r.issuer_id,instrument_id=r.instrument_id,sic_code=code,selection_rank=1,
                    market_cap_rank=RankFraction.from_fraction(cap_rank),valuation_rank=RankFraction.from_fraction(val_rank),
                    liquidity_rank=RankFraction.from_fraction(liq_rank),score=RankFraction.from_fraction(score)))
    byid = {r.issuer_id:r for r in inputs}
    members.sort(key=lambda m:(-m.score.value,-byid[m.issuer_id].market_cap,-byid[m.issuer_id].dollar_volume,m.issuer_id))
    members = [Member.model_validate(m.model_dump()|{'selection_rank':i}) for i,m in enumerate(members,1)]
    selected = {m.issuer_id for m in members}
    exclusions = [Exclusion(issuer_id=r.issuer_id,reason=reasons[r.issuer_id] or
        ('industry_not_selected' if seats and assignments[r.assignment_id].code not in seats else 'ranked_below_cutoff'),
        evidence_ref='synthetic:'+source_sha) for r in inputs if r.issuer_id not in selected]
    diagnostics = []
    if not seats:
        if len(eligible) < 100: diagnostics.append('insufficient_candidates')
        if len([v for v in groups.values() if len(v)>=policy.minimum_industry_candidates]) < 10: diagnostics.append('insufficient_industries')
        if not diagnostics: diagnostics.append('selection_constraints_unsatisfied')
    snapshot = UniverseSnapshot(universe_version='synthetic:'+source_sha,data_kind='synthetic',dataset_mode='current_snapshot',
        asof=asof,published_at=published_at,effective_at=effective_at,catalog=catalog,catalog_sha256=catalog.content_hash,
        policy=policy,config_sha256=policy.content_hash,inputs=inputs,inputs_sha256=content_hash(inputs),
        coverage=HistoricalCoverage(historical_candidates=False,delisted_identities=False,classification_vintages=False,
            fundamental_vintages=False,evidence_ref='synthetic:'+source_sha,limitations=('Synthetic supplied candidates; no historical coverage.',)),
        status='complete' if seats else 'insufficient',diagnostics=tuple(diagnostics),members=tuple(members),
        industry_counts=tuple(IndustryCount(sic_code=k,count=v) for k,v in (seats or {}).items()),exclusions=tuple(exclusions))
    result = Selection(source_sha256=source_sha,snapshot=snapshot,ignored_listings=tuple(sorted(ignored)))
    if len(result.model_dump_json().encode()) > limits.max_bytes: raise SelectionError('limit_bytes')
    return result


def select_synthetic(catalog,policy,candidates,signals,*,asof,published_at,effective_at,limits=None):
    try: return _select(catalog,policy,candidates,signals,asof,published_at,effective_at,limits)
    except (ValidationError,ValueError,TypeError,KeyError,OverflowError,AttributeError):
        raise SelectionError('invalid_selection_input') from None
