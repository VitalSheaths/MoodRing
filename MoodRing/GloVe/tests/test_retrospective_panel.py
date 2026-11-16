"""Retrospective basis, leakage, horizon and source-preservation boundaries."""
from copy import deepcopy
from datetime import timedelta
import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from research_data import retrospective_panel as rp
from research_data.retrospective_news import align_observation, align_news
from research_data.archive import publish, verified_tables, digest, json_bytes
from research_data.research_experiment import TrainOnlyStandardizer


def prices(sessions, ticker='AAPL'):
    """Deterministic finite fixture with both gains and losses; no provider calls."""
    result=[]
    for i,(s,_) in enumerate(sessions):
        c=100+i*.1+np.sin(i)
        result.append(dict(ticker=ticker,session=s,open=str(c),high=str(c+2),low=str(c-2),
            close=str(c),adj_close=str(c*.9),volume='1000',dividends='0',stock_splits='0',
            adjustment='provider_split_adjusted_ohlc_separate_dividend_adjusted_close',
            ingested_at='2026-11-04T00:00:00+00:00',available_at=None,historical_prediction_eligible=False))
    return result


def news(stamp, title='Apple launches a new product'):
    return dict(ticker='AAPL',status='eligible_for_observation_analysis',review_hold_reasons=[],
        availability_at=stamp,gdelt_seen_at=stamp,published_at=None,ingested_at='2026-11-04T00:00:00Z',
        title=title,language='en',sentiment_channel='direct',source_record_sha256='r',
        source_manifest_sha256='m',source_manifest_row=1,possible_cross_url_copy_of=None)


def test_adjust_ohlc_once_preserve_volume_and_sources():
    sessions=rp.cutoff_grid('2024-06-07','2024-06-14')
    records=prices(sessions);before=deepcopy(records)
    records[1]['stock_splits']='10';before=deepcopy(records)
    adj,audit=rp.adjusted_inputs(records,sessions,['AAPL'])
    features,_=rp.price_features(adj,audit,sessions)
    assert records==before
    assert adj['close'].tolist()==pytest.approx([float(r['adj_close']) for r in records])
    assert adj['open'].tolist()==pytest.approx([float(r['open'])*.9 for r in records])
    assert adj['volume'].tolist()==[1000]*len(records)
    assert features.iloc[1].return_1==pytest.approx(float(records[1]['adj_close'])/float(records[0]['adj_close'])-1)
    assert not audit.adjustment_review_required.any()


def test_dividend_adjusted_target_is_not_cash_formula():
    sessions=rp.cutoff_grid('2024-02-08','2024-02-16');rows=prices(sessions)
    for r in rows:r.update(close='101',adj_close='101',open='101',high='102',low='98')
    rows[0].update(close='100',adj_close='99',open='100')
    rows[1]['dividends']='1'
    adjusted,audit=rp.adjusted_inputs(rows,sessions,['AAPL'])
    features,_=rp.price_features(adjusted,audit,sessions)
    assert features.iloc[1].return_1==pytest.approx(101/99-1)
    assert features.iloc[1].return_1!=pytest.approx(102/100-1)
    outcome=rp.labels(rows,sessions,'2026-11-10T00:00:00Z').iloc[0]
    assert outcome.annualized_variance==pytest.approx(252/5*np.log(101/99)**2)
    assert not outcome.historical_prediction_eligible


@pytest.mark.parametrize('position',range(1,6))
def test_every_horizon_position_keeps_split_basis_and_extreme_loss(position):
    sessions=rp.cutoff_grid('2024-06-03','2024-06-12')[:6];rows=prices(sessions)
    for i,r in enumerate(rows):
        c=100 if i<position else 40
        r.update(close=str(c),adj_close=str(c),open=str(c),high=str(c+1),low=str(c-1))
    rows[position]['stock_splits']='10'
    target=rp.labels(rows,sessions,'2026-11-10T00:00:00Z').iloc[0]
    assert target.annualized_variance==pytest.approx(252/5*np.log(.4)**2)
    assert target.annualized_volatility==pytest.approx(np.sqrt(target.annualized_variance))


