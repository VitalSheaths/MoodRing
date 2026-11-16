"""Frozen sparse support and session-delay mechanics; no fitting or inference."""
import numpy as np
import pandas as pd
from .sentiment_inventory import partition
from .matched_comparison import CHANNELS, COUNTS, MASKS, COVERAGE, TONES

def daily_groups(observations, scores, calendar, lag):
    """Delay eligibility bins, retaining original timestamps and source mappings.

    A session delay is a stress assumption, not an asserted publication time.
    No observation crosses its chronological partition into another partition.
    """
    if lag not in (0, 1, 5):
        raise ValueError('unsupported delay')
    days = calendar.session.tolist()
    positions = {d: i for i, d in enumerate(days)}
    o = observations.copy()
    if not set(o.split) <= {'train', 'dev'}:
        raise ValueError("feature_integrity: set(o.split) <= {'train', 'dev'}")
    if not all((partition(r.session) == r.split for r in o.itertuples())):
        raise ValueError('feature_integrity: all((partition(r.session) == r.split for r in o.itertuples()))')
    if not set(o.state) <= {'scored', 'overlength'}:
        raise ValueError("feature_integrity: set(o.state) <= {'scored', 'overlength'}")
    if not set(o.sentiment_channel) <= set(CHANNELS):
        raise ValueError('feature_integrity: set(o.sentiment_channel) <= set(CHANNELS)')
    if not o.observation_id.is_unique:
        raise ValueError('feature_integrity: o.observation_id.is_unique')
    if not (o.policy_observation_eligible.all() and o.exclusion.isna().all()):
        raise ValueError('feature_integrity: o.policy_observation_eligible.all() and o.exclusion.isna().all()')
    if not all((pd.Timestamp(r.availability_at) <= pd.Timestamp(r.cutoff) for r in o.itertuples())):
        raise ValueError('feature_integrity: all((pd.Timestamp(r.availability_at) <= pd.Timestamp(r.cutoff) for r in o.itertuples()))')
    o['effective_session'] = [days[positions[d] + lag] if positions[d] + lag < len(days) else None for d in o.session]
    o['delay_status'] = ['eligible_' + r.split if r.effective_session is not None and partition(r.effective_session) == r.split else 'outside_' + r.split for r in o.itertuples()]
    o['lag'] = lag
    active = o[o.delay_status == 'eligible_' + o.split].copy()
    if not scores.exact_model_input_sha256.is_unique:
        raise ValueError('feature_integrity: scores.exact_model_input_sha256.is_unique')
    active = active.merge(scores, on='exact_model_input_sha256', how='left', validate='many_to_one', suffixes=('', '_score'))
    scored = active.state == 'scored'
    if active.loc[~scored, 'status'].notna().any():
        raise ValueError("feature_integrity: not active.loc[~scored, 'status'].notna().any()")
    if not np.isfinite(active.loc[scored, ['positive', 'negative', 'neutral', 'tone']]).all().all():
        raise ValueError("feature_integrity: np.isfinite(active.loc[scored, ['positive', 'negative', 'neutral', 'tone']]).all().all()")
    if not active.loc[scored, ['positive', 'negative', 'neutral']].ge(0).all().all():
        raise ValueError("feature_integrity: active.loc[scored, ['positive', 'negative', 'neutral']].ge(0).all().all()")
    if not active.loc[scored, ['positive', 'negative', 'neutral']].le(1).all().all():
        raise ValueError("feature_integrity: active.loc[scored, ['positive', 'negative', 'neutral']].le(1).all().all()")
    if not (active.loc[scored, 'status'] == 'scored').all():
        raise ValueError("feature_integrity: (active.loc[scored, 'status'] == 'scored').all()")
    if not active.loc[scored, ['positive', 'negative', 'neutral', 'tone']].notna().all().all():
        raise ValueError("feature_integrity: active.loc[scored, ['positive', 'negative', 'neutral', 'tone']].notna().all().all()")
    if not np.allclose(active.loc[scored, 'positive'] + active.loc[scored, 'negative'] + active.loc[scored, 'neutral'], 1, atol=1e-06, rtol=0):
        raise ValueError("feature_integrity: np.allclose(active.loc[scored, 'positive'] + active.loc[scored, 'negative'] + active.loc[scored, 'neutral'], 1, atol=1e-06, rtol=0)")
    if not np.allclose(active.loc[scored, 'tone'], active.loc[scored, 'positive'] - active.loc[scored, 'negative'], atol=1e-12, rtol=0):
        raise ValueError("feature_integrity: np.allclose(active.loc[scored, 'tone'], active.loc[scored, 'positive'] - active.loc[scored, 'negative'], atol=1e-12, rtol=0)")
    keys = ['ticker', 'effective_session', 'sentiment_channel', 'exact_model_input_sha256']
    active['reconstructed_occurrences'] = active.groupby(keys).observation_id.transform('size')
    active['reconstructed_occurrence_weight'] = 1 / active.reconstructed_occurrences
    if not np.allclose(active.groupby(keys).reconstructed_occurrence_weight.sum(), 1, atol=1e-12):
        raise ValueError('feature_integrity: np.allclose(active.groupby(keys).reconstructed_occurrence_weight.sum(), 1, atol=1e-12)')
    if not (active.groupby(keys).state.nunique() == 1).all():
        raise ValueError('feature_integrity: (active.groupby(keys).state.nunique() == 1).all()')
    for c in ['tone', 'positive', 'negative', 'neutral']:
        if not (active.groupby(keys)[c].nunique(dropna=False) == 1).all():
            raise ValueError('feature_integrity: (active.groupby(keys)[c].nunique(dropna=False) == 1).all()')
    g = active.groupby(keys, as_index=False).agg(state=('state', 'first'), tone=('tone', 'first'), positive=('positive', 'first'), negative=('negative', 'first'), neutral=('neutral', 'first'), observations=('observation_id', 'size'))
    g['lag'] = lag
    daily = []
    for (ticker, day, channel), group in g.groupby(keys[:3]):
        s = group[group.state == 'scored']
        n = len(s)
        daily.append(dict(ticker=ticker, session=day, channel=channel, lag=lag, observations=int(group.observations.sum()), unique_inputs=len(group), scored_inputs=n, overlength_inputs=int((group.state == 'overlength').sum()), unscored_inputs=int((group.state == 'unscored').sum()), failed_inputs=int((group.state == 'failed').sum()), sentiment=float(s.tone.mean()) if n else np.nan, support_factor=n / (n + 5) if n else np.nan, support_weighted_tone=float(s.tone.mean()) * n / (n + 5) if n else np.nan, mean_absolute_tone=float(s.tone.abs().mean()) if n else np.nan, nonneutral_probability=float((s.positive + s.negative).mean()) if n else np.nan, tone_dispersion=float(np.mean((s.tone - s.tone.mean()) ** 2)) if n else np.nan))
    return (o, g, pd.DataFrame(daily))

