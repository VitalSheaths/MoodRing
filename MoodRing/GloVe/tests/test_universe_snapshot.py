from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from ingestion.universe.identity import IdentityCatalog, content_hash
from ingestion.universe.ranking import MetricWeights, RankFraction, RankingInput, SelectionPolicy, earnings_yield, midrank_percentiles
from ingestion.universe.snapshot import UniverseSnapshot
from universe_fixtures import ASOF, fixture_data, snapshot


@pytest.fixture(scope='module')
def complete():
    return snapshot()


def rehash(data):
    data['catalog_sha256']=IdentityCatalog.model_validate(data['catalog']).content_hash
    data['config_sha256']=SelectionPolicy.model_validate(data['policy']).content_hash
    data['inputs_sha256']=content_hash(tuple(sorted((RankingInput.model_validate(r) for r in data['inputs']),key=lambda r:r.issuer_id)))
    return data


def test_exact_midrank_ties_and_missingness():
    assert midrank_percentiles({})=={}
    assert midrank_percentiles({'a':'5'})=={'a':Fraction(1,2)}
    assert midrank_percentiles({'b':'2','a':'2','c':'4'})=={'a':Fraction(1,4),'b':Fraction(1,4),'c':Fraction(1)}
    assert set(midrank_percentiles({'a':'0','b':'0'}).values())=={Fraction(1,2)}
    assert earnings_yield('-5','100')==Fraction(-1,20)
    assert earnings_yield(None,'100') is None
    for cap in (None,'0','-1'):
        with pytest.raises(ValueError,match='invalid_market_cap'): earnings_yield('1',cap)
    with pytest.raises(ValueError): midrank_percentiles({'a':float('nan')})


def test_ranking_does_not_depend_on_decimal_context():
    with localcontext() as ctx:
        ctx.prec=3
        low=earnings_yield('1','7')
        a=midrank_percentiles({'z':'1.000000000002','a':'1.000000000001'})
    with localcontext() as ctx:
        ctx.prec=30
        assert earnings_yield('1','7')==low
        assert midrank_percentiles({'a':'1.000000000001','z':'1.000000000002'})==a


def test_complete_fixture_and_nonuniform_counts(complete):
    assert len(complete.members)==100
    assert len({m.issuer_id for m in complete.members})==100
    assert len(complete.industry_counts)==10
    assert len({c.count for c in complete.industry_counts})>1
    assert any(r.common_earnings_ttm<0 for r in complete.inputs)
    assert complete.policy.review_status=='fixture_only' and complete.data_kind=='synthetic'
    expected=(Path(__file__).parent/'fixtures/universe/expected_snapshot_sha256.txt').read_text().strip()
    assert complete.content_hash==expected
    assert UniverseSnapshot.model_validate_json(complete.model_dump_json()).content_hash==expected


@pytest.mark.parametrize('change',['duplicate_issuer','wrong_count','wrong_sic','wrong_score','wrong_rank','wrong_hash'])
def test_membership_rejects_inconsistent_evidence(complete,change):
    data=complete.model_dump(mode='json')
    if change=='duplicate_issuer': data['members'][1]=data['members'][0]
    if change=='wrong_count': data['industry_counts'][0]['count']+=1
    if change=='wrong_sic': data['members'][0]['sic_code']='9999'
    if change=='wrong_score': data['members'][0]['score']={'numerator':1,'denominator':2}
    if change=='wrong_rank': data['members'][0]['selection_rank']=data['members'][1]['selection_rank']
    if change=='wrong_hash': data['inputs_sha256']='0'*64
    with pytest.raises(ValidationError): UniverseSnapshot.model_validate(data)


def test_each_input_has_one_outcome_and_partial_membership_is_not_a_universe(complete):
    data=complete.model_dump(mode='json'); data['members'].pop()
    with pytest.raises(ValidationError): UniverseSnapshot.model_validate(data)
    data=complete.model_dump(mode='json'); data['exclusions']=[dict(issuer_id=data['members'][0]['issuer_id'],reason='ranked_below_cutoff',evidence_ref='fixture://same')]
    with pytest.raises(ValidationError,match='exactly one'): UniverseSnapshot.model_validate(data)


