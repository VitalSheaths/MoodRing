"""Development-only weighting/context policy; no model or provider calls."""
from collections import Counter, defaultdict
from datetime import datetime
import heapq
import json
import math
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .archive import digest, json_bytes, publish, read_bytes
from .point_in_time import utc
from .retrospective_panel import cutoff_grid
from .retrospective_news import CHANNELS

START,END='2023-01-01','2025-01-01'
POLICY={
    'version':'dev-sentiment-weighting-v1','development_start':START,'development_end_exclusive':END,
    'daily_unit':'exact model-input text within company/session/channel',
    'observation_weight':'1 / occurrences of this exact input within the daily unit',
    'daily_mean':'mean of successfully scored distinct exact inputs; null if none',
    'context_sessions':8,'context_mean':'equal mean of nonmissing daily means; never fill missing days with neutral',
    'sensitivities':['raw observation weighted daily mean','unique exact inputs across the trailing eight sessions'],
    'article_identity':'unchanged; no cross-URL merges or earlier timestamp borrowing',
    'cache_key':'exact UTF-8 text hash + model/tokenizer hashes + revision + no-truncation inference contract',
    'primary_rows':'price_target_candidate and all eight context sessions within development and no known source-gap bin',
    'channel_missingness':'retain null and explicit observed/scored/token-limit/absent counts; no forward fill',
    'model_boundary':'future train-fitted imputation only, with explicit masks; stored missing values remain null',
    'all_missing_training_channel':'omit from all matched arms and report; never invent neutral training values',
    'arms':['price_macro_technical','baseline_plus_coverage_controls','coverage_baseline_plus_direct',
            'coverage_baseline_plus_indirect','coverage_baseline_plus_both'],
    'coverage_controls':'identical observed counts and channel masks in coverage-controlled arms; no probabilities or tone',
    'selection':'same row IDs for every arm; no selection by scores, signs, targets or model performance',
    'indirect_channels':'product and sector/competitor remain separate columns',
    'final_test':'not opened for text, scores, targets or policy tuning',
    'full_scoring_held':True,'training_held':True,'strict_PIT_eligible':False,
}


def verified_table(path, name, columns=None):
    """Read only the requested table/columns from checksum-verified bytes."""
    m=json.loads(read_bytes(path/'manifest.json'));spec=m['files'][name+'.parquet']
    blob=read_bytes(path/(name+'.parquet'))
    if digest(blob)!=spec['sha256'] or len(blob)!=spec['bytes']:raise ValueError('table_checksum')
    table=pq.ParquetFile(pa.BufferReader(blob)).read(columns=columns,use_threads=False)
    if table.num_rows!=spec['rows']:raise ValueError('table_count')
    return m,table


def dev(value):
    """Require timezone-bearing observation dates inside development."""
    value=utc(value)
    return utc(START+'T00:00:00Z')<=value<utc(END+'T00:00:00Z')


def key(row):
    return row['ticker'],row['session'],row['sentiment_channel'],row['exact_model_input_sha256']


def daily_summary(rows, predictions):
    """Aggregate as-of rows without changing article identity or missingness.

    Predictions map exact text hashes to validated scalar tone, or None for a
    missing/token-limited result. One bucket only; no future arrivals are allowed.
    """
    if not rows:return dict(mean=None,raw_mean=None,unique_inputs=0,scored_inputs=0,missing_inputs=0)
    if len({(r['ticker'],r['session'],r['sentiment_channel']) for r in rows})!=1:
        raise ValueError('mixed_daily_bucket')
    if any(utc(r['availability_at'])>utc(r['cutoff']) for r in rows):raise ValueError('future_news')
    counts=Counter(r['exact_model_input_sha256'] for r in rows)
    valid={}
    for h in counts:
        score=predictions.get(h)
        if score is not None:
            if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not -1<=score<=1:
                raise ValueError('invalid_tone')
            valid[h]=score
    return dict(mean=sum(valid.values())/len(valid) if valid else None,
        raw_mean=sum(valid[h]*counts[h] for h in valid)/sum(counts[h] for h in valid) if valid else None,
        unique_inputs=len(counts),scored_inputs=len(valid),missing_inputs=len(counts)-len(valid))


def context_summary(days):
    """Keep missing calendar slots visible; do not collapse eight sessions into eight stories."""
    if len(days)!=8:raise ValueError('eight_context_slots_required')
    values=[r['mean'] for r in days if r['mean'] is not None]
    return dict(mean=sum(values)/len(values) if values else None,
                observed_days=len(values),missing_days=8-len(values),fully_observed=len(values)==8)


class Reservoir:
    """Bounded deterministic distinct-input sample, independent of text sentiment."""
    def __init__(self,limit):self.limit=limit;self.items={};self.heap=[]
    def add(self,item,rank):
        h=item['exact_model_input_sha256']
        if h in self.items:return
        entry=(rank,h)
        if len(self.heap)<self.limit:
            heapq.heappush(self.heap,entry);self.items[h]=item
        elif entry>self.heap[0]:
            _,old=heapq.heapreplace(self.heap,entry);del self.items[old];self.items[h]=item


