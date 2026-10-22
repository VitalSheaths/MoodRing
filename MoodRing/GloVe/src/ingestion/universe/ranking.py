"""Exact ranking primitives and explicit policy inputs, not a production selector."""
from __future__ import annotations

from datetime import date
from fractions import Fraction
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from ingestion.universe.identity import Contract, Evidence, Exact, Id, Time, _decimal


def earnings_yield(earnings, market_cap) -> Fraction | None:
    if market_cap is None or _decimal(market_cap) <= 0:
        raise ValueError('invalid_market_cap')
    return None if earnings is None else Fraction(_decimal(earnings)) / Fraction(_decimal(market_cap))


def midrank_percentiles(values) -> dict[str, Fraction]:
    if not values:
        return {}
    rows = sorted((Fraction(_decimal(value)), key) for key, value in values.items())
    if len(rows) == 1:
        return {rows[0][1]: Fraction(1, 2)}
    result, start = {}, 0
    while start < len(rows):
        end = start + 1
        while end < len(rows) and rows[end][0] == rows[start][0]:
            end += 1
        percentile = Fraction(start + end - 1, 2 * (len(rows) - 1))
        for _, key in rows[start:end]:
            result[key] = percentile
        start = end
    return result


class RankFraction(Contract):
    numerator: StrictInt = Field(ge=0)
    denominator: StrictInt = Field(gt=0)

    @model_validator(mode='after')
    def reduced_percentile(self):
        value = self.value
        if value > 1 or value.numerator != self.numerator or value.denominator != self.denominator:
            raise ValueError('Use a reduced fraction between zero and one')
        return self

    @property
    def value(self):
        return Fraction(self.numerator, self.denominator)

    @classmethod
    def from_fraction(cls, value):
        return cls(numerator=value.numerator, denominator=value.denominator)


class MetricWeights(Contract):
    market_cap: Exact = Field(ge=0, le=1)
    earnings_yield: Exact = Field(ge=0, le=1)
    liquidity: Exact = Field(ge=0, le=1)

    @model_validator(mode='after')
    def sum_one(self):
        if sum(Fraction(v) for v in (self.market_cap, self.earnings_yield, self.liquidity)) != 1:
            raise ValueError('Metric weights must sum exactly to one')
        return self


class SelectionPolicy(Contract):
    policy_version: Id
    review_status: Literal['fixture_only', 'reviewed']
    review_ref: str = Field(min_length=1, max_length=1000)
    company_count: Literal[100]
    industry_count: Literal[10]
    country: Literal['US']
    currency: Literal['USD']
    venues: frozenset[Literal['XNYS', 'XNAS', 'XASE']] = Field(min_length=1)
    weights: MetricWeights
    valuation_metric: Literal['ttm_common_earnings_yield']
    liquidity_metric: Literal['reported_notional', 'close_times_volume']
    volume_scope: Literal['consolidated', 'single_venue']
    minimum_price: Exact = Field(gt=0)
    minimum_dollar_volume: Exact = Field(gt=0)
    minimum_history_sessions: StrictInt = Field(gt=0)
    liquidity_lookback_sessions: StrictInt = Field(gt=0)
    max_price_age_sessions: StrictInt = Field(ge=0)
    max_fundamental_age_days: StrictInt = Field(gt=0)
    minimum_industry_candidates: StrictInt = Field(gt=0)
    industry_priority: Literal['momentum_and_volume_percentiles']
    momentum_sessions: StrictInt = Field(gt=0)
    volume_window_sessions: StrictInt = Field(gt=0)
    momentum_weight: Exact = Field(ge=0, le=1)
    volume_weight: Exact = Field(ge=0, le=1)
    allocation: Literal['issuer_cap_largest_remainder_with_capacity']
    minimum_seats_per_industry: Literal[1]
    primary_listing_rule: Literal['liquidity_desc_mic_instrument_id']
    reconstitution: Literal['quarterly']
    missing_stale_policy: Literal['exclude']
    exclusions: frozenset[Literal['etf', 'fund', 'preferred', 'warrant', 'adr', 'otc', 'suspended', 'penny']]
    tie_breakers: tuple[Literal['market_cap_desc'], Literal['liquidity_desc'], Literal['issuer_id_asc']]

    @model_validator(mode='after')
    def complete_policy(self):
        if Fraction(self.momentum_weight) + Fraction(self.volume_weight) != 1:
            raise ValueError('Industry weights must sum exactly to one')
        if self.exclusions != {'etf', 'fund', 'preferred', 'warrant', 'adr', 'otc', 'suspended', 'penny'}:
            raise ValueError('All required exclusion rules must be explicit')
        return self


