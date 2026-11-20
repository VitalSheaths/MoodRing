"""Metadata-only lineage does not authorize fitting or overwrite evidence."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from utils.research_lineage import ResearchLineage,make_record,artifact_reference

def record(**changes):
    base=dict(kind='preparation',run_id='a'*64,stage='development_only',
              status='prepared',created_at='2026-11-21T00:00:00Z')
    base.update(changes)
    return make_record(**base)

def test_read_only_default_does_not_create_directory(tmp_path):
    store=ResearchLineage(tmp_path/'missing')
    assert store.query()==[] and not store.root.exists()
    with pytest.raises(PermissionError):store.append(record())

def test_append_idempotent_query_parent_and_read_only_report(tmp_path):
    store=ResearchLineage(tmp_path/'lineage',read_only=False);parent=store.append(record())
    child=store.append(record(kind='report',status='completed',parents=[parent]))
    assert store.append(record())==parent
    assert len(store.query())==2 and store.query(parent=parent)[0]['identity']==child
    assert ResearchLineage(store.root).get(child)['promoted'] is False

def test_tampering_is_detected_not_skipped(tmp_path):
    s=ResearchLineage(tmp_path,read_only=False);key=s.append(record());f=tmp_path/(key+'.json')
    x=json.loads(f.read_text());x['status']='failed';f.write_text(json.dumps(x))
    with pytest.raises(ValueError,match='hash_mismatch'):s.query()

@pytest.mark.parametrize('extra',[{'credentials':'secret'},{'dataset':[[1,2]]},{'model_trees':[1]},{'promoted':True}])
def test_payloads_and_promotion_rejected(tmp_path,extra):
    s=ResearchLineage(tmp_path,read_only=False);r=record();r.update(extra)
    with pytest.raises(ValueError):s.append(r)
    assert not list(tmp_path.glob('*.json'))

def test_missing_parent_and_cross_stage_rejected(tmp_path):
    s=ResearchLineage(tmp_path,read_only=False)
    with pytest.raises(FileNotFoundError):s.append(record(parents=['f'*64]))
    parent=s.append(record())
    with pytest.raises(ValueError,match='cross_stage'):s.append(record(stage='final_test',parents=[parent]))

def test_concurrent_duplicate_reservation_cannot_fork_attempt(tmp_path):
    s=ResearchLineage(tmp_path,read_only=False)
    def append(i):
        try:return s.append(record(kind='attempt',status='reserved',attempt_index=0,created_at=f'2026-11-21T00:00:0{i}Z'))
        except ValueError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(append,[1,2]))
    assert result.count('rejected')==1 and len(s.query(kind='attempt'))==1

def test_hash_reference_stores_no_payload_and_detects_change(tmp_path):
    source=tmp_path/'data.parquet';source.write_bytes(b'private payload')
    ref=artifact_reference(tmp_path,'data.parquet','dataset');s=ResearchLineage(tmp_path/'lineage',read_only=False)
    s.append(record(artifacts=[ref]))
    assert b'private payload' not in next(s.root.glob('*.json')).read_bytes()
    assert all(c['matches'] for c in s.verify_artifacts(tmp_path))
    source.write_bytes(b'changed');assert not s.verify_artifacts(tmp_path)[0]['matches']

@pytest.mark.parametrize('name',['../data.json','/data.json','.env','secrets/key.json'])
def test_unsafe_paths_rejected(tmp_path,name):
    with pytest.raises(ValueError):artifact_reference(tmp_path,name,'source')

def test_symlink_rejected(tmp_path):
    target=tmp_path/'source.json';target.write_text('{}');link=tmp_path/'linked.json';link.symlink_to(target)
    with pytest.raises(ValueError):artifact_reference(tmp_path,'linked.json','source')

