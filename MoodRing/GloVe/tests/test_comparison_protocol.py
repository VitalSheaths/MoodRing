"""Synthetic fixtures only: no retained prices, headlines or fitted forecast models."""
import numpy as np
import pandas as pd
import pytest
from research_data.comparison_protocol import (ARMS,FAMILIES,PROTOCOL,FrozenPreprocessor,
    exclusion_ledger,population_contract,feature_arms,validate_predictions,segmented_date_interval)
from research_data.point_in_time import sessions_between
from research_data.research_experiment import split_for
from research_data.risk_evaluation import variance_metrics
from research_data.sentiment_inventory import input_state,partition


def test_exclusion_explains_anchor_and_prior_context_without_macro_gate():
    days=[d for d,_ in sessions_between('2021-01-04','2021-01-15')]
    f=pd.DataFrame([dict(ticker='SYN',session=d,tail=np.nan if i<2 else .1,
        macro_level=np.nan,eight_session_features_complete=i>=9) for i,d in enumerate(days)])
    p=pd.DataFrame([dict(ticker='SYN',session=d,status='mature_retrospective',
        price_target_candidate=i>=9,price_history_review_required=False,
        adjustment_review_required=False) for i,d in enumerate(days)])
    out=exclusion_ledger(f,p,['tail'])
    assert len(out)==9 and out[0]['missing_anchor_features']==['tail']
    assert out[-1]['exclusive_cause']=='context_only_warmup'
    assert out[-1]['context_missing_features']==[dict(session=days[1],missing_features=['tail'])]
    assert all(r['macro_missing_not_an_exclusion']==['macro_level'] for r in out)


def test_partition_context_gap_dilation_and_missing_channels_do_not_select_rows():
    days=[d for d,_ in sessions_between('2025-01-02','2025-02-28')]
    p=pd.DataFrame([dict(ticker='SYN',session=d,status='mature_retrospective',
        price_target_candidate=True,known_source_gap_window=i==20,observed_headline_count=0)
        for i,d in enumerate(days)])
    out=population_contract(p)
    assert sum(not r['partition_context_complete'] for r in out)==7
    assert [i for i,r in enumerate(out) if r['known_gap_in_context']]==list(range(20,28))
    assert sum(r['primary_matched'] for r in out)==len(days)-15
    assert all(r['arms']==list(ARMS) and not r['released_for_training'] for r in out)
    with pytest.raises(ValueError,match='session_gap'):population_contract(p.drop(index=10))


def test_token_boundary_missing_neutral_and_cache_are_distinct():
    assert input_state('',2)=='missing_text'
    assert input_state('synthetic',512)=='unscored'
    assert input_state('synthetic',513,True)=='overlength'
    assert input_state('synthetic',12,True)=='benchmark_available'
    assert partition('2023-01-01T00:00:00Z')=='dev'


def test_train_only_unique_observations_drop_absent_channels_and_freeze_masks():
    x=[[1,np.nan,0],[3,np.nan,2]]
    scaler=FrozenPreprocessor().fit(x,['2021-01-04T21:20:00Z','2021-01-05T21:20:00Z'],
        [('2021-01-04','SYN'),('2021-01-05','SYN')],['price','absent','tone'])
    before=scaler.scaler.mean.copy();result=scaler.transform([[np.nan,999,0],[1,-999,np.nan]])
    assert scaler.dropped==['absent']
    assert result[0,-2]==1 and result[0,-1]==0 and result[1,-1]==1
    scaler.transform([[1e9,1e9,-1e9]])
    np.testing.assert_array_equal(before,scaler.scaler.mean)
    with pytest.raises(ValueError,match='train_only'):FrozenPreprocessor().fit([[1]],['2025-01-02'],[('d','s')],['x'])
    with pytest.raises(ValueError,match='unique_training'):FrozenPreprocessor().fit([[1],[2]],['2021-01-01']*2,[('d','s')]*2,['x'])


def test_feature_arms_distinguish_coverage_and_tone_without_label_leakage():
    arms=feature_arms(['return_1','macro'],['observed_count','missing_mask'],['direct_sentiment'],['product_sentiment','sector_sentiment'])
    assert arms[ARMS[0]]==['return_1','macro']
    assert arms[ARMS[1]]==arms[ARMS[0]]+['observed_count','missing_mask']
    assert arms[ARMS[-1]]==arms[ARMS[1]]+['direct_sentiment','product_sentiment','sector_sentiment']
    with pytest.raises(ValueError,match='tone_in_coverage'):feature_arms(['r'],['tone'],[],[])
    with pytest.raises(ValueError,match='outcome'):feature_arms(['annualized_variance'],[],[],[])


