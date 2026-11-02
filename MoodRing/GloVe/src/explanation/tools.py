"""Bounded read-only tools; no SQL, filesystem or network capability."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime

from ingestion.universe.identity import content_hash
from .contracts import Query, Snapshot, ToolResult
from .indicators import VERSION, calculate


class EvidenceTools:
    """Serve explicitly supplied owned snapshots with a bounded content cache.

    The trusted caller supplies authenticated owner_id. This library is not a
    token issuer or HTTP authentication layer. Permissions precede every cache hit.
    """

    def __init__(self, snapshots: tuple[Snapshot, ...], cache_limit: int = 64):
        """Bind a finite catalogue; do not discover or open archive paths."""
        if len(snapshots) > 16 or not 1 <= cache_limit <= 256:
            raise ValueError('catalogue_or_cache_limit')
        self._snapshots = {s.snapshot_id: s for s in snapshots}
        self._cache: OrderedDict[str, ToolResult] = OrderedDict()
        self._cache_limit = cache_limit

    def cards(self, owner_id: str, query: Query, computed_at: datetime) -> ToolResult:
        """Resolve the latest eligible revision without compressing coverage gaps."""
        snapshot = self._snapshots.get(query.snapshot_id)
        if snapshot is None or snapshot.owner_id != owner_id:
            raise PermissionError('snapshot_unavailable')
        if computed_at.tzinfo is None or query.cutoff > computed_at:
            raise ValueError('invalid_computation_time')
        # Ingestion eligibility can change with computation time even at a fixed
        # historical cutoff. Never reuse a later reconstruction for an earlier one.
        key = content_hash((owner_id, query, computed_at, VERSION))
        if key in self._cache:
            result = self._cache.pop(key)
            self._cache[key] = result
            return result
        sessions = [s for s in snapshot.calendar.sessions if s.session_date <= query.session]
        reason = None
        if query.instrument_id != snapshot.observations[0].instrument.instrument_id:
            reason = 'instrument_not_in_snapshot'
        elif not sessions or sessions[-1].session_date != query.session:
            reason = 'session_not_in_calendar'
        elif sessions[-1].closes_at > query.cutoff:
            reason = 'session_after_cutoff'
        eligible = {}
        for row in snapshot.observations:
            if (row.session.session_date <= query.session and row.eligible(query.cutoff)
                    and row.receipt.evidence.ingested_at <= computed_at
                    and row.instrument.evidence.ingested_at <= computed_at
                    and row.mapping.evidence.ingested_at <= computed_at
                    and (row.canonical_mapping is None or row.canonical_mapping.evidence.ingested_at <= computed_at)):
                eligible[row.session.session_date] = row
        rows = ()
        if reason is None:
            if any(s.session_date not in eligible for s in sessions):
                reason = 'missing_or_ineligible_session'
            else:
                rows = tuple(eligible[s.session_date] for s in sessions)
        result = ToolResult(tool_version=VERSION, query=query,
                            source_revision_ids=tuple(r.revision_id for r in rows),
                            cards=calculate(rows, reason))
        self._cache[key] = result
        if len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)
        return result
