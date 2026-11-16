import numpy as np
import pytest
from research_data.research_experiment import split_for,matched_keys,verify_window,TrainOnlyStandardizer


def test_cross_boundary_and_unmatured_targets_are_purged():
    assert split_for('2022-12-30T21:20:00Z','2023-01-03T21:00:00Z') is None
    assert split_for('2024-12-31T21:20:00Z','2025-01-02T21:00:00Z') is None
    assert split_for('2024-01-02T21:20:00Z','2024-01-03T21:00:00Z')=='dev'
    assert split_for('2026-11-03T21:20:00Z',None) is None
    with pytest.raises(ValueError):split_for('2024-01-02T21:20:00Z','2024-01-02T21:00:00Z')


def test_missing_differs_from_neutral_and_test_never_fits():
    scaler=TrainOnlyStandardizer().fit([[0,10],[1,20]],['2021-01-01T21:20:00Z']*2)
    out=scaler.transform([[0,10],[np.nan,10]])
    assert out[0,2]==0 and out[1,2]==1
    with pytest.raises(ValueError,match='already_frozen'):scaler.fit([[99,99]],['2021-01-01T21:20:00Z'])
    with pytest.raises(ValueError,match='train_only'):TrainOnlyStandardizer().fit([[99]],['2025-01-01T21:20:00Z'])


def test_shared_population_and_feature_cutoff():
    keys,counts=matched_keys({'baseline':[('2024-01-02','AAPL'),('2024-01-03','AAPL')],
                              'direct':[('2024-01-03','AAPL')]})
    assert keys==[('2024-01-03','AAPL')] and counts['baseline']['excluded_from_matched']==1
    verify_window('2024-01-02T21:20:00Z',[dict(value=None,available_at=None)])
    with pytest.raises(ValueError):verify_window('2024-01-02T21:20:00Z',[dict(value=1,available_at=None)])
    with pytest.raises(ValueError):verify_window('2024-01-02T21:20:00Z',[dict(value=1,evidence_resolved=True,available_at='2024-01-03T00:00:00Z')])



def test_string_false_cannot_approve_training_evidence():
    with pytest.raises(ValueError):
        verify_window('2024-01-02T21:20:00Z',[dict(value=1,available_at='2024-01-02T20:00:00Z',evidence_resolved='false')])
