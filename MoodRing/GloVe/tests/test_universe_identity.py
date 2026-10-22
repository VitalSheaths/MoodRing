from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from ingestion.sec.contracts import UniverseMember
from ingestion.universe.identity import DataRights, IdentityCatalog, Instrument, Issuer, SicAssignment, SicCode, SicTaxonomy
from universe_fixtures import ASOF, dated, evidence, snapshot


@pytest.fixture(scope='module')
def catalog():
    return snapshot().catalog


def test_sic_subset_and_legacy_cik_reuse(catalog):
    assert SicCode(code='100',description='AGRICULTURAL PRODUCTION-CROPS').code=='0100'
    issuer=catalog.issuer_for_cik('100000')
    assert issuer.cik=='0000100000'
    member=UniverseMember(ticker='EXAMPLE',cik=issuer.cik,effective_from='2026-01-01T00:00:00Z')
    assert member.cik==issuer.cik
    assert catalog.taxonomy.source_url.endswith('standard-industrial-classification-sic-code-list')
    assert catalog.taxonomy.scope=='subset' and len(catalog.taxonomy.codes)==10
    assert catalog.taxonomy.taxonomy_version=='sec-subset-20261018'


@pytest.mark.parametrize('cik',['0','abc','1'*11,123])
def test_invalid_cik(cik):
    with pytest.raises(ValidationError):
        Issuer(issuer_id='issuer:test',cik=cik,legal_name='Synthetic',country='US',evidence=evidence())


def test_ticker_and_exchange_change_preserve_identity(catalog):
    before=datetime(2026,9,30,tzinfo=timezone.utc)
    old=catalog.resolve('instrument:co001','canonical',before)
    new=catalog.resolve('instrument:co001','canonical',ASOF)
    assert old.instrument_id==new.instrument_id
    assert (old.symbol,old.mic)==('OLD001','XNYS')
    assert (new.symbol,new.mic)==('CO001','XNAS')
    assert catalog.resolve('instrument:co001','fake',ASOF).symbol=='CO001.US'
    assert old.effective_until==new.effective_from
    assert catalog.resolve('instrument:co001','canonical',new.effective_from).symbol=='CO001'


def test_alias_overlap_unknown_exchange_and_changed_identity(catalog):
    data=catalog.model_dump(mode='json')
    duplicate=dict(data['mappings'][0],mapping_id='mapping:overlap')
    data['mappings'].append(duplicate)
    with pytest.raises(ValidationError,match='overlap'): IdentityCatalog.model_validate(data)
    data=catalog.model_dump(mode='json'); data['mappings'][0]['mic']='invalid'
    with pytest.raises(ValidationError): IdentityCatalog.model_validate(data)
    data=catalog.model_dump(mode='json'); data['instruments'][0]['issuer_id']='issuer:unknown'
    with pytest.raises(ValidationError): IdentityCatalog.model_validate(data)


def test_alias_reuse_for_different_instruments_is_ambiguous(catalog):
    data=catalog.model_dump(mode='json')
    a=next(m for m in data['mappings'] if m['mapping_id']=='canonical:co000')
    b=next(m for m in data['mappings'] if m['mapping_id']=='canonical:co002')
    b['symbol']=a['symbol']
    with pytest.raises(ValidationError,match='overlap'): IdentityCatalog.model_validate(data)


def test_share_classes_and_successor_lifetimes(catalog):
    classes=[i for i in catalog.instruments if i.issuer_id=='issuer:co000']
    assert len(classes)==2 and len({i.instrument_id for i in classes})==2
    link=catalog.successors[0]
    assert link.predecessor_id!=link.successor_id
    old=catalog.resolve('instrument:old','canonical',datetime(2026,6,1,tzinfo=timezone.utc))
    assert old.symbol=='OLD'
    with pytest.raises(ValueError,match='unavailable'): catalog.resolve('instrument:old','canonical',ASOF)
    assert catalog.resolve('instrument:new','canonical',ASOF).instrument_id!='instrument:old'


