"""Exact-release train/dev comparison of published generic tone and coverage.

Preparation and preflight never fit preprocessing or forecasting models. Execution
requires a new exact approval and an unused destination. No automatic retry or
resume is permitted: partial fits remain inspectable and need a new decision.
"""
from pathlib import Path
from importlib.metadata import version
import json
import os
import platform
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .archive import digest, json_bytes, read_bytes
from .pilot_runner import relative_file, atomic_json
from .forecast_release_io import load_inputs
from .forecast_runners import (preprocess, RidgeVariance, BoostedVariance,
    BASELINE_CONFIGS, SEEDS, FLOOR, _array_digest, development_comparison)
from .comparison_protocol import FrozenPreprocessor, PROTOCOL
from .risk_evaluation import variance_metrics
from .point_in_time import utc

SCOPE = 'matched_train_dev_sentiment_comparison'
CHANNELS = ('direct', 'indirect_product', 'indirect_sector_competitor')
STATES = ('scored', 'failed', 'unscored', 'overlength', 'missing_text')
COUNTS = ('scored_days', 'missing_score_days', 'observed_days', 'absent_news_days',
          'has_sentiment', 'context_unique_inputs') + tuple(
    s + '_' + unit for s in STATES for unit in ('daily_inputs', 'observations'))
MASKS = ('scored_mask', 'observed_mask', 'absent_news_mask', 'overlength_present_mask',
         'unfinished_present_mask', 'failure_present_mask', 'missing_text_present_mask')
COVERAGE = tuple(ch + '_' + field for ch in CHANNELS for field in COUNTS) + tuple(
    f'{ch}_{field}_{slot}' for ch in CHANNELS
    for field in (*MASKS, 'daily_effective_denominators') for slot in range(8))
TONES = tuple(ch + '_sentiment' for ch in CHANNELS)
ARMS = {
    'baseline_plus_coverage_controls': (),
    'coverage_baseline_plus_direct': (TONES[0],),
    'coverage_baseline_plus_indirect': TONES[1:],
    'coverage_baseline_plus_both': TONES,
}
BASE = 'price_macro_technical'
THREADS = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
           'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')


def _read(root, name):
    return json.loads(read_bytes(relative_file(root, name)))


def _same(a, b):
    return np.allclose(np.asarray(a, float), np.asarray(b, float), rtol=1e-12,
                       atol=1e-12, equal_nan=True)


