"""Small supplied synthetic universe/OHLC lesson; no network or database writes."""
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path

from ingestion.jobs.offline import FakeStore, run
from ingestion.jobs.contracts import Series
from ingestion.market.synthetic import FixtureAdapter, SourcePage
from ingestion.market.bars import Adjustment
from ingestion.market.indexes import INDEX_SLOTS, IndexReference
from ingestion.market.providers import ProviderCapability, ProviderReceipt, RequestParameters
from ingestion.market.sessions import CoverageReport, DateWindow, SessionCalendar
from ingestion.sec.contracts import payload_digest
from ingestion.universe.identity import IdentityCatalog, SymbolMapping, SicCode, content_hash
from ingestion.universe.ranking import RankingInput, SelectionPolicy
from ingestion.universe.selector import IndustrySignal, select_synthetic

SEED = Path(__file__).with_name('fixtures')/'synthetic_market.json'


@lru_cache(maxsize=1)
def inputs():
    raw=SEED.read_bytes(); seed=json.loads(raw)
    def evidence(source='synthetic'):
        return dict(source=source,dataset_version='synthetic-demo-v1',source_ref='synthetic:demo',
            payload_sha256=payload_digest(raw),available_at='2026-01-01T00:00:00Z',ingested_at='2026-10-19T00:00:00Z')
    def dated():return dict(effective_from='2010-01-01T00:00:00Z',evidence=evidence())
    issuers=[];instruments=[];mappings=[];assignments=[];rows=[];signals=[]
    for group,(sic,count) in enumerate(zip(seed['sic_codes'],seed['candidate_counts'])):
        code=sic['code']
        signals.append(IndustrySignal(sic_code=code,momentum=str(group),volume=str(group+1),
            momentum_sessions=63,volume_sessions=20,asof='2026-10-18T20:00:00Z',evidence=evidence()))
        for index in range(count):
            key=f'g{group:02d}c{index:02d}';issuer='issuer:'+key;instrument='instrument:'+key
            issuers.append(dict(issuer_id=issuer,cik=str(10000+group*100+index),legal_name='Synthetic '+key,country='US',evidence=evidence()))
            instruments.append(dict(instrument_id=instrument,issuer_id=issuer,display_name='Synthetic '+key,
                kind='common_stock',country='US',currency='USD',measure_unit='currency',status='active',tradable=True,**dated()))
            for provider in ('canonical','fake'):
                mappings.append(dict(mapping_id=provider+':'+key,instrument_id=instrument,provider=provider,
                    symbol=key.upper(),mic='XNAS',**dated()))
            assignments.append(dict(assignment_id='sic:'+key,issuer_id=issuer,taxonomy_version='synthetic-sic-v1',code=code,**dated()))
            rows.append(RankingInput(issuer_id=issuer,instrument_id=instrument,assignment_id='sic:'+key,mapping_id='canonical:'+key,
                currency='USD',market_cap=str((group+1)*1000000000+index*10000000),market_cap_basis='issuer_wide',
                market_cap_asof='2026-10-18T20:00:00Z',cap_evidence=evidence(),
                common_earnings_ttm=str((-1 if index%7==0 else 1)*(index+1)*1000000),fundamental_period_end='2026-09-30',
                earnings_evidence=evidence(),dollar_volume=str(100000000+index*100000),liquidity_metric='close_times_volume',
                volume_scope='consolidated',liquidity_provider='fake',liquidity_feed='synthetic-daily',
                liquidity_end='2026-10-18T20:00:00Z',liquidity_sessions=60,liquidity_evidence=evidence(),
                trading_status='trading',last_price='100',price_age_sessions=0,history_sessions=500))
    catalog=IdentityCatalog(catalog_version='synthetic-demo-v1',issuers=issuers,instruments=instruments,mappings=mappings,
        taxonomy=dict(taxonomy_version='synthetic-sic-v1',source_url=seed['sic_source'],captured_at=seed['sic_captured_at'],
            scope='subset',codes=tuple(SicCode(**s) for s in seed['sic_codes']),codes_sha256=content_hash(tuple(sorted((SicCode(**s) for s in seed['sic_codes']),key=lambda s:s.code)))),assignments=assignments,successors=[])
    return catalog,SelectionPolicy.model_validate(seed['policy']),tuple(rows),tuple(signals)


@lru_cache(maxsize=1)
def selection():
    catalog,policy,rows,signals=inputs()
    return select_synthetic(catalog,policy,rows,signals,asof='2026-10-18T20:00:00Z',
        published_at='2026-10-19T00:00:00Z',effective_at='2026-10-19T13:30:00Z')


