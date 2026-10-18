"""Exact daily observations and append-only revision validation."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from ingestion.sec.contracts import Sha256
from ingestion.universe.identity import Exact, Id, Instrument, SymbolMapping, Time, content_hash
from .providers import AdjustmentBasis, ProviderReceipt
from .sessions import MarketContract, Session


class Adjustment(MarketContract):
    basis: AdjustmentBasis
    factor_ref: Id | None = None
    factor_sha256: Sha256 | None = None
    vintage_at: Time | None = None

    @model_validator(mode='after')
    def reproducible(self):
        metadata = (self.factor_ref, self.factor_sha256, self.vintage_at)
        if self.basis == 'raw' and any(v is not None for v in metadata):
            raise ValueError('raw_has_no_adjustment_factors')
        if self.basis != 'raw' and any(v is None for v in metadata):
            raise ValueError('adjusted_requires_factor_and_vintage')
        return self


class AdjustedClose(MarketContract):
    value: Exact = Field(ge=0)
    adjustment: Adjustment

    @model_validator(mode='after')
    def adjusted(self):
        if self.adjustment.basis == 'raw':
            raise ValueError('adjusted_close_requires_adjusted_basis')
        return self


class DailyObservation(MarketContract):
    instrument: Instrument
    mapping: SymbolMapping
    canonical_mapping: SymbolMapping | None = None
    session: Session
    receipt: ProviderReceipt
    interval: Literal['1d'] = '1d'
    adjustment: Adjustment
    shape: Literal['ohlc', 'close_only']
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    measure_unit: Literal['currency', 'index_points', 'volatility_points']
    open: Exact | None = Field(default=None, ge=0)
    high: Exact | None = Field(default=None, ge=0)
    low: Exact | None = Field(default=None, ge=0)
    close: Exact = Field(ge=0)
    volume: Exact | None = Field(default=None, ge=0)
    notional: Exact | None = Field(default=None, ge=0)
    adjusted_close: AdjustedClose | None = None
    supersedes: Sha256 | None = None  # revision ID of immediately prior version

    @model_validator(mode='after')
    def validate_observation(self):
        i, m, s, r = self.instrument, self.mapping, self.session, self.receipt
        p = r.parameters
        if (i.instrument_id, m.mapping_id, m.symbol, m.provider, p.interval, p.adjustment) != (
                p.instrument_id, p.mapping_id, p.symbol, r.provider, self.interval, self.adjustment.basis) or m.instrument_id != i.instrument_id:
            raise ValueError('identity_request_mismatch')
        canonical = self.canonical_mapping
        if i.kind == 'common_stock' and canonical is None:
            raise ValueError('stock_requires_canonical_mapping')
        if i.kind == 'common_stock' and (m.mic is None or canonical.mic is None or m.mic != canonical.mic):
            raise ValueError('stock_mapping_mic_mismatch')
        if canonical is not None and (canonical.provider != 'canonical' or canonical.instrument_id != i.instrument_id or
                                      not canonical.eligible(s.closes_at)):
            raise ValueError('canonical_mapping_mismatch')
        if not i.eligible(s.closes_at) or not m.eligible(s.closes_at):
            raise ValueError('identity_unavailable_for_session')
        if (p.window.calendar_id, p.window.calendar_version) != (s.calendar_id, s.calendar_version) or not p.window.contains(s.session_date):
            raise ValueError('session_outside_request')
        if s.closes_at > p.window.cutoff or s.closes_at > r.responded_at:
            raise ValueError('incomplete_daily_observation')
        if r.evidence.available_at is not None and r.evidence.available_at < s.closes_at:
            raise ValueError('daily_availability_before_session_close')
        if (self.currency, self.measure_unit) != (i.currency, i.measure_unit):
            raise ValueError('instrument_units_mismatch')
        if self.shape == 'ohlc':
            if any(v is None for v in (self.open, self.high, self.low)):
                raise ValueError('ohlc_requires_all_prices')
            if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
                raise ValueError('invalid_ohlc_order')
        elif any(v is not None for v in (self.open, self.high, self.low)) or i.kind != 'index':
            raise ValueError('close_only_index_cannot_fabricate_ohlc')
        if i.kind == 'index' and (self.volume is not None or self.notional is not None):
            raise ValueError('index_volume_not_meaningful')
        for adjustment in (self.adjustment, self.adjusted_close.adjustment if self.adjusted_close else None):
            if adjustment is not None and adjustment.vintage_at is not None and adjustment.vintage_at > r.responded_at:
                raise ValueError('adjustment_vintage_after_response')
        return self

    @property
    def series_key(self) -> str:
        s, r = self.session, self.receipt
        return content_hash(dict(instrument_id=self.instrument.instrument_id, provider=r.provider, feed=r.feed,
                                 dataset=r.dataset, dataset_version=r.dataset_version, interval=self.interval,
                                 calendar_id=s.calendar_id, calendar_version=s.calendar_version,
                                 session_mode=s.mode, timezone=s.timezone, currency=self.currency,
                                 measure_unit=self.measure_unit, adjustment=self.adjustment.basis,
                                 schema_version=self.schema_version))

    @property
    def observation_key(self) -> str:
        return content_hash((self.series_key, self.session.session_date))

    @property
    def semantic_checksum(self) -> str:
        return content_hash(dict(observation_key=self.observation_key, session=self.session,
                                 adjustment=self.adjustment, shape=self.shape, open=self.open,
                                 high=self.high, low=self.low, close=self.close, volume=self.volume,
                                 notional=self.notional, adjusted_close=self.adjusted_close))

    @property
    def revision_id(self) -> str:
        return content_hash((self.observation_key, self.semantic_checksum, self.supersedes))

    def eligible(self, cutoff: datetime) -> bool:
        from ingestion.sec.contracts import utc
        cutoff = utc(cutoff)
        return (self.session.closes_at <= cutoff and self.receipt.evidence.eligible(cutoff) and
                all(a is None or a.vintage_at is None or a.vintage_at <= cutoff for a in (
                    self.adjustment, self.adjusted_close.adjustment if self.adjusted_close else None)))


def validate_revisions(observations: tuple[DailyObservation, ...]) -> tuple[DailyObservation, ...]:
    """Input order is append order. Return originals + valid corrections, dedup repeats."""
    seen, latest, output = {}, {}, []
    for observation in observations:
        revision, key = observation.revision_id, observation.observation_key
        if revision in seen:
            continue
        previous = latest.get(key)
        if previous is None:
            if observation.supersedes is not None:
                raise ValueError('missing_revision_predecessor')
        else:
            if observation.supersedes != previous.revision_id:
                raise ValueError('correction_requires_latest_predecessor')
            if observation.semantic_checksum == previous.semantic_checksum:
                raise ValueError('unchanged_values_do_not_create_revision')
            if observation.receipt.evidence.ingested_at < previous.receipt.evidence.ingested_at:
                raise ValueError('revision_ingestion_reversed')
            before, after = previous.receipt.evidence.available_at, observation.receipt.evidence.available_at
            if before is not None and after is not None and after < before:
                raise ValueError('revision_availability_reversed')
        seen[revision], latest[key] = observation, observation
        output.append(observation)
    return tuple(output)


def validate_series(observations: tuple[DailyObservation, ...]) -> tuple[DailyObservation, ...]:
    """A consumer must explicitly separate adjustment modes/feeds into distinct series."""
    if len({o.series_key for o in observations}) > 1:
        raise ValueError('mixed_observation_series')
    return validate_revisions(observations)
