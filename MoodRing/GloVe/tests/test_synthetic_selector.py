from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import random

import pytest

from ingestion.universe.identity import IdentityCatalog
from ingestion.universe.ranking import RankingInput, SelectionPolicy
from ingestion.universe.selector import IndustrySignal, SelectionError, SelectorLimits, _allocation, select_synthetic
from synthetic_support import lesson


def choose(rows=None,signals=None,catalog=None,policy=None,**kwargs):
    cat,pol,inputs,metrics=lesson.inputs()
    return select_synthetic(catalog or cat,policy or pol,inputs if rows is None else rows,metrics if signals is None else signals,
        asof='2026-10-18T20:00:00Z',published_at='2026-10-19T00:00:00Z',effective_at='2026-10-19T13:30:00Z',**kwargs)


def update(row,**kwargs):return type(row).model_validate(row.model_dump()|kwargs)


def test_oversized_pool_selects_exact_unique_issuers_with_variable_sic_quotas():
    result=choose(); snapshot=result.snapshot
    assert len(snapshot.inputs)==191 and len(snapshot.members)==100
    assert len({m.issuer_id for m in snapshot.members})==100
    assert len(snapshot.industry_counts)==10 and len({c.count for c in snapshot.industry_counts})>1
    assert sum(c.count for c in snapshot.industry_counts)==100
    assert len(snapshot.exclusions)==91
    assert snapshot.data_kind=='synthetic' and snapshot.dataset_mode=='current_snapshot'
    assert all(m.sic_code!='1000' for m in snapshot.members)
    assert snapshot.content_hash==lesson.selection().snapshot.content_hash


def test_shuffled_candidates_signals_and_catalog_preserve_hash():
    catalog,_,rows,signals=lesson.inputs(); rows=list(rows);signals=list(signals)
    random.Random(42).shuffle(rows);random.Random(6).shuffle(signals)
    data=catalog.model_dump()
    for key in ('issuers','instruments','mappings','assignments'): data[key]=tuple(reversed(data[key]))
    assert choose(rows,signals,IdentityCatalog.model_validate(data))==choose()


def test_negative_earnings_remain_ranked_missing_earnings_exclude():
    rows=list(lesson.inputs()[2]); negative=next(r for r in rows if r.common_earnings_ttm<0)
    assert negative.issuer_id in {r.issuer_id for r in choose().snapshot.inputs}
    rows[0]=update(rows[0],common_earnings_ttm=None,missing_earnings_reason='synthetic_missing')
    result=choose(rows)
    assert next(e.reason for e in result.snapshot.exclusions if e.issuer_id==rows[0].issuer_id)=='missing_earnings'


@pytest.mark.parametrize('change,expected',[
    ('stale','stale_input'),('future','future_evidence'),('unknown','unknown_availability'),
    ('suspended','suspended'),('history','insufficient_history'),('price','below_threshold'),('cap','missing_cap')])
def test_existing_eligibility_rules_are_reused(change,expected):
    rows=list(lesson.inputs()[2]); row=rows[0]
    if change=='stale': row=update(row,price_age_sessions=1)
    if change=='future': row=update(row,liquidity_end='2026-10-19T00:00:00Z')
    if change=='unknown': row=update(row,liquidity_evidence=update(row.liquidity_evidence,available_at=None))
    if change=='suspended': row=update(row,trading_status='suspended')
    if change=='history': row=update(row,history_sessions=1)
    if change=='price': row=update(row,last_price='1')
    if change=='cap': row=update(row,market_cap=None,missing_cap_reason='synthetic_missing')
    rows[0]=row; result=choose(rows)
    assert next(e.reason for e in result.snapshot.exclusions if e.issuer_id==row.issuer_id)==expected


