def test_ticket_records_have_valid_status_and_closure_evidence():
    from pathlib import Path
    import yaml
    directory = Path(__file__).resolve().parents[1] / 'docs/tickets'
    records = [yaml.safe_load(p.read_text()) for p in directory.glob('MR-*.yml')]
    assert records
    assert len({r['id'] for r in records}) == len(records)
    table = (directory / 'README.md').read_text()
    for record in records:
        assert record['status'] in ('open', 'closed')
        assert record['acceptance'] and record['invariant'] and record['affected_files']
        assert f"| [{record['id']}]({record['id']}.yml) | {record['priority']} | {record['status']} |" in table
        if record['status'] == 'closed':
            assert record['evidence'] and record['reopened_when']
