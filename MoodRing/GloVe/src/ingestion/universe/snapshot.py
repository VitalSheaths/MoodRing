"""Validate supplied membership snapshots without implementing universe selection."""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
from typing import Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from ingestion.sec.contracts import Sha256
from ingestion.universe.identity import Contract, IdentityCatalog, Id, Time, content_hash
from ingestion.universe.ranking import RankFraction, RankingInput, SelectionPolicy, earnings_yield, midrank_percentiles


class HistoricalCoverage(Contract):
    historical_candidates: bool
    delisted_identities: bool
    classification_vintages: bool
    fundamental_vintages: bool
    evidence_ref: str = Field(min_length=1, max_length=1000)
    limitations: tuple[str, ...]


class Member(Contract):
    issuer_id: Id
    instrument_id: Id
    sic_code: str = Field(pattern=r'^\d{4}$')
    selection_rank: StrictInt = Field(ge=1, le=100)
    market_cap_rank: RankFraction
    valuation_rank: RankFraction
    liquidity_rank: RankFraction
    score: RankFraction


Reason = Literal['missing_earnings', 'unknown_availability', 'future_evidence', 'ineligible_identity',
                 'stale_input', 'insufficient_history', 'below_threshold', 'ranked_below_cutoff',
                 'industry_not_selected', 'suspended', 'unknown_trading_status', 'missing_cap', 'invalid_cap', 'unsupported_type', 'ambiguous_source']


class IndustryCount(Contract):
    sic_code: str = Field(pattern=r'^\d{4}$')
    count: StrictInt = Field(gt=0, le=100)


class Exclusion(Contract):
    issuer_id: Id
    reason: Reason
    evidence_ref: str = Field(min_length=1, max_length=1000)


def eligibility_reason(row, catalog, policy, cutoff):
    issuers = {i.issuer_id: i for i in catalog.issuers}
    instruments = {i.instrument_id: i for i in catalog.instruments}
    assignments = {a.assignment_id: a for a in catalog.assignments}
    mappings = {m.mapping_id: m for m in catalog.mappings}
    issuer, instrument = issuers.get(row.issuer_id), instruments.get(row.instrument_id)
    assignment, mapping = assignments.get(row.assignment_id), mappings.get(row.mapping_id)
    if not issuer or not instrument or not assignment or not mapping:
        return 'ineligible_identity'
    if (instrument.issuer_id != issuer.issuer_id or instrument.kind != 'common_stock' or not instrument.tradable
            or instrument.country != policy.country or issuer.country != policy.country or instrument.currency != policy.currency
            or assignment.issuer_id != issuer.issuer_id or mapping.instrument_id != instrument.instrument_id
            or mapping.provider != 'canonical' or mapping.mic not in policy.venues):
        return 'ineligible_identity'
    evidence = tuple(e for e in (issuer.evidence, instrument.evidence, assignment.evidence, mapping.evidence,
                row.cap_evidence, row.earnings_evidence, row.liquidity_evidence, row.shares_evidence) if e is not None)
    if any(e.available_at is None for e in evidence):
        return 'unknown_availability'
    if any(not e.eligible(cutoff) for e in evidence) or (row.market_cap_asof is not None and row.market_cap_asof > cutoff) or row.liquidity_end > cutoff or (row.fundamental_period_end is not None and row.fundamental_period_end > cutoff.date()) or catalog.taxonomy.captured_at > cutoff:
        return 'future_evidence'
    if not all(r.eligible(cutoff) for r in (instrument, assignment, mapping)):
        return 'ineligible_identity'
    if row.shares_asof is not None and row.shares_asof > cutoff:
        return 'future_evidence'
    if row.trading_status != 'trading':
        return 'suspended' if row.trading_status == 'suspended' else 'unknown_trading_status'
    if row.market_cap is None:
        return 'missing_cap'
    if row.common_earnings_ttm is None:
        return 'missing_earnings'
    if row.price_age_sessions > policy.max_price_age_sessions or (cutoff.date()-row.fundamental_period_end).days > policy.max_fundamental_age_days:
        return 'stale_input'
    if row.history_sessions < policy.minimum_history_sessions or row.liquidity_sessions != policy.liquidity_lookback_sessions:
        return 'insufficient_history'
    if row.last_price < policy.minimum_price or row.dollar_volume < policy.minimum_dollar_volume:
        return 'below_threshold'
    return None


