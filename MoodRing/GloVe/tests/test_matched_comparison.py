"""Synthetic fixtures only: no retained-data preprocessing fit or forecasting."""
from copy import deepcopy
import json
import numpy as np
import pandas as pd
import pytest
from research_data import matched_comparison as m
from research_data.archive import digest, json_bytes
from test_forecast_runners import fixture


def published(dataset):
    contexts=[];masks=[]
    for i,(row_id,(day,ticker)) in enumerate(zip(dataset.row_ids,dataset.keys)):
        row=dict(row_id=row_id,session=day,ticker=ticker,split=dataset.split,
            context_sessions=[dataset.feature_keys[j][0] for j in dataset.window_indices[i]],
            historical_prediction_eligible=False)
        for ci,ch in enumerate(m.CHANNELS):
            # Missing, observed zero and negative competitor tone remain distinct.
            means=np.array([np.nan,0.,.3,-.2,np.nan,.1,-.6,.2])
            if ci==2:means[:]=-.4
            if i==0:means[:]=np.nan
            scored=~np.isnan(means);den=scored.astype(int)
            tone=float(means[scored].mean()) if scored.any() else np.nan
            v=dict(row_id=row_id,session=day,ticker=ticker,split=dataset.split,channel=ch,
                sentiment=tone,daily_means=means,daily_effective_denominators=den,
                scored_days=int(scored.sum()),missing_score_days=int((~scored).sum()),
                observed_days=int(scored.sum()),absent_news_days=int((~scored).sum()),has_sentiment=bool(scored.any()))
            for state in m.STATES:
                for unit in ('daily_inputs','observations'):v[state+'_'+unit]=int(scored.sum()) if state=='scored' else 0
            for field in m.MASKS:v[field]=scored.copy() if field in ('scored_mask','observed_mask') else ~scored if field=='absent_news_mask' else np.zeros(8,bool)
            for field in m.COUNTS:row[ch+'_'+field]=int(scored.sum()) if field=='context_unique_inputs' else v[field]
            row[ch+'_sentiment']=tone;masks.append(v)
        contexts.append(row)
    return pd.DataFrame(contexts),pd.DataFrame(masks)


def setup():
    t,d=fixture();tc,tm=published(t);dc,dm=published(d)
    return t,d,m.anchors(t,tc,tm),m.anchors(d,dc,dm)


def test_anchor_bijection_missing_zero_competitor_and_no_broadcast():
    t,d=fixture();c,k=published(t)
    a=m.anchors(t,c.iloc[::-1],k.iloc[::-1])
    assert np.isnan(a.iloc[0].direct_sentiment)
    assert a.iloc[1].indirect_sector_competitor_sentiment==pytest.approx(-.4)
    assert a.iloc[1].direct_scored_mask_1==1 and k.iloc[3].daily_means[1]==0
    assert len(a)==len(t.keys) and tuple(a.columns)==m.COVERAGE+m.TONES
    assert not any('neutral' in n or 'tone' in n or ('sentiment' in n and not n.endswith('_has_sentiment')) for n in m.COVERAGE)
    # has_sentiment is a presence flag; its name is allowed in controls.


@pytest.mark.parametrize('fault',['population','mask_duplicate','context','mean','denominator','unfinished','test','negative_count'])
def test_publication_checks_fail_closed(fault):
    t,_=fixture();c,k=published(t)
    if fault=='population':c=c.iloc[1:]
    if fault=='mask_duplicate':k=pd.concat([k,k.iloc[:1]])
    if fault=='context':c.at[0,'context_sessions']=['2025-01-01']*8
    if fault=='mean':c.at[1,'direct_sentiment']=.9
    if fault=='denominator':k.at[3,'daily_effective_denominators']=np.zeros(8)
    if fault=='unfinished':c.at[0,'direct_unscored_daily_inputs']=1;k.at[0,'unscored_daily_inputs']=1
    if fault=='test':c.at[0,'split']='test'
    if fault=='negative_count':c.at[0,'direct_context_unique_inputs']=-1
    with pytest.raises(ValueError):m.anchors(t,c,k)