class RankingInput(Contract):
    issuer_id: Id
    instrument_id: Id
    assignment_id: Id
    mapping_id: Id
    currency: Literal['USD']
    market_cap: Exact | None = Field(default=None, gt=0)
    missing_cap_reason: str | None = Field(default=None, min_length=1, max_length=200)
    market_cap_basis: Literal['issuer_wide']
    market_cap_asof: Time | None = None
    cap_evidence: Evidence | None = None
    common_earnings_ttm: Exact | None = None
    missing_earnings_reason: str | None = Field(default=None, min_length=1, max_length=200)
    fundamental_period_end: date | None = None
    earnings_evidence: Evidence | None = None
    dollar_volume: Exact = Field(ge=0)
    liquidity_metric: Literal['reported_notional', 'close_times_volume']
    volume_scope: Literal['consolidated', 'single_venue']
    liquidity_provider: Id
    liquidity_feed: Id
    liquidity_end: Time
    liquidity_sessions: StrictInt = Field(gt=0)
    liquidity_evidence: Evidence
    weighted_shares_outstanding: Exact | None = Field(default=None, ge=0)
    share_class_shares_outstanding: Exact | None = Field(default=None, ge=0)
    share_basis: Literal['provider_company_equivalent', 'issuer_common_shares'] | None = None
    shares_asof: Time | None = None
    shares_evidence: Evidence | None = None
    trading_status: Literal['trading', 'suspended', 'unknown']
    last_price: Exact = Field(gt=0)
    price_age_sessions: StrictInt = Field(ge=0)
    history_sessions: StrictInt = Field(ge=0)

    @model_validator(mode='after')
    def missingness(self):
        if self.weighted_shares_outstanding is not None or self.share_class_shares_outstanding is not None:
            if self.share_basis is None or self.shares_asof is None or self.shares_evidence is None:
                raise ValueError('Share counts require their provider basis, as-of time and evidence')
        if self.shares_asof is not None and self.shares_evidence is not None and self.shares_asof > self.shares_evidence.ingested_at:
            raise ValueError('Share count observation cannot follow ingestion')
        if (self.common_earnings_ttm is None) != (self.missing_earnings_reason is not None):
            raise ValueError('Missing earnings require a reason; losses are valid earnings')
        if (self.market_cap is None) != (self.missing_cap_reason is not None):
            raise ValueError('Missing cap requires a reason; nonpositive cap is invalid')
        if self.market_cap is not None and (self.market_cap_asof is None or self.cap_evidence is None):
            raise ValueError('Known cap needs its actual observation time and evidence')
        if self.common_earnings_ttm is not None and (self.fundamental_period_end is None or self.earnings_evidence is None):
            raise ValueError('Known earnings need a reporting period and evidence')
        if (self.fundamental_period_end is not None and self.earnings_evidence is not None and
                self.fundamental_period_end > self.earnings_evidence.ingested_at.date()):
            raise ValueError('Reporting period cannot end after ingestion')
        if (self.market_cap_asof is not None and self.cap_evidence is not None and self.market_cap_asof > self.cap_evidence.ingested_at) or self.liquidity_end > self.liquidity_evidence.ingested_at:
            raise ValueError('Measurements cannot end after ingestion')
        return self
