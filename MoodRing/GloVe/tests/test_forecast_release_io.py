"""Publication/approval tests use synthetic data and fake fit results."""
from pathlib import Path
import pytest
from research_data.archive import digest,json_bytes
from research_data.forecast_runners import make_release_plan
import research_data.forecast_release_io as adapter
from test_forecast_runners import fixture


def test_baseline_denial_before_load_or_training(tmp_path,monkeypatch):
    monkeypatch.setattr(adapter,'load_inputs',lambda *a:pytest.fail('loaded'))
    with pytest.raises(PermissionError):
        adapter.execute(dict(run_id='id'),{},tmp_path,dict(granted=False))
    assert not list(tmp_path.iterdir())


def test_changed_receipt_fails_before_reading_dataset(tmp_path):
    (tmp_path/'receipt').write_text('changed')
    descriptor=dict(bindings={'receipt':digest(b'original')})
    with pytest.raises(ValueError,match='baseline_file_changed'):adapter.load_inputs(descriptor,tmp_path)


def test_baseline_single_use_output_and_sharded_results(tmp_path,monkeypatch):
    train,dev=fixture();scope=dict(output_relative_path='execution')
    (tmp_path/'scope.json').write_bytes(json_bytes(scope))
    bindings={'scope.json':digest(json_bytes(scope))}
    descriptor=dict(bindings=bindings,output_relative_path='execution',scope_receipt='scope.json')
    plan=make_release_plan(train,dev,bindings)
    approval=dict(granted=True,scope='baseline_train_dev_only',run_id=plan['run_id'],
        approved_by='synthetic',approved_at='2026-11-14T00:00:00Z')
    monkeypatch.setattr(adapter,'load_inputs',lambda *a:(train,dev))
    calls=[]
    def fake(*args):
        calls.append(1)
        return dict(run_id=plan['run_id'],results=[dict(synthetic=True)]*37,final_test_held=True)
    monkeypatch.setattr(adapter,'run_baseline_release',fake)
    result=adapter.execute(plan,descriptor,tmp_path,approval)
    assert result['completed_fits']==36 and len(list((tmp_path/'execution').glob('result-*.json')))==37
    with pytest.raises(ValueError,match='output_exists'):adapter.execute(plan,descriptor,tmp_path,approval)
    assert len(calls)==1


def test_output_scope_cannot_be_redirected(tmp_path,monkeypatch):
    train,dev=fixture();scope=dict(output_relative_path='bound')
    (tmp_path/'scope.json').write_bytes(json_bytes(scope));bindings={'scope.json':digest(json_bytes(scope))}
    descriptor=dict(bindings=bindings,output_relative_path='other',scope_receipt='scope.json')
    plan=make_release_plan(train,dev,bindings)
    approval=dict(granted=True,scope='baseline_train_dev_only',run_id=plan['run_id'])
    monkeypatch.setattr(adapter,'load_inputs',lambda *a:(train,dev))
    monkeypatch.setattr(adapter,'run_baseline_release',lambda *a:pytest.fail('training'))
    with pytest.raises(ValueError,match='output_scope'):adapter.execute(plan,descriptor,tmp_path,approval)
    assert not (tmp_path/'other').exists()
