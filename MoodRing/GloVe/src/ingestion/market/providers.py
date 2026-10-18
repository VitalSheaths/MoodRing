"""Typed provider envelopes: declarations and receipts, never network clients."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

from pydantic import Field, model_validator

from ingestion.sec.contracts import Sha256, payload_digest, utc
from ingestion.universe.identity import DataRights, Evidence, Id, Time
from .sessions import DateWindow, MarketContract

AdjustmentBasis = Literal['raw', 'split_adjusted', 'dividend_adjusted', 'total_return']
CapabilityState = Literal['unknown', 'unsupported', 'supported']


class RequestParameters(MarketContract):
    """Allowlisted nonsecret parameters; no arbitrary headers, URLs or dicts."""
    instrument_id: Id
    mapping_id: Id
    symbol: str = Field(pattern=r'^[A-Za-z0-9.^:/_-]{1,64}$')
    interval: Literal['1d'] = '1d'
    adjustment: AdjustmentBasis
    window: DateWindow
    limit: int = Field(strict=True, ge=1, le=100000)
    cursor_ref: Id | None = None  # opaque local reference, not a vendor token


class ProviderCapability(MarketContract):
    provider: Id
    feed: Id
    dataset: Id
    dataset_version: Id
    status: CapabilityState
    instrument_kinds: frozenset[Literal['common_stock', 'index']]
    intervals: frozenset[Literal['1d']]
    adjustments: frozenset[AdjustmentBasis]
    max_records: int = Field(strict=True, ge=1, le=100000)
    max_calendar_days: int = Field(strict=True, ge=1, le=40000)
    coverage_from: Time | None = None
    coverage_until: Time | None = None
    evidence: Evidence | None = None
    rights: DataRights

    @model_validator(mode='after')
    def coherent(self):
        if self.coverage_from is not None and self.coverage_until is not None and self.coverage_from >= self.coverage_until:
            raise ValueError('empty_provider_coverage')
        if self.rights.provider != self.provider or self.rights.dataset_version != self.dataset_version:
            raise ValueError('capability_rights_mismatch')
        if self.status == 'supported' and (self.evidence is None or self.evidence.source != self.provider or
                                          self.evidence.dataset_version != self.dataset_version):
            raise ValueError('supported_capability_requires_matching_evidence')
        return self

    def supports(self, request: RequestParameters, instrument_kind: str, at: datetime) -> bool:
        return (self.status == 'supported' and self.evidence.eligible(at) and
                instrument_kind in self.instrument_kinds and request.interval in self.intervals and
                request.adjustment in self.adjustments and request.limit <= self.max_records and
                (request.window.last_date - request.window.first_date).days + 1 <= self.max_calendar_days and
                (self.coverage_from is None or request.window.first_date >= utc(self.coverage_from).date()) and
                (self.coverage_until is None or request.window.last_date < utc(self.coverage_until).date()))


class ProviderReceipt(MarketContract):
    provider: Id
    feed: Id
    dataset: Id
    dataset_version: Id
    request_ref: Id
    parameters: RequestParameters
    requested_at: Time
    responded_at: Time
    evidence: Evidence  # availability, ingestion and exact payload hash
    payload_ref: str = Field(pattern=r'^sha256:[a-f0-9]{64}$')
    run_ref: Id | None = None  # future handoff; no job lifecycle is implied

    @model_validator(mode='after')
    def chronology(self):
        if not self.requested_at <= self.responded_at <= self.evidence.ingested_at:
            raise ValueError('invalid_receipt_chronology')
        if self.evidence.available_at is not None and self.evidence.available_at > self.responded_at:
            raise ValueError('availability_after_response')
        if (self.evidence.source, self.evidence.dataset_version) != (self.provider, self.dataset_version):
            raise ValueError('receipt_evidence_mismatch')
        # Evidence source_ref is restricted here even though the shared type permits URLs.
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_-]{0,99}', self.evidence.source_ref):
            raise ValueError('source_ref_must_be_sanitized_opaque_reference')
        if self.payload_ref != 'sha256:' + self.evidence.payload_sha256:
            raise ValueError('payload_reference_checksum_mismatch')
        return self

    def verify_payload(self, payload: bytes) -> None:
        if payload_digest(payload) != self.evidence.payload_sha256:
            raise ValueError('payload_checksum_mismatch')


PageOutcome = Literal['complete', 'partial', 'unsupported', 'quarantined', 'retryable']
FailureCode = Literal['unsupported_instrument', 'unsupported_dates', 'unsupported_adjustment',
                      'invalid_payload', 'rate_limited', 'temporarily_unavailable']


class ProviderPage(MarketContract):
    receipt: ProviderReceipt
    outcome: PageOutcome
    observation_checksums: tuple[Sha256, ...] = ()
    next_cursor_ref: Id | None = None
    failure_code: FailureCode | None = None

    @model_validator(mode='after')
    def envelope(self):
        if len(set(self.observation_checksums)) != len(self.observation_checksums):
            raise ValueError('duplicate_page_observation')
        if len(self.observation_checksums) > self.receipt.parameters.limit:
            raise ValueError('page_exceeds_request_limit')
        if self.next_cursor_ref is not None and self.next_cursor_ref == self.receipt.parameters.cursor_ref:
            raise ValueError('repeated_cursor')
        permitted = {'unsupported': {'unsupported_instrument', 'unsupported_dates', 'unsupported_adjustment'},
                     'quarantined': {'invalid_payload'}, 'retryable': {'rate_limited', 'temporarily_unavailable'}}
        if self.outcome in permitted:
            if self.failure_code not in permitted[self.outcome] or self.observation_checksums or self.next_cursor_ref:
                raise ValueError('invalid_failure_envelope')
        elif self.failure_code is not None:
            raise ValueError('success_page_with_failure')
        if self.outcome == 'complete' and self.next_cursor_ref is not None:
            raise ValueError('complete_page_with_cursor')
        # Partial without a cursor means known gaps, not automatically resumable.
        return self


def validate_page_sequence(pages: tuple[ProviderPage, ...]) -> tuple[str, ...]:
    """Validate an already supplied chain; no fetching, retries or checkpoint writes."""
    if not pages:
        return ()
    seen_cursors, checksums = set(), []
    first = pages[0].receipt
    if first.parameters.cursor_ref is not None:
        raise ValueError('chain_must_start_without_cursor')
    base = first.parameters.model_dump(exclude={'cursor_ref'})
    for index, page in enumerate(pages):
        receipt = page.receipt
        if (receipt.provider, receipt.feed, receipt.dataset, receipt.dataset_version) != (first.provider, first.feed, first.dataset, first.dataset_version) or receipt.parameters.model_dump(exclude={'cursor_ref'}) != base:
            raise ValueError('page_request_mismatch')
        cursor = receipt.parameters.cursor_ref
        if cursor in seen_cursors:
            raise ValueError('cursor_cycle')
        seen_cursors.add(cursor)
        if index:
            previous = pages[index - 1]
            if previous.outcome != 'partial' or previous.next_cursor_ref is None or cursor != previous.next_cursor_ref:
                raise ValueError('broken_page_chain')
            if receipt.requested_at < previous.receipt.responded_at:
                raise ValueError('page_chronology')
        checksums.extend(page.observation_checksums)
    if pages[-1].next_cursor_ref in seen_cursors and pages[-1].next_cursor_ref is not None:
        raise ValueError('cursor_cycle')
    return tuple(dict.fromkeys(checksums))
