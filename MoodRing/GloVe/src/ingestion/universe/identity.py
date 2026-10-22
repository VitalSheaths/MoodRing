"""One versioned identity catalogue shared by SEC and future market-data adapters."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from ingestion.sec.contracts import Sha256, cik, explicit_time, utc

VERSION = 'universe-contract-v1'
SEC_SIC_URL = 'https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list'
Id = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9:_-]{0,99}$')]
Time = Annotated[AwareDatetime, BeforeValidator(explicit_time)]


def _decimal(value):
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('Use finite Decimal, decimal string or integer; binary floats are not accepted')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid decimal measurement') from exc
    if not result.is_finite():
        raise ValueError('Nonfinite measurement')
    return result


Exact = Annotated[Decimal, BeforeValidator(_decimal), Field(max_digits=40, decimal_places=12)]


def _canonical(value):
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump())
    if isinstance(value, Decimal):
        text = format(value, 'f')
        text = text.rstrip('0').rstrip('.') if '.' in text else text
        return '0' if value == 0 else text
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical(v) for v in value)
    return value


def content_hash(value) -> str:
    return hashlib.sha256(json.dumps(_canonical(value), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False, str_strip_whitespace=True, hide_input_in_errors=True)
    schema_version: Literal['universe-contract-v1'] = VERSION

    @property
    def content_hash(self):
        return content_hash(self)


class Evidence(Contract):
    source: Id
    dataset_version: Id
    source_ref: str = Field(min_length=1, max_length=1000)
    payload_sha256: Sha256
    ingested_at: Time
    available_at: Time | None = None

    @model_validator(mode='after')
    def chronology(self):
        if self.available_at is not None and self.available_at > self.ingested_at:
            raise ValueError('Ingestion cannot precede known source availability')
        return self

    def eligible(self, cutoff: datetime):
        return self.available_at is not None and self.available_at <= utc(cutoff)


class Dated(Contract):
    effective_from: Time
    effective_until: Time | None = None
    evidence: Evidence

    @model_validator(mode='after')
    def interval(self):
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError('Effective intervals must be nonempty and half-open')
        return self

    def eligible(self, cutoff: datetime):
        cutoff = utc(cutoff)
        return (self.effective_from <= cutoff and (self.effective_until is None or cutoff < self.effective_until)
                and self.evidence.eligible(cutoff))


class Issuer(Contract):
    issuer_id: Id
    cik: str
    legal_name: str = Field(min_length=1, max_length=200)
    country: str = Field(pattern=r'^[A-Z]{2}$')
    evidence: Evidence

    _cik = field_validator('cik', mode='before')(cik)


class Instrument(Dated):
    instrument_id: Id
    issuer_id: Id | None = None
    display_name: str = Field(min_length=1, max_length=200)
    kind: Literal['common_stock', 'index']
    country: str = Field(pattern=r'^[A-Z]{2}$')
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    measure_unit: Literal['currency', 'index_points', 'volatility_points']
    status: Literal['active', 'inactive']
    tradable: bool
    administrator: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode='after')
    def security_type(self):
        if self.kind == 'index':
            if self.issuer_id is not None or self.tradable or self.measure_unit == 'currency' or self.administrator is None:
                raise ValueError('Indexes are nontradable references, without a fabricated issuer/CIK')
        elif self.issuer_id is None or self.currency is None or self.measure_unit != 'currency':
            raise ValueError('Common stocks need issuer, currency and currency units')
        if self.status == 'inactive' and self.effective_until is None:
            raise ValueError('Inactive instruments need an effective end; preserve historical lifetime')
        return self


class SymbolMapping(Dated):
    mapping_id: Id
    instrument_id: Id
    provider: Id  # "canonical" is the canonical dated ticker namespace.
    symbol: str = Field(pattern=r'^[A-Za-z0-9.^:/_-]{1,64}$')
    mic: str | None = Field(default=None, pattern=r'^[A-Z0-9]{4}$')


def sic_code(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 4:
        raise ValueError('SIC must be a string of one to four ASCII digits')
    return value.zfill(4)


class SicCode(Contract):
    code: str
    description: str = Field(min_length=1, max_length=250)

    _code = field_validator('code', mode='before')(sic_code)


class SicTaxonomy(Contract):
    taxonomy_version: Id
    source_url: Literal['https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list']
    captured_at: Time
    scope: Literal['subset', 'complete']
    codes: tuple[SicCode, ...] = Field(min_length=1, max_length=10000)
    codes_sha256: Sha256

    @field_validator('codes')
    @classmethod
    def canonical_codes(cls, value):
        if len({c.code for c in value}) != len(value):
            raise ValueError('Duplicate SIC code')
        return tuple(sorted(value, key=lambda c: c.code))

    @model_validator(mode='after')
    def checksum(self):
        if content_hash(self.codes) != self.codes_sha256:
            raise ValueError('SIC subset checksum mismatch')
        return self


class SicAssignment(Dated):
    assignment_id: Id
    issuer_id: Id
    taxonomy_version: Id
    code: str

    _code = field_validator('code', mode='before')(sic_code)


class Successor(Dated):
    predecessor_id: Id
    successor_id: Id
    reason: Literal['merger', 'acquisition', 'reorganization']

    @model_validator(mode='after')
    def distinct(self):
        if self.predecessor_id == self.successor_id:
            raise ValueError('A successor must be a distinct instrument')
        return self


def _no_overlap(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    for group in groups.values():
        ordered = sorted(group, key=lambda row: row.effective_from)
        for left, right in zip(ordered, ordered[1:]):
            if left.effective_until is None or right.effective_from < left.effective_until:
                raise ValueError('Ambiguous overlapping effective intervals')


class IdentityCatalog(Contract):
    catalog_version: Id
    issuers: tuple[Issuer, ...] = Field(max_length=10000)
    instruments: tuple[Instrument, ...] = Field(max_length=20000)
    mappings: tuple[SymbolMapping, ...] = Field(max_length=50000)
    taxonomy: SicTaxonomy
    assignments: tuple[SicAssignment, ...] = Field(max_length=20000)
    successors: tuple[Successor, ...] = Field(default=(), max_length=10000)

    @field_validator('issuers', 'instruments', 'mappings', 'assignments', 'successors')
    @classmethod
    def canonical_sets(cls, value):
        return tuple(sorted(value, key=lambda item: item.content_hash))

    @model_validator(mode='after')
    def references(self):
        issuers = {i.issuer_id: i for i in self.issuers}
        instruments = {i.instrument_id: i for i in self.instruments}
        for rows, field in ((self.issuers, 'issuer_id'), (self.instruments, 'instrument_id'),
                            (self.mappings, 'mapping_id'), (self.assignments, 'assignment_id')):
            if len({getattr(r, field) for r in rows}) != len(rows):
                raise ValueError(f'Duplicate {field}')
        if len({i.cik for i in self.issuers}) != len(self.issuers):
            raise ValueError('A CIK must map to one stable issuer')
        for instrument in self.instruments:
            if instrument.issuer_id is not None and instrument.issuer_id not in issuers:
                raise ValueError('Unknown instrument issuer')
        for mapping in self.mappings:
            instrument = instruments.get(mapping.instrument_id)
            if instrument is None or (instrument.kind == 'common_stock' and mapping.mic is None):
                raise ValueError('Unknown mapping instrument or missing stock MIC')
            if mapping.effective_from < instrument.effective_from or (instrument.effective_until is not None and
                    (mapping.effective_until is None or mapping.effective_until > instrument.effective_until)):
                raise ValueError('Symbol mapping exceeds instrument lifetime')
        _no_overlap(self.mappings, lambda m: (m.provider, m.symbol))
        _no_overlap(self.mappings, lambda m: (m.provider, m.instrument_id))
        _no_overlap(self.assignments, lambda a: a.issuer_id)
        valid_codes = {c.code for c in self.taxonomy.codes}
        for assignment in self.assignments:
            if assignment.issuer_id not in issuers or assignment.taxonomy_version != self.taxonomy.taxonomy_version or assignment.code not in valid_codes:
                raise ValueError('Unknown SIC issuer, taxonomy or code')
        for link in self.successors:
            before, after = instruments.get(link.predecessor_id), instruments.get(link.successor_id)
            if before is None or after is None or before.kind != after.kind:
                raise ValueError('Unknown or mismatched successor instruments')
            if before.effective_until is None or before.effective_until > link.effective_from or after.effective_from > link.effective_from:
                raise ValueError('Successor relationship conflicts with instrument lifetimes')
        _no_overlap(self.successors, lambda link: link.predecessor_id)
        graph = defaultdict(list)
        incoming = {key: 0 for key in instruments}
        for link in self.successors:
            graph[link.predecessor_id].append(link.successor_id)
            incoming[link.successor_id] += 1
        ready = deque(key for key, count in incoming.items() if count == 0)
        visited = 0
        while ready:
            key = ready.popleft()
            visited += 1
            for successor in graph[key]:
                incoming[successor] -= 1
                if incoming[successor] == 0:
                    ready.append(successor)
        if visited != len(instruments):
            raise ValueError('Successor relationships must not form cycles')
        return self

    def issuer_for_cik(self, value: str) -> Issuer:
        normalized = cik(value)
        return next((i for i in self.issuers if i.cik == normalized), None) or _missing('unknown_cik')

    def resolve(self, instrument_id: str, provider: str, cutoff: datetime) -> SymbolMapping:
        instrument = next((i for i in self.instruments if i.instrument_id == instrument_id), None)
        if instrument is None or not instrument.eligible(cutoff):
            raise ValueError('instrument_unavailable_at_cutoff')
        matches = [m for m in self.mappings if m.instrument_id == instrument_id and m.provider == provider and m.eligible(cutoff)]
        if len(matches) != 1:
            raise ValueError('symbol_unavailable_at_cutoff')
        return matches[0]


def _missing(reason):
    raise ValueError(reason)


class DataRights(Contract):
    provider: Id
    dataset_version: Id
    status: Literal['unknown', 'verified', 'denied'] = 'unknown'
    review_ref: str | None = Field(default=None, min_length=1, max_length=1000)
    reviewed_at: Time | None = None
    valid_until: Time | None = None
    local_research: bool = False
    public_display: bool = False
    redistribution: bool = False
    model_use: bool = False
    retention_policy: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def verified_evidence(self):
        if self.status == 'verified' and (self.review_ref is None or self.reviewed_at is None):
            raise ValueError('Verified rights need a review reference and timestamp')
        if self.valid_until is not None and (self.reviewed_at is None or self.valid_until <= self.reviewed_at):
            raise ValueError('Rights expiry must follow review')
        return self

    def permits(self, purpose: Literal['local_research', 'public_display', 'redistribution', 'model_use'], at: datetime) -> bool:
        if purpose not in ('local_research', 'public_display', 'redistribution', 'model_use'):
            return False
        at = utc(at)
        return (self.status == 'verified' and self.reviewed_at <= at and
                (self.valid_until is None or at < self.valid_until) and getattr(self, purpose))