def context_features(rows, daily, groups, support, lag):
    """Equal-day means with explicit unsupported, absent, overlength and scored states."""
    daily_lookup = daily.set_index(['ticker', 'session', 'channel'])
    scored = groups[groups.state == 'scored']
    input_lookup = {k: v for k, v in scored.groupby(['ticker', 'effective_session', 'sentiment_channel'])}
    eligibility = support[support.lag == lag].set_index('row_id')
    result = []
    fields = ['sentiment', 'support_factor', 'support_weighted_tone', 'mean_absolute_tone', 'nonneutral_probability', 'tone_dispersion']
    for r in rows.itertuples():
        supported = bool(eligibility.loc[r.row_id, 'supported'])
        for channel in CHANNELS:
            d = [daily_lookup.loc[r.ticker, day, channel] for day in r.context_sessions if (r.ticker, day, channel) in daily_lookup.index]
            n = sum((int(x.scored_inputs > 0) for x in d)) if supported else 0
            counts = {f: sum((int(x[f]) for x in d)) if supported else None for f in ['observations', 'unique_inputs', 'scored_inputs', 'overlength_inputs', 'unscored_inputs', 'failed_inputs']}
            weights = {}
            if supported and n:
                for day in r.context_sessions:
                    group = input_lookup.get((r.ticker, day, channel))
                    if group is None:
                        continue
                    weight = 1 / (n * len(group))
                    for h in group.exact_model_input_sha256:
                        weights[h] = weights.get(h, 0) + weight
                if not abs(sum(weights.values()) - 1) <= 1e-12:
                    raise ValueError('feature_integrity: abs(sum(weights.values()) - 1) <= 1e-12')
            state = 'unsupported_delay_window' if not supported else 'scored' if n else 'overlength_only' if counts['overlength_inputs'] else 'unfinished_or_failed' if counts['failed_inputs'] or counts['unscored_inputs'] else 'absent_news'
            item = dict(row_id=r.row_id, ticker=r.ticker, session=r.session, channel=channel, lag=lag, supported=supported, state=state, scored_days=n if supported else None, missing_scored_days=8 - n if supported else None, **counts, context_distinct_scored_inputs=len(weights) if supported else None, effective_input_count=1 / sum((w * w for w in weights.values())) if weights else np.nan, largest_input_weight=max(weights.values()) if weights else np.nan)
            for field in fields:
                item[field] = float(np.mean([x[field] for x in d if x.scored_inputs > 0])) if supported and n else np.nan
            result.append(item)
    return pd.DataFrame(result)


