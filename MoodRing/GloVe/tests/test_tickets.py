"""Validate public issue forms without requiring ignored internal notes."""
from pathlib import Path

import yaml


def test_public_issue_forms_and_links():
    root = Path(__file__).resolve().parents[3]
    forms = root / '.github/ISSUE_TEMPLATE'
    for path in forms.glob('*.yml'):
        form = yaml.safe_load(path.read_text())
        assert form['name'] and form['description'] and form['body']
        ids = [field['id'] for field in form['body'] if 'id' in field]
        assert len(ids) == len(set(ids))
    assert (forms / 'architecture.yml').exists()
    assert (forms / 'bug.yml').exists()
    readme = (root / 'README.md').read_text()
    assert 'https://github.com/VitalSheaths/MoodRing/issues/1' in readme
    assert 'https://img.shields.io/github/issues/VitalSheaths/MoodRing' in readme
    assert 'https://img.shields.io/github/issues-closed/VitalSheaths/MoodRing' in readme
    assert 'docs/tickets' not in readme