def test_insufficient_candidate_and_category_diagnostics(complete):
    data=complete.model_dump(mode='json')
    data.update(status='insufficient',members=[],industry_counts=[],diagnostics=['insufficient_candidates','insufficient_industries'])
    data['inputs']=data['inputs'][:2]
    data['exclusions']=[dict(issuer_id=r['issuer_id'],reason='industry_not_selected',evidence_ref='fixture://insufficient') for r in data['inputs']]
    small=UniverseSnapshot.model_validate(rehash(data))
    assert not small.members and small.status=='insufficient'
    data=complete.model_dump(mode='json'); data.update(status='insufficient',members=[],industry_counts=[],diagnostics=['insufficient_candidates'])
    data['exclusions']=[dict(issuer_id=r['issuer_id'],reason='ranked_below_cutoff',evidence_ref='fixture://all') for r in data['inputs']]
    with pytest.raises(ValidationError,match='contradicts'): UniverseSnapshot.model_validate(data)


@pytest.mark.parametrize('reason',['missing_earnings','unknown_availability','future_evidence','stale_input','insufficient_history','below_threshold'])
def test_bad_inputs_require_explicit_exclusion(complete,reason):
    data=complete.model_dump(mode='json'); row=data['inputs'][0]
    if reason=='missing_earnings': row.update(common_earnings_ttm=None,missing_earnings_reason='not_reported')
    if reason=='unknown_availability': row['cap_evidence']['available_at']=None
    if reason=='future_evidence': row['cap_evidence']['available_at']='2026-10-20T21:00:00Z'
    if reason=='stale_input': row['price_age_sessions']=2
    if reason=='insufficient_history': row['history_sessions']=1
    if reason=='below_threshold': row['last_price']='1'
    with pytest.raises(ValidationError,match='explicit exclusion'): UniverseSnapshot.model_validate(rehash(data))


def test_current_snapshot_cannot_be_relabeled_as_pit(complete):
    data=complete.model_dump(mode='json'); data['dataset_mode']='point_in_time'
    with pytest.raises(ValidationError,match='historical coverage'): UniverseSnapshot.model_validate(data)
    data=complete.model_dump(mode='json'); data['data_kind']='observed'
    with pytest.raises(ValidationError,match='Illustrative'): UniverseSnapshot.model_validate(data)


def test_no_hidden_policy_defaults_or_float_inputs(complete):
    values=complete.policy.model_dump(mode='json')
    for field in ('weights','minimum_price','allocation','tie_breakers','review_ref'):
        missing=dict(values); missing.pop(field)
        with pytest.raises(ValidationError): SelectionPolicy.model_validate(missing)
    values['weights']['market_cap']=.4
    with pytest.raises(ValidationError,match='binary floats'): SelectionPolicy.model_validate(values)
    with pytest.raises(ValidationError): MetricWeights(market_cap='0.5',earnings_yield='0.5',liquidity='0.5')
    with pytest.raises(ValidationError): RankFraction(numerator=2,denominator=4)
    with pytest.raises(ValidationError): RankingInput.model_validate(complete.inputs[0].model_dump()|{'market_cap':'0'})


def test_feed_mixing_and_future_classification_are_rejected(complete):
    data=complete.model_dump(mode='json'); data['inputs'][0]['liquidity_feed']='different-feed'
    with pytest.raises(ValidationError,match='mix'): UniverseSnapshot.model_validate(rehash(data))
    data=complete.model_dump(mode='json')
    assignment=next(a for a in data['catalog']['assignments'] if a['assignment_id']==data['inputs'][0]['assignment_id'])
    assignment['evidence']['available_at']='2026-10-20T21:00:00Z'
    with pytest.raises(ValidationError,match='future_evidence'): UniverseSnapshot.model_validate(rehash(data))