def test_primary_listing_liquidity_then_mic_and_identity_without_double_counting():
    catalog,_,rows,_=lesson.inputs(); base=rows[6]; data=catalog.model_dump()
    instrument=next(i for i in catalog.instruments if i.instrument_id==base.instrument_id)
    mapping=next(m for m in catalog.mappings if m.mapping_id==base.mapping_id)
    data['instruments']+= (instrument.model_dump()|dict(instrument_id='instrument:secondary'),)
    data['mappings']+= (mapping.model_dump()|dict(mapping_id='canonical:secondary',instrument_id='instrument:secondary',symbol='SECONDARY',mic='XNYS'),)
    second=update(base,instrument_id='instrument:secondary',mapping_id='canonical:secondary',dollar_volume=base.dollar_volume+1)
    result=choose(list(rows)+[second],catalog=IdentityCatalog.model_validate(data))
    assert len(result.snapshot.inputs)==191
    assert next(r.instrument_id for r in result.snapshot.inputs if r.issuer_id==base.issuer_id)=='instrument:secondary'
    assert base.instrument_id in result.ignored_listings


@pytest.mark.parametrize('mode',['duplicate','mixed_feed','conflicting_listing'])
def test_ambiguous_or_duplicate_sources_fail(mode):
    rows=list(lesson.inputs()[2])
    if mode=='duplicate': rows.append(rows[0])
    elif mode=='mixed_feed': rows[0]=update(rows[0],liquidity_feed='other')
    else: rows.append(update(rows[0],instrument_id='instrument:other',common_earnings_ttm='1'))
    with pytest.raises(SelectionError):choose(rows)


@pytest.mark.parametrize('kind',['candidates','industries','capacity','signal_evidence'])
def test_insufficiency_never_publishes_partial_members(kind):
    _,_,rows,signals=lesson.inputs()
    if kind=='candidates': rows=rows[:90]
    if kind=='industries': rows=tuple(r for r in rows if r.issuer_id.split(':')[1][:3]<'g09')
    if kind=='capacity': rows=tuple(r for r in rows if int(r.issuer_id[-2:])<9)
    if kind=='signal_evidence': signals=tuple(update(s,evidence=update(s.evidence,available_at=None)) for s in signals)
    result=choose(rows,signals).snapshot
    assert result.status=='insufficient' and result.diagnostics
    assert not result.members and not result.industry_counts
    assert len(result.exclusions)==len(result.inputs)


def test_capacity_redistribution_and_exact_remainder_ties():
    capacities={f'{i:04d}':20 for i in range(10)}
    weights={k:Fraction(1) for k in capacities}
    assert set(_allocation(capacities,weights).values())=={10}
    capacities['0000']=1;weights['0000']=Fraction(100000)
    seats=_allocation(capacities,weights)
    assert seats['0000']==1 and sum(seats.values())==100
    assert all(seats[k]<=capacities[k] for k in seats)
    assert _allocation({k:9 for k in capacities},weights) is None


def test_tied_industry_signals_use_sic_order():
    signals=tuple(update(s,momentum='1',volume='1') for s in lesson.inputs()[3])
    picked=choose(signals=signals).snapshot
    assert {c.sic_code for c in picked.industry_counts}==set(sorted(s.sic_code for s in signals)[:10])


@pytest.mark.parametrize('limits,code',[(dict(max_candidates=190),'limit_candidates'),
    (dict(max_industries=10),'limit_industries'),(dict(max_bytes=1),'limit_bytes')])
def test_selector_bounds(limits,code):
    with pytest.raises(SelectionError,match=code):choose(limits=limits)


@pytest.mark.parametrize('field',list(SelectorLimits.model_fields))
@pytest.mark.parametrize('value',[0,True,'1'])
def test_invalid_or_coerced_limits_fail(field,value):
    with pytest.raises(SelectionError):choose(limits={field:value})


def test_observed_policy_and_future_times_cannot_be_claimed():
    policy=update(lesson.inputs()[1],review_status='reviewed')
    with pytest.raises(SelectionError,match='synthetic_policy_required'):choose(policy=policy)
    with pytest.raises(SelectionError):choose(limits={'max_candidates':1001})


def test_industry_signal_change_is_bound_to_universe_version():
    signals=list(lesson.inputs()[3]); signals[0]=update(signals[0],volume='100')
    assert choose(signals=signals).snapshot.content_hash!=choose().snapshot.content_hash
