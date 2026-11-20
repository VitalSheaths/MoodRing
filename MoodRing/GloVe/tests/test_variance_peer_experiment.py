"""Synthetic release checks: no retained outcomes are fitted by these tests."""
from copy import deepcopy
from unittest.mock import patch
import json
import numpy as np
import pandas as pd
import pytest
from research_data import variance_peer_experiment as e
from test_forecast_runners import fixture


def anchors(data):
    """Supply independent coverage and nullable candidate values for synthetic rows."""
    a=pd.DataFrame(0.,index=range(len(data.keys)),columns=[*e.matched.COVERAGE,*e.FEATURES,*e.MASKS])
    for j,n in enumerate(e.FEATURES):
        a[n]=.01*(j+1)+np.arange(len(a))*.001
    a['peer_count']=9.;a['peer_coverage']=1.
    for n in ('proxy_variance_22',*e.SIGNED,'peer_variance_22'):
        a.loc[0,n]=np.nan;a[n+'_missing']=a[n].isna().astype(float)
    return a


def synthetic():
    """A small fixture uses the same gates and four-arm execution structure."""
    t,d=fixture();ta,da=anchors(t),anchors(d)
    p=dict(run_id='synthetic-only',identity=dict(descriptor={'output':'execution'},grid=list(e.ARMS),
        configuration=dict(e.TREE,max_iter=2,min_leaf=2)))
    approval=dict(granted=True,scope=e.SCOPE,run_id=p['run_id'],approved_by='synthetic test',approved_at='2026-11-20T00:00:00Z')
    return (t,ta,d,da),p,approval


def test_fixed_grid_shared_coverage_and_no_tone():
    assert len(e.ARMS)==4 and e.TREE==dict(max_leaf_nodes=7,learning_rate=.03,max_iter=50,max_bins=32,min_leaf=5)
    assert set(e.MASKS)<=set(e.CONTROLS)
    assert not set(e.matched.TONES).intersection(e.CONTROLS)
    assert all(n in e.ARMS['P_peers'] for n in (*e.HORIZONS,*e.SIGNED,'peer_variance_22'))


@pytest.mark.parametrize('change',[{'granted':False},{'scope':'other'},{'run_id':'other'},{'approved_by':None}])
def test_exact_approval_required_before_loading(tmp_path,change):
    _,p,a=synthetic()
    with patch.object(e,'plan',side_effect=AssertionError('no source reads')):
        with pytest.raises(PermissionError):e.execute(p,tmp_path,dict(a,**change))
    assert not list(tmp_path.iterdir())


def test_changed_packet_rejected_without_output(tmp_path):
    _,p,a=synthetic()
    with patch.object(e,'plan',return_value={'changed':True}):
        with pytest.raises(ValueError,match='experiment_changed'):e.execute(p,tmp_path,a)
    assert not list(tmp_path.iterdir())


def test_changed_file_fails_before_publication_or_table_parsing(tmp_path):
    (tmp_path/'source').write_text('changed')
    with pytest.raises(ValueError,match='changed_input'):
        e.load(dict(bindings={'source':'0'*64}),tmp_path)


def test_shared_design_prefix_masks_and_dev_isolation():
    (t,ta,d,da),_,_=synthetic()
    matrices,state=e.designs(t,ta,d,da)
    altered=deepcopy(d);altered.features*=1e5;altered.target*=1e6
    changed=da.copy()
    for n in e.FEATURES:changed[n]=1e4
    matrices2,state2=e.designs(t,ta,altered,changed)
    assert state==state2
    width=matrices['C_controls'][0].shape[1]
    for arm,cols in e.ARMS.items():
        assert matrices[arm][0].shape[1]==width+len(cols)
        np.testing.assert_array_equal(matrices[arm][0][:,:width],matrices['C_controls'][0])
        np.testing.assert_array_equal(matrices[arm][0],matrices2[arm][0])
    # Nullable value is training-mean imputed to standardized zero; its common
    # missingness flag still distinguishes it from an observed zero.
    assert matrices['V_horizons'][0][0,-1]==0.
    assert 'proxy_variance_22_missing' in state['common']['kept']


def test_complete_four_attempts_preserve_keys_targets_and_refuse_replay(tmp_path):
    data,p,a=synthetic();target=data[0].target.copy()
    with patch.object(e,'plan',return_value=p),patch.object(e,'load',return_value=data):
        r=e.execute(p,tmp_path,a)
        with pytest.raises(FileExistsError):e.execute(p,tmp_path,a)
    assert r['completed_fits']==4
    assert len(list((tmp_path/'execution').glob('attempt-*.json')))==4
    c=json.loads((tmp_path/'execution/completion.json').read_text())
    assert len(c['result_sha256'])==4 and not c['configuration_selection']
    for f in (tmp_path/'execution').glob('result-*.json'):
        record=json.loads(f.read_text())
        assert record['row_ids']==list(data[2].row_ids)
        assert min(record['predicted_variance'])>0
        assert record['extreme_targets_removed']==0
    np.testing.assert_array_equal(data[0].target,target)


def test_interruption_reserves_one_attempt_and_blocks_resume(tmp_path):
    data,p,a=synthetic()
    with patch.object(e,'plan',return_value=p),patch.object(e,'load',return_value=data),patch.object(e.BoostedVariance,'fit',side_effect=KeyboardInterrupt) as fit:
        with pytest.raises(KeyboardInterrupt):e.execute(p,tmp_path,a)
        assert (tmp_path/'execution/attempt-0000.json').exists()
        assert not (tmp_path/'execution/completion.json').exists()
        with pytest.raises(FileExistsError):e.execute(p,tmp_path,a)
        assert fit.call_count==1


@pytest.mark.parametrize('kind',['row','cutoff','value','mask'])
def test_candidate_binding_rejects_key_time_value_or_mask_change(kind):
    t,_=fixture();a=anchors(t)
    c=a[[*e.FEATURES,*e.MASKS]].copy()
    c.insert(0,'row_id',t.row_ids);c['session']=[k[0] for k in t.keys];c['ticker']=[k[1] for k in t.keys]
    index={k:i for i,k in enumerate(t.feature_keys)}
    c['cutoff']=[t.feature_cutoffs[index[k]] for k in t.keys]
    source=c.copy();source['log_return_1']=.01
    expected=c.drop(columns='row_id').copy()
    if kind=='row':c.loc[0,'ticker']='OTHER'
    if kind=='cutoff':c.loc[0,'cutoff']='2021-01-01T00:00:00Z'
    if kind=='value':c.loc[1,'proxy_variance_22']=999.
    if kind=='mask':c.loc[0,'proxy_variance_22_missing']=0.
    with patch.object(e,'build_features',return_value=expected):
        with pytest.raises(ValueError):e.candidate_anchors(t,source,c)

