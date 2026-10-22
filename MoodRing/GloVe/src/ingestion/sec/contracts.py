"""SEC v1 metadata contracts. These describe evidence, not XBRL extraction."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

VERSION = 'sec-contract-v1'
Accession = Annotated[str, Field(pattern=r'^\d{10}-\d{2}-\d{6}$')]
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
UtcTime = AwareDatetime


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('An explicit timezone offset is required')
    return value.astimezone(timezone.utc)


def explicit_time(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime):
        raise ValueError('Use an offset-aware datetime or ISO timestamp, not epoch coercion')
    return utc(value)


def cik(value: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 10:
        raise ValueError('CIK must be a string of one to ten ASCII digits')
    if int(value) == 0:
        raise ValueError('CIK must be positive')
    return value.zfill(10)


def payload_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    schema_version: Literal['sec-contract-v1'] = VERSION


class UniverseMember(Contract):
    ticker: str = Field(pattern=r'^[A-Z][A-Z0-9.-]{0,15}$')
    cik: str
    effective_from: UtcTime
    effective_until: UtcTime | None = None

    _cik = field_validator('cik', mode='before')(cik)
    _utc = field_validator('effective_from', 'effective_until', mode='before')(explicit_time)

    @model_validator(mode='after')
    def interval(self):
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError('Universe intervals must be nonempty and half-open')
        return self

    def active(self, at: datetime) -> bool:
        at = utc(at)
        return self.effective_from <= at and (self.effective_until is None or at < self.effective_until)


class UniverseManifest(Contract):
    universe_version: str = Field(min_length=1, max_length=128)
    members: tuple[UniverseMember, ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode='after')
    def nonoverlapping_tickers(self):
        ordered = sorted(self.members, key=lambda m: (m.ticker, m.effective_from))
        for left, right in zip(ordered, ordered[1:]):
            if left.ticker == right.ticker and (left.effective_until is None or right.effective_from < left.effective_until):
                raise ValueError('Ticker membership intervals overlap')
        return self

    def contains(self, issuer_cik: str, at: datetime) -> bool:
        return any(m.cik == cik(issuer_cik) and m.active(at) for m in self.members)


class Provenance(Contract):
    source: Literal['sec_edgar'] = 'sec_edgar'
    payload_ref: str = Field(min_length=1, max_length=1000)
    payload_sha256: Sha256
    ingested_at: UtcTime
    available_at: UtcTime | None = None
    availability_basis: Literal['sec_publication', 'observed_public_access'] | None = None
    availability_evidence: str | None = Field(default=None, min_length=1, max_length=1000)

    _utc = field_validator('ingested_at', 'available_at', mode='before')(explicit_time)

    @model_validator(mode='after')
    def availability(self):
        fields = (self.available_at, self.availability_basis, self.availability_evidence)
        if any(v is not None for v in fields) and not all(v is not None for v in fields):
            raise ValueError('Availability requires time, basis and evidence together')
        if self.available_at is not None and self.available_at > self.ingested_at:
            raise ValueError('Ingestion cannot precede known source availability')
        return self


class Filing(Contract):
    accession: Accession
    cik: str
    form: Literal['10-K', '10-Q', '8-K', '10-K/A', '10-Q/A', '8-K/A']
    filing_date: date
    report_period: date | None = None
    accepted_at: UtcTime | None = None
    document_name: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$')
    source_url: str
    amends_accession: Accession | None = None
    provenance: Provenance

    _cik = field_validator('cik', mode='before')(cik)
    _utc = field_validator('accepted_at', mode='before')(explicit_time)

    @model_validator(mode='after')
    def metadata(self):
        if '..' in self.document_name:
            raise ValueError('Document name cannot contain traversal components')
        expected = f'https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/{self.accession.replace("-", "")}/{self.document_name}'
        if self.source_url != expected:
            raise ValueError('Source URL must match SEC issuer, accession and document')
        if self.report_period is not None and self.report_period > self.filing_date:
            raise ValueError('Report period cannot follow filing date')
        if self.form.endswith('/A') != (self.amends_accession is not None):
            raise ValueError('Amendments require an explicit base accession; originals cannot amend')
        if self.amends_accession == self.accession:
            raise ValueError('A filing cannot amend itself')
        if self.accepted_at is not None:
            if self.accepted_at > self.provenance.ingested_at:
                raise ValueError('Ingestion cannot precede acceptance')
            if self.provenance.available_at is not None and self.provenance.available_at < self.accepted_at:
                raise ValueError('Availability cannot precede acceptance')
        return self

    @property
    def source_key(self) -> str:
        return f'sec_edgar:{self.accession}'

    def eligible(self, cutoff: datetime) -> bool:
        # Historical research cutoff, not a claim that this system ingested it then.
        cutoff = utc(cutoff)
        return self.provenance.available_at is not None and self.provenance.available_at <= cutoff


class NormalizedFact(Contract):
    accession: Accession
    concept: str = Field(min_length=1, max_length=200)
    value: Decimal | None = None
    missing_reason: str | None = Field(default=None, min_length=1, max_length=200)
    unit: Literal['USD', 'shares', 'USD/shares', 'pure']
    period_start: date | None = None
    period_end: date
    fiscal_year: int = Field(ge=1900, le=2200)
    evidence_pointer: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def fact(self):
        if self.period_start is not None and self.period_start > self.period_end:
            raise ValueError('Fact period start cannot follow its end')
        if (self.value is None) != (self.missing_reason is not None):
            raise ValueError('Missing values require a reason; known values cannot have one')
        return self


class Envelope(Contract):
    filing: Filing
    facts: tuple[NormalizedFact, ...] = ()

    @model_validator(mode='after')
    def fact_provenance(self):
        if any(f.accession != self.filing.accession or f.period_end > self.filing.filing_date for f in self.facts):
            raise ValueError('Facts must belong to this filing and cannot describe a future period')
        return self
