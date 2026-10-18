"""Finite, unentitled index-reference slots; no vendor aliases or observations."""
from __future__ import annotations

from datetime import date
from typing import Literal
from types import MappingProxyType

from pydantic import Field, field_validator, model_validator

from ingestion.universe.identity import DataRights, Evidence, Id, Instrument
from .providers import CapabilityState
from .sessions import MarketContract

# Identifiers are project registry slots, not vendor symbols or licensed taxonomy IDs.
INDEX_SLOTS = {
    'sp500': ('S&P 500', 'S&P Dow Jones Indices', 'price', 'index_points'),
    'nasdaq_composite': ('Nasdaq Composite', 'Nasdaq', 'price', 'index_points'),
    'nasdaq100': ('Nasdaq-100', 'Nasdaq', 'price', 'index_points'),
    'djia': ('Dow Jones Industrial Average', 'S&P Dow Jones Indices', 'price', 'index_points'),
    'russell1000': ('Russell 1000', 'FTSE Russell', 'price', 'index_points'),
    'russell2000': ('Russell 2000', 'FTSE Russell', 'price', 'index_points'),
    'russell3000': ('Russell 3000', 'FTSE Russell', 'price', 'index_points'),
    'sp400': ('S&P MidCap 400', 'S&P Dow Jones Indices', 'price', 'index_points'),
    'sp600': ('S&P SmallCap 600', 'S&P Dow Jones Indices', 'price', 'index_points'),
    'wilshire5000': ('Wilshire 5000', 'Wilshire', 'price', 'index_points'),
    'vix': ('Cboe VIX', 'Cboe', 'volatility', 'volatility_points'),
}
for _sector in ('communication_services', 'consumer_discretionary', 'consumer_staples', 'energy',
                'financials', 'health_care', 'industrials', 'information_technology', 'materials',
                'real_estate', 'utilities'):
    INDEX_SLOTS['sp500_sector_' + _sector] = (
        'S&P 500 sector reference: ' + _sector.replace('_', ' '), 'S&P Dow Jones Indices', 'price', 'index_points')

INDEX_SLOTS = MappingProxyType(INDEX_SLOTS)


class IndexReference(MarketContract):
    slot: str
    instrument: Instrument
    region: Literal['US'] = 'US'
    variant: Literal['price', 'total_return', 'volatility']
    calendar_id: Id
    calendar_version: Id
    timezone: str
    available_from: date | None = None
    available_until: date | None = None  # exclusive
    capability: CapabilityState = 'unknown'
    shape: Literal['unknown', 'ohlc', 'close_only'] = 'unknown'
    provider_mapping_id: Id | None = None
    evidence: Evidence | None = None
    rights: DataRights

    @model_validator(mode='after')
    def reference(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        if self.slot not in INDEX_SLOTS:
            raise ValueError('unknown_index_slot')
        _, administrator, expected_variant, units = INDEX_SLOTS[self.slot]
        i = self.instrument
        if i.kind != 'index' or i.tradable or i.measure_unit != units or i.administrator != administrator:
            raise ValueError('index_identity_mismatch')
        if (expected_variant == 'price' and self.variant == 'volatility') or (expected_variant == 'volatility' and self.variant != 'volatility'):
            raise ValueError('index_variant_mismatch')
        if self.available_from is not None and self.available_until is not None and self.available_until <= self.available_from:
            raise ValueError('empty_index_availability')
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('unknown_timezone') from exc
        if self.capability == 'supported':
            if self.provider_mapping_id is None or self.evidence is None or self.shape == 'unknown' or self.available_from is None:
                raise ValueError('supported_index_requires_coverage_evidence')
            if self.evidence.source != self.rights.provider or self.evidence.dataset_version != self.rights.dataset_version:
                raise ValueError('index_provider_evidence_mismatch')
        elif self.provider_mapping_id is not None or self.shape != 'unknown':
            raise ValueError('unverified_index_mapping')
        return self


class IndexRegistry(MarketContract):
    registry_version: Id
    references: tuple[IndexReference, ...] = Field(min_length=1, max_length=100)

    @field_validator('references')
    @classmethod
    def ordered(cls, value):
        return tuple(sorted(value, key=lambda r: (r.slot, r.variant)))

    @model_validator(mode='after')
    def unique(self):
        if len({(r.slot, r.variant) for r in self.references}) != len(self.references):
            raise ValueError('duplicate_index_variant')
        if len({r.instrument.instrument_id for r in self.references}) != len(self.references):
            raise ValueError('index_variants_require_distinct_ids')
        if {r.slot for r in self.references} != set(INDEX_SLOTS):
            raise ValueError('incomplete_index_registry')
        return self
