"""Synthetic release tests; real retained targets are never fitted by this suite."""
from copy import deepcopy
from unittest.mock import patch
import json
import numpy as np
import pandas as pd
import pytest
from research_data import sparse_timing_experiments as e
from research_data.archive import digest,json_bytes
from test_forecast_runners import fixture,frames


def anchors(data):
    names=(*e.matched.COVERAGE,*e.matched.TONES,*e.SUPPORT,*e.WEIGHTED,*e.INTENSITY)
    a=pd.DataFrame(np.zeros((len(data.keys),len(names))),columns=names)
    for name in e.matched.TONES:a[name]=np.nan
    a.iloc[0,a.columns.get_loc(e.matched.TONES[0])]=0.
    return a


def synthetic(scope):
    t,d=fixture();variants={c:(t,anchors(t),d,anchors(d)) for c,_ in e.grid(scope)}
    p=dict(run_id='synthetic-only',identity=dict(scope=scope,descriptor={'output':'execution'},
        max_fits=len(e.grid(scope)),grid=[dict(condition=c,arm=a) for c,a in e.grid(scope)],
        configuration=dict(e.TREE,max_iter=2,min_leaf=2)))
    a=dict(granted=True,scope=scope,run_id=p['run_id'],approved_by='synthetic test',
        approved_at='2026-11-16T00:00:00Z',accept_reviewed_candidate=True)
    return variants,p,a


def test_six_and_twelve_fixed_fits_no_combined_delay_or_master():
    assert len(e.grid(e.SCOPES[0]))==6 and len(e.grid(e.SCOPES[1]))==12
    assert set(c for c,_ in e.grid(e.SCOPES[1]))=={'m0-n0','m1-n0','m5-n0','m20-n0','m0-n1','m0-n5'}
    assert e.TREE['max_iter']==100 and e.TREE['max_leaf_nodes']==7
    with pytest.raises(ValueError):e.grid('final_test')


def test_unapproved_or_changed_policy_fails_before_loading(tmp_path):
    _,p,a=synthetic(e.SCOPES[0])
    for change in ({'granted':False},{'accept_reviewed_candidate':False},{'run_id':'old'},{'scope':'baseline_train_dev_only'}):
        with patch.object(e,'plan',side_effect=AssertionError('no read')):
            with pytest.raises(PermissionError):e.execute(p,tmp_path,dict(a,**change))
    assert not list(tmp_path.iterdir())


def test_identity_change_cannot_release(tmp_path):
    _,p,a=synthetic(e.SCOPES[0])
    with patch.object(e,'plan',return_value={'changed':True}):
        with pytest.raises(ValueError,match='experiment_changed'):e.execute(p,tmp_path,a)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('scope',e.SCOPES)
def test_complete_synthetic_grid_exact_rows_and_attempt_accounting(tmp_path,scope):
    variants,p,a=synthetic(scope)
    with patch.object(e,'plan',return_value=p),patch.object(e,'load',return_value=variants):
        result=e.execute(p,tmp_path,a)
        with pytest.raises(FileExistsError):e.execute(p,tmp_path,a)
    output=tmp_path/'execution';assert result['completed_fits']==len(e.grid(scope))
    assert len(list(output.glob('attempt-*.json')))==len(e.grid(scope))
    for path in output.glob('result-*.json'):
        r=json.loads(path.read_text());assert r['row_ids']==list(variants['m0-n0'][2].row_ids)
        assert min(r['predicted_variance'])>0 and r['extreme_targets_removed']==0


def test_interruption_reserves_attempt_and_no_resume(tmp_path):
    variants,p,a=synthetic(e.SCOPES[0])
    with patch.object(e,'plan',return_value=p),patch.object(e,'load',return_value=variants),patch.object(e.BoostedVariance,'fit',side_effect=KeyboardInterrupt) as fit:
        with pytest.raises(KeyboardInterrupt):e.execute(p,tmp_path,a)
        assert fit.call_count==1 and (tmp_path/'execution/attempt-0000.json').exists()
        assert not (tmp_path/'execution/completion.json').exists()
        with pytest.raises(FileExistsError):e.execute(p,tmp_path,a)
        assert fit.call_count==1


