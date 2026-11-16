"""Synthetic forecasting fixtures only; no retained data or real fitting approval."""
from copy import deepcopy
import numpy as np
import pandas as pd
import pytest
from research_data.archive import digest,json_bytes
from research_data.point_in_time import sessions_between
from research_data.forecast_runners import (build_dataset,preprocess,RidgeVariance,BoostedVariance,
    fit_baseline,make_release_plan,run_baseline_release,BASELINE_CONFIGS)


def frames(start,end,split):
    days=sessions_between(start,end);features=[];contexts=[];labels=[]
    for ticker,offset in [('SYN1',0.),('SYN2',.2)]:
        for i,(day,close) in enumerate(days):
            cutoff=close.isoformat()
            features.append(dict(ticker=ticker,session=day,cutoff=cutoff,x=i+offset,
                macro=np.nan if i%3==0 else i*.1,absent=np.nan,historical_variance_20=.04+i*.001))
            if i<7 or i+5>=len(days):continue
            contexts.append(dict(ticker=ticker,session=day,row_id=digest(json_bytes([ticker,day])),
                context_sessions=[d for d,_ in days[i-7:i+1]],primary_matched=True,known_gap_in_context=False))
            labels.append(dict(ticker=ticker,session=day,cutoff=cutoff,label_end_session=days[i+5][0],
                label_received_at='2026-01-01T00:00:00Z',label_asof='2026-02-01T00:00:00Z',
                source_record_sha256=['a'*64]*6,annualized_variance=.04+i*.001+offset,
                status='mature_retrospective',split=split))
    return pd.DataFrame(features),pd.DataFrame(contexts),pd.DataFrame(labels)


def fixture():
    a=frames('2021-01-04','2021-02-05','train')
    b=frames('2023-01-03','2023-02-03','dev')
    return build_dataset(*a,['x','macro','absent'],'train'),build_dataset(*b,['x','macro','absent'],'dev')


def test_context_preprocessor_unique_rows_masks_and_no_dev_fit():
    train,dev=fixture();tx,dx,state=preprocess(train,dev)
    assert state['training_unique_feature_rows']==len(train.feature_keys)<len(train.keys)*8
    assert state['dropped']==['absent'] and tx.shape[1]==8*2*2
    prior=deepcopy(state);changed=deepcopy(dev);changed.features[:]=999
    assert preprocess(train,changed)[2]==prior
    assert 1 in tx[:,3::4] # macro missingness remains distinct from standardized zero.


@pytest.mark.parametrize('violation',['test','future','cross_context','horizon','maturity','duplicate','target_feature','row_id'])
def test_dataset_fails_closed(violation):
    f,c,y=frames('2021-01-04','2021-02-05','train')
    split='train';names=['x']
    if violation=='test':split='test'
    if violation=='future':f.loc[f.session==c.iloc[0].context_sessions[0],'cutoff']='2022-01-01T00:00:00Z'
    if violation=='cross_context':c.at[0,'context_sessions']=['2019-12-20']+c.iloc[0].context_sessions[1:]
    if violation=='horizon':y.at[0,'label_end_session']=y.iloc[1].label_end_session
    if violation=='maturity':y.at[0,'label_asof']='2020-01-01T00:00:00Z'
    if violation=='duplicate':f=pd.concat([f,f.iloc[:1]])
    if violation=='target_feature':names=['annualized_variance']
    if violation=='row_id':c.at[0,'row_id']='wrong'
    with pytest.raises(ValueError):build_dataset(f,c,y,names,split)


def test_future_labels_accepted_after_prediction_but_before_label_asof_and_extremes_kept():
    f,c,y=frames('2021-01-04','2021-02-05','train');y.at[0,'annualized_variance']=10000.
    train=build_dataset(f,c,y,['x'],'train')
    assert train.target.max()==10000. and len(train.keys)==len(c)


def test_exact_five_session_boundary_is_purged():
    f,c,y=frames('2022-12-01','2022-12-30','train')
    y.at[0,'label_end_session']='2023-01-03'
    with pytest.raises(ValueError,match='five_session_split_purge'):build_dataset(f,c,y,['x'],'train')


