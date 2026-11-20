"""Synthetic fixtures only: no retained outcomes are fitted by this suite."""
from copy import deepcopy
from unittest.mock import patch
import json
import numpy as np
import pandas as pd
import pytest
from research_data import cv_timing_experiment as e
from test_forecast_runners import fixture


def anchors(data):
    a=pd.DataFrame(0.,index=range(len(data.keys)),columns=[*e.CONTROLS,*e.HORIZONS])
    for j,n in enumerate(e.HORIZONS):a[n]=.01*(j+1)+np.arange(len(a))*.001
    a.loc[0,'proxy_variance_22']=np.nan;a['proxy_variance_22_missing']=a.proxy_variance_22.isna().astype(float)
    a.loc[1,list(e.matched.COVERAGE)]=np.nan
    return a


def synthetic():
    t,d=fixture();variants={f'm{m}-n{n}':(t,anchors(t),d,anchors(d)) for m,n in e.CONDITIONS}
    contrasts=[(k+'/V',k+'/C') for k in variants]+[(k+'/'+a,'m0-n0/'+a) for k in variants if k!='m0-n0' for a in e.ARMS]
    p=dict(run_id='a'*64,identity=dict(descriptor={'output':'execution'},configuration=dict(e.TREE,max_iter=2,min_leaf=2),contrasts=contrasts))
    approval=dict(granted=True,scope=e.SCOPE,run_id=p['run_id'],approved_by='synthetic fixture',approved_at='2026-11-21T00:00:00Z')
    return variants,p,approval


def test_fixed_changed_design_six_variants_twelve_fits():
    assert len(e.CONDITIONS)*len(e.ARMS)==12 and e.TREE['max_iter']==50
    assert len(e.CONTROLS)==243 and set(e.MASKS)<=set(e.CONTROLS)
    assert not any('peer' in n or 'negative_variance' in n or 'positive_variance' in n for n in e.CONTROLS)
    assert not set(e.matched.TONES).intersection(e.CONTROLS)
    assert e.ARMS=={'C':(), 'V':('proxy_variance_1','proxy_variance_5','proxy_variance_22')}


def test_unknown_delay_window_retained_not_converted_to_absent_news():
    rows=pd.DataFrame(dict(row_id=['r1','r2'],split=['dev','dev']))
    contexts=pd.DataFrame([dict(row_id=r,channel=c,supported=r=='r1') for r in rows.row_id for c in e.matched.CHANNELS])
    rebuilt=pd.DataFrame([dict(row_id='r1',**{n:0 for n in e.matched.COVERAGE})])
    with patch.object(e,'context_anchors',return_value=rebuilt) as called:
        actual,valid=e.coverage_anchors(rows,pd.DataFrame(),contexts)
    assert list(actual.row_id)==['r1','r2'] and valid.tolist()==[True,False]
    assert actual.loc[0,list(e.matched.COVERAGE)].eq(0).all()
    assert actual.loc[1,list(e.matched.COVERAGE)].isna().all()
    assert called.call_args.args[0].row_id.tolist()==['r1']
    contexts.loc[0,'supported']=False
    with pytest.raises(ValueError,match='inconsistent_channel_support'):e.coverage_anchors(rows,pd.DataFrame(),contexts)


@pytest.mark.parametrize('field',['target','historical_variance','features','row_ids','feature_cutoffs','window_indices'])
def test_population_invariant_rejects_price_outcome_window_or_order_changes(field):
    t,_=fixture();changed=deepcopy(t)
    if field=='row_ids':changed.row_ids=tuple(reversed(changed.row_ids))
    elif field=='feature_cutoffs':changed.feature_cutoffs=('2022-01-01',)+changed.feature_cutoffs[1:]
    else:getattr(changed,field).flat[0]+=1
    with pytest.raises(ValueError):e.invariant_population(t,changed)


def test_train_only_preprocessing_common_prefix_and_missingness():
    v,_,_=synthetic();t,ta,d,da=v['m0-n0'];x,state=e.designs(t,ta,d,da)
    changed=deepcopy(d);changed.features*=1e5;changed.target*=1e5
    altered=da.copy();altered.loc[:,e.HORIZONS]=999
    y,state2=e.designs(t,ta,changed,altered)
    assert state==state2
    for arm in e.ARMS:np.testing.assert_array_equal(x[arm][0],y[arm][0])
    width=x['C'][0].shape[1]
    assert x['V'][0].shape[1]==width+3
    np.testing.assert_array_equal(x['V'][0][:,:width],x['C'][0])
    assert x['V'][0][0,-1]==0 and 'proxy_variance_22_missing' in state['common']['kept']
    with pytest.raises(ValueError,match='anchor_layout'):e.designs(t,ta.assign(peer_count=9),d,da)


@pytest.mark.parametrize('change',[{'granted':False},{'run_id':'b'*64},{'scope':'old_scope'},{'approved_by':None}])
def test_approval_gate_before_source_reads(tmp_path,change):
    _,p,a=synthetic()
    with patch.object(e,'plan',side_effect=AssertionError('no read')):
        with pytest.raises(PermissionError):e.execute(p,tmp_path,dict(a,**change))
    assert not list(tmp_path.iterdir())


def test_changed_packet_and_source_fail_before_output(tmp_path):
    _,p,a=synthetic()
    with patch.object(e,'plan',return_value={'changed':True}):
        with pytest.raises(ValueError,match='experiment_changed'):e.execute(p,tmp_path,a)
    (tmp_path/'input').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed_input'):e.load({'bindings':{'input':'0'*64}},tmp_path)
    assert not (tmp_path/'execution').exists()


def test_twelve_synthetic_attempts_reconcile_lineage_and_no_replay(tmp_path):
    from utils.research_lineage import ResearchLineage
    v,p,a=synthetic()
    with patch.object(e,'plan',return_value=p),patch.object(e,'load',return_value=v):
        result=e.execute(p,tmp_path,a)
        with pytest.raises(FileExistsError):e.execute(p,tmp_path,a)
    assert result['completed_fits']==12
    out=tmp_path/'execution';receipt=json.loads((out/'completion.json').read_text())
    assert len(receipt['result_sha256'])==12 and receipt['reserved_attempts']==12
    assert len(json.loads((out/'contrasts.json').read_text()))==16
    s=ResearchLineage(out/'lineage')
    assert len(s.query(kind='attempt',status='reserved'))==12
    assert len(s.query(kind='run',status='completed'))==1
    assert all(c['matches'] for c in s.verify_artifacts(tmp_path))
    for f in out.glob('result-*.json'):
        r=json.loads(f.read_text());assert r['row_ids']==list(v['m0-n0'][2].row_ids)
        assert r['diagnostics']['subsets']['unknown_delayed_news_coverage']['rows']==1


def test_interruption_keeps_reservation_and_blocks_resume(tmp_path):
    from research_data.forecast_runners import BoostedVariance
    from utils.research_lineage import ResearchLineage
    v,p,a=synthetic()
    with patch.object(e,'plan',return_value=p),patch.object(e,'load',return_value=v),patch.object(BoostedVariance,'fit',side_effect=KeyboardInterrupt) as fit:
        with pytest.raises(KeyboardInterrupt):e.execute(p,tmp_path,a)
        with pytest.raises(FileExistsError):e.execute(p,tmp_path,a)
    assert fit.call_count==1 and (tmp_path/'execution/attempt-0000.json').exists()
    assert not (tmp_path/'execution/completion.json').exists()
    assert len(ResearchLineage(tmp_path/'execution/lineage').query(kind='run',status='failed'))==1
