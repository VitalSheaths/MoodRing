"""Mechanical, offline application of frozen dev weights and token eligibility.

Reads held-out text only for hashing/tokenization; never runs a classifier, tunes
rules, merges articles, rewrites observation times or publishes panel sentiment.
"""
from collections import Counter, defaultdict
import json
import os
from pathlib import Path

import pyarrow as pa

from .archive import digest, json_bytes, publish, read_bytes
from .sentiment_policy import POLICY, key, verified_table
from .research_experiment import SPLITS
from .point_in_time import utc


def partition(session):
    """Assign by existing calendar boundaries, including rows with purged labels."""
    if session:
        for name, start, end in SPLITS:
            if start <= session[:10] < end:
                return name
    return 'outside_panel'


def input_state(title, length, benchmark_available=False):
    """Keep missing text, token rejection, cached benchmark and unscored distinct."""
    if not isinstance(title, str) or not title.strip():
        return 'missing_text'
    if length > 512:
        return 'overlength'
    return 'benchmark_available' if benchmark_available else 'unscored'


def source_pairs(build, audit, receipts):
    """Resolve every observation to checksum-verified, hold-filtered source bytes."""
    for path in sorted((build/'news/alignment').glob('*/manifest.json')):
        day = path.parent.name
        if digest(read_bytes(path)) != receipts['build-v2/news/alignment/'+day+'/manifest.json']:
            raise ValueError('alignment_manifest_changed')
        manifest, aligned = verified_table(path.parent, 'observations')
        source = audit/'curated'/day
        if digest(read_bytes(source/'manifest.json')) != manifest['source_day_manifest_sha256']:
            raise ValueError('source_manifest_changed')
        _, original = verified_table(source, 'eligible_headlines')
        if aligned.num_rows != original.num_rows:
            raise ValueError('source_alignment_count')
        rows = []
        for row, old in zip(aligned.to_pylist(), original.to_pylist()):
            for field in ('ticker','source_record_sha256','source_manifest_sha256','source_manifest_row',
                          'availability_at','gdelt_seen_at','published_at','ingested_at'):
                if row[field] != old[field]:
                    raise ValueError('source_identity_or_time_changed')
            if old['status'] != 'eligible_for_observation_analysis' or old.get('review_hold_reasons'):
                raise ValueError('hold_or_ineligible_source')
            title = old['title']
            if digest((title or '').encode()) != row['exact_model_input_sha256']:
                raise ValueError('source_title_binding')
            if row['session'] and utc(row['availability_at']) > utc(row['cutoff']):
                raise ValueError('future_observation')
            rows.append((row, title, old.get('language')))
        yield day, rows


