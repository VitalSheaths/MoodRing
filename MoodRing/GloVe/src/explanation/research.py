"""Read-only bridge from approved train/dev features to the existing answer contract.

This is deliberately separate from DailyObservation and strict PIT eligibility.
It imports already-calculated descriptive values, never fabricates publication
times, never reads label tables and never computes predictions. The trusted local
operator supplies the owner and a separately pinned descriptor digest.
"""
from collections import OrderedDict
from datetime import date, datetime
import io
import json
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import Field, model_validator

from ingestion.universe.identity import Id, Time, content_hash
from research_data.archive import digest, read_bytes
from research_data.pilot_runner import relative_file
from research_data.panel_scope import UNIVERSE
from .contracts import Record, Card, ToolResult
from .dictionary import ENTRIES, TECHNICAL_KEYS
from .indicators import UNSUPPORTED

VERSION = 'approved-research-cards-v1'
LIMITATIONS = (
    'Retrospective vendor-adjusted research snapshot; strict PIT eligibility remains false.',
    'The cutoff is a historical reference, not proof of historical price-version availability.',
    'Cards import frozen descriptive features. No forecast, future label, or sentiment score is loaded.',
    'Macro receipt assumptions and the known 2025 news gap remain unresolved in the wider study.',
)


class ResearchRow(Record):
    """One approved comparison anchor and its eight past context sessions."""
    session: date
    cutoff: Time
    row_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    context_sessions: tuple[date, ...] = Field(min_length=8, max_length=8)
    values: tuple[tuple[str, float | None], ...]

    @model_validator(mode='after')
    def valid_context(self):
        """Require an exact indicator schema and eight ordered past sessions."""
        if (tuple(sorted(set(self.context_sessions))) != self.context_sessions
                or self.context_sessions[-1] != self.session
                or self.cutoff.date() != self.session
                or set(k for k,v in self.values) != set(TECHNICAL_KEYS)
                or len(self.values) != len(TECHNICAL_KEYS)):
            raise ValueError('research_context_or_schema')
        return self


class ResearchSnapshot(Record):
    """Bounded retrospective view; ownership participates in its content identity."""
    owner_id: Id
    ticker: str
    split: Literal['train', 'dev']
    loaded_at: Time
    source_bindings: tuple[tuple[str, str], ...]
    rows: tuple[ResearchRow, ...] = Field(min_length=1, max_length=512)
    data_kind: Literal['retrospective_research'] = 'retrospective_research'

    @model_validator(mode='after')
    def validate_rows(self):
        """Reject duplicate anchors, unknown stocks and cross-partition contexts."""
        if self.ticker not in UNIVERSE or not self.source_bindings:
            raise ValueError('research_scope')
        dates = tuple(r.session for r in self.rows)
        if dates != tuple(sorted(set(dates))):
            raise ValueError('duplicate_or_unsorted_anchor')
        first, last = ('2020-01-01','2023-01-01') if self.split == 'train' else ('2023-01-01','2025-01-01')
        if any(not first <= d.isoformat() < last for r in self.rows for d in r.context_sessions):
            raise ValueError('partition_boundary')
        return self

    @property
    def snapshot_id(self):
        """Bind content, ownership and source receipts into one immutable identity."""
        return 'snapshot:' + content_hash(self)


