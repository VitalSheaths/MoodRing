"""Auditable research alignment; policy proxies never become proven availability."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pyarrow as pa

from .archive import digest, json_bytes, publish, verified_tables
from . import panel_scope as scope
from .prepare import technical_frame


def session_grid(start=scope.START, end=scope.END):
    """Use actual session closes, including holidays, early closes and DST."""
    calendar = xcals.get_calendar('XNYS', start=start, end=end)
    return [(s.date().isoformat(), calendar.session_close(s).to_pydatetime()) for s in calendar.sessions]


def date_only_available_after(value):
    """Conservatively defer a vintage date until the next New York calendar day.

    This is an explicit research assumption, not an inferred release timestamp.
    Raw available_at remains null; same-day release effects cannot be measured.
    """
    day = date.fromisoformat(value) + timedelta(days=1)
    return datetime.combine(day, time.min, ZoneInfo('America/New_York')).astimezone(timezone.utc)


def macro_panel(rows, sessions, complete_series):
    """Apply vintage updates before each close; never join on observation date alone."""
    result = []
    for series in scope.MACRO:
        events, seen = [], {}
        for row in rows:
            if row['series_id'] != series:
                continue
            period = date.fromisoformat(row['observation_date']).isoformat()
            begin = date.fromisoformat(row['realtime_start']).isoformat()
            finish = date.fromisoformat(row['realtime_end']).isoformat()
            if finish < begin:
                raise ValueError('invalid_macro_chronology')
            if period > begin:
                # Some provider vintages predate their stated observation period.
                # Preserve them in the raw archive, but quarantine from features.
                continue
            value = None if row['value'] is None else float(row['value'])
            if value is not None and not np.isfinite(value):
                raise ValueError('nonfinite_macro')
            key = (period, begin)
            if key in seen and seen[key] != value:
                raise ValueError('conflicting_macro_vintage')
            if key in seen:
                continue
            seen[key] = value
            events.append((date_only_available_after(begin), period, row, value))
        events.sort(key=lambda e: (e[0], e[1]))
        current, cursor = {}, 0
        for session, cutoff in sessions:
            while cursor < len(events) and events[cursor][0] <= cutoff:
                event = events[cursor]
                current[event[1]] = event
                cursor += 1
            latest = current[max(current)] if current and series in complete_series else None
            row = latest[2] if latest else None
            value = latest[3] if latest else None
            record = {'series_id': series, 'session': session, 'cutoff': cutoff.isoformat(),
                      'value': value, 'observation_date': latest[1] if latest else None,
                      'vintage_date': row['realtime_start'] if row else None,
                      'policy_available_after': latest[0].isoformat() if latest else None,
                      'source_record_sha256': digest(json_bytes(row)) if row else None,
                      'age_calendar_days': (date.fromisoformat(session)-date.fromisoformat(latest[1])).days if latest else None,
                      'missing_reason': ('source_collection_incomplete' if series not in complete_series else
                                         'no_prior_vintage' if latest is None else
                                         'source_value_missing' if value is None else None),
                      'timing_policy': 'next_calendar_day_after_vintage_in_new_york',
                      'historical_prediction_eligible': False,
                      'change_from_previous_period': None, 'year_on_year_fraction': None}
            if latest and value is not None:
                previous = sorted(p for p in current if p < latest[1])
                if previous and current[previous[-1]][3] is not None:
                    record['change_from_previous_period'] = value-current[previous[-1]][3]
                # Monthly levels only; negative oil/rates are never logarithmized.
                if series in ('CPIAUCSL', 'PAYEMS'):
                    year_ago = (date.fromisoformat(latest[1]).replace(year=int(latest[1][:4])-1)).isoformat()
                    base = current.get(year_ago)
                    if base and base[3] is not None and base[3] > 0:
                        record['year_on_year_fraction'] = value/base[3]-1
            result.append(record)
    return pd.DataFrame(result)


def news_alignment(rows, sessions):
    """Preserve unknowns and attach candidate articles to the first later close.

    Provider publication and GDELT observation times are named proxies. Company
    candidates are not accepted mappings; empty bins are not proof of no news.
    """
    closes = [cutoff for _, cutoff in sessions]
    result = []
    for row in rows:
        record = {'article_id': row['article_id'], 'provider': row['provider'],
                  'source_record_sha256': digest(json_bytes(row)),
                  'session': None, 'event_time_proxy': None, 'time_basis': None,
                  'candidate_tickers': sorted(set(row.get('provider_related_tickers', []) +
                                                  row.get('title_candidate_tickers', []) +
                                                  row.get('entity_candidate_tickers', [])).intersection(scope.UNIVERSE)),
                  'mapping_status': 'unreviewed_candidate', 'exclusion': None,
                  'historical_prediction_eligible': False}
        raw = row.get('gdelt_seen_at') or row.get('published_at')
        if not raw:
            record['exclusion'] = 'unknown_time'
        else:
            try:
                when = datetime.fromisoformat(raw.replace('Z', '+00:00'))
                if when.tzinfo is None:
                    raise ValueError('naive_time')
                when = when.astimezone(timezone.utc)
                record['event_time_proxy'] = when.isoformat()
                record['time_basis'] = 'gdelt_observation' if row.get('gdelt_seen_at') else 'provider_publication_unverified'
                index = bisect_left(closes, when)
                if when.date() < date.fromisoformat(sessions[0][0]):
                    record['exclusion'] = 'before_study'
                elif index == len(sessions):
                    record['exclusion'] = 'after_last_cutoff'
                else:
                    record['session'] = sessions[index][0]
            except (TypeError, ValueError):
                record['exclusion'] = 'invalid_or_naive_time'
        if not record['candidate_tickers']:
            record['mapping_status'] = 'unresolved'
        result.append(record)
    return result


def news_daily(aligned, sessions):
    """Count unique observed candidate articles without fabricating neutral sentiment."""
    buckets = {}
    for row in aligned:
        if row['session'] and not row['exclusion']:
            for ticker in row['candidate_tickers']:
                buckets.setdefault((ticker, row['session']), set()).add(row['article_id'])
    return pd.DataFrame([{'ticker': ticker, 'session': session,
                         'candidate_headlines_observed': len(buckets.get((ticker, session), set())),
                         'news_coverage_complete': False, 'sentiment_score': None,
                         'sentiment_status': 'not_scored', 'mapping_status': 'candidate_only'}
                        for ticker in scope.UNIVERSE for session, _ in sessions])


def news_monthly(aligned, sessions):
    """Report observed unique candidate coverage without asserting missing months had no news."""
    months = sorted({s[:7] for s, _ in sessions})
    buckets, days = {}, {}
    for row in aligned:
        if row['session'] and not row['exclusion']:
            for ticker in row['candidate_tickers']:
                key = (ticker, row['session'][:7])
                buckets.setdefault(key, set()).add(row['article_id'])
                days.setdefault(key, set()).add(row['session'])
    return pd.DataFrame([{'ticker':t, 'month':m, 'observed_unique_candidate_articles':len(buckets.get((t,m),set())),
                          'sessions_with_candidates':len(days.get((t,m),set())),
                          'historical_archive_complete':False, 'mapping_status':'unreviewed_candidate'}
                         for t in scope.UNIVERSE for m in months])


def exploratory_outcomes(prices):
    """Separate forward outcomes from features; right-censor unmatured horizons.

    These research outcomes are not accepted five-head training labels. All future
    columns live in a separate artifact and must never be model input features.
    """
    result = []
    for ticker, source in prices.groupby('ticker', sort=True):
        group = source.sort_values('session').reset_index(drop=True)
        close = group['close'].astype(float).to_numpy()
        low = group['low'].astype(float).to_numpy()
        sessions = group['session'].tolist()
        logs = np.log(close)
        for horizon in scope.HORIZONS:
            for i, session in enumerate(sessions):
                mature = i+horizon < len(group)
                future_logs = np.diff(logs[i:i+horizon+1]) if mature else None
                result.append({'ticker': ticker, 'session': session, 'horizon_sessions': horizon,
                               'outcome_end_session': sessions[i+horizon] if mature else None,
                               'forward_simple_return': close[i+horizon]/close[i]-1 if mature else None,
                               'forward_log_return': logs[i+horizon]-logs[i] if mature else None,
                               'forward_daily_log_volatility': float(np.std(future_logs, ddof=1)) if mature and horizon > 1 else None,
                               'forward_low_vs_start_close': max(0., 1-float(min(low[i+1:i+horizon+1]))/close[i]) if mature else None,
                               'status': 'matured_retrospective' if mature else 'right_censored',
                               'acceptance_status': 'exploratory_not_T0_accepted'})
    return pd.DataFrame(result)


def prepare(snapshot: Path, news_snapshots: list[Path], output: Path):
    """Publish joined inputs, provenance and forward outcomes in distinct tables."""
    manifest, tables = verified_tables(snapshot)
    if manifest.get('scope_version') != scope.VERSION or len(manifest['price_coverage']) != 10 or not all(
            r['coverage_complete'] for r in manifest['price_coverage']):
        raise ValueError('complete_ten_stock_panel_required')
    prices = tables['prices'].to_pandas(use_threads=False)
    sessions = session_grid()
    # Recheck the rows, not just the producer's manifest claim.
    expected = {s for s, _ in sessions}
    if set(prices['ticker']) != set(scope.UNIVERSE) or prices.duplicated(['ticker', 'session']).any() or any(
            set(group['session']) != expected for _, group in prices.groupby('ticker')):
        raise ValueError('panel_session_mismatch')
    complete = {r['series_id'] for r in manifest['macro_coverage'] if r['complete']}
    macro = macro_panel(tables['macro'].to_pylist() if 'macro' in tables else [], sessions, complete)
    news, news_sources = [], []
    for path in news_snapshots:
        source_manifest, source_tables = verified_tables(path)
        news_sources.append(digest(json_bytes(source_manifest)))
        if 'news' in source_tables:
            news.extend(source_tables['news'].to_pylist())
    aligned = news_alignment(news, sessions)
    daily = news_daily(aligned, sessions)
    features = technical_frame(prices)
    # Explicit naming and one-to-one/many-to-one joins prevent silent row loss.
    features = features.merge(daily, on=['ticker', 'session'], how='left', validate='one_to_one')
    wide = macro.pivot(index='session', columns='series_id', values='value').add_prefix('macro_').reset_index()
    features = features.merge(wide, on='session', how='left', validate='many_to_one')
    for field, suffix in (('change_from_previous_period', '_change'), ('age_calendar_days', '_age_days')):
        selected = macro.pivot(index='session', columns='series_id', values=field).add_prefix('macro_').add_suffix(suffix).reset_index()
        features = features.merge(selected, on='session', how='left', validate='many_to_one')
    features['macro_yield_curve_10y_minus_2y_pp'] = features['macro_DGS10']-features['macro_DGS2']
    for series in ('CPIAUCSL', 'PAYEMS'):
        selected = macro[macro.series_id == series][['session', 'year_on_year_fraction']].rename(
            columns={'year_on_year_fraction': f'macro_{series}_yoy_fraction'})
        features = features.merge(selected, on='session', how='left', validate='many_to_one')
    features['cutoff'] = features['session'].map({s: t.isoformat() for s, t in sessions})
    features['timing_policy'] = 'provisional_conservative_reconstruction'
    outcomes = exploratory_outcomes(prices)
    metadata = {'created_at': datetime.now(timezone.utc).isoformat(), 'scope_version': scope.VERSION,
                'source_manifest_sha256': digest(json_bytes(manifest)), 'news_source_manifests': news_sources,
                'feature_version': 'panel-log-risk-macro-v1', 'rows': len(features),
                'macro_timing_policy': 'next_calendar_day_after_ALFRED_vintage_in_America_New_York',
                'news_timing_policy': 'first_session_close_at_or_after_named_time_proxy',
                'macro_complete_series': sorted(complete), 'horizons_sessions': scope.HORIZONS,
                'macro_quarantined_future_observation_records': sum(
                    r['observation_date'] > r['realtime_start'] for r in tables['macro'].to_pylist()) if 'macro' in tables else 0,
                'news_record_exclusions': dict(Counter(r['exclusion'] or 'aligned_candidate_or_unresolved' for r in aligned)),
                'news_unique_urls_or_article_ids': len({r['article_id'] for r in news}),
                'news_observations': len(news), 'pit_eligible': False,
                'sentiment_status': 'not_scored', 'model_status': 'not_trained',
                'acceptance_status': 'provisional_not_human_accepted',
                'feature_missing_counts': {c: int(features[c].isna().sum()) for c in features.columns}}
    output_tables = {'features': pa.Table.from_pandas(features, preserve_index=False, nthreads=1),
                     'macro_alignment': pa.Table.from_pandas(macro, preserve_index=False, nthreads=1),
                     'news_monthly_coverage': pa.Table.from_pandas(news_monthly(aligned,sessions), preserve_index=False,nthreads=1),
                     'exploratory_outcomes': pa.Table.from_pandas(outcomes, preserve_index=False, nthreads=1)}
    if aligned:
        output_tables['news_alignment'] = pa.Table.from_pylist(aligned)
    publish(output, output_tables, metadata)
    verified_tables(output)
    return metadata


def main():
    """Align only supplied verified local snapshots; this command has no network I/O."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--news-snapshot', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.snapshot, args.news_snapshot, args.output)
    print(f'Prepared {report["rows"]} retrospective rows; no model fitted, sentiment not scored.')


if __name__ == '__main__':
    main()