def test_horizon_boundary_censoring_and_late_receipt_are_separate():
    sessions=rp.cutoff_grid('2022-12-20','2023-01-12');rows=prices(sessions)
    out=rp.labels(rows,sessions,'2026-11-10T00:00:00Z')
    boundary=out[out.status=='crosses_split_boundary']
    assert len(boundary)==5 and boundary.annualized_variance.isna().all()
    assert len(out[out.status=='right_censored'])==5
    assert out.iloc[0].status=='mature_retrospective'  # receipt long after prediction is permitted
    early=rp.labels(rows,sessions,'2025-01-01T00:00:00Z')
    assert early.iloc[0].status=='not_mature_by_label_asof'
    assert early.annualized_variance.isna().all()


def test_future_prices_do_not_change_features_and_warmup_is_missing():
    sessions=rp.cutoff_grid('2023-01-03','2024-04-01');rows=prices(sessions)
    def calculate(data):
        a,b=rp.adjusted_inputs(data,sessions,['AAPL']);return rp.price_features(a,b,sessions)[0]
    before=calculate(rows);changed=deepcopy(rows)
    for r in changed[280:]:
        for key in ('open','high','low','close','adj_close'):r[key]=str(float(r[key])*1.2)
    after=calculate(changed)
    pd.testing.assert_frame_equal(before.iloc[:280],after.iloc[:280])
    assert not before.eight_session_features_complete.iloc[:259].any()
    assert before.eight_session_features_complete.iloc[259]
    assert before.historical_variance_20.iloc[:20].isna().all()
    assert before.historical_variance_20.iloc[20]==pytest.approx(252*np.mean(before.log_return_1.iloc[1:21]**2))


def test_duplicate_gap_mixed_basis_and_invalid_prices_fail_closed():
    sessions=rp.cutoff_grid('2024-01-02','2024-01-12');rows=prices(sessions)
    for data in (rows+rows[:1],rows[1:]):
        with pytest.raises(ValueError):rp.adjusted_inputs(data,sessions,['AAPL'])
    changed=deepcopy(rows);changed[0]['adjustment']='as_traded'
    with pytest.raises(ValueError,match='basis'):rp.adjusted_inputs(changed,sessions,['AAPL'])
    changed=deepcopy(rows);changed[0]['adj_close']='nan'
    with pytest.raises(ValueError,match='invalid_price'):rp.adjusted_inputs(changed,sessions,['AAPL'])


def test_macro_revision_lags_and_negative_values(monkeypatch):
    monkeypatch.setattr(rp.panel_scope,'MACRO',{'PAYEMS':()})
    sessions=rp.cutoff_grid('2023-02-02','2023-03-15')
    base=dict(series_id='PAYEMS',observation_date='2023-01-01',value='100',
              realtime_start='2023-02-02',realtime_end='2023-02-05')
    revised=dict(base,value='-10',realtime_start='2023-02-06',realtime_end='9999-12-31')
    out=rp.macro_variants([base,revised],sessions,{'PAYEMS'})
    zero=out[out.extra_session_lag==0].set_index('session')
    one=out[out.extra_session_lag==1].set_index('session')
    assert pd.isna(zero.loc['2023-02-02','value'])
    assert zero.loc['2023-02-03','value']==100
    assert one.loc['2023-02-06','value']==100
    assert zero.loc['2023-02-07','value']==-10
    assert one.loc['2023-02-07','value']==100
    changed=rp.macro_variants([base,dict(revised,value='999')],sessions,{'PAYEMS'})
    pd.testing.assert_frame_equal(out[out.session<'2023-02-07'],changed[changed.session<'2023-02-07'])


def test_news_cutoff_early_close_dst_missingness_and_hold():
    sessions=rp.cutoff_grid('2024-11-27','2024-12-02')
    assert sessions[1][1].isoformat()=='2024-11-29T18:20:00+00:00'
    assert align_observation(news('2024-11-29T18:10:00Z'),sessions)[1]['session']=='2024-11-29'
    assert align_observation(news('2024-11-29T18:21:00Z'),sessions)[1]['session']=='2024-12-02'
    dst=rp.cutoff_grid('2024-03-08','2024-03-11')
    assert dst[0][1].hour==21 and dst[1][1].hour==20
    held=news('2024-11-29T18:10:00Z');held['review_hold_reasons']=['source_title_binding']
    with pytest.raises(ValueError,match='ineligible'):align_observation(held,sessions)
    conflicting=news('2024-11-29T18:10:00Z');conflicting['published_at']='2024-11-29T19:00:00Z'
    with pytest.raises(ValueError,match='conflict'):align_observation(conflicting,sessions)


