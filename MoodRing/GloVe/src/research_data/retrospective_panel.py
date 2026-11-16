"""Separate frozen vendor-adjusted research data; never a strict PIT adapter.

Local verified snapshots only. No inference, fitting, preprocessing or acquisition.
Features and future labels are published in separate immutable tables.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

from .archive import digest, json_bytes, publish, read_bytes, verified_tables
from .alignment import macro_panel, session_grid
from .prepare import technical_frame
from .research_experiment import SPLITS, split_for
from .point_in_time import utc
from . import panel_scope

VERSION = 'retrospective-vendor-adjusted-rv5-v1'
MACRO_LAGS = (0, 1, 5, 20)
CONTRACT = {
    'version': VERSION, 'strict_pit': False,
    'return': 'log(frozen_adj_close[t]/frozen_adj_close[t-1])',
    'target': '252/5 * sum(next_five_log_returns**2)',
    'target_quantity': 'annualized_variance', 'reporting': 'sqrt variance as volatility',
    'label_horizon': 5, 'context_sessions': 8, 'benchmark_sessions': 20,
    'feature_prices': 'retained OHLC * same-row Adj Close / Close',
    'volume': 'retained provider split basis; never dividend-rescaled',
    'cutoff': 'actual exchange close plus 20 minutes',
    'macro': 'next New York day after vintage date; assumed, not evidenced receipt',
    'macro_extra_session_lags': MACRO_LAGS,
    'adjustment_residual_review_threshold_bp': 1.0,
    'threshold_meaning': 'Numerical reconciliation flag, not a test of historical completeness',
    'extremes': 'retained without clipping or return-magnitude exclusions',
    'splits': SPLITS, 'scoring_held': True, 'training_held': True,
}


def cutoff_grid(start, end):
    """Preserve exchange holidays, early closes and daylight saving changes."""
    return [(s, close + timedelta(minutes=20)) for s, close in session_grid(start, end)]


def adjusted_inputs(records, sessions, universe):
    """Validate all expected prices, retain provenance and apply one row factor.

    Dividend/split fields are candidates used for consistency diagnostics only.
    They do not become an independently verified action ledger.
    """
    frame = pd.DataFrame(records).sort_values(['ticker', 'session']).reset_index(drop=True)
    expected = [s for s, _ in sessions]
    if set(frame.ticker) != set(universe) or frame.duplicated(['ticker', 'session']).any():
        raise ValueError('price_identity_or_duplicate')
    for _, group in frame.groupby('ticker'):
        if group.session.tolist() != expected:
            raise ValueError('price_session_gap')
    basis = 'provider_split_adjusted_ohlc_separate_dividend_adjusted_close'
    if set(frame.adjustment) != {basis}:
        raise ValueError('mixed_or_unknown_price_basis')
    fields = ['open', 'high', 'low', 'close', 'adj_close', 'volume', 'dividends', 'stock_splits']
    values = frame[fields].astype(float)
    if not np.isfinite(values.to_numpy()).all() or (values[['open','high','low','close','adj_close']] <= 0).any().any():
        raise ValueError('invalid_price')
    if (values[['volume','dividends','stock_splits']] < 0).any().any():
        raise ValueError('invalid_volume_or_action')
    if ((values.low > values[['open','close']].min(axis=1)) |
            (values.high < values[['open','close']].max(axis=1))).any():
        raise ValueError('invalid_ohlc_order')
    adjusted = frame.copy()
    factor = values.adj_close / values.close
    for name in ('open', 'high', 'low', 'close'):
        adjusted[name] = values[name] * factor
    # Avoid a rounding round-trip in the primary return series.
    adjusted['close'] = values.adj_close
    adjusted['volume'] = values.volume
    audit = []
    for ticker, group in frame.groupby('ticker', sort=True):
        previous = None
        for row in group.to_dict('records'):
            c, a, d = (float(row[k]) for k in ('close', 'adj_close', 'dividends'))
            residual = None
            if previous:
                p = float(previous['close'])
                if d >= p:
                    raise ValueError('invalid_dividend_multiplier')
                vendor = a / float(previous['adj_close']) - 1
                multiplier = c / (p-d) - 1
                residual = 10000 * (vendor-multiplier)
            audit.append(dict(ticker=ticker, session=row['session'],
                source_record_sha256=digest(json_bytes(row)), source_received_at=row['ingested_at'],
                adjustment_factor=a/c, provider_dividend_candidate=d,
                provider_split_candidate=float(row['stock_splits']),
                multiplier_residual_bp=residual,
                adjustment_review_required=residual is not None and abs(residual) > 1.0,
                historical_prediction_eligible=False))
            previous = row
    return adjusted, pd.DataFrame(audit)


def price_features(adjusted, audit, sessions):
    """Reuse trailing indicators on the declared adjusted basis, without fitting."""
    features = technical_frame(adjusted)
    numeric = [c for c in features if c not in ('ticker','session','mode','historical_prediction_eligible')]
    features['historical_variance_20'] = features.groupby('ticker', sort=False)['log_return_1'].transform(
        lambda x: x.pow(2).rolling(20, min_periods=20).mean()*252)
    numeric.append('historical_variance_20')
    if np.isinf(features[numeric].to_numpy(dtype=float)).any():
        raise ValueError('infinite_feature')
    features['feature_complete'] = features[numeric].notna().all(axis=1)
    features['eight_session_features_complete'] = features.groupby('ticker', sort=False)['feature_complete'].transform(
        lambda x: x.astype(int).rolling(8, min_periods=8).sum().eq(8))
    features = features.merge(audit[['ticker','session','adjustment_review_required']],
                              on=['ticker','session'], validate='one_to_one')
    # Recursive EMA features retain earlier inputs. Fail readiness after a flagged
    # source interval until a documented repair/reseed policy exists.
    features['price_history_review_required'] = features.groupby('ticker')['adjustment_review_required'].cummax()
    features['cutoff'] = features.session.map({s:t.isoformat() for s,t in sessions})
    features['mode'] = VERSION
    features['price_basis'] = CONTRACT['feature_prices']
    features['historical_prediction_eligible'] = False
    return features, numeric


def labels(records, sessions, label_asof):
    """Build retrospective outcomes separately; purge boundary/censored horizons.

    Receipt by label_asof controls maturity. Future outcome receipt is never
    compared to the earlier prediction cutoff. No strict-PIT flag is granted.
    """
    label_asof = utc(label_asof)
    cutoffs = dict(sessions)
    closes = {s:t-timedelta(minutes=20) for s,t in sessions}
    result = []
    frame = pd.DataFrame(records).sort_values(['ticker','session'])
    for ticker, group in frame.groupby('ticker', sort=True):
        rows = group.to_dict('records')
        for i, anchor in enumerate(rows):
            window = rows[i:i+6]
            row = dict(ticker=ticker, session=anchor['session'], cutoff=cutoffs[anchor['session']].isoformat(),
                target_version=VERSION, label_asof=label_asof.isoformat(), label_end_session=None,
                label_received_at=None, annualized_variance=None, annualized_volatility=None,
                split=None, status='right_censored', source_record_sha256=[], historical_prediction_eligible=False)
            if len(window)==6:
                end=window[-1]['session']
                row['label_end_session']=end
                split=split_for(cutoffs[anchor['session']],closes[end])
                received=max(utc(r['ingested_at']) for r in window)
                row['label_received_at']=received.isoformat()
                row['source_record_sha256']=[digest(json_bytes(r)) for r in window]
                if any(utc(r['ingested_at'])<closes[r['session']] for r in window):
                    raise ValueError('source_received_before_session_close')
                if split is None:
                    row['status']='crosses_split_boundary'
                elif received>label_asof or closes[end]>label_asof:
                    row['status']='not_mature_by_label_asof'
                else:
                    prices=[float(r['adj_close']) for r in window]
                    if any(not math.isfinite(x) or x<=0 for x in prices):
                        raise ValueError('invalid_label_price')
                    variance=252/5*sum(math.log(b/a)**2 for a,b in zip(prices,prices[1:]))
                    row.update(status='mature_retrospective',split=split,
                               annualized_variance=variance,annualized_volatility=math.sqrt(variance))
            result.append(row)
    return pd.DataFrame(result)


def macro_variants(rows, sessions, complete_series):
    """Rebuild vintage-aware values under declared lag assumptions, never receipts.

    Delaying query cutoffs by N sessions defers every vintage N eligible sessions.
    Prior-period and year-ago values are recomputed by the existing vintage join.
    The source manifest binds all revisions, including the operands of changes.
    """
    extended = cutoff_grid(panel_scope.MACRO_START, sessions[-1][0])
    positions = {s:i for i,(s,_) in enumerate(extended)}
    results=[]
    for lag in MACRO_LAGS:
        queries=[(s,extended[positions[s]-lag][1]) for s,_ in sessions]
        selected=macro_panel(rows,queries,complete_series)
        selected['query_cutoff']=selected['cutoff']
        selected['cutoff']=selected.session.map({s:t.isoformat() for s,t in sessions})
        selected['extra_session_lag']=lag
        for r in selected.to_dict('records'):
            if r['policy_available_after'] and utc(r['policy_available_after'])>utc(r['query_cutoff']):
                raise ValueError('macro_future_proxy')
        results.append(selected)
    return pd.concat(results,ignore_index=True)


def join_macro(features, macro):
    """One shared macro row per session/series; never multiply stock rows."""
    out=features.copy()
    columns=[]
    for field,suffix in (('value',''),('change_from_previous_period','_change'),
                         ('age_calendar_days','_age_days')):
        wide=macro.pivot(index='session',columns='series_id',values=field).add_prefix('macro_').add_suffix(suffix)
        columns.extend(wide.columns)
        out=out.merge(wide.reset_index(),on='session',validate='many_to_one')
    for series in ('CPIAUCSL','PAYEMS'):
        col=f'macro_{series}_yoy_fraction';columns.append(col)
        selected=macro[macro.series_id==series][['session','year_on_year_fraction']].rename(columns={'year_on_year_fraction':col})
        out=out.merge(selected,on='session',validate='many_to_one')
    col='macro_yield_curve_10y_minus_2y_pp';columns.append(col)
    out[col]=out['macro_DGS10']-out['macro_DGS2']
    if np.isinf(out[columns].to_numpy(dtype=float)).any():raise ValueError('infinite_macro')
    return out,columns


def build(snapshot, news_audit, output, acceptance, label_asof):
    """Create a fresh local dataset and audit; downstream release always stays held."""
    accepted=json.loads(read_bytes(acceptance))
    if accepted.get('retrospective_scope_approved') is not True or accepted.get('vendor_adjusted_log_returns_approved') is not True:
        raise ValueError('explicit_scope_and_return_basis_acceptance_required')
    if output.exists():raise ValueError('fresh_output_required')
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('output_symlink')
    output.mkdir(parents=True,mode=0o700)
    manifest,tables=verified_tables(snapshot)
    source_manifest=digest(read_bytes(snapshot/'manifest.json'))
    if manifest.get('scope_version')!=panel_scope.VERSION:raise ValueError('snapshot_scope')
    sessions=cutoff_grid(panel_scope.START,panel_scope.END)
    records=tables['prices'].to_pylist()
    adjusted,price_audit=adjusted_inputs(records,sessions,panel_scope.UNIVERSE)
    features,technical_columns=price_features(adjusted,price_audit,sessions)
    outcomes=labels(records,sessions,label_asof)
    # Future label quality is assessed over its own complete six-record horizon;
    # an issue after prediction must not be hidden by feature-only readiness.
    quality={(r.ticker,r.session):bool(r.adjustment_review_required) for r in price_audit.itertuples()}
    indices={s:i for i,(s,_) in enumerate(sessions)}
    outcomes['adjustment_review_required']=[any(quality[(r.ticker,s)] for s,_ in
        sessions[indices[r.session]:indices[r.session]+6]) for r in outcomes.itertuples()]
    complete={r['series_id'] for r in manifest['macro_coverage'] if r['complete']}
    macro=macro_variants(tables['macro'].to_pylist(),sessions,complete)
    from .retrospective_news import align_news
    news,news_report=align_news(news_audit,output/'news',sessions,panel_scope.UNIVERSE)
    features,macro_columns=join_macro(features,macro[macro.extra_session_lag==0])
    features=features.merge(news[news.extra_session_lag==0].drop(columns=['extra_session_lag','historical_prediction_eligible']),
                            on=['ticker','session'],validate='one_to_one')
    if len(features)!=len(records) or len(outcomes)!=len(records):raise ValueError('panel_row_loss')
    if 'historical_prediction_eligible' not in features or features.historical_prediction_eligible.any():
        raise ValueError('retrospective_PIT_flag_missing_or_true')
    if any(c.endswith(('_x','_y')) for c in features.columns):raise ValueError('ambiguous_join_columns')
    if any(name in features for name in ('annualized_variance','annualized_volatility','label_end_session')):
        raise ValueError('label_in_features')
    population=features[['ticker','session','eight_session_features_complete','price_history_review_required',
                         'known_source_gap_window','observed_headline_count']].merge(
        outcomes[['ticker','session','status','split','adjustment_review_required']],on=['ticker','session'],validate='one_to_one')
    population['price_target_candidate']=(population.eight_session_features_complete &
        ~population.price_history_review_required & ~population.adjustment_review_required &
        population.status.eq('mature_retrospective'))
    # Candidates are shared across all arms; no sentiment-dependent row selection
    # occurs before scoring quality and missingness policies have been released.
    population['matched_comparison_released']=False
    population['historical_prediction_eligible']=False
    population['no_gap_news_observed_candidate']=(population.price_target_candidate &
        ~population.known_source_gap_window & population.observed_headline_count.gt(0))
    coverage=[]
    for (ticker,year),idx in population.groupby(['ticker',population.session.str[:4]]).groups.items():
        g=population.loc[idx]
        coverage.append(dict(ticker=ticker,year=year,rows=len(g),
            price_target_candidates=int(g.price_target_candidate.sum()),
            no_gap_news_observed_candidates=int(g.no_gap_news_observed_candidate.sum()),
            missing_news_rows=int(g.observed_headline_count.eq(0).sum()),
            known_gap_rows=int(g.known_source_gap_window.sum()),
            label_exclusions=dict(Counter(g.status))))
    names=('retrospective_panel.py','retrospective_news.py','prepare.py','alignment.py',
           'research_experiment.py','point_in_time.py','archive.py','panel_scope.py')
    code={n:digest(read_bytes(Path(__file__).with_name(n))) for n in names}
    code['features/technical/macd.py']=digest(read_bytes(Path(__file__).parents[1]/'features/technical/macd.py'))
    metadata=dict(version=VERSION,contract=CONTRACT,code=code,acceptance_sha256=digest(read_bytes(acceptance)),
        source_manifest_sha256=source_manifest,source_price_sha256=manifest['files']['prices.parquet']['sha256'],
        source_macro_sha256=manifest['files']['macro.parquet']['sha256'],
        news_audit_sha256=news_report['source_audit_sha256'],label_asof=label_asof,
        feature_columns=technical_columns+macro_columns,
        coverage_columns_not_sentiment=['observed_headline_count','known_source_gap_window'],
        full_scoring_held=True,historical_training_held=True,strict_PIT_eligible=False,
        preprocessing_fitted=False,model_fitted=False,provider_calls=0,cloud_calls=0)
    output_tables={'features':features,'labels':outcomes,'price_provenance':price_audit,
                   'macro_alignment':macro,'news_coverage':news,'population':population}
    publish(output/'dataset',{k:pa.Table.from_pandas(v,preserve_index=False,nthreads=1) for k,v in output_tables.items()},metadata)
    verified_tables(output/'dataset')
    # Recheck source manifests after publication; original sources are read only.
    if digest(read_bytes(snapshot/'manifest.json'))!=source_manifest:raise ValueError('source_changed')
    report=dict(version=VERSION,source_manifest_sha256=source_manifest,rows=len(features),
        price_target_candidates=int(population.price_target_candidate.sum()),
        no_gap_news_observed_candidates=int(population.no_gap_news_observed_candidate.sum()),
        label_status_counts=dict(Counter(outcomes.status)),
        label_split_counts=dict(Counter(outcomes['split'].dropna())),
        price_adjustment_review_rows=int(price_audit.adjustment_review_required.sum()),
        price_adjustment_max_residual_bp=float(price_audit.multiplier_residual_bp.abs().max()),
        candidate_split_events=int(price_audit.provider_split_candidate.gt(0).sum()),
        candidate_dividend_events=int(price_audit.provider_dividend_candidate.gt(0).sum()),
        feature_missing_counts={c:int(features[c].isna().sum()) for c in technical_columns+macro_columns},
        macro_missing_by_lag={str(lag):int(g.value.isna().sum()) for lag,g in macro.groupby('extra_session_lag')},
        coverage_by_stock_year=coverage,news=news_report,
        structural_checks_passed=True,adjustment_numeric_check_passed=not price_audit.adjustment_review_required.any(),
        ready_for_scoring=False,ready_for_training=False,strict_PIT_rows=0,
        unresolved=['News relevance/duplicate weighting release and scoring benchmark remain pending',
                    'Macro receipt and headline version timing are retrospective assumptions',
                    'Coverage/missingness policy and matched sentiment population not released'],
        full_scoring_held=True,historical_training_held=True,provider_calls=0,cloud_calls=0)
    (output/'quality-report.json').write_bytes(json_bytes(report))
    (output/'release-gate.json').write_bytes(json_bytes(dict(full_scoring_held=True,historical_training_held=True,
        strict_PIT_rows=0,retrospective_scope_accepted=True,return_basis_accepted=True,
        ready_for_scoring=False,ready_for_training=False,reasons=report['unresolved'])))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ('snapshot','news-audit','output','acceptance'):
        parser.add_argument('--'+arg,type=Path,required=True)
    parser.add_argument('--label-asof',required=True)
    args=parser.parse_args()
    result=build(args.snapshot,args.news_audit,args.output,args.acceptance,args.label_asof)
    print(json.dumps({k:result[k] for k in ('rows','price_target_candidates','label_status_counts',
        'price_adjustment_review_rows','ready_for_scoring','ready_for_training')},indent=2))
