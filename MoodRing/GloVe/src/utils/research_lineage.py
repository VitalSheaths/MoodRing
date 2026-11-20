"""Metadata-only lineage over existing immutable research artifacts.

This is a local index, not another experiment registry or trading ledger.
Readers never rewrite records. Writers append content-addressed records; source
datasets, credentials, model parameters and predictions stay in their artifacts.
A lineage status or approval reference does not authorize execution or promotion.
"""
from pathlib import Path, PurePosixPath
from datetime import datetime
import fcntl
import hashlib
import json
import os
import re
import tempfile

VERSION = 'research-lineage-v1'
KINDS = {'source_snapshot','dataset','features','split','configuration','preparation',
         'approval','run','attempt','preprocessing','model','predictions','metrics',
         'audit','contrast','report'}
STATUSES = {'recorded','prepared','approval_required','authorized','reserved','completed','failed','blocked'}
STAGES = {'synthetic','development_only','final_test'}
ROLES = {'source','dataset','features','split','configuration','packet','approval',
         'reservation','preprocessing','model','predictions','metrics','audit','contrast','report','code','receipt'}
HEX = re.compile(r'^[a-f0-9]{64}$')
COMMIT = re.compile(r'^[a-f0-9]{40}$')
MAX_RECORD = 256*1024
MAX_RECORDS = 10000


def canonical(value):
    """Canonical finite JSON; identity includes every metadata field."""
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()


def _path(root,relative):
    """Reject absolute/traversal/secret paths and symlinks without opening data."""
    if not isinstance(relative,str) or not relative:raise ValueError('relative_artifact_path_required')
    rel=PurePosixPath(relative)
    if rel.is_absolute() or '..' in rel.parts or '\\' in relative:
        raise ValueError('relative_artifact_path_required')
    if any(s in {'.env','.aws','.ssh','.git','credentials','secrets'} or s.startswith('.env.') for s in rel.parts):
        raise ValueError('sensitive_artifact_path')
    path=Path(root).absolute().joinpath(*rel.parts)
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('symlink_path')
    return path


def artifact_reference(root,path,role):
    """Hash an existing file without storing or interpreting its private payload."""
    if role not in ROLES:raise ValueError('artifact_role')
    f=_path(root,path)
    if not f.is_file():raise ValueError('artifact_not_file')
    h=hashlib.sha256();size=0
    with f.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block);size+=len(block)
    return dict(role=role,path=path,sha256=h.hexdigest(),bytes=size)


def validate(record):
    """Strict metadata allowlist rejects payloads and arbitrary secret fields."""
    required={'version','kind','run_id','stage','status','created_at','parents','artifacts',
              'code_commit','configuration_commit','approval_id','attempt_index','promoted','trading_signal'}
    if not isinstance(record,dict) or set(record)!=required:raise ValueError('lineage_metadata_schema')
    if record['version']!=VERSION or record['kind'] not in KINDS or record['status'] not in STATUSES or record['stage'] not in STAGES:
        raise ValueError('lineage_enum')
    for key in ('run_id','approval_id'):
        value=record[key]
        if (value is None and key=='run_id') or (value is not None and not HEX.fullmatch(str(value))):
            raise ValueError('lineage_identity')
    for key in ('code_commit','configuration_commit'):
        if record[key] is not None and not COMMIT.fullmatch(str(record[key])):raise ValueError('commit_identity')
    if record['promoted'] is not False or record['trading_signal'] is not False:raise ValueError('no_promotion_authority')
    ts=datetime.fromisoformat(record['created_at'].replace('Z','+00:00'))
    if ts.tzinfo is None or ts.utcoffset().total_seconds()!=0:raise ValueError('utc_timestamp_required')
    parents=record['parents'];refs=record['artifacts']
    if not isinstance(parents,list) or len(parents)>256 or len(set(parents))!=len(parents) or any(not HEX.fullmatch(str(v)) for v in parents):
        raise ValueError('parent_identities')
    if not isinstance(refs,list) or len(refs)>256:raise ValueError('artifact_bounds')
    seen=set()
    for r in refs:
        if not isinstance(r,dict) or set(r)!={'role','path','sha256','bytes'}:raise ValueError('artifact_schema')
        if r['role'] not in ROLES or not HEX.fullmatch(str(r['sha256'])) or type(r['bytes']) is not int or r['bytes']<0:
            raise ValueError('artifact_binding')
        _path(Path.cwd(),r['path'])
        key=(r['role'],r['path'])
        if key in seen:raise ValueError('duplicate_artifact_reference')
        seen.add(key)
    idx=record['attempt_index']
    if record['kind']=='attempt':
        if type(idx) is not int or not 0<=idx<10000:raise ValueError('attempt_index')
    elif idx is not None:raise ValueError('unexpected_attempt_index')
    if len(canonical(record))>MAX_RECORD:raise ValueError('lineage_record_bound')
    return record