def anchors(dataset, contexts, masks):
    """Keyed anchor summaries, appended once; preserve nullable tone and slot masks.

    Context row order in publication is not model row order. Require a bijection
    then reorder explicitly; never intersect or broadcast anchor summaries into
    earlier feature sessions. Counts cannot include neutral/zero-tone statistics.
    """
    c = contexts[contexts.split == dataset.split].copy()
    m = masks[masks.split == dataset.split].copy()
    if c.row_id.duplicated().any() or set(c.row_id) != set(dataset.row_ids):
        raise ValueError('published_population_bijection')
    if m.duplicated(['row_id', 'channel']).any() or set(zip(m.row_id, m.channel)) != {
            (r, ch) for r in dataset.row_ids for ch in CHANNELS}:
        raise ValueError('published_mask_bijection')
    c = c.set_index('row_id').loc[list(dataset.row_ids)]
    m = m.set_index(['row_id', 'channel'])
    out = pd.DataFrame(index=range(len(c)))
    for field in COVERAGE[:len(CHANNELS)*len(COUNTS)]:
        out[field] = c[field].to_numpy(float)
    values = {name: [] for name in COVERAGE[len(CHANNELS)*len(COUNTS):]}
    for i, (row_id, r) in enumerate(c.iterrows()):
        day, ticker = dataset.keys[i]
        expected = [dataset.feature_keys[j][0] for j in dataset.window_indices[i]]
        if (r.session, r.ticker) != (day, ticker) or list(r.context_sessions) != expected:
            raise ValueError('context_order_or_membership')
        if bool(r.historical_prediction_eligible):
            raise ValueError('retrospective_not_PIT')
        for ch in CHANNELS:
            v = m.loc[(row_id, ch)]
            if (v.session, v.ticker) != (day, ticker):
                raise ValueError('context_mask_key')
            means = np.asarray(v.daily_means, float)
            den = np.asarray(v.daily_effective_denominators, float)
            if means.shape != (8,) or den.shape != (8,) or np.isinf(means).any():
                raise ValueError('context_slot_shape')
            scored = ~np.isnan(means)
            if not np.array_equal(scored, v.scored_mask) or not np.array_equal(scored, den > 0):
                raise ValueError('missing_neutral_or_denominator')
            if (den < 0).any() or (den != np.floor(den)).any() or (np.abs(means[scored]) > 1).any():
                raise ValueError('context_denominator_or_tone_range')
            mean = means[scored].mean() if scored.any() else np.nan
            if not _same([mean, mean], [r[ch+'_sentiment'], v.sentiment]):
                raise ValueError('context_mean_reconstruction')
            for field in COUNTS:
                if field == 'context_unique_inputs':
                    continue  # This distinct-across-days count is bound by source audit.
                if r[ch+'_'+field] != v[field]:
                    raise ValueError('context_count_reconciliation')
            if int(scored.sum()) != v.scored_days or int((~scored).sum()) != v.missing_score_days:
                raise ValueError('context_scored_day_count')
            if v.failed_daily_inputs or v.unscored_daily_inputs:
                raise ValueError('unfinished_scores_block_comparison')
            for field in (*MASKS, 'daily_effective_denominators'):
                arr = np.asarray(v[field])
                if arr.shape != (8,):
                    raise ValueError('context_slot_shape')
                if field in MASKS and not np.isin(arr, [False, True]).all():
                    raise ValueError('boolean_mask')
                for slot, value in enumerate(arr):
                    values[f'{ch}_{field}_{slot}'].append(float(value))
    out = pd.concat([out, pd.DataFrame(values)], axis=1)
    for name in TONES:
        out[name] = c[name].to_numpy(float)
    x = out[list(COVERAGE)].to_numpy(float)
    if not np.isfinite(x).all() or (x < 0).any():
        raise ValueError('invalid_coverage')
    return out[list(COVERAGE)+list(TONES)]


def verify_publication(descriptor, root):
    """Verify later publication authority, not stale nested candidate flags."""
    prefix = descriptor['published_features']
    receipt = _read(root, prefix+'/publication.json')
    approval = _read(root, prefix+'/approval.json')
    if (receipt['publication_released'] is not True
        or receipt['publication_scope'] != 'train_dev_aggregation_only'
        or approval['granted'] is not True
        or approval['release_packet_id'] != receipt['release_packet_id']
        or approval['run_id'] != receipt['aggregation_run_id']
        or receipt['approval_sha256'] != descriptor['bindings'][prefix+'/approval.json']
        or Path(receipt['destination']) != relative_file(root, prefix)):
        raise ValueError('publication_authority')
    required = {'candidate/'+n for n in ('contexts.parquet','daily.parquet','manifest.json')} | {
        'candidate-audit-fields/'+n for n in ('context_masks.parquet','daily_denominators.parquet','manifest.json')}
    if set(receipt['files_sha256']) != required:
        raise ValueError('six_publication_files')
    for name, sha in receipt['files_sha256'].items():
        if descriptor['bindings'].get(prefix+'/'+name) != sha:
            raise ValueError('publication_file_binding')
    return receipt


