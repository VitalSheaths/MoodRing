"""Auditable train/development sentiment aggregation, with publication held.

Computation is pure and uses only supplied frozen observations and completed
score receipts. It never fetches, scores text, merges articles, changes timestamps,
or trains a model. Source hashes identify records; exact input hashes identify
computations. A separate exact-result approval is required for publication.
"""
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import math
from pathlib import Path
import re

import pyarrow as pa

from .archive import digest, json_bytes, publish, read_bytes
from .point_in_time import sessions_between, utc
from .retrospective_news import CHANNELS
from .sentiment_inventory import partition
from .sentiment_policy import daily_summary, context_summary

STATES = ('scored', 'failed', 'unscored', 'overlength', 'missing_text')
MAX_OBSERVATIONS = 1_500_000
MAX_CONTEXTS = 20_000
VERSION = 'train-dev-sentiment-aggregation-v1'
HASH_SCHEME = 'ordered-length-framed-canonical-json-records-sha256-v1'


def framed_rows_sha256(rows, *, max_rows=MAX_OBSERVATIONS):
    """Hash ordered canonical records without allocating the whole sequence JSON.

    Each record is prefixed with its unsigned eight-byte big-endian byte length;
    an eight-byte final record count follows the sequence. A scheme/domain prefix
    separates this digest from a raw file or canonical JSON-array checksum.
    """
    state = hashlib.sha256((HASH_SCHEME + '\0').encode())
    count = 0
    for row in rows:
        count += 1
        if count > max_rows:
            raise ValueError('aggregation_hash_sequence_bound')
        blob = json_bytes(row)
        if len(blob) > 1024 * 1024:
            raise ValueError('aggregation_hash_record_bound')
        state.update(len(blob).to_bytes(8, 'big')); state.update(blob)
    state.update(count.to_bytes(8, 'big'))
    return state.hexdigest()


def _output_digest(daily, contexts, audit):
    """Bind each output component without materializing a large JSON bundle."""
    return digest(json_bytes(dict(hash_scheme=HASH_SCHEME,
        daily=framed_rows_sha256(daily, max_rows=MAX_CONTEXTS * 8 * len(CHANNELS)),
        contexts=framed_rows_sha256(contexts, max_rows=MAX_CONTEXTS),
        audit=digest(json_bytes(audit)))))