def test_shared_preprocessing_training_only_appended_once_and_all_missing_drop():
    t,d,ta,da=setup();ta[m.TONES[2]]=np.nan
    design,state=m.prepare_designs(t,d,ta,da)
    changed=da.copy();changed[:]=98765
    design2,state2=m.prepare_designs(t,d,ta,changed)
    assert state==state2
    assert state['anchor_blocks'][m.TONES[2]]['dropped']==[m.TONES[2]]
    coverage=design['baseline_plus_coverage_controls'][0]
    for arm,(x,y) in design.items():np.testing.assert_array_equal(x[:,:coverage.shape[1]],coverage)
    assert design['coverage_baseline_plus_direct'][0].shape[1]==coverage.shape[1]+2
    assert design['coverage_baseline_plus_both'][0].shape[1]==coverage.shape[1]+4
    for arm in design:np.testing.assert_array_equal(design[arm][0],design2[arm][0])


def test_generic_tone_and_attribution_do_not_reverse_competitor_features():
    t,d,ta,da=setup()
    assert (ta[m.TONES[2]].dropna()<0).all()
    assert m.ARMS['coverage_baseline_plus_indirect']==m.TONES[1:]
    assert m.ARMS['coverage_baseline_plus_direct']==(m.TONES[0],)


@pytest.mark.parametrize('family,config',[
    ('regularized_regression',dict(alpha=1.)),
    ('boosted_trees',dict(max_leaf_nodes=3,learning_rate=.1,max_iter=3,min_leaf=2))])
def test_small_synthetic_fit_variance_extremes_and_row_order(family,config):
    t,d,ta,da=setup();t.target[-1]=10000
    designs,_=m.prepare_designs(t,d,ta,da);arm='coverage_baseline_plus_both'
    r=m.fit_arm(t,d,*designs[arm],arm,family,config,17)
    assert r['row_ids']==list(d.row_ids) and r['keys']==list(d.keys)
    assert min(r['predicted_variance'])>=m.FLOOR and r['extreme_targets_removed']==0
    assert r['metrics'].keys()=={'qlike','volatility_mae','volatility_rmse'}
    json_bytes(r)


