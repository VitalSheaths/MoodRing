"""Expand a committed small synthetic seed; no network or production selector."""
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
import json
import hashlib

from ingestion.universe.identity import IdentityCatalog, SicCode, content_hash
from ingestion.universe.ranking import RankFraction, RankingInput, SelectionPolicy, earnings_yield, midrank_percentiles
from ingestion.universe.snapshot import UniverseSnapshot

ASOF=datetime(2026,10,20,20,tzinfo=timezone.utc)
SEED=Path(__file__).parent/'fixtures/universe/seed.json'


def evidence(available='2026-01-01T00:00:00Z'):
    return dict(source='synthetic',dataset_version='synthetic-v1',source_ref='fixture://universe/seed.json',
                payload_sha256=hashlib.sha256(SEED.read_bytes()).hexdigest(),available_at=available,ingested_at='2026-10-21T00:00:00Z')


def dated(start='2010-01-01T00:00:00Z',end=None):
    return dict(effective_from=start,effective_until=end,evidence=evidence())


def fixture_data():
    seed=json.loads(SEED.read_text())
    codes=tuple(SicCode(**r) for r in seed['sic_codes'])
    issuers=[]; instruments=[]; mappings=[]; assignments=[]; inputs=[]
    for row in seed['companies']:
        key=row['key']; issuer='issuer:'+key; instrument='instrument:'+key
        issuers.append(dict(issuer_id=issuer,cik=row['cik'],legal_name='Synthetic '+key,country='US',evidence=evidence()))
        instruments.append(dict(instrument_id=instrument,issuer_id=issuer,display_name='Synthetic '+key,
                                kind='common_stock',country='US',currency='USD',measure_unit='currency',status='active',tradable=True,**dated()))
        mappings.extend([dict(mapping_id='canonical:'+key,instrument_id=instrument,provider='canonical',symbol=key.upper(),mic='XNAS',**dated()),
                         dict(mapping_id='provider:'+key,instrument_id=instrument,provider='fake',symbol=key.upper()+'.US',mic='XNAS',**dated())])
        assignments.append(dict(assignment_id='sic:'+key,issuer_id=issuer,taxonomy_version='sec-subset-20261018',code=row['sic'],**dated()))
        inputs.append(RankingInput(issuer_id=issuer,instrument_id=instrument,assignment_id='sic:'+key,mapping_id='canonical:'+key,
                       currency='USD',market_cap=row['market_cap'],market_cap_basis='issuer_wide',market_cap_asof=ASOF,
                       cap_evidence=evidence('2026-10-20T20:00:00Z'),common_earnings_ttm=row['earnings'],
                       fundamental_period_end='2026-09-30',earnings_evidence=evidence('2026-10-10T15:00:00Z'),
                       dollar_volume=row['dollar_volume'],liquidity_metric='close_times_volume',volume_scope='consolidated',
                       liquidity_provider='fake',liquidity_feed='synthetic-consolidated',liquidity_end=ASOF,liquidity_sessions=60,
                       liquidity_evidence=evidence('2026-10-20T20:00:00Z'),trading_status='trading',last_price='20',price_age_sessions=0,history_sessions=500))
    # One issuer, second share class: different instrument but same company identity.
    instruments.append(dict(instruments[0],instrument_id='instrument:co000-b',display_name='Synthetic co000 class B'))
    mappings.append(dict(mapping_id='canonical:co000-b',instrument_id='instrument:co000-b',provider='canonical',symbol='CO000.B',mic='XNYS',**dated()))
    # Dated rename and exchange change: same instrument, not a new company.
    for mapping in list(mappings):
        if mapping['instrument_id']=='instrument:co001':
            mapping['effective_from']='2026-10-01T00:00:00Z'
            old=dict(mapping,mapping_id=mapping['mapping_id']+'-old',symbol='OLD001',mic='XNYS',**dated(end='2026-10-01T00:00:00Z'))
            mappings.append(old)
    # A past instrument and a separate successor identity; neither enters membership.
    for key,end,start,status in [('old','2026-07-01T00:00:00Z','2010-01-01T00:00:00Z','inactive'),
                                 ('new',None,'2026-07-01T00:00:00Z','active')]:
        issuers.append(dict(issuer_id='issuer:'+key,cik='999001' if key=='old' else '999002',legal_name='Synthetic '+key,country='US',evidence=evidence()))
        instruments.append(dict(instrument_id='instrument:'+key,issuer_id='issuer:'+key,display_name='Synthetic '+key,kind='common_stock',
                                country='US',currency='USD',measure_unit='currency',status=status,tradable=True,**dated(start,end)))
        mappings.append(dict(mapping_id='canonical:'+key,instrument_id='instrument:'+key,provider='canonical',symbol=key.upper(),mic='XNYS',**dated(start,end)))
    instruments.append(dict(instrument_id='index:synthetic',display_name='Synthetic volatility reference',kind='index',country='US',
                            currency=None,measure_unit='volatility_points',status='active',tradable=False,administrator='Synthetic administrator',**dated()))
    mappings.append(dict(mapping_id='canonical:index',instrument_id='index:synthetic',provider='canonical',symbol='^SYNTH',mic=None,**dated()))
    catalog=IdentityCatalog(catalog_version='synthetic-catalog-v1',issuers=issuers,instruments=instruments,mappings=mappings,
               taxonomy=dict(taxonomy_version='sec-subset-20261018',source_url=seed['sic_source'],captured_at=seed['sic_captured_at'],scope='subset',codes=codes,codes_sha256=content_hash(codes)),
               assignments=assignments,successors=[dict(predecessor_id='instrument:old',successor_id='instrument:new',reason='merger',**dated('2026-07-01T00:00:00Z'))])
    policy=SelectionPolicy(**seed['policy'])
    groups=defaultdict(list)
    sic={r['key']:r['sic'] for r in seed['companies']}
    for row in inputs: groups[sic[row.issuer_id.split(':')[1]]].append(row)
    members=[]
    for code, rows in groups.items():
        caps=midrank_percentiles({r.issuer_id:r.market_cap for r in rows})
        yields={r.issuer_id:earnings_yield(r.common_earnings_ttm,r.market_cap) for r in rows}
        levels={v:i for i,v in enumerate(sorted(set(yields.values())))}
        valuations=midrank_percentiles({k:levels[v] for k,v in yields.items()})
        liquid=midrank_percentiles({r.issuer_id:r.dollar_volume for r in rows})
        for r in rows:
            score=caps[r.issuer_id]*Fraction(policy.weights.market_cap)+valuations[r.issuer_id]*Fraction(policy.weights.earnings_yield)+liquid[r.issuer_id]*Fraction(policy.weights.liquidity)
            members.append(dict(issuer_id=r.issuer_id,instrument_id=r.instrument_id,sic_code=code,selection_rank=1,
                                market_cap_rank=RankFraction.from_fraction(caps[r.issuer_id]),valuation_rank=RankFraction.from_fraction(valuations[r.issuer_id]),
                                liquidity_rank=RankFraction.from_fraction(liquid[r.issuer_id]),score=RankFraction.from_fraction(score)))
    byid={r.issuer_id:r for r in inputs}
    members.sort(key=lambda m:(-m['score'].value,-byid[m['issuer_id']].market_cap,-byid[m['issuer_id']].dollar_volume,m['issuer_id']))
    for i,m in enumerate(members,1): m['selection_rank']=i
    ordered_inputs=tuple(sorted(inputs,key=lambda r:r.issuer_id))
    return dict(universe_version='synthetic-universe-v1',data_kind='synthetic',dataset_mode='current_snapshot',asof=ASOF,
                published_at='2026-10-21T12:00:00Z',effective_at='2026-10-21T13:30:00Z',catalog=catalog,catalog_sha256=catalog.content_hash,
                policy=policy,config_sha256=policy.content_hash,inputs=ordered_inputs,inputs_sha256=content_hash(ordered_inputs),
                coverage=dict(historical_candidates=False,delisted_identities=False,classification_vintages=False,fundamental_vintages=False,
                              evidence_ref='fixture://current-only',limitations=['Synthetic snapshot; no historical universe coverage claimed.']),
                status='complete',diagnostics=[],members=members,industry_counts=[dict(sic_code=k,count=v) for k,v in seed['industry_counts'].items()],exclusions=[])


def snapshot():
    return UniverseSnapshot(**fixture_data())