def test_index_is_readonly_and_has_no_fabricated_cik(catalog):
    index=next(i for i in catalog.instruments if i.kind=='index')
    assert not index.tradable and index.issuer_id is None and index.currency is None
    assert index.measure_unit=='volatility_points'
    for changes in ({'tradable':True},{'issuer_id':'issuer:co000'},{'measure_unit':'currency'}):
        with pytest.raises(ValidationError): Instrument.model_validate(index.model_dump()|changes)
    with pytest.raises(ValidationError): Instrument.model_validate(index.model_dump()|{'kind':'etf'})


def test_future_unknown_alias_and_classification_are_not_eligible(catalog):
    mapping=next(m for m in catalog.mappings if m.mapping_id=='canonical:co000')
    for available in (None,'2026-10-20T21:00:00Z'):
        data=catalog.model_dump(mode='json')
        next(m for m in data['mappings'] if m['mapping_id']==mapping.mapping_id)['evidence']['available_at']=available
        changed=IdentityCatalog.model_validate(data)
        with pytest.raises(ValueError,match='unavailable'): changed.resolve(mapping.instrument_id,'canonical',ASOF)
    assignment=next(a for a in catalog.assignments if a.issuer_id=='issuer:co000')
    data=assignment.model_dump(); data['evidence']=evidence('2026-10-20T21:00:00Z')
    assert not SicAssignment.model_validate(data).eligible(ASOF)


def test_single_primary_sic_and_subset_integrity(catalog):
    data=catalog.model_dump(mode='json'); duplicate=dict(data['assignments'][0],assignment_id='sic:duplicate')
    data['assignments'].append(duplicate)
    with pytest.raises(ValidationError,match='overlap'): IdentityCatalog.model_validate(data)
    data=catalog.taxonomy.model_dump(mode='json'); data['codes'][0]['description']='changed'
    with pytest.raises(ValidationError,match='checksum'): SicTaxonomy.model_validate(data)
    data=catalog.model_dump(mode='json'); data['assignments'][0]['code']='9999'
    with pytest.raises(ValidationError,match='Unknown SIC'): IdentityCatalog.model_validate(data)


def test_rights_default_deny_and_review_expiry():
    values=dict(provider='fake',dataset_version='v1',retention_policy='fixture-only; no provider entitlement')
    unknown=DataRights(**values,public_display=True)
    assert not unknown.permits('public_display',ASOF)
    with pytest.raises(ValidationError): DataRights(**values,status='verified')
    verified=DataRights(**values,status='verified',review_ref='fixture://review',reviewed_at='2026-10-01T00:00:00Z',
                        valid_until='2026-11-01T00:00:00Z',local_research=True)
    assert verified.permits('local_research',ASOF)
    assert not verified.permits('public_display',ASOF)
    assert not verified.permits('model_use',ASOF)
    assert not verified.permits('redistribution',ASOF)
    assert not verified.permits('local_research',datetime(2026,11,1,tzinfo=timezone.utc))
    assert not verified.permits('anything',ASOF)


def test_catalog_order_is_semantically_canonical_and_frozen(catalog):
    data=catalog.model_dump(mode='json')
    for field in ('issuers','instruments','mappings','assignments','successors'): data[field].reverse()
    data['taxonomy']['codes'].reverse()
    assert IdentityCatalog.model_validate(data).content_hash==catalog.content_hash
    with pytest.raises(ValidationError): catalog.catalog_version='changed'


def test_unknown_rights_and_validation_errors_do_not_echo_input_secrets():
    values=dict(provider='fake',dataset_version='v1',retention_policy='fixture-only',secret='DO_NOT_ECHO_THIS_VALUE')
    with pytest.raises(ValidationError) as exc:
        DataRights.model_validate(values)
    assert 'DO_NOT_ECHO_THIS_VALUE' not in str(exc.value)


def test_successor_metadata_cannot_reuse_or_invent_instruments(catalog):
    data=catalog.model_dump(mode='json'); data['successors'][0]['successor_id']=data['successors'][0]['predecessor_id']
    with pytest.raises(ValidationError): IdentityCatalog.model_validate(data)
    data=catalog.model_dump(mode='json'); data['successors'][0]['successor_id']='instrument:unknown'
    with pytest.raises(ValidationError): IdentityCatalog.model_validate(data)
