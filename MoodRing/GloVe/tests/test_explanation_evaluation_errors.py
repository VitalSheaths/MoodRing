"""Regression coverage for unexpected exceptions reported by CodeRabbit."""
import json

import pytest

from explanation import evaluation


@pytest.mark.parametrize('error', [ValueError('unexpected_error'), RuntimeError('broken')])
def test_unexpected_exception_never_passes(monkeypatch, tmp_path, error):
    """A normal case cannot pass just because its error matches a fallback string."""
    path = tmp_path / 'dev.json'
    path.write_text(json.dumps([{'id': 'normal', 'question': 'Show cards'}]))
    monkeypatch.setattr(evaluation, 'GOLDEN', path)

    def fail(*args, **kwargs):
        """Simulate a failure before an answer exists."""
        raise error

    monkeypatch.setattr(evaluation, 'scenario', fail)
    report = evaluation.evaluate()
    assert report['passed'] == 0
    assert report['outcomes'][0]['failures'] == ['unexpected_error']


def test_explicit_expected_exception_still_passes(monkeypatch, tmp_path):
    """Retain the explicit expected-error oracle for an unauthorized owner."""
    path = tmp_path / 'dev.json'
    path.write_text(json.dumps([{'id': 'denied', 'question': 'Show cards',
                                 'variant': 'other_owner', 'error': 'snapshot_unavailable'}]))
    monkeypatch.setattr(evaluation, 'GOLDEN', path)
    assert evaluation.evaluate()['passed'] == 1