def adapter(*,count=100,days=2,adjustment='raw',include_indexes=True):
    snapshot=selection().snapshot
    dates=['2026-10-19','2026-10-20'][:days]
    calendar=SessionCalendar(calendar_id='synthetic-session',calendar_version='synthetic-v1',timezone='America/New_York',
        mode='regular',valid_from=dates[0],valid_until=dates[-1],non_sessions=[],sessions=[dict(calendar_id='synthetic-session',
        calendar_version='synthetic-v1',timezone='America/New_York',session_date=d,mode='regular',
        opens_at=d+'T13:30:00Z',closes_at=d+'T20:00:00Z') for d in dates])
    window=DateWindow(start=dates[0],end=dates[-1],start_inclusive=True,end_inclusive=True,
        calendar_id=calendar.calendar_id,calendar_version=calendar.calendar_version,cutoff='2026-10-21T01:00:00Z',leap_day_policy='clamp_feb28')
    ev=dict(source='fake',dataset_version='synthetic-demo-v1',source_ref='synthetic:demo',payload_sha256=payload_digest(SEED.read_bytes()),
        available_at='2026-01-01T00:00:00Z',ingested_at='2026-10-21T00:00:00Z')
    rights=dict(provider='fake',dataset_version='synthetic-demo-v1',status='verified',review_ref='synthetic:fixture-only',
        reviewed_at='2026-01-01T00:00:00Z',local_research=True,public_display=False,redistribution=False,model_use=False,
        retention_policy='Synthetic bounded test evidence only')
    capability=ProviderCapability(provider='fake',feed='synthetic-daily',dataset='synthetic-bars',dataset_version='synthetic-demo-v1',
        status='supported',instrument_kinds={'common_stock','index'},intervals={'1d'},adjustments={'raw','split_adjusted'},
        max_records=32,max_calendar_days=31,evidence=ev,rights=rights)
    instruments={i.instrument_id:i for i in snapshot.catalog.instruments}
    objects=[(instruments[m.instrument_id],None) for m in snapshot.members[:count]];index_mappings=[]
    if include_indexes:
        template=objects[0][0].model_dump()
        for slot in ('sp500','russell2000'):
            name,admin,variant,unit=INDEX_SLOTS[slot]
            instrument=type(objects[0][0]).model_validate(template|dict(instrument_id='index:'+slot,issuer_id=None,
                kind='index',display_name='Synthetic '+name,currency=None,measure_unit=unit,tradable=False,administrator=admin))
            mapping=SymbolMapping(mapping_id='fake:'+slot,instrument_id=instrument.instrument_id,provider='fake',
                symbol='SYNTH_'+slot.upper(),mic=None,effective_from='2010-01-01T00:00:00Z',evidence=ev)
            index_mappings.append(mapping)
            supported=slot=='sp500'
            ref=IndexReference(slot=slot,instrument=instrument,variant=variant,calendar_id=calendar.calendar_id,
                calendar_version=calendar.calendar_version,timezone=calendar.timezone,available_from=dates[0],
                capability='supported' if supported else 'unsupported',shape='close_only' if supported else 'unknown',
                provider_mapping_id=mapping.mapping_id if supported else None,evidence=ev if supported else None,rights=rights)
            objects.append((instrument,ref))
    series=[];pages=[]
    basis=Adjustment(basis=adjustment,**({} if adjustment=='raw' else dict(factor_ref='synthetic:factor',factor_sha256='a'*64,vintage_at='2026-10-20T21:00:00Z')))
    for instrument,reference in objects:
        mapping=next(m for m in index_mappings if m.instrument_id==instrument.instrument_id) if reference else snapshot.catalog.resolve(instrument.instrument_id,'fake',window.cutoff)
        request=RequestParameters(instrument_id=instrument.instrument_id,mapping_id=mapping.mapping_id,symbol=mapping.symbol,
            adjustment=adjustment,window=window,limit=1)
        report=CoverageReport(instrument_id=instrument.instrument_id,window=window,calendar=calendar,
            active_from='2010-01-01',days=[dict(session_date=d,outcome='provider_gap') for d in dates])
        series.append(Series(instrument=instrument,request=request,coverage=report,required=not reference or reference.slot=='sp500',reference=reference))
        for i,day in enumerate(dates):
            parameters=RequestParameters.model_validate(request.model_dump()|{'cursor_ref':None if i==0 else 'cursor:second'})
            record=dict(session_date=day,close='102')
            if reference is None: record.update(open='100',high='105',low='99',volume='1000')
            raw=json.dumps(dict(data_kind='synthetic',records=[record]),sort_keys=True,separators=(',',':')).encode()
            evidence=ev|dict(payload_sha256=payload_digest(raw),available_at=day+'T20:00:00Z')
            receipt=ProviderReceipt(provider='fake',feed='synthetic-daily',dataset='synthetic-bars',dataset_version='synthetic-demo-v1',
                request_ref='synthetic:page',parameters=parameters,requested_at='2026-10-21T00:00:00Z',responded_at='2026-10-21T00:00:00Z',
                evidence=evidence,payload_ref='sha256:'+payload_digest(raw))
            pages.append(SourcePage(receipt=receipt,payload=raw,adjustment=basis,outcome='complete' if i==len(dates)-1 else 'partial',
                next_cursor_ref=None if i==len(dates)-1 else 'cursor:second'))
    return FixtureAdapter(snapshot,series,capability,pages,index_mappings=index_mappings)


JOB_LIMITS=dict(max_instruments=110,max_date_span=31,max_pages_per_instrument=4,max_records_per_page=32,
    max_total_records=1000,max_attempts=3,max_retries_per_page=1,max_continuation_depth=4,max_checkpoint_bytes=8388608,max_steps=500)


def main():
    picked=selection(); provider=adapter(count=3)
    manifest=provider.manifest('synthetic:demo',limits=JOB_LIMITS,allow_partial=True)
    store=FakeStore(manifest,lambda:datetime(2026,10,21,2,tzinfo=timezone.utc))
    result=run(store,provider,store.claim('synthetic:worker'))
    print('Synthetic companies selected:',len(picked.snapshot.members))
    print('SIC quotas:',{r.sic_code:r.count for r in picked.snapshot.industry_counts})
    print('Synthetic job (3 selected equities + 2 reference slots):',result['state'],result['counters'])
    assert len(picked.snapshot.members)==100 and result['state']=='completed_with_gaps'


if __name__=='__main__':main()