def run(build, audit, receipts_path, benchmark, model_path, output):
    """Tokenize bounded day batches with local pinned files; no model inference."""
    from transformers import AutoTokenizer
    from .news_sentiment import model_provenance
    if any(p.is_symlink() for p in (output,*output.parents)):
        raise ValueError('output_symlink')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    provenance = model_provenance(model_path)
    report = json.loads(read_bytes(benchmark/'report.json'))
    if report['model']['local_file_sha256'] != provenance['local_file_sha256'] or report['model']['packages'] != provenance['packages']:
        raise ValueError('benchmark_tokenizer_contract_changed')
    scores = json.loads(read_bytes(benchmark/'scores.json'))
    known = {r['exact_model_input_sha256'] for r in scores if r['status']=='benchmark_only'}
    receipts = json.loads(read_bytes(receipts_path))
    policy_hash = digest(json_bytes(POLICY))
    if report['policy_sha256'] != policy_hash:
        raise ValueError('frozen_policy_changed')
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
    tokens = {}; groups = Counter(); stats = defaultdict(Counter); unique = defaultdict(set)
    daily = defaultdict(Counter); languages = Counter(); boundary = Counter(); days = 0
    for day, rows in source_pairs(build, audit, receipts):
        days += 1
        new = {r['exact_model_input_sha256']: title for r,title,_ in rows if r['exact_model_input_sha256'] not in tokens}
        items = list(new.items())
        for begin in range(0,len(items),256):
            batch = items[begin:begin+256]
            encoded = tokenizer([t or '' for _,t in batch], truncation=False, padding=False)['input_ids']
            for (h,title), ids in zip(batch,encoded):
                tokens[h] = (len(ids), input_state(title,len(ids),h in known))
        for row, _, language in rows:
            split = partition(row['session']); h = row['exact_model_input_sha256']; state = tokens[h][1]
            stats[split]['eligible_observations'] += 1; stats[split][state] += 1
            unique[split].add(h); languages[str(language)] += 1
            same = split != 'outside_panel' and partition(row['availability_at']) == split
            if not same:
                boundary[split] += 1
                continue
            k = key(row); groups[k] += 1
            daily[k[:3]]['eligible_observations'] += 1
            daily[k[:3]][state] += 1
        if days % 250 == 0:
            print(json.dumps(dict(stage='token_inventory',days=days,unique_inputs=len(tokens))),flush=True)
    # State sums are mutually exclusive. Benchmark availability is not a panel score.
    for split in stats:
        stats[split]['token_eligible'] = stats[split]['benchmark_available'] + stats[split]['unscored']
        stats[split]['panel_scored'] = 0
        stats[split]['panel_unscored_token_eligible'] = stats[split]['token_eligible']
        stats[split]['cross_partition_or_outside_excluded'] = boundary[split]
        states = Counter(tokens[h][1] for h in unique[split])
        stats[split]['unique_inputs'] = len(unique[split])
        stats[split]['unique_by_state'] = dict(states)
        if stats[split]['eligible_observations'] != sum(stats[split][s] for s in ('missing_text','overlength','benchmark_available','unscored')):
            raise ValueError('inventory_reconciliation')
    for k,n in groups.items():
        daily[k[:3]]['daily_unique_votes'] += 1
        daily[k[:3]][tokens[k[3]][1]+'_unique'] += 1
    # Materialize the same 1/n rule without deleting any source or borrowing time.
    written = 0
    for day, rows in source_pairs(build,audit,receipts):
        weighted = []
        for row,_,_ in rows:
            split = partition(row['session']); n = groups.get(key(row))
            eligible = split != 'outside_panel' and partition(row['availability_at']) == split
            h = row['exact_model_input_sha256']
            weighted.append(dict(row,split=split,policy_sha256=policy_hash,
                daily_input_occurrences=n if eligible else None,
                daily_input_weight=1/n if eligible else None,
                policy_observation_eligible=eligible,token_count=tokens[h][0],token_state=tokens[h][1],
                sentiment=None,panel_scored=False))
        if weighted:
            publish(output/'weights'/day,{'observations':pa.Table.from_pylist(weighted)},
                    dict(policy_sha256=policy_hash,article_merges=0,scoring_held=True,training_held=True))
        written += len(weighted)
    ordered = sorted(tokens)
    for begin in range(0,len(ordered),50000):
        publish(output/'tokens'/str(begin//50000),{'inputs':pa.Table.from_pylist([
            dict(exact_model_input_sha256=h,token_count=tokens[h][0],state=tokens[h][1])
            for h in ordered[begin:begin+50000]])},dict(tokenizer_files=provenance['local_file_sha256'],truncation=False))
    publish(output/'daily',{'coverage':pa.Table.from_pylist([
        dict(ticker=k[0],session=k[1],channel=k[2],**{field:v.get(field,0) for field in (
            'eligible_observations','daily_unique_votes','missing_text','overlength','benchmark_available','unscored',
            'missing_text_unique','overlength_unique','benchmark_available_unique','unscored_unique')})
        for k,v in sorted(daily.items())])},dict(policy_sha256=policy_hash,all_sentiment_null=True))
    summary=dict(partitions=dict(stats),unique_global_inputs=len(tokens),global_unique_states=dict(Counter(s for _,s in tokens.values())),
        maximum_tokens=max(n for n,_ in tokens.values()),source_observations=written,source_day_shards=days,
        policy_eligible_observations=sum(groups.values()),daily_exact_input_groups=len(groups),
        repeated_votes_capped=sum(groups.values())-len(groups),source_languages=dict(languages),
        frozen_policy_sha256=policy_hash,model_provenance=provenance,
        benchmark_scores_sha256=digest(read_bytes(benchmark/'scores.json')),
        alignment_receipts_sha256=digest(read_bytes(receipts_path)),source_audit_sha256=digest(read_bytes(audit/'audit-report.json')),
        code_sha256=digest(read_bytes(Path(__file__))),panel_scored=0,new_model_forward_inputs=0,
        article_merges=0,timestamps_changed=0,full_scoring_held=True,training_held=True,
        units='Observation partition is assigned session; unique input counts overlap partitions. Missing news bins are separate.',
        benchmark_note='506 existing dev-only inference results remain isolated; availability by hash does not populate panel scores.',
        boundary_policy='Exclude observation if its own availability and assigned session fall in different partitions; preserve source row.',
        network_calls=0,paid_calls=0)
    (output/'report.json').write_bytes(json_bytes(summary))
    return summary


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('build','audit','receipts-path','benchmark','model-path','output'):
        p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();result=run(**vars(a));print(json.dumps(result,indent=2))