def pairs(build,audit,receipts):
    """Read development day shards only, resolving every item to its retained source."""
    for path in sorted((build/'news/alignment').glob('*/manifest.json')):
        day=path.parent.name
        if not START<=day<END:continue
        raw=read_bytes(path)
        rel='build-v2/news/alignment/'+day+'/manifest.json'
        if digest(raw)!=receipts[rel]:raise ValueError('alignment_manifest_changed')
        m,t=verified_table(path.parent,'observations')
        source=audit/'curated'/day
        if digest(read_bytes(source/'manifest.json'))!=m['source_day_manifest_sha256']:
            raise ValueError('original_manifest_changed')
        _,original=verified_table(source,'eligible_headlines')
        rows=t.to_pylist();old=original.to_pylist()
        if len(rows)!=len(old):raise ValueError('alignment_count')
        retained=[];boundary=0
        for row,base in zip(rows,old):
            if not dev(row['availability_at']):raise ValueError('nondev_observation_in_dev_day')
            if not row['session'] or not START<=row['session']<END:boundary+=1;continue
            if not dev(row['cutoff']):raise ValueError('nondev_cutoff')
            for field in ('ticker','source_record_sha256','source_manifest_sha256','source_manifest_row','availability_at'):
                if row[field]!=base[field]:raise ValueError('source_mismatch')
            if base['status']!='eligible_for_observation_analysis' or base.get('review_hold_reasons'):
                raise ValueError('held_or_ineligible_source')
            if digest(base['title'].encode())!=row['exact_model_input_sha256']:raise ValueError('text_mismatch')
            retained.append((row,base['title']))
        yield day,retained,boundary