def frozen_news(path, rows, day='2024-01-02'):
    table=pa.Table.from_pylist(rows) if rows else pa.table({'ticker':pa.array([],type=pa.string())})
    m=publish(path/'curated'/day,{'eligible_headlines':table},{'binding':'b'})
    (path/'frozen-plan.json').write_bytes(json_bytes({'binding':'b'}))
    (path/'audit-report.json').write_bytes(json_bytes(dict(binding='b',full_scoring_held=True,
        eligible_after_dedup=len(rows),curated_shards=[dict(day=day,sha256=digest(json_bytes(m)))])))


def test_news_same_text_keeps_individual_occurrences_and_lags(tmp_path):
    sessions=rp.cutoff_grid('2024-01-02','2024-01-12')
    a=news('2024-01-02T20:00:00Z');b=dict(a,source_record_sha256='s',source_manifest_row=2,
         availability_at='2024-01-02T21:10:00Z',gdelt_seen_at='2024-01-02T21:10:00Z',possible_cross_url_copy_of='r')
    frozen_news(tmp_path/'source',[a,b])
    coverage,report=align_news(tmp_path/'source',tmp_path/'aligned',sessions,['AAPL'])
    first=coverage[(coverage.extra_session_lag==0)&(coverage.session=='2024-01-02')].iloc[0]
    assert first.direct_observation_count==2 and first.direct_exact_text_count==1
    assert pd.isna(first.direct_sentiment)
    assert coverage[(coverage.extra_session_lag==1)&(coverage.session=='2024-01-03')].iloc[0].direct_observation_count==2
    _,tables=verified_tables(tmp_path/'aligned/alignment/2024-01-02')
    aligned=tables['observations'].to_pylist()
    assert [r['availability_at'] for r in aligned]==[a['availability_at'],b['availability_at']]
    assert report['merges_applied']==0


def test_build_separates_labels_provenance_and_pause_gates(tmp_path,monkeypatch):
    monkeypatch.setattr(rp.panel_scope,'START','2023-01-03')
    monkeypatch.setattr(rp.panel_scope,'END','2024-04-01')
    monkeypatch.setattr(rp.panel_scope,'UNIVERSE',{'AAPL':()})
    sessions=rp.cutoff_grid(rp.panel_scope.START,rp.panel_scope.END);rows=prices(sessions)
    macro=[dict(series_id=s,observation_date='2022-01-01',value='10',realtime_start='2022-02-01',
                realtime_end='9999-12-31') for s in rp.panel_scope.MACRO]
    publish(tmp_path/'snapshot',{'prices':pa.Table.from_pylist(rows),'macro':pa.Table.from_pylist(macro)},
        dict(scope_version=rp.panel_scope.VERSION,macro_coverage=[dict(series_id=s,complete=True) for s in rp.panel_scope.MACRO]))
    frozen_news(tmp_path/'news',[],day='2023-01-03')
    approval=tmp_path/'approval.json';approval.write_text(json.dumps(dict(retrospective_scope_approved=True,vendor_adjusted_log_returns_approved=True)))
    result=rp.build(tmp_path/'snapshot',tmp_path/'news',tmp_path/'output',approval,'2026-11-10T00:00:00Z')
    metadata,tables=verified_tables(tmp_path/'output/dataset')
    assert tables['features'].num_rows==tables['labels'].num_rows==len(rows)
    assert 'annualized_variance' not in tables['features'].column_names
    assert 'historical_prediction_eligible' in tables['features'].column_names
    assert not any(tables['features']['historical_prediction_eligible'].to_pylist())
    assert not any(c.endswith(('_x','_y')) for c in tables['features'].column_names)
    assert not result['ready_for_training'] and not result['ready_for_scoring']
    assert result['strict_PIT_rows']==0 and result['price_target_candidates']>0
    assert metadata['preprocessing_fitted'] is False
    assert result['news']['covered_rows_by_lag']=={'0':0,'1':0,'5':0}
    with pytest.raises(ValueError,match='fresh_output'):
        rp.build(tmp_path/'snapshot',tmp_path/'news',tmp_path/'output',approval,'2026-11-10T00:00:00Z')


def test_training_preprocessing_remains_independent_of_other_splits():
    scaler=TrainOnlyStandardizer().fit([[1.],[3.]],['2021-01-04T21:20:00Z','2021-01-05T21:20:00Z'])
    before=scaler.mean.copy();scaler.transform([[999999.]])
    np.testing.assert_array_equal(before,scaler.mean)
    with pytest.raises(ValueError,match='train_only'):
        TrainOnlyStandardizer().fit([[1.]],['2023-01-04T21:20:00Z'])
