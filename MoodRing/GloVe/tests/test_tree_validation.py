"""Synthetic chronology, approval and selection checks; never fit retained prices."""
from copy import deepcopy
from unittest.mock import patch
import json
import numpy as np
import pandas as pd
import pytest
from research_data import tree_validation as t
from research_data.forecast_runners import build_dataset
from research_data.point_in_time import sessions_between
from research_data.archive import digest,json_bytes
from test_forecast_runners import frames


def example():
    f,c,y=frames('2021-01-04','2021-06-30','train')
    data=build_dataset(f,c,y,['x','macro','absent'],'train')
    indexed=y.set_index(['session','ticker']);ends=[indexed.loc[k,'label_end_session'] for k in data.keys]
    specs=(('A','2021-03-01','2021-04-30'),('B','2021-05-03','2021-06-30'))
    return data,ends,t.folds(data,ends,specs)


def test_grid_is_current_plus_exact_eight_one_at_a_time_changes():
    assert len(t.CONFIGS)==9 and sum(x['max_iter'] for x in t.CONFIGS.values())*3==3000
    for name,cfg in t.CONFIGS.items():
        changes=[k for k in cfg if cfg[k]!=t.BASE[k]]
        assert len(changes)==(0 if name=='C0_current' else 2 if name=='C8_rate001_iter200' else 1)
    assert max(c['max_leaf_nodes'] for c in t.CONFIGS.values())==11


def test_purge_all_five_outcomes_and_disjoint_validation():
    data,ends,parts=example();seen=set()
    for p in parts:
        assert max(ends[i] for i in p['fit_indices'])<p['start']
        assert all(ends[i]<=p['end'] for i in p['validation_indices'])
        assert len(p['purged_row_ids'])==10  # Five dates, two synthetic stocks.
        assert not seen.intersection(p['validation'].row_ids);seen.update(p['validation'].row_ids)
        assert p['validation'].split=='train'  # Global split identity never relabeled.
    bad=deepcopy(data);bad.split='dev'
    with pytest.raises(ValueError):t.folds(bad,ends)


def test_unique_training_preprocessing_and_validation_mutation_isolated():
    _,_,parts=example();p=parts[0];a,b=p['train'],p['validation']
    x,z,state=t.prepare_fold(a,b)
    other=deepcopy(b);other.features*=1e6;other.target[:]=10000
    x2,_,state2=t.prepare_fold(a,other)
    assert state==state2;np.testing.assert_equal(x,x2)
    assert state['unique_training_feature_rows']<len(a.keys)*8
    assert state['dropped']==['absent']


def test_unapproved_and_changed_run_rejected_before_output(tmp_path):
    proposal=dict(run_id='synthetic',identity={'descriptor':{}})
    with patch.object(t,'load',side_effect=AssertionError('no load')):
        with pytest.raises(PermissionError):t.execute(proposal,tmp_path,{'granted':False})
    approval=dict(granted=True,scope=t.SCOPE,run_id='synthetic',approved_by='fixture',approved_at='2026-11-16T00:00:00Z')
    with patch.object(t,'plan',return_value={'changed':True}):
        with pytest.raises(ValueError,match='study_changed'):t.execute(proposal,tmp_path,approval)
    assert not list(tmp_path.iterdir())


def test_event_diagnostics_retain_repeated_extreme_windows_and_zero_targets():
    _,_,parts=example();b=deepcopy(parts[0]['validation']);b.target[:]=.04;b.target[:4]=100.;b.target[-1]=0.
    events=pd.DataFrame(dict(row_id=b.row_ids,annualized_variance=b.target,dominant_session=['2021-03-05']*len(b.keys)))
    p=np.full(len(b.keys),.04);p[:4]=1e-8
    report=t.tail_diagnostics(b,p,events)
    assert report['extremes_removed']==0 and sum(g['rows'] for g in report['worst_events'])==len(b.keys)
    assert report['top_five_event_excess_share']==1.
    assert report['worst_rows'][0]['excess_qlike']>1e9
    raw,excess=t.row_losses([.04],[0.]);assert np.isfinite(raw).all() and np.isfinite(excess).all()


def test_synthetic_interruption_reserves_fit_no_retry(tmp_path):
    data,ends,parts=example();proposal=dict(run_id='synthetic',identity={'descriptor':{'output':'run','event_labels':'events.parquet','bindings':{}}})
    events=pd.DataFrame(dict(row_id=data.row_ids,annualized_variance=data.target,dominant_session=ends));events.to_parquet(tmp_path/'events.parquet',index=False)
    proposal['identity']['descriptor']['bindings']['events.parquet']=digest((tmp_path/'events.parquet').read_bytes())
    approval=dict(granted=True,scope=t.SCOPE,run_id='synthetic',approved_by='fixture',approved_at='2026-11-16T00:00:00Z')
    with patch.object(t,'plan',return_value=proposal),patch.object(t,'load',return_value=(data,ends,parts)),patch.object(t.BoostedVariance,'fit',side_effect=KeyboardInterrupt) as fit:
        with pytest.raises(KeyboardInterrupt):t.execute(proposal,tmp_path,approval)
        assert fit.call_count==1 and (tmp_path/'run/attempt-000.json').exists()
        assert not (tmp_path/'run/completion.json').exists()
        with pytest.raises(FileExistsError):t.execute(proposal,tmp_path,approval)
        assert fit.call_count==1


def test_complete_synthetic_release_selects_qlike_and_preserves_all_keys(tmp_path,monkeypatch):
    data,ends,parts=example();proposal=dict(run_id='synthetic',identity={'descriptor':{'output':'run','event_labels':'events.parquet','bindings':{}}})
    events=pd.DataFrame(dict(row_id=data.row_ids,annualized_variance=data.target,dominant_session=ends));events.to_parquet(tmp_path/'events.parquet',index=False)
    proposal['identity']['descriptor']['bindings']['events.parquet']=digest((tmp_path/'events.parquet').read_bytes())
    approval=dict(granted=True,scope=t.SCOPE,run_id='synthetic',approved_by='fixture',approved_at='2026-11-16T00:00:00Z')
    class FakeTree:
        def __init__(self,**config):self.rate=config['learning_rate'];self.trees=[]
        def fit(self,x,y):self.intercept=float(y.mean());return self
        def predict(self,x):return np.full(len(x),self.intercept)
    monkeypatch.setattr(t,'plan',lambda *args:proposal);monkeypatch.setattr(t,'load',lambda *args:(data,ends,parts))
    monkeypatch.setattr(t,'BoostedVariance',FakeTree)
    result=t.execute(proposal,tmp_path,approval)
    assert result['completed_fits']==18 # Two small synthetic folds, nine configs.
    summary=json.loads((tmp_path/'run/completion.json').read_text())
    assert summary['selected_by_pooled_qlike']=='C1_leaves3' # Declared simpler-config tie break.
    assert not summary['development_evaluated'] and summary['final_test_held']
    records=[json.loads(p.read_text()) for p in sorted((tmp_path/'run').glob('result-*.json'))]
    records[0]['row_ids'].reverse()
    with pytest.raises(ValueError,match='population_order'):t.summarize(records,parts,events)