def load_snapshot(root, descriptor_path, descriptor_sha256, owner_id, ticker, loaded_at):
    """Verify pinned bytes before reading exactly features and context columns.

    Paths are operator inputs, never question text. A descriptor records external
    approval provenance; a hash is integrity evidence, not proof of human review.
    No directory discovery, label loading or final-test file read is performed.
    """
    root = Path(root).absolute()
    raw = read_bytes(relative_file(root, descriptor_path), limit=200_000)
    if digest(raw) != descriptor_sha256:
        raise ValueError('descriptor_changed')
    d = json.loads(raw)
    if (d.get('version') != VERSION or d.get('split') not in ('train','dev')
            or d.get('purpose') != 'descriptive_cards_only'
            or not d.get('approval_reference') or not d.get('historical_prediction_eligible') is False
            or set(d.get('files',{})) != {'features','contexts','manifest','research_manifest'}):
        raise ValueError('descriptor_scope')
    if ticker not in UNIVERSE:
        raise ValueError('ticker_scope')
    blobs = {}
    for kind, spec in d['files'].items():
        if not isinstance(spec.get('sha256'), str) or len(spec['sha256']) != 64:
            raise ValueError('binding_schema')
        data = read_bytes(relative_file(root,spec['path']), limit=16*1024*1024)
        if digest(data) != spec['sha256']:
            raise ValueError('source_changed:'+kind)
        blobs[kind] = data
    manifest = json.loads(blobs['manifest'])
    if manifest.get('split') != d['split'] or manifest.get('final_test_excluded') is not True:
        raise ValueError('manifest_scope')
    for kind in ('features','contexts'):
        if manifest['files'][kind+'.parquet']['sha256'] != d['files'][kind]['sha256']:
            raise ValueError('manifest_binding')
    research = json.loads(blobs['research_manifest'])
    if (research['contract'].get('strict_pit') is not False or
            research['contract'].get('return') != 'log(frozen_adj_close[t]/frozen_adj_close[t-1])'):
        raise ValueError('adjustment_contract')
    features = pd.read_parquet(io.BytesIO(blobs['features']),
        columns=['ticker','session','cutoff',*TECHNICAL_KEYS])
    contexts = pd.read_parquet(io.BytesIO(blobs['contexts']),
        columns=['ticker','session','split','row_id','context_sessions','primary_matched'])
    if max(len(features),len(contexts)) > 8000:
        raise ValueError('table_row_bound')
    if features.duplicated(['ticker','session']).any() or contexts.duplicated(['ticker','session']).any():
        raise ValueError('duplicate_source_key')
    first,last = ('2020-01-01','2023-01-01') if d['split']=='train' else ('2023-01-01','2025-01-01')
    for frame in (features,contexts):
        if not frame.session.map(lambda x:first <= str(x) < last).all():
            raise ValueError('file_partition_scope')
    if set(contexts.split) != {d['split']} or not contexts.primary_matched.eq(True).fillna(False).all():
        raise ValueError('unapproved_population')
    f = features[features.ticker == ticker].set_index('session')
    rows = []
    for r in contexts[contexts.ticker == ticker].sort_values('session').itertuples():
        if r.session not in f.index or any(s not in f.index for s in r.context_sessions):
            raise ValueError('missing_context_feature')
        values=f.loc[r.session]
        rows.append(ResearchRow(session=r.session, cutoff=values['cutoff'], row_id=r.row_id,
            context_sessions=tuple(r.context_sessions), values=tuple(
                (k, None if pd.isna(values[k]) else float(values[k])) for k in TECHNICAL_KEYS)))
    bindings = (('descriptor',descriptor_sha256),) + tuple(
        (kind,d['files'][kind]['sha256']) for kind in sorted(d['files']))
    return ResearchSnapshot(owner_id=owner_id,ticker=ticker,split=d['split'],
        loaded_at=loaded_at,source_bindings=bindings,rows=tuple(rows))


class ResearchEvidenceTools:
    """Serve supplied approved values; question text has no file or query capability."""
    def __init__(self, snapshots, cache_limit=64):
        """Install a bounded catalogue and owner-scoped result cache."""
        if not 1 <= len(snapshots) <= 16 or not 1 <= cache_limit <= 256:
            raise ValueError('catalogue_or_cache_limit')
        self._snapshots = {s.snapshot_id:s for s in snapshots}
        self._cache = OrderedDict()
        self._limit = cache_limit

    def cards(self, owner_id, query, computed_at: datetime):
        """Authorize before cache lookup and return only the exact approved row."""
        snapshot = self._snapshots.get(query.snapshot_id)
        if snapshot is None or snapshot.owner_id != owner_id:
            raise PermissionError('snapshot_unavailable')
        if (computed_at.tzinfo is None or computed_at < snapshot.loaded_at
                or computed_at < query.cutoff):
            raise ValueError('invalid_computation_time')
        if query.instrument_id != 'stock:'+snapshot.ticker:
            raise ValueError('instrument_not_in_snapshot')
        row = next((r for r in snapshot.rows if r.session == query.session),None)
        if row is None or row.cutoff != query.cutoff:
            raise ValueError('unapproved_session_or_cutoff')
        key = content_hash((owner_id,query,computed_at,VERSION))
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        groups={'Price':'trend','Volume':'volume','Trend':'trend','Risk':'volatility'}
        cards = [Card(key=k,bundle=groups[ENTRIES[k]['group']],unit=ENTRIES[k]['unit'],
            status='missing' if v is None else 'ok',value=v,
            reason='missing_in_approved_snapshot' if v is None else None) for k,v in row.values]
        cards += [Card(key=k,bundle=b,unit='not_applicable',status=s,reason=r) for k,b,s,r in UNSUPPORTED]
        result = ToolResult(tool_version=VERSION,query=query,data_kind='retrospective_research',
            mode='retrospective_diagnostic',source_revision_ids=('feature-row:'+content_hash(row),),
            source_bindings=snapshot.source_bindings, context_sessions=row.context_sessions,
            limitations=LIMITATIONS,cards=tuple(cards))
        self._cache[key]=result
        if len(self._cache)>self._limit:self._cache.popitem(last=False)
        return result
