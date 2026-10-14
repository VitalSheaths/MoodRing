"""Bounded fixture runner with explicit, caller-persisted checkpoints.

This is a contract exerciser, not durable production ingestion. No network, SQL,
implicit clock or mutable global state is involved.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from ingestion.sec.contracts import Contract, Envelope, Sha256, UniverseManifest, payload_digest

MAX_RECORDS = 1000
MAX_LINE_BYTES = 65536


def fingerprint(envelope: Envelope) -> str:
    # Retrieval time/location can change without changing source evidence.
    data = envelope.model_dump(mode='json', exclude={'filing': {'provenance': {'ingested_at', 'payload_ref'}}})
    return payload_digest(json.dumps(data, sort_keys=True, separators=(',', ':')).encode())


class Outcome(Contract):
    line: int = Field(ge=0)
    status: Literal['accepted', 'duplicate', 'quarantined', 'retryable']
    reason: str
    source_key: str | None = None


class Checkpoint(Contract):
    source_sha256: Sha256
    universe_sha256: Sha256
    next_line: int = Field(default=0, ge=0, le=MAX_RECORDS)
    records: tuple[Envelope, ...] = Field(default=(), max_length=MAX_RECORDS)
    outcomes: tuple[Outcome, ...] = Field(default=(), max_length=MAX_RECORDS)

    @model_validator(mode='after')
    def coherent(self):
        # Completed lines are contiguous; a retryable line is never committed.
        if len(self.outcomes) != self.next_line or any(o.line != i or o.status == 'retryable' for i, o in enumerate(self.outcomes)):
            raise ValueError('Checkpoint outcomes must cover exactly the committed prefix')
        keys = [r.filing.source_key for r in self.records]
        accepted = [o.source_key for o in self.outcomes if o.status == 'accepted']
        if len(set(keys)) != len(keys) or keys != accepted:
            raise ValueError('Checkpoint records must match accepted source identities in order')
        seen = {}
        for record in self.records:
            filing = record.filing
            if filing.provenance.available_at is None:
                raise ValueError('Accepted checkpoint records need availability')
            if filing.amends_accession:
                base = seen.get(f'sec_edgar:{filing.amends_accession}')
                if base is None or base.cik != filing.cik or base.form.removesuffix('/A') != filing.form.removesuffix('/A'):
                    raise ValueError('Checkpoint amendments need a matching earlier base')
                if base.report_period is not None and filing.report_period is not None and base.report_period != filing.report_period:
                    raise ValueError('Amendment report period differs from its base')
                if filing.provenance.available_at < base.provenance.available_at:
                    raise ValueError('Amendment availability cannot precede its base')
            seen[filing.source_key] = filing
        return self


def file_digest(path: Path) -> str:
    if path.stat().st_size > MAX_RECORDS * MAX_LINE_BYTES:
        raise ValueError('Fixture file exceeds bounded input limit')
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            result.update(chunk)
    return result.hexdigest()


def universe_digest(universe: UniverseManifest) -> str:
    return payload_digest(universe.model_dump_json().encode())


def run_batch(path: Path, universe: UniverseManifest, checkpoint: Checkpoint | None = None,
              *, batch_size: int = 10) -> tuple[Checkpoint, tuple[Outcome, ...]]:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 100:
        raise ValueError('batch_size must be an integer between 1 and 100')
    path = Path(path)
    digest = file_digest(path)
    u_digest = universe_digest(universe)
    state = checkpoint or Checkpoint(source_sha256=digest, universe_sha256=u_digest)
    if state.source_sha256 != digest or state.universe_sha256 != u_digest:
        raise ValueError('Checkpoint belongs to different source bytes or universe')
    records, outcomes, events = list(state.records), list(state.outcomes), []
    known = {r.filing.source_key: r for r in records}
    # Validate resumed membership too; a checkpoint is structured state, not a signed artifact.
    if any(not universe.contains(r.filing.cik, r.filing.provenance.available_at) for r in records):
        raise ValueError('Checkpoint contains out-of-universe records')
    with path.open('rb') as stream:
        index = 0
        while True:
            if index >= state.next_line and len(events) >= batch_size:
                break
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                if index < state.next_line:
                    raise ValueError('Checkpoint cursor exceeds source length')
                break
            if len(raw) > MAX_LINE_BYTES:
                raise ValueError('Fixture line exceeds bounded input limit')
            if index < state.next_line:
                index += 1
                continue
            if index >= MAX_RECORDS:
                raise ValueError('Offline fixture record limit exceeded')
            try:
                wire = json.loads(raw.decode('utf-8'))
                if not isinstance(wire, dict) or set(wire) != {'envelope', 'payload_text'} or not isinstance(wire['payload_text'], str):
                    raise ValueError('Invalid fixture wrapper')
                envelope = Envelope.model_validate(wire['envelope'])
                filing = envelope.filing
                key = filing.source_key
                if payload_digest(wire['payload_text'].encode()) != filing.provenance.payload_sha256:
                    event = Outcome(line=index, status='quarantined', reason='payload_hash_mismatch', source_key=key)
                elif key in known:
                    duplicate = fingerprint(envelope) == fingerprint(known[key])
                    event = Outcome(line=index, status='duplicate' if duplicate else 'quarantined',
                                    reason='same_source_evidence' if duplicate else 'conflicting_source_identity', source_key=key)
                elif filing.provenance.available_at is None:
                    event = Outcome(line=index, status='quarantined', reason='unknown_availability', source_key=key)
                elif not universe.contains(filing.cik, filing.provenance.available_at):
                    event = Outcome(line=index, status='quarantined', reason='outside_universe', source_key=key)
                elif filing.amends_accession and f'sec_edgar:{filing.amends_accession}' not in known:
                    event = Outcome(line=index, status='retryable', reason='missing_amendment_base', source_key=key)
                else:
                    base = known.get(f'sec_edgar:{filing.amends_accession}')
                    if base and (base.filing.cik != filing.cik or base.filing.form.removesuffix('/A') != filing.form.removesuffix('/A')
                                 or (base.filing.report_period is not None and filing.report_period is not None
                                     and base.filing.report_period != filing.report_period)
                                 or filing.provenance.available_at < base.filing.provenance.available_at):
                        event = Outcome(line=index, status='quarantined', reason='conflicting_amendment_base', source_key=key)
                    else:
                        event = Outcome(line=index, status='accepted', reason='validated_source_evidence', source_key=key)
                        records.append(envelope)
                        known[key] = envelope
            except (ValidationError, ValueError, TypeError, UnicodeDecodeError):
                # No raw payload or provider exception data in public validation outcomes.
                event = Outcome(line=index, status='quarantined', reason='invalid_contract')
            events.append(event)
            if event.status == 'retryable':
                break
            outcomes.append(event)
            index += 1
    if file_digest(path) != digest:
        raise ValueError('Source bytes changed during the batch')
    return Checkpoint(source_sha256=digest, universe_sha256=u_digest, next_line=len(outcomes),
                      records=tuple(records), outcomes=tuple(outcomes)), tuple(events)


def eligible_records(checkpoint: Checkpoint, cutoff) -> tuple[Envelope, ...]:
    """Return all eligible revisions, retaining originals. No implicit replacement."""
    return tuple(r for r in checkpoint.records if r.filing.eligible(cutoff))
