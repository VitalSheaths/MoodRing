from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json

import pytest

from ingestion.jobs.measurement import (Capture,MeasurementError,RetentionPolicy,
    encode_report,phase_delta,retention_dry_run)
from capacity_support import runner

NOW=datetime(2026,10,22,tzinfo=timezone.utc)


def inventory():
    return dict(jobs=[dict(id='PRIVATE_PARENT',state='completed',ended_at=NOW-timedelta(days=100)),
        dict(id='PRIVATE_ACTIVE',state='running',ended_at=None,parent='PRIVATE_PARENT'),
        dict(id='PRIVATE_OLD',state='failed',ended_at=NOW-timedelta(days=100)),
        dict(id='PRIVATE_RECENT',state='cancelled',ended_at=NOW)],
        artifacts=[dict(id='PRIVATE_SHARED',bytes=10),dict(id='PRIVATE_CANDIDATE',bytes=20),dict(id='PRIVATE_UNREFERENCED',bytes=30)],
        references=[('PRIVATE_PARENT','PRIVATE_SHARED'),('PRIVATE_ACTIVE','PRIVATE_SHARED'),('PRIVATE_OLD','PRIVATE_CANDIDATE')])


def test_dry_run_preserves_active_ancestors_shared_and_unreferenced_evidence():
    data=inventory();before=deepcopy(data);result=retention_dry_run(data,NOW)
    assert data==before and result['held_jobs']==3 and result['candidate_jobs']==1
    assert result['shared_artifact_count']==1 and result['protected_artifact_bytes']==60
    assert result['bytes_referenced_by_retained_jobs']==10
    assert result['bytes_referenced_only_by_review_candidates']==20
    assert result['unreferenced_artifact_bytes']==30
    assert result['proposed_delete_bytes']==0 and not result['enforcement_authorized']
    assert 'PRIVATE_' not in json.dumps(result)
    assert 'dependency_of_retained_job' in {j['reason'] for j in result['jobs']}


@pytest.mark.parametrize('state,days',[('completed',30),('completed_with_gaps',60),('failed',90),('cancelled',14)])
def test_state_ages_are_illustrative_and_boundary_is_explicit(state,days):
    data=dict(jobs=[dict(id='a',state=state,ended_at=NOW-timedelta(days=days))],artifacts=[],references=[])
    assert retention_dry_run(data,NOW)['candidate_jobs']==1
    data['jobs'][0]['ended_at']+=timedelta(microseconds=1)
    assert retention_dry_run(data,NOW)['candidate_jobs']==0


@pytest.mark.parametrize('state',['pending','running','paused','retryable'])
def test_nonterminal_jobs_never_become_candidates(state):
    data=dict(jobs=[dict(id='a',state=state,ended_at=NOW-timedelta(days=1000))],artifacts=[],references=[])
    assert retention_dry_run(data,NOW)['candidate_jobs']==0


@pytest.mark.parametrize('change',['missing_parent','cycle','missing_reference','unknown_artifact'])
def test_incomplete_graph_forces_hold_without_guessing(change):
    data=inventory()
    if change=='missing_parent':data['jobs'][0]['parent']='missing'
    if change=='cycle':data['jobs'][0]['parent']='PRIVATE_ACTIVE'
    if change=='missing_reference':data['references'].append(('missing','PRIVATE_SHARED'))
    if change=='unknown_artifact':data['references'].append(('PRIVATE_ACTIVE','missing'))
    result=retention_dry_run(data,NOW)
    assert result['incomplete_inventory'] and result['candidate_jobs']==0 and result['held_jobs']==4
    assert result['proposed_delete_bytes']==0


def test_explicit_holds_and_missing_or_future_end_are_protected():
    data=inventory();assert retention_dry_run(data,NOW,holds=['PRIVATE_OLD'])['candidate_jobs']==0
    for end in (None,NOW+timedelta(days=1)):
        data['jobs'][2]['ended_at']=end
        assert retention_dry_run(data,NOW)['candidate_jobs']==0


