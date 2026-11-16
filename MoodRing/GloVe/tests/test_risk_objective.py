import math
import pytest
from research_data.risk_objective import realized_target as _realized_target
from research_data.point_in_time import sessions_between
from research_data.research_experiment import TrainOnlyStandardizer,split_for
import numpy as np


def realized_target(cutoff,rows):
    return _realized_target(cutoff,rows,label_asof='2026-01-02T00:00:00Z')


def records():
    return [dict(ticker='TEST',session=s,total_return_wealth=100*math.exp(i*.01),total_return_verified=True,
        label_basis_id='synthetic-consistent-basis',available_at=c.replace(minute=5).isoformat(),source_ids=[str(i)]) for i,(s,c) in enumerate(sessions_between('2024-01-02','2024-01-09'))]


def test_realized_target_retains_extremes_and_maturity():
    rows=records();target=realized_target('2024-01-02T21:20:00Z',rows)
    assert target['variance']==pytest.approx(252*.01**2)
    rows[-1]['total_return_wealth']=1
    assert realized_target('2024-01-02T21:20:00Z',rows)['variance']>10
    assert realized_target('2024-01-02T21:20:00Z',rows[:5]) is None
    rows[-1]['total_return_verified']=False
    assert realized_target('2024-01-02T21:20:00Z',rows) is None


def test_target_requires_consecutive_sessions_and_no_same_close_execution():
    rows=records();rows[2]['session']='2024-01-05'
    with pytest.raises(ValueError):realized_target('2024-01-02T21:20:00Z',rows)
    with pytest.raises(ValueError):realized_target('2024-01-02T21:00:00Z',records())
    assert split_for('2024-12-30T21:20:00Z','2025-01-07T21:05:00Z') is None


def test_eval_changes_never_change_training_preprocessing():
    scaler=TrainOnlyStandardizer().fit([[1,2],[3,4]],['2021-01-04T21:20:00Z']*2)
    before=(scaler.mean.copy(),scaler.scale.copy());reference=scaler.transform([[2,3]])
    scaler.transform([[1e100,np.nan],[-1e100,5]])
    np.testing.assert_equal(scaler.mean,before[0]);np.testing.assert_equal(scaler.scale,before[1])
    np.testing.assert_equal(scaler.transform([[2,3]]),reference)
