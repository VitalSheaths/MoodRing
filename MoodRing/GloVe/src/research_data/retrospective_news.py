"""Align frozen eligible observations without scoring, merging or inferring times."""
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import timedelta
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .archive import digest, json_bytes, publish, read_bytes
from .point_in_time import utc

LAGS=(0,1,5)
CHANNELS=('direct','indirect_product','indirect_sector_competitor')
# Retained audit's documented gap, not a new assertion of exact outage endpoints.
# Use the full stated UTC dates conservatively and flag partially affected bins.
GAP_START='2025-06-15T00:00:00+00:00'
GAP_END_EXCLUSIVE='2025-07-02T00:00:00+00:00'


def align_observation(row, sessions, cutoffs=None):
    """Use this occurrence's observation time and first eligible prediction cutoff."""
    if row['status']!='eligible_for_observation_analysis' or row.get('review_hold_reasons'):
        raise ValueError('ineligible_news')
    stamp=utc(row['availability_at'])
    if stamp!=utc(row['gdelt_seen_at']):raise ValueError('invented_observation_time')
    if row.get('published_at') and utc(row['published_at'])>stamp:raise ValueError('news_timestamp_conflict')
    if not row.get('title') or row['sentiment_channel'] not in CHANNELS or row.get('language')!='en':
        raise ValueError('news_text_or_channel')
    index=bisect_left(cutoffs if cutoffs is not None else [t for _,t in sessions],stamp)
    fields=('ticker','source_record_sha256','source_manifest_sha256','source_manifest_row',
            'availability_at','gdelt_seen_at','published_at','ingested_at','sentiment_channel')
    result={k:row.get(k) for k in fields}
    result.update(exact_model_input_sha256=digest(row['title'].encode()),
        possible_cross_url_copy_of=row.get('possible_cross_url_copy_of'),
        session=sessions[index][0] if index<len(sessions) else None,
        cutoff=sessions[index][1].isoformat() if index<len(sessions) else None,
        exclusion='after_last_cutoff' if index==len(sessions) else None,
        time_basis='retained_observation_proxy_not_exact_historical_text',
        historical_prediction_eligible=False)
    return index,result


def align_news(source, output, sessions, universe):
    """Verify each eligible shard, retain mappings and emit daily coverage variants.

    Exact-text counts are a diagnostic weighting sensitivity, not article merges
    or a released sentiment aggregation policy. All sentiment fields remain null.
    """
    report=json.loads(read_bytes(source/'audit-report.json'))
    plan=json.loads(read_bytes(source/'frozen-plan.json'))
    if report['binding']!=plan['binding'] or not report['full_scoring_held']:
        raise ValueError('news_audit_binding')
    output.mkdir(parents=True,exist_ok=False)
    counts=Counter();text_sets=defaultdict(set);cross_counts=Counter()
    receipts=[];total=outside=0;channels=Counter()
    cutoffs=[t for _,t in sessions]
    expected={r['day']:r['sha256'] for r in report['curated_shards']}
    paths=sorted((source/'curated').glob('*/manifest.json'))
    if set(expected)!={p.parent.name for p in paths}:raise ValueError('news_shard_census')
    for n,path in enumerate(paths):
        raw=read_bytes(path);m=json.loads(raw)
        if digest(raw)!=expected[path.parent.name] or m['binding']!=plan['binding']:
            raise ValueError('news_manifest_changed')
        entry=m['files']['eligible_headlines.parquet']
        blob=read_bytes(path.parent/'eligible_headlines.parquet')
        if digest(blob)!=entry['sha256'] or len(blob)!=entry['bytes']:
            raise ValueError('news_checksum')
        table=pq.ParquetFile(pa.BufferReader(blob)).read(use_threads=False)
        if table.num_rows!=entry['rows']:raise ValueError('news_row_count')
        aligned=[]
        for row in table.to_pylist():
            if row['ticker'] not in universe:raise ValueError('unknown_news_company')
            index,item=align_observation(row,sessions,cutoffs)
            aligned.append(item);total+=1;channels[row['sentiment_channel']]+=1
            if index==len(sessions):outside+=1;continue
            key=(row['ticker'],index,row['sentiment_channel'])
            counts[key]+=1;text_sets[key].add(item['exact_model_input_sha256'])
            cross_counts[key]+=bool(item['possible_cross_url_copy_of'])
        if aligned:
            publish(output/'alignment'/path.parent.name,{'observations':pa.Table.from_pylist(aligned)},
                    dict(source_audit_binding=plan['binding'],source_day_manifest_sha256=digest(raw),
                         scoring_held=True,historical_prediction_eligible=False))
        receipts.append(dict(day=path.parent.name,sha256=digest(raw),rows=table.num_rows))
        if (n+1)%500==0:print(f'Aligned {n+1}/{len(paths)} news days; {total} observations',flush=True)
    if total!=report['eligible_after_dedup']:raise ValueError('news_count_reconciliation')
    coverage=[];gap_bins=[]
    for i,(_,end) in enumerate(sessions):
        start=sessions[i-1][1] if i else end.replace(hour=0,minute=0,second=0)
        if start<utc(GAP_END_EXCLUSIVE) and end>=utc(GAP_START):gap_bins.append(i)
    for lag in LAGS:
        for ticker in universe:
            for i,(session,_) in enumerate(sessions):
                origin=i-lag
                row=dict(ticker=ticker,session=session,extra_session_lag=lag,
                    known_source_gap_window=origin in gap_bins,
                    historical_prediction_eligible=False,news_archive_complete=False)
                for channel in CHANNELS:
                    key=(ticker,origin,channel)
                    row[channel+'_observation_count']=counts[key] if origin>=0 else 0
                    row[channel+'_exact_text_count']=len(text_sets.get(key,())) if origin>=0 else 0
                    row[channel+'_cross_url_link_count']=cross_counts[key] if origin>=0 else 0
                    row[channel+'_sentiment']=None
                row['observed_headline_count']=sum(row[c+'_observation_count'] for c in CHANNELS)
                row['sentiment_status']='not_scored' if row['observed_headline_count'] else 'missing_no_observed_eligible_news'
                row['news_lag_warmup_missing']=origin<0
                coverage.append(row)
    frame=pd.DataFrame(coverage)
    meta=dict(source_audit_binding=plan['binding'],source_audit_sha256=digest(read_bytes(source/'audit-report.json')),
        eligible_observations=total,aligned_observations=total-outside,after_last_cutoff=outside,
        source_day_shards=len(receipts),channels=dict(channels),timestamps_modified=0,merges_applied=0,
        exact_text_weighting_status='diagnostic_counts_only_not_released',scoring_held=True,
        known_gap_policy=dict(start=GAP_START,end_exclusive=GAP_END_EXCLUSIVE,
            basis='conservative whole-day envelope of retained June15–July1 source-gap report; not newly evidenced exact endpoints',
            partial_bin_sessions=[sessions[i][0] for i in gap_bins]),
        covered_rows_by_lag={str(lag):int(g.observed_headline_count.gt(0).sum()) for lag,g in frame.groupby('extra_session_lag')},
        all_stock_empty_sessions=frame[(frame.extra_session_lag==0)].groupby('session').observed_headline_count.sum().loc[lambda s:s==0].index.tolist())
    (output/'source-receipts.json').write_bytes(json_bytes(receipts))
    (output/'report.json').write_bytes(json_bytes(meta))
    return frame,meta
