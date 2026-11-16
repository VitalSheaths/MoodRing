"""Prepare retrospective price/technical inputs without fitting a prediction model."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

from features.technical.macd import add_macd
from .archive import digest, json_bytes, publish, verified_tables


def wilder_series(values: pd.Series, period=14) -> pd.Series:
    """Use a full arithmetic seed; preserve warm-up as missing rather than zero."""
    result = pd.Series(np.nan, index=values.index)
    if len(values) < period:
        return result
    current = float(values.iloc[:period].mean())
    result.iloc[period-1] = current
    for index in range(period, len(values)):
        current = ((period-1)*current + float(values.iloc[index])) / period
        result.iloc[index] = current
    return result


def lower_tail_mean(values):
    """Describe the lower five-percent return tail in a trailing window, not a forecast."""
    threshold = np.quantile(values, .05)
    return float(np.mean(values[values <= threshold]))


def technical_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute trailing inputs within each ticker; callers must first prove coverage.

    These are derived floats over a provider-adjusted reconstruction. They are
    not DailyObservation projections and carry no historical prediction claim.
    """
    results = []
    for ticker, original in frame.groupby('ticker', sort=True):
        group = original.sort_values('session').reset_index(drop=True).copy()
        if group['session'].duplicated().any():
            raise ValueError('duplicate_feature_session')
        for column in ('open', 'high', 'low', 'close', 'volume'):
            group[column] = group[column].astype(float)
        if not np.isfinite(group[['open', 'high', 'low', 'close', 'volume']].to_numpy()).all():
            raise ValueError('nonfinite_feature_input')
        close, high, low, volume = (group[c] for c in ('close', 'high', 'low', 'volume'))
        if (close <= 0).any() or (low <= 0).any() or (volume < 0).any():
            raise ValueError('feature_input_domain')
        out = add_macd(group[['ticker', 'close']])
        out['session'] = group['session']
        out['return_1'] = close.pct_change(fill_method=None)
        out['price_change_1'] = close.diff()
        out['log_close'] = np.log(close)
        out['log_return_1'] = np.log1p(out['return_1'])
        out['range_pct'] = (high-low)/close
        out['relative_volume_20'] = volume / volume.shift(1).rolling(20).mean().replace(0, np.nan)
        change = close.diff().iloc[1:]
        gain, loss = wilder_series(change.clip(lower=0)), wilder_series(-change.clip(upper=0))
        out['rsi_14'] = (100-100/(1+gain/loss)).mask((gain == 0) & (loss == 0))
        true_range = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        out['atr_14_pct'] = wilder_series(true_range.iloc[1:])/close
        mean, sigma = close.rolling(20).mean(), close.rolling(20).std(ddof=0)
        out['bollinger_pct_b'] = (close-mean+2*sigma)/(4*sigma.replace(0, np.nan))
        out['bollinger_bandwidth'] = 4*sigma/mean
        out['realized_vol_20'] = out['log_return_1'].rolling(20).std(ddof=1)
        out['realized_vol_5'] = out['log_return_1'].rolling(5).std(ddof=1)
        out['realized_vol_63'] = out['log_return_1'].rolling(63).std(ddof=1)
        out['downside_semideviation_20'] = np.sqrt(out['log_return_1'].clip(upper=0).pow(2).rolling(20).mean())
        out['worst_log_return_63'] = out['log_return_1'].rolling(63).min()
        out['drawdown_from_63_close_peak'] = close / close.rolling(63).max() - 1
        out['historical_log_return_q05_252'] = out['log_return_1'].rolling(252).quantile(.05)
        out['historical_log_return_tail_mean_252'] = out['log_return_1'].rolling(252).apply(lower_tail_mean, raw=True)
        out['obv_change_20'] = (np.sign(close.diff())*volume).rolling(20).sum()
        out = out.drop(columns=['close'])
        out['mode'] = 'retrospective_provider_view'
        out['historical_prediction_eligible'] = False
        results.append(out)
    return pd.concat(results, ignore_index=True)


def prepare(snapshot: Path, output: Path) -> dict:
    """Read verified source bytes and publish feature inputs as a new immutable unit."""
    manifest, tables = verified_tables(snapshot)
    if 'prices' not in tables or not all(r['coverage_complete'] for r in manifest['price_coverage']):
        raise ValueError('complete_price_coverage_required')
    frame = technical_frame(tables['prices'].to_pandas(use_threads=False))
    metadata = {'created_at': datetime.now(timezone.utc).isoformat(),
                'source_manifest_sha256': digest(json_bytes(manifest)),
                'source_price_sha256': manifest['files']['prices.parquet']['sha256'],
                'feature_version': 'retrospective-technicals-v2-log-risk', 'rows': len(frame),
                'pit_eligible': False, 'model_status': 'not_trained', 'labels_status': 'not_built',
                'macro_join_status': 'pending_release_time_policy',
                'sentiment_join_status': 'pending_timestamp_mapping_and_scoring',
                'warmup_missing_by_feature': {k: int(frame[k].isna().sum()) for k in frame.columns},
                'acceptance_status': 'provisional_not_human_accepted'}
    publish(output, {'technical_features': pa.Table.from_pandas(frame, preserve_index=False, nthreads=1)}, metadata)
    verified_tables(output)
    return metadata


def main():
    """Require explicit source and new output paths; perform no acquisition or training."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.snapshot, args.output)
    print(f'Prepared {report["rows"]} retrospective feature rows; no model fitted.')


if __name__ == '__main__':
    main()