class UniverseSnapshot(Contract):
    universe_version: Id
    data_kind: Literal['synthetic', 'observed']
    dataset_mode: Literal['current_snapshot', 'point_in_time']
    asof: Time
    published_at: Time
    effective_at: Time
    catalog: IdentityCatalog
    catalog_sha256: Sha256
    policy: SelectionPolicy
    config_sha256: Sha256
    inputs: tuple[RankingInput, ...] = Field(max_length=10000)
    inputs_sha256: Sha256
    coverage: HistoricalCoverage
    status: Literal['complete', 'insufficient']
    diagnostics: tuple[Literal['insufficient_candidates', 'insufficient_industries', 'selection_constraints_unsatisfied'], ...]
    members: tuple[Member, ...] = Field(max_length=100)
    industry_counts: tuple[IndustryCount, ...] = Field(max_length=10)
    exclusions: tuple[Exclusion, ...] = Field(max_length=10000)

    @field_validator('inputs', 'members', 'exclusions')
    @classmethod
    def canonical_sets(cls, value):
        return tuple(sorted(value, key=lambda row: row.issuer_id))

    @field_validator('industry_counts')
    @classmethod
    def canonical_counts(cls, value):
        if len({r.sic_code for r in value}) != len(value):
            raise ValueError('Duplicate industry count')
        return tuple(sorted(value, key=lambda r: r.sic_code))

    @model_validator(mode='after')
    def snapshot(self):
        if not self.asof <= self.published_at <= self.effective_at:
            raise ValueError('Universe must be published after cutoff and before it takes effect')
        if self.catalog.content_hash != self.catalog_sha256 or self.policy.content_hash != self.config_sha256 or content_hash(self.inputs) != self.inputs_sha256:
            raise ValueError('Catalogue, configuration or input checksum mismatch')
        if self.data_kind == 'observed' and self.policy.review_status != 'reviewed':
            raise ValueError('Illustrative policy cannot certify an observed universe')
        if self.dataset_mode == 'point_in_time' and not all((self.coverage.historical_candidates, self.coverage.delisted_identities,
                self.coverage.classification_vintages, self.coverage.fundamental_vintages)):
            raise ValueError('Point-in-time label requires explicit historical coverage evidence')
        inputs = {r.issuer_id: r for r in self.inputs}
        selected = {m.issuer_id: m for m in self.members}
        excluded = {e.issuer_id: e for e in self.exclusions}
        if len(inputs) != len(self.inputs) or len(selected) != len(self.members) or len(excluded) != len(self.exclusions):
            raise ValueError('Duplicate issuer in inputs, membership or exclusions')
        if selected.keys() & excluded.keys() or selected.keys() | excluded.keys() != inputs.keys():
            raise ValueError('Every candidate must have exactly one selected or excluded outcome')
        scopes = {(r.liquidity_provider, r.liquidity_feed, r.volume_scope, r.liquidity_metric) for r in self.inputs}
        if len(scopes) > 1 or any(r.volume_scope != self.policy.volume_scope or r.liquidity_metric != self.policy.liquidity_metric for r in self.inputs):
            raise ValueError('Do not mix provider/feed or liquidity measurement bases in one ranking')
        reasons = {key: eligibility_reason(row, self.catalog, self.policy, self.asof) for key, row in inputs.items()}
        for key, reason in reasons.items():
            if reason is not None and (key not in excluded or excluded[key].reason != reason):
                raise ValueError(f'Ineligible candidate needs explicit exclusion: {reason}')
            if reason is None and key in excluded and excluded[key].reason not in ('ranked_below_cutoff', 'industry_not_selected'):
                raise ValueError('Eligible candidate cannot have a fabricated input-failure reason')
        assignments = {a.assignment_id: a for a in self.catalog.assignments}
        eligible = {key: row for key, row in inputs.items() if reasons[key] is None}
        groups = {}
        for row in eligible.values():
            groups.setdefault(assignments[row.assignment_id].code, []).append(row)
        if self.status == 'insufficient':
            if self.members or self.industry_counts or not self.diagnostics:
                raise ValueError('Insufficient snapshots publish diagnostics, not partial membership')
            if 'insufficient_candidates' in self.diagnostics and len(eligible) >= 100:
                raise ValueError('Candidate insufficiency contradicts validated inputs')
            if 'insufficient_industries' in self.diagnostics and len([g for g in groups.values() if len(g) >= self.policy.minimum_industry_candidates]) >= 10:
                raise ValueError('Industry insufficiency contradicts validated inputs')
            return self
        counts = Counter(m.sic_code for m in self.members)
        if self.diagnostics or len(self.members) != 100 or len(counts) != 10 or dict(counts) != {r.sic_code:r.count for r in self.industry_counts}:
            raise ValueError('Complete snapshot needs exactly 100 issuers, ten SIC categories and matching counts')
        if sorted(m.selection_rank for m in self.members) != list(range(1,101)):
            raise ValueError('Selection ranks must cover 1 through 100 exactly')
        for code, members in groups.items():
            cap = midrank_percentiles({r.issuer_id: r.market_cap for r in members})
            # Keep rational yields exact; midrank accepts finite Decimals, so rank rational yields directly below.
            yields = {r.issuer_id: earnings_yield(r.common_earnings_ttm, r.market_cap) for r in members}
            ordered_yields = {v:i for i,v in enumerate(sorted(set(yields.values())))}
            # Ordinal levels preserve exact rational equality/order for the percentile primitive.
            valuation = midrank_percentiles({key: ordered_yields[value] for key,value in yields.items()})
            liquidity = midrank_percentiles({r.issuer_id: r.dollar_volume for r in members})
            for row in members:
                member = selected.get(row.issuer_id)
                if member is None:
                    continue
                if len(members) < self.policy.minimum_industry_candidates or member.sic_code != code or member.instrument_id != row.instrument_id:
                    raise ValueError('Membership conflicts with identity or SIC eligibility')
                w = self.policy.weights
                score = cap[row.issuer_id]*Fraction(w.market_cap) + valuation[row.issuer_id]*Fraction(w.earnings_yield) + liquidity[row.issuer_id]*Fraction(w.liquidity)
                if tuple(r.value for r in (member.market_cap_rank,member.valuation_rank,member.liquidity_rank,member.score)) != (cap[row.issuer_id],valuation[row.issuer_id],liquidity[row.issuer_id],score):
                    raise ValueError('Supplied component ranks or score do not match candidate evidence')
        ordered = sorted(self.members, key=lambda m: (-m.score.value,-inputs[m.issuer_id].market_cap,-inputs[m.issuer_id].dollar_volume,m.issuer_id))
        if any(member.selection_rank != rank for rank,member in enumerate(ordered,1)):
            raise ValueError('Selection rank violates deterministic score/tie ordering')
        return self