def test_ridge_matches_closed_form_and_does_not_clip_targets():
    x=np.arange(30,dtype=float).reshape(10,3);y=np.linspace(.01,.5,10)
    r=RidgeVariance(1).fit(x,y);center=x-x.mean(0)
    expected=np.linalg.solve(center.T@center+np.eye(3),center.T@(y-y.mean()))
    np.testing.assert_allclose(r.coef,expected)
    huge=y.copy();huge[-1]=1000
    assert RidgeVariance(1).fit(x,huge).predict(x).max()>100
    assert (r.predict(x)>0).all()


def test_bounded_boosting_learns_nonlinearity_and_preserves_extremes():
    x=np.linspace(-1,1,60)[:,None];y=.01+2*x[:,0]**2
    m=BoostedVariance(max_leaf_nodes=7,learning_rate=.1,max_iter=30,min_leaf=3).fit(x,y)
    p=m.predict(x)
    assert np.mean((p-y)**2)<.1*np.var(y) and (p>0).all()
    np.testing.assert_array_equal(p,BoostedVariance(max_leaf_nodes=7,learning_rate=.1,max_iter=30,min_leaf=3).fit(x,y).predict(x))
    huge=y.copy();huge[-3:]=1000
    assert BoostedVariance(max_leaf_nodes=7,learning_rate=.1,max_iter=30,min_leaf=3).fit(x,huge).predict(x).max()>500


def test_all_baseline_families_exact_rows_variance_metrics_and_train_only_state():
    train,dev=fixture();expected=deepcopy(train.target)
    results=[fit_baseline(train,dev,'historical_variance_20',{}),
        fit_baseline(train,dev,'regularized_regression',dict(alpha=1)),
        fit_baseline(train,dev,'boosted_trees',dict(max_leaf_nodes=7,learning_rate=.1,max_iter=5,min_leaf=2))]
    for r in results:
        assert r['keys']==list(dev.keys) and r['row_ids']==list(dev.row_ids)
        assert (r['predicted_variance']>0).all() and set(r['metrics'])=={'qlike','volatility_mae','volatility_rmse'}
        assert r['extreme_targets_removed']==0 and not r['final_test_evaluated']
    np.testing.assert_array_equal(expected,train.target)
    np.testing.assert_array_equal(results[0]['predicted_variance'],dev.historical_variance)


def test_release_binds_data_config_and_refuses_unapproved_training(monkeypatch):
    train,dev=fixture();plan=make_release_plan(train,dev,{'synthetic':'a'*64})
    assert len(BASELINE_CONFIGS)==12 and plan['identity']['max_fits']==36
    with pytest.raises(PermissionError):run_baseline_release(plan,train,dev,dict(granted=False))
    approval=dict(granted=True,scope='baseline_train_dev_only',run_id=plan['run_id'],approved_by='synthetic fixture',approved_at='2026-11-14T00:00:00Z')
    altered=deepcopy(dev);altered.features[0,0]+=1
    with pytest.raises(ValueError,match='release_changed'):run_baseline_release(plan,train,altered,approval)
    calls=[]
    def fake(t,d,f,c,seed=17):
        calls.append((f,c,seed));return dict(family=f,config=c,seed=seed,metrics=dict(qlike=1),
            keys=list(d.keys),predicted_variance=d.historical_variance.copy())
    monkeypatch.setattr('research_data.forecast_runners.fit_baseline',fake)
    result=run_baseline_release(plan,train,dev,approval)
    assert len(calls)==37 and result['final_test_held'] and result['master_training_held']
    assert result['deterministic_seed_repetitions']
    assert result['intervals_descriptive_after_development_selection']
    assert result['development_intervals']['regularized_regression']['20']['status']=='unavailable'


def test_non_session_label_endpoint_rejected():
    f,c,y=frames('2021-01-04','2021-02-05','train')
    y.at[0,'label_end_session']='2021-01-23' # Saturday cannot replace evidenced endpoint.
    with pytest.raises(ValueError,match='non_session_horizon'):build_dataset(f,c,y,['x'],'train')
