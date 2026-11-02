"""Immutable bot views over existing market contracts, not another price store."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ingestion.market.bars import DailyObservation, validate_series
from ingestion.market.sessions import SessionCalendar
from ingestion.universe.identity import Id, Time, content_hash


class Record(BaseModel):
    """Strict, immutable serializable evidence boundary."""

    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class Snapshot(Record):
    """Bounded supplied synthetic series; future archive adapters are separate work."""

    owner_id: Id
    data_kind: Literal['synthetic'] = 'synthetic'
    calendar: SessionCalendar
    observations: tuple[DailyObservation, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode='after')
    def validate_source(self):
        """Reuse series/revision validation and require an exact supplied schedule."""
        if len(self.calendar.sessions) > 512:
            raise ValueError('calendar_limit')
        sessions = {s.session_date: s for s in self.calendar.sessions}
        seen = {}
        for row in self.observations:
            if row.receipt.provider != 'bot-fixture' or row.shape != 'ohlc':
                raise ValueError('synthetic_ohlc_only')
            if sessions.get(row.session.session_date) != row.session:
                raise ValueError('calendar_mismatch')
            if row.close <= 0 or row.low <= 0:
                raise ValueError('positive_prices_required')
            if row.revision_id in seen and seen[row.revision_id] != row:
                raise ValueError('contradictory_revision_evidence')
            seen[row.revision_id] = row
        validate_series(self.observations)
        return self

    @property
    def snapshot_id(self) -> str:
        """Bind every query to content, ownership, calendar and receipt evidence."""
        return 'snapshot:' + content_hash(self)


class Query(Record):
    """Context is selected explicitly, never inferred from a language model."""

    snapshot_id: str = Field(pattern=r'^snapshot:[a-f0-9]{64}$')
    instrument_id: Id
    session: date
    cutoff: Time


class Card(Record):
    """A measurement or an explicit reason why it cannot be computed."""

    key: str
    bundle: str
    unit: str
    status: Literal['ok', 'missing', 'unsupported', 'not_evaluated']
    value: float | None = None
    reason: str | None = None

    @model_validator(mode='after')
    def no_success_defaults(self):
        """Missing outputs cannot masquerade as successful numbers."""
        if self.status == 'ok':
            if self.value is None or self.reason is not None:
                raise ValueError('ok_requires_value')
        elif self.value is not None or not self.reason:
            raise ValueError('missing_requires_reason')
        return self


class ToolResult(Record):
    """Read-only calculation with sufficient identity to replay its inputs."""

    tool_version: str
    query: Query
    data_kind: Literal['synthetic'] = 'synthetic'
    mode: Literal['historical_reconstruction'] = 'historical_reconstruction'
    source_revision_ids: tuple[str, ...]
    cards: tuple[Card, ...]

    @property
    def evidence_id(self) -> str:
        """Keep computation identity independent of model choice and wall time."""
        return 'tool:' + content_hash(self)


class Passage(Record):
    """Versioned curated knowledge, separate from market measurements."""

    key: str
    text: str
    keywords: tuple[str, ...]

    @property
    def evidence_id(self) -> str:
        """Changing wording or retrieval metadata changes the citation identity."""
        return 'kb:' + content_hash(self)


class Statement(Record):
    """One claim and its evidence grade; C never claims verified support."""

    text: str = Field(min_length=1, max_length=2000)
    grade: Literal['A', 'B', 'C']
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode='after')
    def citation_shape(self):
        """Require evidence for A/B and prohibit fabricated support for C."""
        if (self.grade in ('A', 'B')) != bool(self.evidence_ids):
            raise ValueError('grade_citation_mismatch')
        return self


class Answer(Record):
    """Same answer contract for templates and future model adapters."""

    status: Literal['answered', 'unsupported_question', 'unavailable', 'adapter_rejected']
    adapter_version: str
    prompt_version: str = 'evidence-answer-v1'
    computed_at: Time
    result: ToolResult
    passages: tuple[Passage, ...]
    statements: tuple[Statement, ...]

    def resolve_evidence(self, evidence_id: str) -> ToolResult | Passage:
        """Resolve only records carried in this authorized answer envelope."""
        if evidence_id == self.result.evidence_id:
            return self.result
        for passage in self.passages:
            if passage.evidence_id == evidence_id:
                return passage
        raise KeyError('evidence_not_in_answer')

    @model_validator(mode='after')
    def resolved_claims(self):
        """Reject unknown citations, wrong grades and claims absent from records."""
        allowed_a = set()
        for card in self.result.cards:
            value = f'{card.value:.10g} {card.unit}' if card.status == 'ok' else f'{card.status} ({card.reason})'
            allowed_a.add(f'{card.key}: {value}.')
        for statement in self.statements:
            for evidence_id in statement.evidence_ids:
                try:
                    record = self.resolve_evidence(evidence_id)
                except KeyError as exc:
                    raise ValueError('unresolved_evidence') from exc
                if statement.grade == 'A' and (not isinstance(record, ToolResult) or statement.text not in allowed_a):
                    raise ValueError('grade_a_requires_tool_fact')
                if statement.grade == 'B' and (not isinstance(record, Passage) or statement.text != record.text):
                    raise ValueError('grade_b_requires_passage')
        return self