SUPPORT_FIELDS = ('support_factor', 'effective_input_count', 'largest_input_weight')
INTENSITY_FIELDS = ('mean_absolute_tone', 'nonneutral_probability', 'tone_dispersion')


def context_anchors(rows, groups, sparse_contexts):
    """Reconstruct original coverage controls and new support from complete groups.

    Missing means remain NaN; counts and each of eight daily masks are explicit.
    Article identity is untouched. An exact input repeated on multiple days keeps
    every daily occurrence, but contributes once to the context distinct count.
    """
    by_day = {k: v for k, v in groups.groupby(
        ['ticker', 'effective_session', 'sentiment_channel'])}
    sparse = sparse_contexts.set_index(['row_id', 'channel'], verify_integrity=True)
    result = []
    for row in rows.itertuples():
        if getattr(row, 'split', partition(row.session)) not in ('train', 'dev'):
            raise ValueError('final_test_held')
        item = {'row_id': row.row_id}
        for channel in CHANNELS:
            c = sparse.loc[(row.row_id, channel)]
            if not c.supported:
                raise ValueError('unsupported_context_not_absent_news')
            day_counts = []; all_hashes = set(); means = []
            for day in row.context_sessions:
                g = by_day.get((row.ticker, day, channel))
                counts = {state+'_'+unit: 0 for state in ('scored','failed','unscored','overlength','missing_text')
                          for unit in ('inputs','observations')}
                if g is not None:
                    if not set(g.state) <= {'scored', 'overlength'}:
                        raise ValueError('unfinished_inputs_block_features')
                    all_hashes.update(g.exact_model_input_sha256)
                    for state, part in g.groupby('state'):
                        counts[state+'_inputs'] = len(part)
                        counts[state+'_observations'] = int(part.observations.sum())
                    s = g[g.state == 'scored']
                    mean = float(s.tone.mean()) if len(s) else np.nan
                else:
                    mean = np.nan
                means.append(mean); day_counts.append(counts)
            observed = [sum(v for k,v in d.items() if k.endswith('_inputs')) > 0 for d in day_counts]
            scored = [d['scored_inputs'] > 0 for d in day_counts]
            prefix = channel + '_'
            item.update({prefix+'sentiment': float(np.nanmean(means)) if any(scored) else np.nan,
                prefix+'scored_days': sum(scored), prefix+'missing_score_days': 8-sum(scored),
                prefix+'observed_days': sum(observed), prefix+'absent_news_days': 8-sum(observed),
                prefix+'has_sentiment': any(scored), prefix+'context_unique_inputs': len(all_hashes)})
            for state in ('scored','failed','unscored','overlength','missing_text'):
                item[prefix+state+'_daily_inputs'] = sum(d[state+'_inputs'] for d in day_counts)
                item[prefix+state+'_observations'] = sum(d[state+'_observations'] for d in day_counts)
            masks = dict(scored_mask=scored, observed_mask=observed,
                absent_news_mask=[not x for x in observed],
                overlength_present_mask=[d['overlength_inputs']>0 for d in day_counts],
                unfinished_present_mask=[d['unscored_inputs']>0 for d in day_counts],
                failure_present_mask=[d['failed_inputs']>0 for d in day_counts],
                missing_text_present_mask=[d['missing_text_inputs']>0 for d in day_counts],
                daily_effective_denominators=[d['scored_inputs'] for d in day_counts])
            for name, values in masks.items():
                for slot, value in enumerate(values):item[f'{prefix}{name}_{slot}'] = value
            for name in (*SUPPORT_FIELDS, 'support_weighted_tone', *INTENSITY_FIELDS):
                item[prefix+name] = c[name]
            if not np.isclose(item[prefix+'sentiment'],c.sentiment,atol=1e-12,rtol=0,equal_nan=True):
                raise ValueError('independent_context_tone_mismatch')
        result.append(item)
    columns = ['row_id', *COVERAGE, *TONES] + [ch+'_'+f for f in (*SUPPORT_FIELDS,'support_weighted_tone',*INTENSITY_FIELDS) for ch in CHANNELS]
    return pd.DataFrame(result)[columns]