def load_comparison(descriptor, root):
    """Read only train/dev tables; hash-only checks of earlier full-panel bindings."""
    root = Path(root).absolute()
    required = [descriptor['baseline_descriptor'],descriptor['baseline_proposal'],descriptor['baseline_approval'],
        descriptor['baseline_execution']+'/completion.json',
        descriptor['published_features']+'/publication.json',
        descriptor['published_features']+'/approval.json']
    if any(p not in descriptor['bindings'] for p in required):
        raise ValueError('required_receipt_not_bound')
    for name, sha in descriptor['bindings'].items():
        if digest(read_bytes(relative_file(root, name))) != sha:
            raise ValueError('comparison_bound_file_changed:'+name)
    verify_publication(descriptor, root)
    baseline = _read(root, descriptor['baseline_descriptor'])
    for name, sha in baseline['bindings'].items():
        if descriptor['bindings'].get(name) != sha:
            raise ValueError('baseline_binding_missing')
    train, dev = load_inputs(baseline, root)
    prior_plan = _read(root, descriptor['baseline_proposal'])
    prior_approval = _read(root,descriptor['baseline_approval'])
    if (prior_approval.get('granted') is not True or prior_approval.get('run_id')!=prior_plan['run_id']
            or prior_approval.get('scope')!='baseline_train_dev_only'):
        raise ValueError('baseline_approval_lineage')
    if train.identity() != prior_plan['identity']['train'] or dev.identity() != prior_plan['identity']['dev']:
        raise ValueError('frozen_baseline_population_changed')
    prefix = descriptor['published_features']
    contexts = pq.read_table(relative_file(root, prefix+'/candidate/contexts.parquet'), use_threads=False).to_pandas()
    masks = pq.read_table(relative_file(root, prefix+'/candidate-audit-fields/context_masks.parquet'), use_threads=False).to_pandas()
    if set(contexts.split) != {'train','dev'} or set(masks.split) != {'train','dev'}:
        raise ValueError('final_test_held')
    ta, da = anchors(train, contexts, masks), anchors(dev, contexts, masks)
    completion = _read(root, descriptor['baseline_execution']+'/completion.json')
    if completion['run_id'] != prior_plan['run_id']:
        raise ValueError('baseline_run_lineage')
    results = []
    for name, sha in sorted(completion['result_sha256'].items()):
        path = descriptor['baseline_execution']+'/'+name
        if descriptor['bindings'].get(path) != sha:
            raise ValueError('baseline_result_binding')
        r = _read(root, path)
        if r['row_ids'] != list(dev.row_ids) or r['keys'] != [list(k) for k in dev.keys]:
            raise ValueError('baseline_reuse_order')
        if r['training_rows'] != len(train.keys) or r['development_rows'] != len(dev.keys):
            raise ValueError('baseline_reuse_counts')
        metrics = variance_metrics(r['predicted_variance'], dev.target)
        if any(not _same(metrics[k], r['metrics'][k]) for k in metrics):
            raise ValueError('baseline_metrics_changed')
        results.append(r)
    expected = [(x['family'], {k:v for k,v in x.items() if k!='family'}, s)
                for x in BASELINE_CONFIGS for s in SEEDS]
    if len(results) != 37 or [(r['family'],r['config'],r['seed']) for r in results[1:]] != expected:
        raise ValueError('baseline_reuse_grid')
    if results[0]['family'] != 'historical_variance_20' or not np.array_equal(
            results[0]['predicted_variance'], dev.historical_variance):
        raise ValueError('baseline_reference_changed')
    return train, dev, ta, da, results, completion


