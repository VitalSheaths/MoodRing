"""Historical selection cannot use today's survivors or future market cap."""
from datetime import date
from decimal import Decimal
import pytest
from research_data.universe_cohort import select_cohort


def candidate(name,cap='100',**extra):
    return dict(issuer_id='issuer:'+name,instrument_id='stock:'+name,
        ipo_date=date(1990,1,1),listing_start=date(1990,1,1),listing_end=None,
        market_cap=Decimal(cap),cap_asof=date(2009,12,31),cap_available_date=date(2009,12,31),
        common_stock=True,us_domiciled=True,source_reference='synthetic-fixture',**extra)


def test_future_exit_retained_and_future_winner_excluded():
    old=candidate('old');old['listing_end']=date(2015,1,1)
    future=candidate('future','1000');future['ipo_date']=date(2012,1,1)
    r=select_cohort([old,future],count=1)
    assert r['members'][0]['issuer_id']=='issuer:old'
    assert r['members'][0]['future_exit_retained']
    assert not r['historical_top100_claim']


def test_future_cap_and_duplicate_share_classes_rejected():
    a=candidate('a');a['cap_asof']=date(2026,1,1);a['cap_available_date']=date(2026,1,1)
    r=select_cohort([a],count=1)
    assert r['status']=='insufficient_candidates'
    with pytest.raises(ValueError):select_cohort([candidate('a'),candidate('a')],count=1)


def test_cap_only_order_and_exit_at_selection():
    a=candidate('a','5');b=candidate('b','10');c=candidate('c','100')
    c['listing_end']=date(2009,12,31)
    r=select_cohort([a,b,c],count=2)
    assert [m['issuer_id'] for m in r['members']]==['issuer:b','issuer:a']
