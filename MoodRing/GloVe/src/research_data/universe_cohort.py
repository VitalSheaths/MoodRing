"""Offline market-cap-only cohort research; no production universe replacement.

Retains pre-2010 listings that later exit. This ranks supplied candidates only;
complete historical coverage must be established separately before calling it a
historical top-100 universe. Existing identity primitives are reused.
"""
from datetime import date, timedelta

from pydantic import Field
from ingestion.universe.identity import Contract, Id, Exact, content_hash


class CohortCandidate(Contract):
    """One explicitly chosen listing per issuer with dated USD market cap."""
    issuer_id: Id
    instrument_id: Id
    ipo_date: date
    listing_start: date
    listing_end: date | None = None
    market_cap: Exact = Field(gt=0)
    cap_asof: date
    cap_available_date: date
    currency: str = 'USD'
    common_stock: bool
    us_domiciled: bool
    source_reference: str = Field(min_length=1,max_length=500)


def select_cohort(candidates, selection_date=date(2009,12,31), count=100):
    """Rank supplied eligible issuers without conditioning on future survival.

    Selection happens after the stated date; same-day cap availability is an
    explicit end-of-day assumption, not an intraday receipt assertion. A future
    listing end is kept as lifecycle metadata and never excludes a candidate.
    """
    if not 1<=count<=100 or not 1<=len(candidates)<=10000:
        raise ValueError('cohort_bounds')
    rows=[CohortCandidate.model_validate(r) for r in candidates]
    if len({r.issuer_id for r in rows})!=len(rows) or len({r.instrument_id for r in rows})!=len(rows):
        raise ValueError('one_explicit_listing_per_issuer_required')
    accepted=[];decisions=[]
    for r in rows:
        reason=None
        if r.ipo_date>=date(2010,1,1):reason='ipo_not_before_2010'
        elif r.listing_start>selection_date or (r.listing_end and r.listing_end<=selection_date):reason='not_active_at_selection'
        elif not r.common_stock or not r.us_domiciled or r.currency!='USD':reason='universe_definition'
        elif r.cap_asof>selection_date or r.cap_available_date>selection_date:reason='future_market_cap'
        elif r.cap_available_date<r.cap_asof or selection_date-r.cap_asof>timedelta(days=31):reason='stale_or_inconsistent_cap'
        if reason is None:accepted.append(r)
        decisions.append({'issuer_id':r.issuer_id,'eligibility_reason':reason})
    ranked=sorted(accepted,key=lambda r:(-r.market_cap,r.issuer_id))
    selected=ranked[:count]
    return dict(version='historical-pre2010-cohort-v1',selection_date=str(selection_date),
        source_hash=content_hash(tuple(rows)),requested_count=count,selected_count=len(selected),
        candidate_coverage_verified=False,historical_top100_claim=False,
        status='supplied_candidates_ranked' if len(selected)==count else 'insufficient_candidates',
        members=[dict(issuer_id=r.issuer_id,instrument_id=r.instrument_id,rank=i+1,
                      source_reference=r.source_reference,future_exit_retained=bool(r.listing_end and r.listing_end>selection_date))
                 for i,r in enumerate(selected)],decisions=decisions)