@pytest.mark.parametrize('family',FAMILIES)
def test_all_families_use_positive_variance_and_exact_shared_rows(family):
    keys=[('2024-01-03','SYN1'),('2024-01-03','SYN2')]
    arms={(f,a):(keys,[.04,.09]) for f in FAMILIES for a in ARMS}
    validate_predictions(keys,arms)
    arms[(family,ARMS[0])]=(list(reversed(keys)),[.04,.09])
    with pytest.raises(ValueError,match='unmatched'):validate_predictions(keys,arms)
    arms[(family,ARMS[0])]=(keys,[0,.09])
    with pytest.raises(ValueError,match='positive_variance'):validate_predictions(keys,arms)


def test_variance_qlike_and_volatility_errors_keep_extremes():
    m=variance_metrics([.04,.09],[.01,.16])
    assert m['qlike']==pytest.approx((np.log(.04)+.01/.04+np.log(.09)+.16/.09)/2)
    assert m['volatility_mae']==pytest.approx(.1)
    assert m['volatility_rmse']==pytest.approx(.1)
    assert variance_metrics([.04],[100])['qlike']>variance_metrics([.04],[1])['qlike']
    assert PROTOCOL['family_fit_contracts']['historical_variance_20'].startswith('no fitting')


def test_historical_benchmark_is_identical_across_feature_arms():
    keys=[('2024-01-03','SYN')]
    arms={(f,a):(keys,[.04]) for f in FAMILIES for a in ARMS}
    arms[('historical_variance_20',ARMS[-1])]=(keys,[.09])
    with pytest.raises(ValueError,match='must_not_depend'):validate_predictions(keys,arms)


def test_twenty_session_benchmark_and_five_session_purging():
    from research_data.risk_objective import historical_variance
    days=sessions_between('2021-01-04','2021-02-05')[-20:]
    values=[.01]*19+[-.20]
    rows=[dict(session=d,value=v,available_at=t.isoformat(),evidence_resolved=True) for (d,t),v in zip(days,values)]
    result=historical_variance(days[-1][0]+'T21:20:00Z',rows)
    assert result['variance']==pytest.approx(252*np.mean(np.square(values)))
    boundary=sessions_between('2022-12-01','2023-01-10')
    purged=[d for i,(d,t) in enumerate(boundary[:-5]) if d<'2023-01-01' and split_for(t,boundary[i+5][1]) is None]
    assert len(purged)==5


def test_date_blocks_preserve_pairs_stocks_overlap_and_gaps():
    days=[d for d,_ in sessions_between('2024-01-02','2024-05-31')]
    kept=days[:30]+days[40:70]
    dates=[d for d in kept for _ in range(2)];tickers=['SYN1','SYN2']*len(kept)
    result=segmented_date_interval(dates,tickers,[3]*len(dates),[1]*len(dates),repeats=100)
    assert result['segment_lengths']==[30,30] and not result['gap_bridging']
    assert result['mean_paired_difference']==result['lower_95']==result['upper_95']==2
    with pytest.raises(ValueError,match='bootstrap_bound'):
        segmented_date_interval(dates,tickers,[1]*len(dates),[0]*len(dates),block_sessions=4)


def test_release_flags_cannot_be_confused_with_retrospective_acceptance():
    assert PROTOCOL['full_scoring_held'] and PROTOCOL['training_held'] and not PROTOCOL['strict_PIT_eligible']


@pytest.mark.parametrize('violation',['held','title','future','time_changed'])
def test_inventory_fails_closed_on_source_or_hold_violations(tmp_path,violation):
    import pyarrow as pa
    from research_data.archive import publish,digest,read_bytes
    from research_data.sentiment_inventory import source_pairs
    day='2024-01-03';original=dict(ticker='SYN',source_record_sha256='a'*64,
        source_manifest_sha256='b'*64,source_manifest_row=0,availability_at=day+'T20:00:00Z',
        gdelt_seen_at=day+'T20:00:00Z',published_at=None,ingested_at='2026-11-03T00:00:00Z',
        title='Synthetic company news',language='en',status='eligible_for_observation_analysis',review_hold_reasons=[])
    if violation=='held':original['review_hold_reasons']=['company_attribution']
    if violation=='future':original['availability_at']=day+'T22:00:00Z'
    aligned={k:original[k] for k in ('ticker','source_record_sha256','source_manifest_sha256','source_manifest_row',
        'availability_at','gdelt_seen_at','published_at','ingested_at')}
    aligned.update(session=day,cutoff=day+'T21:20:00Z',exact_model_input_sha256=digest(original['title'].encode()))
    if violation=='title':aligned['exact_model_input_sha256']='c'*64
    if violation=='time_changed':aligned['availability_at']=day+'T19:00:00Z'
    source=tmp_path/'audit/curated'/day;publish(source,{'eligible_headlines':pa.Table.from_pylist([original])},{})
    dest=tmp_path/'build/news/alignment'/day
    publish(dest,{'observations':pa.Table.from_pylist([aligned])},dict(source_day_manifest_sha256=digest(read_bytes(source/'manifest.json'))))
    receipts={'build-v2/news/alignment/'+day+'/manifest.json':digest(read_bytes(dest/'manifest.json'))}
    with pytest.raises(ValueError):list(source_pairs(tmp_path/'build',tmp_path/'audit',receipts))