def make_plan(descriptor, root):
    """Reconcile inputs and freeze scope without fitting or making output folders."""
    train, dev, ta, da, results, completion = load_comparison(descriptor, root)
    if any(os.environ.get(n) != '2' for n in THREADS):
        raise ValueError('two_thread_runtime_required')
    identity = dict(version='matched-published-context-v1',scope=SCOPE,
        descriptor=descriptor,train=train.identity(),dev=dev.identity(),
        anchor_names=list(ta.columns),anchor_sha256={
            'train':_array_digest(ta.to_numpy()),'dev':_array_digest(da.to_numpy())},
        coverage_columns=list(COVERAGE),tone_columns=list(TONES),arms={
            BASE:[],**{k:list(COVERAGE)+list(v) for k,v in ARMS.items()}},
        baseline_reuse_run_id=completion['run_id'],baseline_reused_results=37,
        fixed_configurations=completion['selected_on_development'],
        configs=BASELINE_CONFIGS,seeds=list(SEEDS),max_new_fits=144,
        max_fit_attempts=144,attempts_per_fit=1,automatic_resume=False,
        deterministic_seed_repetitions=True,forecast_floor=FLOOR,
        fitting_objective='MSE; ridge L2, NumPy bounded CART boosting',
        feature_layout='unchanged baseline eight-session flattening; append anchor coverage then channel context means once',
        preprocessing='baseline unique training daily rows; appended blocks unique training anchors; shared blocks identical across arms; masks appended; all-missing training columns dropped',
        selection='primary fixed frozen baseline-selected configurations; secondary per-arm minimum dev QLIKE, config JSON then seed tie break; descriptive only',
        contrasts=[['baseline_plus_coverage_controls',BASE]]+[
            [a,'baseline_plus_coverage_controls'] for a in list(ARMS)[1:]],
        uncertainty=dict(primary_block=20,sensitivity_blocks=[5,10,40],replicates=2000,seed=17,
                         unit='paired prediction-date mean; retain all stocks; do not bridge gaps'),
        coverage_sensitivities=['any channel scored all eight sessions',
            'direct and at least one indirect channel scored at every session'],
        timing_scenarios='published zero-extra-delay inputs only; extra macro/news delay scenarios require separate preparation and approval',
        generic_tone='positive minus negative; no target sentiment claim or competitor sign reversal',
        primary_metric='annualized variance QLIKE',secondary_metrics=['volatility_MAE','volatility_RMSE'],
        final_test_held=True,master_training_held=True,strict_PIT_eligible=False,
        protocol_sha256=digest(json_bytes(PROTOCOL)),
        code_sha256={p.name:digest(read_bytes(p)) for p in sorted(Path(__file__).parent.glob('*.py'))},
        runtime=dict(python=platform.python_version(),machine=platform.machine(),
            packages={n:version(n) for n in ('numpy','pandas','pyarrow','exchange-calendars')},
            threads={n:os.environ[n] for n in THREADS}))
    return dict(identity=identity,run_id=digest(json_bytes(identity)),status='awaiting_exact_comparison_approval')


def _block(train, dev, ta, da, names):
    """Fit anchor-only block on training anchors; no context-slot duplication."""
    names = list(names)
    if not names or ta[names].isna().all().all():
        return np.empty((len(ta),0)), np.empty((len(da),0)), dict(names=names,dropped=names,kept=[])
    index = {k:i for i,k in enumerate(train.feature_keys)}
    cutoff = [train.feature_cutoffs[index[k]] for k in train.keys]
    scaler = FrozenPreprocessor().fit(ta[names],cutoff,train.keys,names)
    state = dict(names=names,kept=[n for n,k in zip(names,scaler.keep) if k],dropped=scaler.dropped,
        mean=scaler.scaler.mean.tolist(),scale=scaler.scaler.scale.tolist(),
        training_anchor_rows=len(train.keys),missingness_masks_appended=True)
    return scaler.transform(ta[names]), scaler.transform(da[names]), state


def prepare_designs(train, dev, ta, da):
    """Execution-only: fit shared training blocks once, reuse in all four arms."""
    tx, dx, baseline = preprocess(train, dev)
    blocks = {'coverage':_block(train,dev,ta,da,COVERAGE)}
    blocks.update({n:_block(train,dev,ta,da,[n]) for n in TONES})
    designs = {}
    for arm, tones in ARMS.items():
        chosen = ['coverage',*tones]
        designs[arm] = (np.column_stack([tx]+[blocks[k][0] for k in chosen]),
            np.column_stack([dx]+[blocks[k][1] for k in chosen]))
    return designs, dict(baseline=baseline,anchor_blocks={k:v[2] for k,v in blocks.items()})