def _sha(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('sha256_required')
    return value


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _score(record, contract):
    """Validate a complete inference receipt, including explicit null failures."""
    if record.get('inference_contract_sha256') != contract:
        raise ValueError('inference_contract_mismatch')
    status = record.get('status')
    if status == 'failed':
        if record.get('probabilities') is not None or record.get('headline_sentiment') is not None or not record.get('failure_reason'):
            raise ValueError('failure_must_remain_null')
        return None
    if status != 'scored':
        raise ValueError('score_receipt_status')
    probs = record.get('probabilities')
    if not isinstance(probs, dict) or set(probs) != {'positive', 'negative', 'neutral'}:
        raise ValueError('score_probability_schema')
    if any(not _finite(x) or not 0 <= x <= 1 for x in probs.values()) or abs(sum(probs.values()) - 1) > 1e-5:
        raise ValueError('invalid_probabilities')
    tone = record.get('headline_sentiment')
    if not _finite(tone) or abs(tone - (probs['positive'] - probs['negative'])) > 1e-6:
        raise ValueError('tone_probability_mismatch')
    return tone


def build_aggregation(observations, scores, contexts, *, policy_sha256,
                      inference_contract_sha256, expected_row_ids):
    """Reconcile complete source groups and exact matched rows without publishing.

    observations: retained weighted source rows, including policy exclusions.
    scores: exact UTF-8 input SHA256 -> complete scored/failed receipt.
    contexts: exact ordered primary matched train/dev rows, each carrying eight
      session IDs and a row_id. This function rejects final-test rows altogether.
    expected_row_ids: independently frozen ordered comparison keys, not an
      intersection inferred from whichever scores happen to exist.

    Partial scores are allowed for diagnostic computation. Their nulls and
    unscored counts stay explicit; publication refuses unfinished eligible inputs.
    """
    _sha(policy_sha256); _sha(inference_contract_sha256)
    if not isinstance(scores, dict) or not 0 < len(contexts) <= MAX_CONTEXTS or len(observations) > MAX_OBSERVATIONS:
        raise ValueError('aggregation_scope_bound')
    if len(expected_row_ids) != len(set(expected_row_ids)) or [r['row_id'] for r in contexts] != list(expected_row_ids):
        raise ValueError('unmatched_context_rows')
    rows_by_key = {}; context_scope = set(); calendar = {}
    for r in contexts:
        ticker, session, split = r['ticker'], r['session'], r['split']
        if split not in ('train', 'dev') or partition(session) != split:
            raise ValueError('train_dev_only')
        if r.get('primary_matched') is not True or r.get('known_gap_in_context', False):
            raise ValueError('unmatched_or_gap_context')
        if r['row_id'] != digest(json_bytes([ticker, session])) or (ticker, session) in rows_by_key:
            raise ValueError('context_identity')
        window = r['context_sessions']
        if len(window) != 8 or window[-1] != session or window != sorted(set(window)):
            raise ValueError('eight_context_sessions_required')
        if any(partition(d) != split for d in window):
            raise ValueError('context_crosses_partition')
        expected = sessions_between(window[0], session)
        if [d for d, _ in expected] != window:
            raise ValueError('context_calendar_gap')
        for d, close in expected:
            context_scope.add((ticker, d)); calendar[d] = close + timedelta(minutes=20)
        rows_by_key[(ticker, session)] = r
    groups = defaultdict(list); source_keys = set(); eligible_hashes = set()
    token_signature = {}; excluded = Counter(); included_count = 0
    for r in observations:
        # A caller cannot silently mix test observations into an authorized scope.
        if r.get('split') not in ('train', 'dev'):
            raise ValueError('train_dev_only')
        source_key = tuple(r[k] for k in ('ticker', 'source_record_sha256',
                                          'source_manifest_sha256', 'source_manifest_row', 'availability_at'))
        _sha(r['source_record_sha256']); _sha(r['source_manifest_sha256'])
        if type(r['source_manifest_row']) is not int or r['source_manifest_row'] < 0:
            raise ValueError('source_row_index')
        utc(r['availability_at'])
        if source_key in source_keys:
            raise ValueError('duplicate_source_observation')
        source_keys.add(source_key)
        if r.get('policy_sha256') != policy_sha256:
            raise ValueError('weighting_policy_mismatch')
        if r.get('policy_observation_eligible') is not True:
            excluded['policy_ineligible'] += 1
            continue
        if r.get('review_revision_action') in ('exclude', 'quarantine'):
            raise ValueError('held_observation_vote')
        if partition(r['session']) != r['split'] or partition(r['availability_at']) != r['split']:
            raise ValueError('observation_crosses_partition')
        if (r['ticker'], r['session']) not in context_scope:
            raise ValueError('observation_outside_frozen_context')
        cutoff = utc(r['cutoff'])
        if cutoff != calendar[r['session']]:
            raise ValueError('incorrect_session_cutoff')
        if utc(r['availability_at']) > cutoff:
            raise ValueError('future_observation')
        h = _sha(r['exact_model_input_sha256'])
        tokens = r['token_count']; raw_state = r['token_state']
        if type(tokens) is not int or tokens < 0:
            raise ValueError('token_count')
        if raw_state == 'missing_text':
            state = 'missing_text'
        elif tokens > 512:
            state = 'overlength'
        elif tokens < 2 or raw_state not in ('unscored', 'benchmark_available', 'scored'):
            raise ValueError('token_state')
        else:
            state = 'token_eligible'
        signature = (tokens, state)
        if h in token_signature and token_signature[h] != signature:
            raise ValueError('inconsistent_input_tokens')
        token_signature[h] = signature
        if state == 'token_eligible':
            eligible_hashes.add(h)
        elif h in scores:
            raise ValueError('score_for_token_ineligible_input')
        if r.get('sentiment') is not None or r.get('panel_scored') not in (False, None):
            raise ValueError('source_score_state_changed')
        if r['sentiment_channel'] not in CHANNELS:
            raise ValueError('unknown_channel')
        groups[(r['ticker'], r['session'], r['sentiment_channel'], h)].append(r)
        included_count += 1
    if set(scores) - eligible_hashes:
        raise ValueError('orphan_score_receipt')
    tones = {}
    for h, receipt in scores.items():
        _sha(h)
        if receipt.get('exact_model_input_sha256', h) != h:
            raise ValueError('score_input_binding')
        tones[h] = _score(receipt, inference_contract_sha256)
    daily_groups = defaultdict(dict); states = {}; sum_weights = 0.
    for key, members in groups.items():
        h = key[-1]; n = len(members)
        if any(type(r['daily_input_occurrences']) is not int or r['daily_input_occurrences'] != n or
               not _finite(r['daily_input_weight']) or abs(r['daily_input_weight'] - 1/n) > 1e-12 for r in members):
            raise ValueError('incomplete_or_changed_daily_weights')
        weight = math.fsum(r['daily_input_weight'] for r in members)
        if abs(weight - 1) > 1e-10:
            raise ValueError('daily_weight_sum')
        sum_weights += weight
        token_state = token_signature[h][1]
        states[h] = (scores[h]['status'] if h in scores else 'unscored') if token_state == 'token_eligible' else token_state
        daily_groups[key[:3]][h] = members
    daily = []; daily_index = {}
    for ticker, day in sorted(context_scope):
        for channel in CHANNELS:
            units = daily_groups.get((ticker, day, channel), {})
            members = [r for group in units.values() for r in group]
            counts = Counter(states[h] for h in units)
            observation_counts = Counter()
            for h, group in units.items():
                observation_counts[states[h]] += len(group)
            summary = daily_summary(members, {h: tones.get(h) for h in units})
            missing = sum(counts[s] for s in STATES if s != 'scored')
            result = dict(ticker=ticker, session=day, split=partition(day), channel=channel,
                          sentiment=summary['mean'], observed=bool(members),
                          scored=counts['scored'] > 0, absent_news=not members,
                          observation_count=len(members), unique_input_count=len(units),
                          missing_input_count=missing,
                          zero_tone_input_count=sum(states[h] == 'scored' and tones[h] == 0 for h in units),
                          neutral_label_input_count=sum(states[h] == 'scored' and
                              max(scores[h]['probabilities'], key=scores[h]['probabilities'].get) == 'neutral' for h in units),
                          policy_sha256=policy_sha256, historical_prediction_eligible=False)
            for state in STATES:
                result[state + '_inputs'] = counts[state]
                result[state + '_observations'] = observation_counts[state]
            if sum(counts.values()) != len(units) or sum(observation_counts.values()) != len(members):
                raise ValueError('daily_count_reconciliation')
            if (result['sentiment'] is None) != (counts['scored'] == 0):
                raise ValueError('daily_null_reconciliation')
            daily.append(result); daily_index[(ticker, day, channel)] = result
    output_contexts = []
    for r in contexts:
        result = dict(row_id=r['row_id'], ticker=r['ticker'], session=r['session'],
                      split=r['split'], context_sessions=list(r['context_sessions']),
                      historical_prediction_eligible=False)
        for channel in CHANNELS:
            days = [daily_index[(r['ticker'], d, channel)] for d in r['context_sessions']]
            values = context_summary([dict(mean=d['sentiment']) for d in days])
            result[channel + '_sentiment'] = values['mean']
            result[channel + '_scored_days'] = values['observed_days']
            result[channel + '_missing_score_days'] = values['missing_days']
            result[channel + '_observed_days'] = sum(d['observed'] for d in days)
            result[channel + '_absent_news_days'] = sum(d['absent_news'] for d in days)
            result[channel + '_has_sentiment'] = values['mean'] is not None
            result[channel + '_context_unique_inputs'] = len(set().union(*[
                set(daily_groups.get((r['ticker'], d, channel), {})) for d in r['context_sessions']]))
            for state in STATES:
                result[channel + '_' + state + '_daily_inputs'] = sum(d[state + '_inputs'] for d in days)
                result[channel + '_' + state + '_observations'] = sum(d[state + '_observations'] for d in days)
        output_contexts.append(result)
    score_states = Counter(states[h] for h in eligible_hashes)
    coverage = defaultdict(Counter)
    for row in daily:
        tally = coverage[(row['split'], row['ticker'], row['channel'])]
        tally['session_bins'] += 1
        tally['absent_news_bins'] += row['absent_news']
        tally['scored_bins'] += row['scored']
        tally['observation_count'] += row['observation_count']
        tally['daily_unique_input_votes'] += row['unique_input_count']
        for state in STATES:
            tally[state + '_daily_input_votes'] += row[state + '_inputs']
            tally[state + '_observations'] += row[state + '_observations']
    audit = dict(source_observations=len(observations), included_observations=included_count,
                 excluded_observations=dict(excluded), source_identity_count=len(source_keys),
                 daily_exact_input_groups=len(groups), daily_group_weight_sum=sum_weights,
                 distinct_token_eligible_inputs=len(eligible_hashes),
                 unique_scored_inputs=score_states['scored'], unique_failed_inputs=score_states['failed'],
                 unique_unscored_inputs=score_states['unscored'],
                 all_eligible_scores_successful=not score_states['failed'] and not score_states['unscored'],
                 daily_rows=len(daily), matched_rows=len(output_contexts),
                 partitions=dict(Counter(r['split'] for r in output_contexts)),
                 coverage_by_split_stock_channel=[dict(split=s, ticker=t, channel=c, **dict(v))
                    for (s,t,c),v in sorted(coverage.items())],
                 final_test_rows=0, new_article_merges=0, source_times_changed=False,
                 missing_is_neutral=False, aggregation_released=False, training_released=False)
    identity = dict(version=VERSION, hash_scheme=HASH_SCHEME, policy_sha256=policy_sha256,
                    inference_contract_sha256=inference_contract_sha256,
                    source_rows_sha256=framed_rows_sha256(observations),
                    score_receipts_sha256=framed_rows_sha256(([h,scores[h]] for h in sorted(scores))),
                    contexts_sha256=framed_rows_sha256(contexts, max_rows=MAX_CONTEXTS),
                    matched_row_ids_sha256=framed_rows_sha256(expected_row_ids, max_rows=MAX_CONTEXTS),
                    code_sha256=digest(read_bytes(Path(__file__))),
                    output_sha256=_output_digest(daily, output_contexts, audit))
    return dict(identity=identity, run_id=digest(json_bytes(identity)),
                daily=daily, contexts=output_contexts, audit=audit)


def publish_aggregation(output, result, approval=None):
    """Publish only an explicitly approved, complete exact aggregation artifact.

    No training is released by this operation. Refuse unresolved eligible failures
    and unscored inputs rather than treating a partial canary as panel sentiment.
    Token-ineligible and absent-news states remain legitimate explicit nulls.
    """
    if not isinstance(approval, dict) or approval.get('granted') is not True or approval.get('scope') != 'train_dev_aggregation_only':
        raise PermissionError('aggregation_release_required')
    identity = result['identity']
    if digest(json_bytes(identity)) != result['run_id'] or approval.get('run_id') != result['run_id']:
        raise ValueError('aggregation_identity_changed')
    if identity['code_sha256'] != digest(read_bytes(Path(__file__))):
        raise ValueError('aggregation_code_changed')
    if _output_digest(result['daily'], result['contexts'], result['audit']) != identity['output_sha256']:
        raise ValueError('aggregation_output_changed')
    if not approval.get('approved_by'):
        raise PermissionError('aggregation_operator_required')
    utc(approval.get('approved_at'))
    if not result['audit']['all_eligible_scores_successful']:
        raise PermissionError('aggregation_scores_incomplete')
    return publish(Path(output), {k: pa.Table.from_pylist(result[k]) for k in ('daily', 'contexts')},
                   dict(run_id=result['run_id'], identity=identity, audit=result['audit'],
                        aggregation_released=True, forecasting_training_released=False,
                        final_test_released=False, strict_PIT_eligible=False))