def review_overlay(observations, review, source_cases, source_packet_sha256):
    """Apply exact reviewed development mappings to a new candidate only.

    Human tone judgments never overwrite classifier probabilities. A quarantine
    removes the exact company/source/input mapping from candidate votes while
    retaining its original records, times and decision provenance separately.
    """
    if (review.get('schema')!='moodring-development-headline-review-v1' or
        review.get('source_packet_sha256')!=source_packet_sha256 or
        review.get('provenance',{}).get('human_completed') is not True or
        not review.get('reviewer') or not review.get('completed_at')):
        raise ValueError('human_review_binding')
    originals={r['review_id']:r for r in source_cases}
    cases=review['cases']
    if len(cases)!=len(originals) or {r['review_id'] for r in cases}!=set(originals):
        raise ValueError('human_review_case_set')
    held=np.zeros(len(observations),dtype=bool);decisions=[]
    for case in cases:
        original=originals[case['review_id']]
        fields=('ticker','source_record_sha256','exact_model_input_sha256')
        if any(case[k]!=original[k] for k in fields) or partition(original['session'])!='dev':
            raise ValueError('human_review_source_identity')
        match=np.ones(len(observations),dtype=bool)
        for k in fields:match &= observations[k].to_numpy()==case[k]
        if not match.any() or set(observations.loc[match,'split'])!={'dev'}:
            raise ValueError('review_only_development_mappings')
        decision=case['labels']['decision']
        if decision not in ('retain current mapping','quarantine pending evidence'):
            raise ValueError('review_decision_requires_separate_rule_revision')
        if decision=='quarantine pending evidence':held |= match
        decisions.append(dict(review_id=case['review_id'],decision=decision,matched_observations=int(match.sum()),
                              **{k:case[k] for k in fields}))
    return observations.loc[~held].copy(),observations.loc[held].copy(),dict(
        decisions=decisions,quarantined_observations=int(held.sum()),human_provenance=review['provenance'],
        original_scores_changed=False,existing_holds_changed=False,training_rule_tuning=False,
        human_accuracy_established=False,new_article_merges=0)
