"""Pure, bounded synthetic submissions metadata adapter; no I/O or implicit clock."""
from __future__ import annotations

from datetime import date
import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .contracts import Accession, Contract, Envelope, Provenance, Sha256, UtcTime, cik, explicit_time, payload_digest
from .contracts import UniverseManifest
from .offline import Checkpoint, Outcome, classify, eligible_records, universe_digest

VERSION = 'submissions-subset-v1'
FIELDS = ('accessionNumber', 'form', 'filingDate', 'reportDate', 'acceptanceDateTime', 'primaryDocument')
FORMS = {'10-K', '10-Q', '8-K', '10-K/A', '10-Q/A', '8-K/A'}


class NormalizationError(Exception):
    """Only fixed, non-source-derived codes cross the adapter boundary."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Limits(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    max_input_bytes: int = Field(default=1048576, ge=1, le=1048576)
    max_rows: int = Field(default=1000, ge=1, le=1000)
    max_string_bytes: int = Field(default=65536, ge=1, le=65536)
    max_depth: int = Field(default=64, ge=1, le=64)
    max_result_bytes: int = Field(default=4194304, ge=1, le=4194304)


class Availability(Contract):
    available_at: UtcTime
    basis: Literal['sec_publication', 'observed_public_access']
    evidence: str = Field(min_length=1, max_length=1000)
    _time = field_validator('available_at', mode='before')(explicit_time)


class Context(Contract):
    adapter_version: Literal['submissions-subset-v1'] = VERSION
    ingested_at: UtcTime
    availability: dict[Accession, Availability] = Field(default_factory=dict, max_length=1000)
    amendments: dict[Accession, Accession] = Field(default_factory=dict, max_length=1000)
    _time = field_validator('ingested_at', mode='before')(explicit_time)


class Row(Contract):
    line: int = Field(ge=0, le=999)
    envelope: Envelope
    pointers: tuple[str, ...] = Field(min_length=7, max_length=7)


class Result(Contract):
    binding_sha256: Sha256
    raw_sha256: Sha256
    context_sha256: Sha256
    total_rows: int = Field(ge=0, le=1000)
    checkpoint: Checkpoint
    rows: tuple[Row, ...] = Field(max_length=1000)
    events: tuple[Outcome, ...] = Field(max_length=1000)

    def public_summary(self):
        # No context, source IDs, pointers or private error detail in this view.
        return dict(total_rows=self.total_rows, processed_rows=len(self.events),
                    remaining_rows=self.total_rows - len(self.events),
                    committed_rows=self.checkpoint.next_line,
                    counts={s: sum(e.status == s for e in self.events)
                            for s in ('accepted','duplicate','quarantined','retryable')})


def _fail(code):
    raise NormalizationError(code)


def _precheck(payload, limits):
    if not isinstance(payload, bytes): _fail('invalid_input')
    if len(payload) > limits.max_input_bytes: _fail('limit_input_bytes')
    # Bound nesting and lexical strings before allocating a decoded object graph.
    depth, quoted, escaped, length = 0, False, False, 0
    for byte in payload:
        if quoted:
            if not escaped and byte == 34:
                quoted = False
            else:
                length += 1
                if length > 6 * limits.max_string_bytes: _fail('limit_string_bytes')
                if escaped: escaped = False
                elif byte == 92: escaped = True
        elif byte == 34:
            quoted, length = True, 0
        elif byte in (123,91):
            depth += 1
            if depth > limits.max_depth: _fail('limit_depth')
        elif byte in (125,93):
            depth -= 1
            if depth < 0: _fail('invalid_json')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: _fail('duplicate_json_key')
        result[key] = value
    return result


def _float(value):
    number = float(value)
    if not math.isfinite(number): _fail('nonfinite_json_number')
    return number


def _decode(payload, limits):
    _precheck(payload, limits)
    try:
        text = payload.decode('utf-8')
    except UnicodeDecodeError:
        raise NormalizationError('invalid_utf8') from None
    try:
        data = json.loads(text, object_pairs_hook=_object, parse_float=_float,
                          parse_constant=lambda _: _fail('nonfinite_json_number'))
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                if len(item.encode('utf-8')) > limits.max_string_bytes: _fail('limit_string_bytes')
            elif isinstance(item, dict): stack.extend(item.keys()); stack.extend(item.values())
            elif isinstance(item, list): stack.extend(item)
        return data
    except (ValueError, TypeError, RecursionError, UnicodeEncodeError):
        raise NormalizationError('invalid_json') from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _date(value, optional=False):
    if optional and value == '': return None
    if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
        raise ValueError('invalid date')
    return value


def _normalize(payload, context, universe, limits, previous):
    limits = Limits.model_validate(limits or {})
    # Context dictionaries are bounded before Pydantic copies/validates their entries.
    raw_context = context.model_dump(mode='json') if isinstance(context, Context) else context
    if not isinstance(raw_context, dict): _fail('invalid_context')
    for key in ('availability','amendments'):
        values = raw_context.get(key,{})
        if not isinstance(values, dict) or len(values) > limits.max_rows: _fail('limit_context')
    context = Context.model_validate(raw_context)
    universe = UniverseManifest.model_validate(universe)
    data = _decode(payload, limits)
    if not isinstance(data, dict) or not isinstance(data.get('filings'), dict): _fail('invalid_schema')
    issuer = data.get('cik')
    if isinstance(issuer, bool) or not isinstance(issuer,(str,int)): _fail('invalid_issuer')
    try: issuer = cik(str(issuer))
    except ValueError: raise NormalizationError('invalid_issuer') from None
    recent = data['filings'].get('recent')
    if not isinstance(recent,dict) or any(not isinstance(recent.get(f),list) for f in FIELDS): _fail('invalid_schema')
    count = len(recent[FIELDS[0]])
    if count > limits.max_rows: _fail('limit_rows')
    if any(len(recent[f]) != count for f in FIELDS): _fail('misaligned_arrays')
    raw_sha = payload_digest(payload)
    context_sha = payload_digest(_canonical(context.model_dump(mode='json')))
    universe_sha = universe_digest(universe)
    binding = payload_digest(_canonical(dict(version=VERSION, raw=raw_sha, context=context_sha,
                                            universe=universe_sha, limits=limits.model_dump())))
    if previous is not None:
        # Recompute rather than trusting prior result data as acceptance authority.
        previous = Result.model_validate(previous)
        if previous.binding_sha256 != binding: _fail('replay_context_mismatch')
    known, records, outcomes, events, rows = {}, [], [], [], []
    for index in range(count):
        values = {f: recent[f][index] for f in FIELDS}
        envelope = None
        try:
            if any(not isinstance(v,str) for v in values.values()): raise ValueError('invalid scalar')
            if not re.fullmatch(r'[0-9]{10}-[0-9]{2}-[0-9]{6}', values['accessionNumber']):
                raise ValueError('invalid accession')
            if values['form'] not in FORMS:
                event = Outcome(line=index,status='quarantined',reason='unsupported_form')
            elif values['form'].endswith('/A') and values['accessionNumber'] not in context.amendments:
                event = Outcome(line=index,status='retryable',reason='missing_amendment_link')
            else:
                accession, document = values['accessionNumber'], values['primaryDocument']
                availability = context.availability.get(accession)
                provenance = Provenance(payload_ref='sha256:'+raw_sha, payload_sha256=raw_sha,
                    ingested_at=context.ingested_at, available_at=availability.available_at if availability else None,
                    availability_basis=availability.basis if availability else None,
                    availability_evidence=availability.evidence if availability else None)
                envelope = Envelope(filing=dict(accession=accession,cik=issuer,form=values['form'],
                    filing_date=_date(values['filingDate']),report_period=_date(values['reportDate'],True),
                    accepted_at=values['acceptanceDateTime'] or None,document_name=document,
                    source_url=f'https://www.sec.gov/Archives/edgar/data/{int(issuer)}/{accession.replace("-","")}/{document}',
                    amends_accession=context.amendments.get(accession),provenance=provenance))
                event = classify(envelope,raw_sha,universe,known,index)
        except (ValidationError,ValueError,TypeError,OverflowError):
            event = Outcome(line=index,status='quarantined',reason='invalid_contract')
        if envelope is not None:
            rows.append(Row(line=index,envelope=envelope,
                pointers=('/cik',)+tuple(f'/filings/recent/{f}/{index}' for f in FIELDS)))
        events.append(event)
        if event.status == 'retryable': break
        outcomes.append(event)
        if event.status == 'accepted':
            records.append(envelope); known[envelope.filing.source_key] = envelope
    checkpoint = Checkpoint(source_sha256=raw_sha,universe_sha256=universe_sha,next_line=len(outcomes),
                            records=tuple(records),outcomes=tuple(outcomes))
    result = Result(binding_sha256=binding,raw_sha256=raw_sha,context_sha256=context_sha,total_rows=count,
                    checkpoint=checkpoint,rows=tuple(rows),events=tuple(events))
    if len(result.model_dump_json().encode()) > limits.max_result_bytes: _fail('limit_result_bytes')
    return result


def normalize(payload: bytes, context: Context | dict, universe: UniverseManifest,
              *, limits: Limits | dict | None = None, previous: Result | None = None) -> Result:
    """Normalize supplied bytes only. `previous` binds replay, never authorizes state."""
    try:
        return _normalize(payload,context,universe,limits,previous)
    except (ValidationError,ValueError,TypeError,OverflowError,RecursionError):
        raise NormalizationError('invalid_context_or_contract') from None


def eligible(result: Result, cutoff, *, require_local_receipt=False):
    try:
        cutoff = explicit_time(cutoff)
    except (ValueError,TypeError,OverflowError):
        raise NormalizationError('invalid_cutoff') from None
    if cutoff is None: raise NormalizationError('invalid_cutoff')
    return tuple(r for r in eligible_records(result.checkpoint,cutoff)
                 if not require_local_receipt or r.filing.provenance.ingested_at <= cutoff)
