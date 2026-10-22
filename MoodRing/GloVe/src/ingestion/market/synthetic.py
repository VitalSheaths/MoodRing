"""Bounded supplied synthetic pages; no filesystem, HTTP, clock or scheduler."""
import json
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ingestion.jobs.contracts import Manifest, Series, configuration_hash
from ingestion.jobs.offline import SuppliedPage
from ingestion.sec.contracts import payload_digest
from ingestion.universe.identity import Id, SymbolMapping, content_hash
from ingestion.universe.snapshot import UniverseSnapshot
from .bars import Adjustment, DailyObservation
from .providers import ProviderCapability, ProviderPage, ProviderReceipt, PageOutcome, FailureCode, RequestParameters
from .sessions import MarketContract


class AdapterError(Exception):
    def __init__(self, code): self.code=code; super().__init__(code)


class AdapterLimits(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True,strict=True)
    max_series: int = Field(default=110,ge=1,le=110)
    max_days: int = Field(default=31,ge=1,le=31)
    max_pages: int = Field(default=4,ge=1,le=4)
    max_records_page: int = Field(default=32,ge=1,le=32)
    max_records: int = Field(default=1000,ge=1,le=1000)
    max_payload_bytes: int = Field(default=262144,ge=1,le=262144)
    max_bytes: int = Field(default=8388608,ge=1,le=8388608)


class SourcePage(MarketContract):
    receipt: ProviderReceipt
    payload: bytes = Field(strict=True,max_length=262144)
    adjustment: Adjustment
    outcome: PageOutcome = 'complete'
    next_cursor_ref: Id | None = None
    failure_code: FailureCode | None = None
    retry_index: int = Field(default=0,strict=True,ge=0,le=1)

    @property
    def source_hash(self):
        return content_hash(self.model_dump(exclude={'payload'})|{'payload_hex':self.payload.hex()})


def _pairs(pairs):
    result = {}
    for key,value in pairs:
        if key in result: raise AdapterError('invalid_payload')
        result[key] = value
    return result


def _decode(payload,limits):
    # Pre-decode depth bound includes ignored source bytes; no recursive JSON allocation first.
    depth,quoted,escaped = 0,False,False
    for byte in payload:
        if quoted:
            if escaped: escaped=False
            elif byte == 92: escaped=True
            elif byte == 34: quoted=False
        elif byte == 34: quoted=True
        elif byte in (123,91):
            depth+=1
            if depth>16: raise AdapterError('invalid_payload')
        elif byte in (125,93): depth-=1
    def invalid(_): raise AdapterError('invalid_payload')
    try:
        data=json.loads(payload.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=invalid,
                        parse_float=invalid)
        if not isinstance(data,dict) or set(data) != {'data_kind','records'} or data['data_kind'] != 'synthetic' or not isinstance(data['records'],list):
            raise AdapterError('invalid_payload')
        if len(data['records'])>limits.max_records_page: raise AdapterError('limit_records_page')
        return data['records']
    except (ValueError,TypeError,RecursionError,UnicodeError):
        raise AdapterError('invalid_payload') from None