def fit_arm(train, dev, x, y, arm, family, config, seed):
    """One deterministic variance fit; no target trimming, no final test."""
    if train.split!='train' or dev.split!='dev' or arm not in ARMS or seed not in SEEDS:
        raise ValueError('fit_scope')
    if family == 'regularized_regression':
        model = RidgeVariance(**config)
    elif family == 'boosted_trees':
        model = BoostedVariance(**config)
    else:
        raise ValueError('family_not_released')
    prediction = model.fit(x,train.target).predict(y)
    state = (dict(kind='ridge',alpha=model.alpha,mean=model.mean.tolist(),coef=model.coef.tolist(),intercept=model.intercept)
        if family=='regularized_regression' else dict(kind='numpy_cart_boosting',intercept=model.intercept,
            learning_rate=model.rate,trees=model.trees,config=dict(config)))
    return dict(arm=arm,family=family,config=config,seed=seed,row_ids=list(dev.row_ids),keys=list(dev.keys),
        predicted_variance=prediction.tolist(),metrics=variance_metrics(prediction,dev.target),model_state=state,
        training_rows=len(train.keys),development_rows=len(dev.keys),fitting_objective='MSE',
        extreme_targets_removed=0,final_test_evaluated=False)


def comparisons(results, dev, da, fixed):
    """Predeclared paired contrasts; secondary tuning is descriptive, not confirmation."""
    chosen = {}; reports = {}
    for mode in ('fixed_baseline_config','per_arm_dev_selected'):
        chosen[mode] = {}
        for family in ('regularized_regression','boosted_trees'):
            for arm in (BASE,*ARMS):
                options = [r for r in results if r['family']==family and r['arm']==arm]
                if mode == 'fixed_baseline_config':
                    options = [r for r in options if r['config']==fixed[family]]
                r = min(options,key=lambda r:(r['metrics']['qlike'],json_bytes(r['config']),r['seed']))
                chosen[mode][family+'/'+arm] = dict(config=r['config'],seed=r['seed'],metrics=r['metrics'])
        reports[mode] = {}
        for family in ('regularized_regression','boosted_trees'):
            for candidate, reference in [('baseline_plus_coverage_controls',BASE)]+[
                    (a,'baseline_plus_coverage_controls') for a in list(ARMS)[1:]]:
                def selected(arm):
                    pick=chosen[mode][family+'/'+arm]
                    return next(r for r in results if r['family']==family and r['arm']==arm
                                and r['config']==pick['config'] and r['seed']==pick['seed'])
                r,c = selected(reference),selected(candidate)
                entry = {}
                for block in (20,5,10,40):
                    try:
                        interval = development_comparison(r,c,dev,block_sessions=block,repeats=2000)
                    except ValueError as error:
                        if str(error)!='insufficient_contiguous_date_blocks':raise
                        interval=dict(status='unavailable',reason=str(error))
                    entry[str(block)]=interval
                reports[mode][family+'/'+candidate+'-vs-'+reference]=entry
    # Descriptive sensitivity metrics only, same fixed subset for every arm.
    scored = {ch:da[[f'{ch}_scored_mask_{i}' for i in range(8)]].to_numpy(bool) for ch in CHANNELS}
    subsets = dict(any_channel_all_eight=np.logical_or.reduce(list(scored.values())).all(1),
        direct_and_indirect_all_eight=(scored['direct'] & (scored[CHANNELS[1]]|scored[CHANNELS[2]])).all(1))
    sensitivities={}
    for name, mask in subsets.items():
        entries=[]
        for r in results:
            if r['seed']!=17:continue
            entries.append(dict(arm=r['arm'],family=r['family'],config=r['config'],
                metrics=variance_metrics(np.asarray(r['predicted_variance'])[mask],dev.target[mask]) if mask.any() else None))
        sensitivities[name]=dict(row_ids=[v for v,k in zip(dev.row_ids,mask) if k],rows=int(mask.sum()),results=entries)
    return dict(selected=chosen,paired_qlike_intervals=reports,coverage_sensitivities=sensitivities,
        development_selection_only=True,intervals_descriptive=True,deterministic_repeats_not_independent=True)