def test_canonical_order_decimal_spelling_and_versioning(complete):
    data=complete.model_dump(mode='json'); data['inputs'].reverse(); data['members'].reverse(); data['industry_counts'].reverse()
    data['inputs'][0]['market_cap']+='0' if '.' in data['inputs'][0]['market_cap'] else '.000'
    assert UniverseSnapshot.model_validate(data).content_hash==complete.content_hash
    data=complete.model_dump(mode='json'); data['universe_version']='another-version'
    assert UniverseSnapshot.model_validate(data).content_hash!=complete.content_hash
    data=complete.model_dump(mode='json'); data['schema_version']='universe-contract-v2'
    with pytest.raises(ValidationError): UniverseSnapshot.model_validate(data)


def test_missing_data_can_be_reported_as_insufficiency_without_padding(complete):
    data=complete.model_dump(mode='json'); missing=data['inputs'][0]
    missing.update(common_earnings_ttm=None,missing_earnings_reason='not_reported')
    data.update(status='insufficient',members=[],industry_counts=[],diagnostics=['insufficient_candidates'])
    data['exclusions']=[dict(issuer_id=r['issuer_id'],reason='missing_earnings' if r is missing else 'ranked_below_cutoff',
                             evidence_ref='fixture://insufficient') for r in data['inputs']]
    result=UniverseSnapshot.model_validate(rehash(data))
    assert len(result.inputs)==100 and not result.members
    assert sum(e.reason=='missing_earnings' for e in result.exclusions)==1


def test_synthetic_pit_contract_label_needs_all_coverage_declarations(complete):
    data=complete.model_dump(mode='json'); data['dataset_mode']='point_in_time'
    for field in ('historical_candidates','delisted_identities','classification_vintages','fundamental_vintages'):
        data['coverage'][field]=True
    result=UniverseSnapshot.model_validate(data)
    assert result.dataset_mode=='point_in_time' and result.data_kind=='synthetic'


@pytest.mark.parametrize('missing',['cap','earnings'])
def test_absent_fundamentals_do_not_require_fabricated_dates(complete,missing):
    data=complete.model_dump(mode='json'); row=data['inputs'][0]
    if missing=='cap':
        row.update(market_cap=None,missing_cap_reason='not_observed',market_cap_asof=None,cap_evidence=None)
    else:
        row.update(common_earnings_ttm=None,missing_earnings_reason='not_observed',fundamental_period_end=None,earnings_evidence=None)
    data.update(status='insufficient',members=[],industry_counts=[],diagnostics=['insufficient_candidates'])
    data['exclusions']=[dict(issuer_id=r['issuer_id'],reason='missing_'+missing if r is row else 'ranked_below_cutoff',
                             evidence_ref='fixture://missing-observation') for r in data['inputs']]
    result=UniverseSnapshot.model_validate(rehash(data))
    assert not result.members
    assert any(e.reason=='missing_'+missing for e in result.exclusions)


def test_suspended_and_unknown_trading_status_are_not_eligible(complete):
    for status in ('suspended','unknown'):
        data=complete.model_dump(mode='json'); data['inputs'][0]['trading_status']=status
        with pytest.raises(ValidationError,match='explicit exclusion'):
            UniverseSnapshot.model_validate(rehash(data))


def test_optional_share_counts_preserve_provider_basis(complete):
    row=complete.inputs[0].model_dump()
    with pytest.raises(ValidationError,match='Share counts'):
        RankingInput.model_validate(row|{'weighted_shares_outstanding':'100'})
    row.update(weighted_shares_outstanding='100',share_class_shares_outstanding='30',share_basis='provider_company_equivalent',
               shares_asof=ASOF,shares_evidence=complete.inputs[0].cap_evidence)
    valid=RankingInput.model_validate(row)
    assert valid.weighted_shares_outstanding!=valid.share_class_shares_outstanding