class FixtureAdapter:
    def __init__(self,snapshot,series,capability,pages,*,index_mappings=(),limits=None):
        try: self._init(snapshot,series,capability,pages,index_mappings,limits)
        except (ValidationError,ValueError,TypeError,KeyError,AttributeError):
            raise AdapterError('invalid_source_context') from None

    def _init(self,snapshot,series,capability,pages,index_mappings,limits):
        limits=AdapterLimits.model_validate(limits or {})
        if not isinstance(series,(tuple,list)) or not 1<=len(series)<=limits.max_series: raise AdapterError('limit_series')
        if not isinstance(pages,(tuple,list)) or len(pages)>limits.max_series*limits.max_pages*2: raise AdapterError('limit_pages')
        if len(index_mappings)>limits.max_series: raise AdapterError('limit_series')
        snapshot=UniverseSnapshot.model_validate(snapshot)
        if snapshot.data_kind!='synthetic' or snapshot.dataset_mode!='current_snapshot' or snapshot.status!='complete':
            raise AdapterError('synthetic_complete_universe_required')
        series=tuple(Series.model_validate(s) for s in series)
        if len({s.key for s in series})!=len(series): raise AdapterError('duplicate_series')
        capability=ProviderCapability.model_validate(capability)
        # This adapter is a fixture protocol, not a vehicle for credentialed vendor access.
        if capability.provider!='fake' or not capability.dataset_version.startswith('synthetic'):
            raise AdapterError('synthetic_provider_required')
        selected={m.instrument_id for m in snapshot.members}
        instruments={i.instrument_id:i for i in snapshot.catalog.instruments}
        for s in series:
            if s.instrument.kind=='common_stock' and (s.key not in selected or instruments.get(s.key)!=s.instrument):
                raise AdapterError('series_not_in_universe')
            if (s.request.window.last_date-s.request.window.first_date).days+1>limits.max_days: raise AdapterError('limit_days')
            if s.request.limit>limits.max_records_page: raise AdapterError('limit_records_page')
            if s.reference and (s.reference.rights.provider,s.reference.rights.dataset_version)!=(capability.provider,capability.dataset_version):
                raise AdapterError('reference_provider_mismatch')
        mappings=tuple(SymbolMapping.model_validate(m) for m in index_mappings)
        if any(m.instrument_id not in {s.key for s in series if s.instrument.kind=='index'} or m.provider!=capability.provider for m in mappings):
            raise AdapterError('invalid_index_mapping')
        if len({m.mapping_id for m in mappings})!=len(mappings): raise AdapterError('duplicate_mapping')
        source_pages=tuple(SourcePage.model_validate(p) for p in pages)
        total=0; row_count=0; bykey={s.key:s for s in series}; lookup={}; distinct={s.key:set() for s in series}
        for page in source_pages:
            request=page.receipt.parameters; spec=bykey.get(request.instrument_id)
            if spec is None or request.model_dump(exclude={'cursor_ref'})!=spec.request.model_dump(exclude={'cursor_ref'}):
                raise AdapterError('request_context_mismatch')
            if (page.receipt.provider,page.receipt.feed,page.receipt.dataset,page.receipt.dataset_version)!=(
                    capability.provider,capability.feed,capability.dataset,capability.dataset_version):
                raise AdapterError('provider_context_mismatch')
            if request.adjustment!=page.adjustment.basis: raise AdapterError('adjustment_context_mismatch')
            total+=len(page.payload)
            if len(page.payload)>limits.max_payload_bytes: raise AdapterError('limit_payload_bytes')
            if total>limits.max_bytes: raise AdapterError('limit_bytes')
            try: row_count+=len(_decode(page.payload,limits))
            except AdapterError as exc:
                if exc.code.startswith('limit_'): raise
                # Invalid source evidence remains a bounded quarantine candidate.
            if row_count>limits.max_records: raise AdapterError('limit_records')
            key=(content_hash(request),page.retry_index)
            if key in lookup: raise AdapterError('duplicate_request_response')
            lookup[key]=page; distinct[spec.key].add(content_hash(request))
            if len(distinct[spec.key])>limits.max_pages: raise AdapterError('limit_pages')
        self.snapshot,self.series,self.capability,self.limits=snapshot,series,capability,limits
        self._series=MappingProxyType(bykey); self._pages=MappingProxyType(lookup)
        self._mappings=mappings
        self.source_sha256=content_hash(dict(universe=snapshot.content_hash,series=sorted(series,key=lambda s:s.key),
            capability=capability,limits=limits.model_dump(),mappings=sorted(mappings,key=lambda m:m.mapping_id),
            pages=sorted(p.source_hash for p in source_pages)))

    def _materialize(self,source,spec):
        receipt=source.receipt; request=receipt.parameters
        outcome,failure,cursor=source.outcome,source.failure_code,source.next_cursor_ref
        cap=self.capability
        if not cap.rights.permits('local_research',request.window.cutoff) or not cap.supports(request,spec.instrument.kind,request.window.cutoff):
            outcome,failure,cursor='unsupported',('unsupported_adjustment' if request.adjustment not in cap.adjustments else 'unsupported_instrument'),None
        if spec.reference and (spec.reference.capability!='supported' or spec.reference.evidence is None or
                not spec.reference.evidence.eligible(request.window.cutoff) or
                not spec.reference.rights.permits('local_research',request.window.cutoff)):
            outcome,failure,cursor='unsupported','unsupported_instrument',None
        observations=[]
        try:
            receipt.verify_payload(source.payload)
            values=_decode(source.payload,self.limits)
            if outcome in ('complete','partial'):
                for value in values:
                    if not isinstance(value,dict) or set(value)-{'session_date','open','high','low','close','volume','notional','supersedes'}:
                        raise ValueError('invalid_payload')
                    day=value.get('session_date')
                    session=next((s for s in spec.coverage.calendar.sessions if s.session_date.isoformat()==day),None)
                    if session is None: raise ValueError('unknown_session')
                    if spec.instrument.kind=='common_stock':
                        mapping=self.snapshot.catalog.resolve(spec.key,receipt.provider,session.closes_at)
                        canonical=self.snapshot.catalog.resolve(spec.key,'canonical',session.closes_at)
                        shape='ohlc'
                    else:
                        mapping=next((m for m in self._mappings if m.mapping_id==request.mapping_id and m.instrument_id==spec.key and m.eligible(session.closes_at)),None)
                        canonical=None; shape=spec.reference.shape
                        if mapping is None or spec.reference.provider_mapping_id!=mapping.mapping_id: raise ValueError('unknown_mapping')
                    observation=DailyObservation(instrument=spec.instrument,mapping=mapping,canonical_mapping=canonical,
                        session=session,receipt=receipt,adjustment=source.adjustment,shape=shape,
                        currency=spec.instrument.currency,measure_unit=spec.instrument.measure_unit,
                        **{k:v for k,v in value.items() if k!='session_date'})
                    if not observation.eligible(request.window.cutoff): raise ValueError('ineligible_observation')
                    if spec.reference and (spec.reference.available_from is not None and session.session_date<spec.reference.available_from or
                        spec.reference.available_until is not None and session.session_date>=spec.reference.available_until): raise ValueError('reference_outside_coverage')
                    observations.append(observation)
            if len(observations)>request.limit: raise ValueError('page_limit')
            page=ProviderPage(receipt=receipt,outcome=outcome,failure_code=failure,next_cursor_ref=cursor,
                observation_checksums=tuple(o.semantic_checksum for o in observations))
        except (ValueError,TypeError,AdapterError):
            observations=[]
            page=ProviderPage(receipt=receipt,outcome='quarantined',failure_code='invalid_payload')
        return SuppliedPage(page,source.payload,tuple(observations))

    def fetch(self,request,retry_index):
        try:
            request=RequestParameters.model_validate(request)
            if isinstance(retry_index,bool) or not isinstance(retry_index,int) or not 0<=retry_index<=1:
                raise AdapterError('invalid_retry_index')
            source=self._pages.get((content_hash(request),retry_index))
            if source is None: raise AdapterError('request_not_pinned')
            return self._materialize(source,self._series[request.instrument_id])
        except (ValueError,TypeError,KeyError,AttributeError):
            raise AdapterError('invalid_request') from None

    def manifest(self,job_id,*,limits,allow_partial=False,lease_seconds=3600,backoff_seconds=1):
        config=configuration_hash(limits,allow_partial,lease_seconds,backoff_seconds,'requested_window')
        result=Manifest(job_id=job_id,provider=self.capability.provider,feed=self.capability.feed,
            dataset=self.capability.dataset,dataset_version=self.capability.dataset_version,
            universe_sha256=self.snapshot.content_hash,source_sha256=self.source_sha256,
            config_sha256=config,code_version='synthetic-adapter-v1',series=self.series,limits=limits,
            allow_partial=allow_partial,coverage_basis='requested_window',lease_seconds=lease_seconds,backoff_seconds=backoff_seconds)
        result.verify_universe(self.snapshot)
        return result