def execute(plan, root, approval):
    """One exclusive release; reserve each fit before execution, checkpoint afterward."""
    if (approval.get('granted') is not True or approval.get('scope')!=SCOPE
        or approval.get('run_id')!=plan['run_id'] or not approval.get('approved_by')):
        raise PermissionError('exact_comparison_approval_required')
    utc(approval.get('approved_at'))
    descriptor=plan['identity']['descriptor'];root=Path(root).absolute()
    if make_plan(descriptor,root)!=plan:raise ValueError('comparison_plan_changed')
    train,dev,ta,da,reused,completion=load_comparison(descriptor,root)
    output=relative_file(root,descriptor['output_relative_path'])
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    atomic_json(output/'started.json',dict(run_id=plan['run_id'],approval=approval,max_fit_attempts=144,
        automatic_resume=False,final_test_held=True,master_training_held=True))
    designs,state=prepare_designs(train,dev,ta,da)
    if any(r['preprocessing']!=state['baseline'] for r in reused[1:]):
        raise ValueError('baseline_preprocessing_changed')
    atomic_json(output/'preprocessing.json',state)
    results=[dict(r,keys=[tuple(k) for k in r['keys']],arm=BASE) for r in reused]
    hashes={};index=0
    for arm in ARMS:
        for candidate in BASELINE_CONFIGS:
            config=dict(candidate);family=config.pop('family')
            for seed in SEEDS:
                if index>=plan['identity']['max_fit_attempts']:raise ValueError('fit_budget')
                atomic_json(output/f'attempt-{index:04d}.json',dict(index=index,arm=arm,family=family,config=config,seed=seed))
                result=fit_arm(train,dev,*designs[arm],arm,family,config,seed)
                if result['keys']!=list(dev.keys) or result['row_ids']!=list(dev.row_ids):
                    raise ValueError('fit_result_population')
                path=output/f'result-{index:04d}.json';atomic_json(path,result)
                hashes[path.name]=digest(read_bytes(path));results.append(result);index+=1
    if index!=144:raise ValueError('incomplete_fit_grid')
    for arm in ARMS:
        for candidate in BASELINE_CONFIGS:
            config=dict(candidate);family=config.pop('family')
            repeated=[r['predicted_variance'] for r in results if r['arm']==arm
                      and r['family']==family and r['config']==config]
            if len(repeated)!=3 or any(not np.array_equal(repeated[0],v) for v in repeated[1:]):
                raise ValueError('deterministic_seed_reconciliation')
    summary=comparisons(results,dev,da,completion['selected_on_development'])
    # Reference is numerically identical in all arms; no extra fit or selection.
    summary['historical_reference']=dict(result_sha256=completion['result_sha256']['result-0000.json'],
        metrics=reused[0]['metrics'],identical_in_all_arms=True)
    atomic_json(output/'completion.json',dict(summary,run_id=plan['run_id'],completed_fits=index,
        result_sha256=hashes,baseline_reused_results=37,final_test_held=True,master_training_held=True,
        feature_publication_unchanged=True,strict_PIT_eligible=False))
    return dict(run_id=plan['run_id'],completed_fits=index,final_test_held=True,master_training_held=True)


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','root','approval'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(execute(json.loads(read_bytes(args.plan)),args.root,json.loads(read_bytes(args.approval))),indent=2))