def prepare(build,audit,alignment_receipts,output):
    """Freeze dev weighting/matched rows and a bounded benchmark packet, not scores."""
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('output_symlink')
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    receipts=json.loads(read_bytes(alignment_receipts))
    policy_hash=digest(json_bytes(POLICY))
    (output/'policy.json').write_bytes(json_bytes(POLICY))
    counts=Counter();daily_sets=defaultdict(set);seen_inputs=set();strata={}
    reservoir=Reservoir(512);longest=Reservoir(32);total=boundary=days=0
    for day,rows,excluded in pairs(build,audit,receipts):
        days+=1;boundary+=excluded
        for row,title in rows:
            k=key(row);counts[k]+=1;daily_sets[k[:3]].add(k[3]);seen_inputs.add(k[3]);total+=1
            if total>1000000:raise ValueError('development_observation_bound')
            item=dict(row,title=title,source_day=day)
            stratum=(row['ticker'],row['session'][:4],row['sentiment_channel'])
            bucket=strata.setdefault(stratum,Reservoir(8))
            rank=-int(k[3],16);bucket.add(item,rank);reservoir.add(item,rank)
            longest.add(item,(len(title),rank))
    # Fixed strata/hash sample plus long-input stress cases, never chosen by scores.
    sample={}
    for stratum in sorted(strata):
        for h,item in sorted(strata[stratum].items.items()):sample.setdefault(h,dict(item,selection='stratified_hash'))
    for h,item in sorted(reservoir.items.items()):
        if len(sample)>=480:break
        sample.setdefault(h,dict(item,selection='hash_fill'))
    for h,item in longest.items.items():sample.setdefault(h,dict(item,selection='long_input_stress'))
    for h,item in sorted(reservoir.items.items()):
        if len(sample)>=512:break
        sample.setdefault(h,dict(item,selection='hash_fill'))
    if len(sample)!=512:raise ValueError('benchmark_sample_bound')
    weighted_count=0;weight_sums=defaultdict(float)
    for day,rows,_ in pairs(build,audit,receipts):
        weighted=[]
        for row,_ in rows:
            k=key(row);n=counts[k];record=dict(row,daily_input_occurrences=n,
                daily_input_weight=1/n,weighting_policy_sha256=policy_hash)
            weighted.append(record);weighted_count+=1;weight_sums[k]+=1/n
        if weighted:
            publish(output/'weights'/day,{'observations':pa.Table.from_pylist(weighted)},
                    dict(policy_sha256=policy_hash,split='development',article_merges=0,scoring_held=True))
    if total!=weighted_count or any(abs(v-1)>1e-10 for v in weight_sums.values()):raise ValueError('weight_reconciliation')
    # Read only population metadata columns; labels/features/target values remain unopened.
    dm,pop=verified_table(build/'dataset','population',columns=['ticker','session','price_target_candidate','known_source_gap_window'])
    pop=pop.filter(pc.and_(pc.greater_equal(pop['session'],START),pc.less(pop['session'],END)))
    dev_rows=pop.to_pylist();grid=cutoff_grid(START,'2024-12-31');positions={s:i for i,(s,_) in enumerate(grid)}
    by_key={(r['ticker'],r['session']):r for r in dev_rows}
    context=[];matched=[]
    for row in dev_rows:
        ticker,s=row['ticker'],row['session'];i=positions[s]
        window=[d for d,_ in grid[max(0,i-7):i+1]]
        complete=len(window)==8
        gap=any(by_key[(ticker,d)]['known_source_gap_window'] for d in window)
        record=dict(ticker=ticker,session=s,price_target_candidate=row['price_target_candidate'],
            dev_context_complete=complete,known_gap_in_context=gap,
            primary_matched=bool(row['price_target_candidate'] and complete and not gap))
        for channel in CHANNELS:
            sets=[daily_sets[(ticker,d,channel)] for d in window]
            record[channel+'_observed_days']=sum(bool(x) for x in sets)
            record[channel+'_missing_days']=8-sum(bool(x) for x in sets)
            record[channel+'_daily_unique_votes']=sum(len(x) for x in sets)
            record[channel+'_context_unique_inputs']=len(set().union(*sets))
            record[channel+'_mean']=None
        any_days=sum(any(daily_sets[(ticker,d,c)] for c in CHANNELS) for d in window)
        record['sensitivity_any_news_all_eight_days']=record['primary_matched'] and any_days==8
        record['sensitivity_direct_all_eight_days']=record['primary_matched'] and record['direct_observed_days']==8
        record['sensitivity_direct_and_indirect_all_eight_days']=record['sensitivity_direct_all_eight_days'] and all(
            bool(daily_sets[(ticker,d,'indirect_product')] or daily_sets[(ticker,d,'indirect_sector_competitor')]) for d in window)
        record['historical_prediction_eligible']=False;context.append(record)
        if record['primary_matched']:
            matched.append(dict(ticker=ticker,session=s,row_id=digest(json_bytes([ticker,s])),
                                arms=POLICY['arms'],split='dev',released_for_training=False))
    packet=dict(split='development',policy_sha256=policy_hash,items=list(sample.values()),
        inference_budget=dict(max_unique_inputs=512,max_total_scored_occurrences=1600,rounds=3,warmup_inputs=16),
        selection='Up to eight hash-selected inputs per ticker/year/channel, hash fill and longest-input stress; diagnostic, not population accuracy',
        original_records_changed=False,full_scoring_held=True,training_held=True)
    (output/'benchmark-input.json').write_bytes(json_bytes(packet))
    publish(output/'development',{'context':pa.Table.from_pylist(context),'matched_rows':pa.Table.from_pylist(matched)},
            dict(policy_sha256=policy_hash,source_dataset_manifest_sha256=digest(read_bytes(build/'dataset/manifest.json')),
                 split='development',target_values_read=False,scoring_held=True,training_held=True))
    summary=dict(policy_sha256=policy_hash,dev_day_shards=days,dev_observations=total,
        boundary_observations_excluded=boundary,unique_dev_model_inputs=len(seen_inputs),
        company_session_channel_input_groups=len(counts),extra_observation_votes_capped=total-len(counts),
        group_weights_sum_to_one=True,article_merges=0,timestamps_changed=0,
        input_population_rows=len(dev_rows),price_target_candidates=sum(r['price_target_candidate'] for r in dev_rows),
        primary_matched_rows=len(matched),matched_rows_sha256=digest(json_bytes(matched)),
        same_rows_all_arms=True,train_test_headline_shards_read=0,target_values_read=False,
        context_boundary_candidates_excluded=sum(r['price_target_candidate'] and not r['dev_context_complete'] for r in context),
        sensitivity_rows={k:sum(r[k] for r in context) for k in ('sensitivity_any_news_all_eight_days',
            'sensitivity_direct_all_eight_days','sensitivity_direct_and_indirect_all_eight_days')},
        no_observed_context_by_channel={c:sum(r['primary_matched'] and r[c+'_observed_days']==0 for r in context) for c in CHANNELS},
        benchmark_sample_rows=len(sample),sample_by_channel=dict(Counter(r['sentiment_channel'] for r in sample.values())),
        sample_by_ticker=dict(Counter(r['ticker'] for r in sample.values())),sample_by_year=dict(Counter(r['session'][:4] for r in sample.values())),
        source_dataset_manifest_sha256=digest(read_bytes(build/'dataset/manifest.json')),
        source_news_audit_sha256=digest(read_bytes(audit/'audit-report.json')),
        alignment_receipts_sha256=digest(read_bytes(alignment_receipts)),
        code_sha256=digest(read_bytes(Path(__file__))),full_scoring_held=True,training_held=True,
        notes=['Operational policy frozen without predictive performance tuning.',
               'Development first seven sessions lack an entirely development context and are excluded from this pilot.',
               'Train/test application is deferred; no final-test scores, target values or text were opened.',
               'Observed coverage is not archive completeness; relevance accuracy remains unestablished.'])
    (output/'report.json').write_bytes(json_bytes(summary))
    return summary


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('build','audit','alignment-receipts','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=prepare(a.build,a.audit,a.alignment_receipts,a.output)
    print(json.dumps(r,indent=2))