def test_approval_required_before_any_loading_or_output(tmp_path,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('unapproved work')
    monkeypatch.setattr(m,'make_plan',forbidden)
    for a in ({},{'granted':True,'scope':'baseline_train_dev_only','run_id':'x'},
              {'granted':True,'scope':m.SCOPE,'run_id':'wrong','approved_by':'test'}):
        with pytest.raises(PermissionError):m.execute({'run_id':'x'},tmp_path,a)
    assert list(tmp_path.iterdir())==[]


def test_changed_plan_rejected_before_fit(tmp_path,monkeypatch):
    p=dict(run_id='x',identity={'descriptor':{}})
    monkeypatch.setattr(m,'make_plan',lambda *a:dict(run_id='changed'))
    a=dict(granted=True,scope=m.SCOPE,run_id='x',approved_by='synthetic',approved_at='2026-11-14T00:00:00Z')
    with pytest.raises(ValueError,match='plan_changed'):m.execute(p,tmp_path,a)
    assert list(tmp_path.iterdir())==[]


def test_partial_fit_reserved_and_no_automatic_resume(tmp_path,monkeypatch):
    t,d,ta,da=setup();design,state=m.prepare_designs(t,d,ta,da)
    p=dict(run_id='synthetic',identity=dict(descriptor=dict(output_relative_path='run'),max_fit_attempts=144))
    a=dict(granted=True,scope=m.SCOPE,run_id=p['run_id'],approved_by='synthetic',approved_at='2026-11-14T00:00:00Z')
    monkeypatch.setattr(m,'make_plan',lambda *args:p)
    reuse=[dict(keys=list(d.keys),preprocessing=None)]
    monkeypatch.setattr(m,'load_comparison',lambda *args:(t,d,ta,da,reuse,{}))
    monkeypatch.setattr(m,'prepare_designs',lambda *args:(design,state))
    calls=[]
    def interrupted(*args):calls.append(1);raise KeyboardInterrupt()
    monkeypatch.setattr(m,'fit_arm',interrupted)
    with pytest.raises(KeyboardInterrupt):m.execute(p,tmp_path,a)
    assert len(calls)==1 and (tmp_path/'run/attempt-0000.json').is_file()
    assert not (tmp_path/'run/completion.json').exists()
    with pytest.raises(FileExistsError):m.execute(p,tmp_path,a)
    assert len(calls)==1


def test_fit_limit_checked_before_reservation(tmp_path,monkeypatch):
    t,d,ta,da=setup();design,state=m.prepare_designs(t,d,ta,da)
    p=dict(run_id='synthetic',identity=dict(descriptor=dict(output_relative_path='run'),max_fit_attempts=0))
    a=dict(granted=True,scope=m.SCOPE,run_id=p['run_id'],approved_by='synthetic',approved_at='2026-11-14T00:00:00Z')
    monkeypatch.setattr(m,'make_plan',lambda *args:p)
    monkeypatch.setattr(m,'load_comparison',lambda *args:(t,d,ta,da,[dict(keys=list(d.keys))],{}))
    monkeypatch.setattr(m,'prepare_designs',lambda *args:(design,state))
    with pytest.raises(ValueError,match='fit_budget'):m.execute(p,tmp_path,a)
    assert not list((tmp_path/'run').glob('attempt*'))


def test_complete_mocked_grid_reconciles_budget_and_reuses_reference(tmp_path,monkeypatch):
    t,d,ta,da=setup();design,state=m.prepare_designs(t,d,ta,da)
    p=dict(run_id='synthetic',identity=dict(descriptor=dict(output_relative_path='run'),max_fit_attempts=144))
    a=dict(granted=True,scope=m.SCOPE,run_id=p['run_id'],approved_by='synthetic',approved_at='2026-11-14T00:00:00Z')
    calls=[]
    def fake(t,d,x,y,arm,family,config,seed):
        calls.append((arm,family,config,seed))
        return dict(arm=arm,family=family,config=config,seed=seed,keys=list(d.keys),row_ids=list(d.row_ids),
            predicted_variance=d.historical_variance.tolist(),metrics=m.variance_metrics(d.historical_variance,d.target))
    reference=dict(family='historical_variance_20',config={},seed=17,keys=list(d.keys),row_ids=list(d.row_ids),
        predicted_variance=d.historical_variance.tolist(),metrics=m.variance_metrics(d.historical_variance,d.target))
    reused=[reference]
    for cfg in m.BASELINE_CONFIGS:
        conf=dict(cfg);family=conf.pop('family')
        for seed in m.SEEDS:
            reused.append(dict(fake(t,d,None,None,m.BASE,family,conf,seed),preprocessing=state['baseline']))
    fixed={'regularized_regression':{'alpha':10.},'boosted_trees':{k:v for k,v in m.BASELINE_CONFIGS[4].items() if k!='family'}}
    completion=dict(selected_on_development=fixed,result_sha256={'result-0000.json':'a'*64})
    monkeypatch.setattr(m,'make_plan',lambda *args:p)
    monkeypatch.setattr(m,'load_comparison',lambda *args:(t,d,ta,da,reused,completion))
    monkeypatch.setattr(m,'prepare_designs',lambda *args:(design,state))
    monkeypatch.setattr(m,'fit_arm',fake);calls.clear()
    result=m.execute(p,tmp_path,a)
    assert len(calls)==144 and result['completed_fits']==144
    assert len(list((tmp_path/'run').glob('attempt-*.json')))==144
    done=json.loads((tmp_path/'run/completion.json').read_text())
    assert len(done['result_sha256'])==144 and done['baseline_reused_results']==37
    assert done['historical_reference']['identical_in_all_arms']
    assert len(done['paired_qlike_intervals']['fixed_baseline_config'])==8
    assert done['final_test_held'] and done['master_training_held']