def test_dev_changes_cannot_change_training_state_and_masks():
    t,d=fixture();ta,da=anchors(t),anchors(d)
    x,state=e.designs(t,d,ta,da,e.SCOPES[0])
    altered=deepcopy(d);altered.features*=1e4;changed=da.copy();changed.loc[:,e.matched.TONES]=.9
    y,state2=e.designs(t,altered,ta,changed,e.SCOPES[0])
    assert state==state2
    for arm in x:np.testing.assert_array_equal(x[arm][0],y[arm][0])
    assert x['R'][0].shape[1]>x['Q'][0].shape[1]


def test_loader_rejects_changed_bytes_order_and_test_partition(tmp_path):
    t,d=fixture();desc=dict(bindings={},variants={'m0-n0':{}},feature_names=['x','macro','absent'],
        ordered_row_ids={'train':list(t.row_ids),'dev':list(d.row_ids)})
    for split,start,end,data in [('train','2021-01-04','2021-02-05',t),('dev','2023-01-03','2023-02-03',d)]:
        f,c,y=frames(start,end,split);a=anchors(data);a.insert(0,'row_id',data.row_ids)
        part={}
        for name,frame in [('features',f),('contexts',c),('labels',y),('anchors',a)]:
            name=split+'-'+name+'.parquet';frame.to_parquet(tmp_path/name,index=False)
            desc['bindings'][name]=digest((tmp_path/name).read_bytes());part[name.split('-')[1].split('.')[0]]=name
        desc['variants']['m0-n0'][split]=part
    assert len(e.load(desc,tmp_path)['m0-n0'][0].keys)==len(t.keys)
    wrong=deepcopy(desc);wrong['ordered_row_ids']['dev'].reverse()
    with pytest.raises(ValueError,match='ordered_population'):e.load(wrong,tmp_path)
    path=tmp_path/'dev-features.parquet';path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='bound_file_changed'):e.load(desc,tmp_path)


def test_large_exact_packet_roundtrip_and_upper_bound(tmp_path):
    p=tmp_path/'packet.json'
    value={'identity': {'synthetic_padding':'x'*(17*1024*1024)}}
    p.write_text(json.dumps(value))
    assert e.read_proposal(p)==value
    with p.open('wb') as stream:stream.truncate(65*1024*1024)
    with pytest.raises(ValueError,match='archive_file_bound'):e.read_proposal(p)


def test_plan_json_roundtrip_is_executable_identity(monkeypatch,tmp_path):
    from types import SimpleNamespace
    def data(split,count):
        return SimpleNamespace(keys=tuple(('2021-01-04' if split=='train' else '2023-01-03',str(i)) for i in range(count)),
            row_ids=tuple(str(i) for i in range(count)),target=np.ones(count),historical_variance=np.ones(count),
            identity=lambda:dict(split=split,nested_tuple=('synthetic',)))
    t,d=data('train',4920),data('dev',4900)
    monkeypatch.setattr(e,'load',lambda *a:{'m0-n0':(t,anchors(t),d,anchors(d))})
    for name in e.matched.THREADS:monkeypatch.setenv(name,'2')
    review=tmp_path/'review.json';review.write_text(json.dumps(dict(human_provenance={'human_completed':True},original_scores_changed=False)))
    desc=dict(review_overlay='review.json',bindings={'review.json':digest(review.read_bytes())})
    with patch.object(e.matched.FrozenPreprocessor,'fit',side_effect=AssertionError('preparation fit')):
        plan=e.plan(e.SCOPES[0],desc,tmp_path)
        assert json.loads(json_bytes(plan))==plan
        assert plan['run_id']==digest(json_bytes(plan['identity']))