def make_record(kind,run_id,stage,status,created_at,*,parents=(),artifacts=(),
                code_commit=None,configuration_commit=None,approval_id=None,attempt_index=None):
    """Construct metadata; execution authority remains with the frozen runner."""
    return validate(dict(version=VERSION,kind=kind,run_id=run_id,stage=stage,status=status,
        created_at=created_at,parents=list(parents),artifacts=list(artifacts),code_commit=code_commit,
        configuration_commit=configuration_commit,approval_id=approval_id,attempt_index=attempt_index,
        promoted=False,trading_signal=False))


class ResearchLineage:
    """Append-only JSON evidence, with a read-only query interface by default."""

    def __init__(self,root,*,read_only=True):
        self.root=Path(root).absolute();self.read_only=read_only
        if any(p.is_symlink() for p in (self.root,*self.root.parents)):raise ValueError('symlink_path')
        if not read_only:self.root.mkdir(parents=True,exist_ok=True,mode=0o700)

    def get(self,identity):
        """Verify content identity before returning a defensive decoded record."""
        if not HEX.fullmatch(str(identity)):raise ValueError('lineage_identity')
        f=_path(self.root,identity+'.json')
        if f.stat().st_size>MAX_RECORD:raise ValueError('lineage_record_bound')
        raw=f.read_bytes();record=validate(json.loads(raw))
        if hashlib.sha256(canonical(record)).hexdigest()!=identity:raise ValueError('lineage_hash_mismatch')
        return record

    def query(self,*,run_id=None,kind=None,status=None,stage=None,parent=None):
        """Read metadata only; malformed or changed evidence is not silently skipped."""
        if not self.root.exists():return []
        files=sorted(self.root.glob('*.json'))
        if len(files)>MAX_RECORDS:raise ValueError('lineage_store_bound')
        found=[]
        for f in files:
            value=self.get(f.stem)
            if any(want is not None and value[k]!=want for k,want in
                   [('run_id',run_id),('kind',kind),('status',status),('stage',stage)]):continue
            if parent is not None and parent not in value['parents']:continue
            found.append(dict(identity=f.stem,record=value))
        return found

    def append(self,record):
        """Exclusive atomic append; no update/delete path, no duplicate reservation."""
        if self.read_only:raise PermissionError('read_only_lineage')
        record=validate(record);blob=canonical(record);identity=hashlib.sha256(blob).hexdigest()
        lock_path=_path(self.root,'.append.lock')
        fd=os.open(lock_path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            dest=_path(self.root,identity+'.json')
            if dest.exists():
                if self.get(identity)!=record:raise ValueError('lineage_collision')
                return identity
            entries=self.query()
            if len(entries)>=MAX_RECORDS:raise ValueError('lineage_store_bound')
            for parent in record['parents']:
                prior=self.get(parent)
                if prior['stage']!=record['stage']:raise ValueError('cross_stage_parent')
            if record['kind']=='attempt' and record['status']=='reserved':
                if any(x['record']['run_id']==record['run_id'] and x['record']['kind']=='attempt'
                       and x['record']['status']=='reserved' and x['record']['attempt_index']==record['attempt_index'] for x in entries):
                    raise ValueError('attempt_already_reserved')
            tmp=None
            try:
                with tempfile.NamedTemporaryFile(dir=self.root,prefix='.pending-',delete=False) as f:
                    tmp=Path(f.name);f.write(blob);f.flush();os.fsync(f.fileno())
                os.link(tmp,dest)  # Never replace an existing evidence file.
                dirfd=os.open(self.root,os.O_RDONLY)
                try:os.fsync(dirfd)
                finally:os.close(dirfd)
            finally:
                if tmp is not None:tmp.unlink(missing_ok=True)
            return identity

    def verify_artifacts(self,artifact_root,*,run_id=None):
        """Compare hashes/sizes without returning private source contents."""
        checks=[]
        for item in self.query(run_id=run_id):
            for ref in item['record']['artifacts']:
                actual=artifact_reference(artifact_root,ref['path'],ref['role'])
                checks.append(dict(record_id=item['identity'],path=ref['path'],
                                   matches=actual==ref))
        return checks


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Read metadata-only research lineage; never execute or promote.')
    parser.add_argument('--store',type=Path,required=True)
    for key in ('run-id','kind','status','stage','parent'):parser.add_argument('--'+key)
    args=parser.parse_args()
    print(json.dumps(ResearchLineage(args.store).query(run_id=args.run_id,kind=args.kind,
        status=args.status,stage=args.stage,parent=args.parent),indent=2))