@pytest.mark.parametrize('change',['jobs','artifacts','references','state','bytes','duplicate','policy','naive_time','unknown_hold'])
def test_invalid_inventory_or_bounds_fail_safely(change):
    data=inventory();kwargs={};now=NOW
    if change=='jobs':data['jobs']*=4
    if change=='artifacts':data['artifacts']*=34
    if change=='references':data['references']*=34
    if change=='state':data['jobs'][0]['state']='PRIVATE_INVALID'
    if change=='bytes':data['artifacts'][0]['bytes']=-1
    if change=='duplicate':data['jobs'].append(data['jobs'][0])
    if change=='policy':kwargs['policy']=dict(completed_days=True)
    if change=='naive_time':now=NOW.replace(tzinfo=None)
    if change=='unknown_hold':kwargs['holds']=['PRIVATE_MISSING']
    with pytest.raises(MeasurementError) as error:retention_dry_run(data,now,**kwargs)
    assert 'PRIVATE_' not in str(error.value)


def test_lsn_delta_is_cluster_observation_and_unsupported_stays_null():
    base=dict(database_bytes=10,allocated_relation_bytes=5,heap_main_bytes=2,toast_total_bytes=1,index_bytes=2,
        logical_selected_column_bytes=3,unique_artifact_bytes=1)
    before=Capture(base,{},'0/FFFFFFF0');after=Capture(base,{},'1/10')
    assert phase_delta(before,after)['cluster_wal_insert_bytes']==32
    assert phase_delta(Capture(base,{},None),after)['cluster_wal_insert_bytes'] is None
    with pytest.raises(MeasurementError,match='wal_sample_discontinuity'):phase_delta(after,before)
    with pytest.raises(MeasurementError,match='invalid_wal_sample'):phase_delta(Capture(base,{},'PRIVATE_BAD'),after)


def test_report_bound_and_nonfinite_values_rejected():
    assert encode_report({'mode':'dry_run_no_deletion'})
    with pytest.raises(MeasurementError,match='report_limit'):encode_report({'data':'x'*1048576})
    with pytest.raises(MeasurementError,match='invalid_report'):encode_report({'bad':float('nan')})


@pytest.mark.parametrize('field',list(runner.WorkloadLimits.model_fields))
@pytest.mark.parametrize('value',[0,True,'1'])
def test_workload_limits_are_strict_and_cannot_be_increased(field,value):
    with pytest.raises(ValueError):runner.WorkloadLimits(**{field:value})
    with pytest.raises(ValueError):runner.WorkloadLimits(**{field:getattr(runner.WorkloadLimits(),field)+1})


@pytest.mark.parametrize('url,ack',[(None,True),('PRIVATE_CONNECTION',False),('sqlite://',True),('postgresql://localhost/application',True)])
def test_no_fallback_to_application_database_or_implicit_consent(url,ack):
    with pytest.raises(MeasurementError) as error:runner.measure(url,acknowledged=ack)
    assert 'PRIVATE_' not in str(error.value)


def test_growth_sensitivity_is_not_a_capacity_or_retained_wal_claim():
    from ingestion.jobs.measurement import growth_scenario
    report=dict(jobs=10,baseline=dict(allocated_relation_bytes=100),
        final=dict(logical_selected_column_bytes=1000,unique_artifact_bytes=200,artifact_reference_bytes=400,allocated_relation_bytes=500),
        phases=[dict(delta=dict(cluster_wal_insert_bytes=100))])
    plain=growth_scenario(report,jobs_per_day=1,days=30)
    shared=growth_scenario(report,jobs_per_day=1,days=30,artifact_sharing_percent=50)
    revised=growth_scenario(report,jobs_per_day=1,days=30,extra_duplicate_percent=50,extra_revision_percent=25)
    assert shared['logical_selected_column_bytes']<plain['logical_selected_column_bytes']<revised['logical_selected_column_bytes']
    assert plain['outside_observed_job_count'] and plain['retained_wal_bytes'] is None
    assert plain['mode']=='illustrative_not_capacity_approval'
    assert shared['physical_mixed_workload_extrapolation_bytes']==plain['physical_mixed_workload_extrapolation_bytes']
    report['phases'][0]['delta']['cluster_wal_insert_bytes']=None
    assert growth_scenario(report)['cluster_wal_insert_extrapolation_bytes'] is None
    with pytest.raises(MeasurementError):growth_scenario(report,jobs_per_day=True)


def test_private_driver_errors_are_not_exposed():
    from sqlalchemy.exc import OperationalError
    from ingestion.jobs.measurement import collect
    class Broken:
        def connect(self):raise OperationalError('PRIVATE_SQL',{'password':'PRIVATE_TOKEN'},Exception('/private/driver'))
    with pytest.raises(MeasurementError) as error:collect(Broken(),'moodring_test_'+'a'*32)
    assert str(error.value)=='measurement_unavailable'
    assert 'PRIVATE_' not in str(error.value)
