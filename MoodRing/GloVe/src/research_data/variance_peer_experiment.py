"""Four fixed train/dev variance-feature fits, prepared separately from execution.

No acquisition, sentiment inference or final-test loader. Real fitting requires
the exact proposal identity and an unused output directory. Preparation fits no
preprocessing or model, and old experiment packets are never rewritten.
"""
from pathlib import Path
from importlib.metadata import version
import io
import json
import os
import platform
import numpy as np
import pandas as pd
from .archive import digest, json_bytes, read_bytes, file_digest
from .pilot_runner import relative_file, atomic_json
from .forecast_runners import build_dataset, preprocess, BoostedVariance, FLOOR, _array_digest, development_comparison
from .risk_evaluation import variance_metrics
from .point_in_time import utc
from . import matched_comparison as matched
from .volatility_structure import FEATURES, build_features

SCOPE = 'fixed_variance_peer_train_dev_v1'
TREE = dict(max_leaf_nodes=7, learning_rate=.03, max_iter=50, max_bins=32, min_leaf=5)
MASKS = tuple(n+'_missing' for n in FEATURES)
CONTROLS = (*matched.COVERAGE, 'peer_count', 'peer_coverage', *MASKS)
HORIZONS = ('proxy_variance_1', 'proxy_variance_5', 'proxy_variance_22')
SIGNED = ('negative_variance_22', 'positive_variance_22')
ARMS = {'C_controls': (), 'V_horizons': HORIZONS,
        'S_signed': HORIZONS+SIGNED, 'P_peers': HORIZONS+SIGNED+('peer_variance_22',)}
CONTRASTS = (('V_horizons','C_controls'), ('S_signed','V_horizons'),
             ('P_peers','S_signed'), ('P_peers','C_controls'))


def candidate_anchors(data, source, candidate):
    """Reconstruct all fields and masks before aligning to exact model row order."""
    if candidate.row_id.duplicated().any() or set(candidate.row_id)!=set(data.row_ids):
        raise ValueError('candidate_population_bijection')
    c=candidate.set_index('row_id').loc[list(data.row_ids)]
    if list(zip(c.session,c.ticker))!=list(data.keys):
        raise ValueError('candidate_key_binding')
    expected=build_features(source[['ticker','session','cutoff','log_return_1']],
        sorted(source.session.unique()),sorted(source.ticker.unique()))
    expected=expected.set_index(['session','ticker']).loc[list(data.keys)]
    if [utc(x) for x in c.cutoff]!=[utc(x) for x in expected.cutoff]:
        raise ValueError('candidate_cutoff_binding')
    for n in FEATURES:
        if not np.allclose(c[n].to_numpy(float),expected[n].to_numpy(float),
                           equal_nan=True,rtol=1e-12,atol=1e-14):
            raise ValueError('candidate_reconstruction:'+n)
        if not np.isin(c[n+'_missing'],[True,False]).all() or not np.array_equal(
                c[n+'_missing'].to_numpy(bool),c[n].isna()):
            raise ValueError('candidate_missing_mask:'+n)
    return c[[*FEATURES,*MASKS]].reset_index(drop=True).astype(float)


def load(descriptor, root):
    """Verify bound files, published coverage and the unchanged train/dev keys."""
    root=Path(root).absolute()
    for name,sha in descriptor['bindings'].items():
        if file_digest(relative_file(root,name))!=sha:
            raise ValueError('changed_input:'+name)
    def blob(name):
        if name not in descriptor['bindings']:raise ValueError('unbound_input')
        b=read_bytes(relative_file(root,name),limit=64*1024*1024)
        if digest(b)!=descriptor['bindings'][name]:raise ValueError('input_changed_during_read')
        return b
    def table(name):return pd.read_parquet(io.BytesIO(blob(name)))
    def record(name):return json.loads(blob(name))
    matched.verify_publication(descriptor,root)
    prefix=descriptor['published_features']
    contexts=table(prefix+'/candidate/contexts.parquet')
    masks=table(prefix+'/candidate-audit-fields/context_masks.parquet')
    if set(contexts.split)!={'train','dev'} or set(masks.split)!={'train','dev'}:
        raise ValueError('final_test_held')
    previous=record(descriptor['baseline_proposal'])
    selection=record(descriptor['tree_selection'])
    if selection['selected_by_pooled_qlike']!='C5_iterations50' or selection['completed_fits']!=27:
        raise ValueError('fixed_configuration_lineage')
    result=[]
    if set(descriptor['datasets'])!={'train','dev'}:raise ValueError('train_dev_only')
    for split in ('train','dev'):
        part=descriptor['datasets'][split]
        f,c,y=[table(part[n]) for n in ('features','contexts','labels')]
        d=build_dataset(f,c,y,descriptor['feature_names'],split)
        if d.identity()!=previous['identity'][split]:raise ValueError('baseline_population_changed')
        cov=matched.anchors(d,contexts,masks)[list(matched.COVERAGE)]
        extra=candidate_anchors(d,f,table(part['candidate']))
        a=pd.concat([cov,extra],axis=1)
        if not np.isfinite(a[list(CONTROLS)].to_numpy()).all():
            raise ValueError('explicit_coverage_controls_required')
        result.extend((d,a))
    return tuple(result)  # train, train anchors, dev, dev anchors


def plan(descriptor,root):
    """Freeze exact inputs, rows, configuration and finite budget without fitting."""
    t,ta,d,da=load(descriptor,root)
    if (len(t.keys),len(d.keys))!=(4920,4900):raise ValueError('approved_population_count')
    if any(os.environ.get(k)!='2' for k in matched.THREADS):raise ValueError('two_threads_required')
    if any(n in t.names for n in FEATURES):raise ValueError('feature_name_overlap')
    identity=dict(version=SCOPE,scope=SCOPE,descriptor=descriptor,
        train=t.identity(),dev=d.identity(),anchor_names=list(ta.columns),
        anchor_sha256=dict(train=_array_digest(ta.to_numpy()),dev=_array_digest(da.to_numpy())),
        configuration=TREE,configuration_origin='C5_iterations50 from completed 27-fit training-fold study',
        grid=list(ARMS),arms={n:list(cols) for n,cols in ARMS.items()},
        common_controls=list(CONTROLS),contrasts=[list(x) for x in CONTRASTS],
        layout='unchanged 59 baseline features over eight sessions; common controls and new variance values appended once per anchor',
        preprocessing='fit baseline on unique training daily rows; anchor blocks on training anchors only; shared blocks reused; value masks already in common controls',
        dependencies='proxy_variance_1=252*log_return_1^2; negative22+positive22=proxy22; peer_coverage=peer_count/9 for the ten-stock cohort',
        missingness={s:{n:int(a[n].isna().sum()) for n in FEATURES} for s,a in [('train',ta),('dev',da)]},
        max_fits=4,max_reserved_attempts=4,attempts_per_fit=1,max_boosting_iterations=200,
        max_leaves_per_tree=7,max_bins=32,threads=2,seed=17,deterministic=True,
        automatic_retry=False,automatic_resume=False,configuration_selection=False,
        fitting_loss='annualized variance squared error; NumPy bounded CART boosting',
        forecast_floor=FLOOR,primary_metric='mean QLIKE = log(p)+y/p',
        secondary_metrics=['annualized volatility MAE','annualized volatility RMSE','floor count','ticker and coverage diagnostics','largest row losses'],
        uncertainty=dict(primary_block=20,sensitivity_blocks=[5,10,40],replicates=2000,seed=17,
                         unit='paired date means, all stocks together; no gap bridging'),
        benchmark='unchanged 20-session historical variance; no fitted parameters',
        context_policy='existing eight-session contexts; new 22-session fields stay partition-local and missing at warm-up, no new row exclusions',
        target='252/5 * sum of next five vendor-adjusted log returns squared; complete mature labels within the same split',
        timing='zero extra delay; unchanged retrospective macro/news timing assumptions',
        interpretation='exploratory reused development period; fixed configuration, not independent confirmation; no per-arm tuning or causal claim',
        extremes_preserved=True,original_results_immutable=True,final_test_held=True,
        sentiment_values_used_as_predictors=False,sentiment_inference_held=True,master_training_held=True,
        qlike_tree_training_held=True,strict_PIT_eligible=False,paid_calls=0,new_acquisitions=0,
        code_sha256={p.name:digest(read_bytes(p)) for p in sorted(Path(__file__).parent.glob('*.py'))},
        runtime=dict(python=platform.python_version(),machine=platform.machine(),
            packages={n:version(n) for n in ('numpy','pandas','pyarrow','exchange-calendars')},
            threads={k:os.environ[k] for k in matched.THREADS}))
    identity=json.loads(json_bytes(identity))
    return dict(run_id=digest(json_bytes(identity)),identity=identity,status='awaiting_exact_training_approval')


def designs(t,ta,d,da):
    """Train-only shared blocks; new value masks are common to every arm."""
    x,z,base=preprocess(t,d)
    common=matched._block(t,d,ta,da,CONTROLS)
    values={}
    for n in dict.fromkeys(n for cols in ARMS.values() for n in cols):
        a,b,state=matched._block(t,d,ta,da,[n])
        kept=len(state['kept'])
        values[n]=(a[:,:kept],b[:,:kept],state)
    result={arm:tuple(np.column_stack([v,common[i]]+[values[n][i] for n in cols])
                      for i,v in enumerate((x,z))) for arm,cols in ARMS.items()}
    return result,dict(baseline=base,common=common[2],values={k:v[2] for k,v in values.items()},
                       value_mask_policy='masks retained in shared controls, not added a second time per arm')


def diagnostics(prediction,data,anchors):
    """Descriptive row/ticker/coverage errors; no subgroup-specific model choice."""
    p=np.asarray(prediction,float);y=data.target
    metrics=variance_metrics(p,y);loss=np.log(p)+y/p
    scored=np.column_stack([anchors[ch+'_scored_days'] for ch in matched.CHANNELS])
    groups={'no_scored_news':scored.sum(1)==0,
            'partial_news_context':(scored.sum(1)>0)&(scored<8).any(1),
            'all_channels_eight_days':(scored==8).all(1),
            'monthly_variance_missing':anchors.proxy_variance_22_missing.to_numpy(bool),
            'monthly_variance_present':~anchors.proxy_variance_22_missing.to_numpy(bool)}
    groups.update({'ticker:'+s:np.asarray([k[1]==s for k in data.keys]) for s in sorted({k[1] for k in data.keys})})
    return dict(metrics=metrics,floor_count=int((p==FLOOR).sum()),
        subsets={n:dict(rows=int(m.sum()),metrics=variance_metrics(p[m],y[m]) if m.any() else None) for n,m in groups.items()},
        worst_rows=[dict(row_id=data.row_ids[j],session=data.keys[j][0],ticker=data.keys[j][1],
            qlike=float(loss[j]),observed_variance=float(y[j]),predicted_variance=float(p[j])) for j in np.argsort(-loss,kind='stable')[:20]],
        overlapping_outcomes_are_not_independent=True)


def execute(proposal,root,approval):
    """Reserve four one-shot attempts; fail closed on changed inputs or interruption."""
    if (approval.get('granted') is not True or approval.get('scope')!=SCOPE or
        approval.get('run_id')!=proposal.get('run_id') or not approval.get('approved_by')):
        raise PermissionError('exact_variance_peer_training_approval_required')
    utc(approval.get('approved_at'))
    i=proposal['identity']
    if plan(i['descriptor'],root)!=proposal:raise ValueError('experiment_changed')
    t,ta,d,da=load(i['descriptor'],root)
    output=relative_file(Path(root).absolute(),i['descriptor']['output'])
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    atomic_json(output/'started.json',dict(run_id=proposal['run_id'],approval=approval,max_fits=4))
    matrices,state=designs(t,ta,d,da)
    atomic_json(output/'preprocessing.json',state)
    records={};hashes={}
    for idx,arm in enumerate(i['grid']):
        atomic_json(output/f'attempt-{idx:04d}.json',dict(index=idx,arm=arm,status='reserved',attempt=1))
        x,z=matrices[arm]
        model=BoostedVariance(**i['configuration']).fit(x,t.target)
        prediction=model.predict(z)
        r=dict(arm=arm,configuration=i['configuration'],row_ids=list(d.row_ids),keys=list(d.keys),
            predicted_variance=prediction.tolist(),training_rows=len(t.keys),development_rows=len(d.keys),
            diagnostics=diagnostics(prediction,d,da),extreme_targets_removed=0,
            model_state=dict(kind='numpy_cart_boosting',intercept=model.intercept,rate=model.rate,trees=model.trees))
        name=f'result-{idx:04d}.json';atomic_json(output/name,r)
        hashes[name]=file_digest(output/name);records[arm]=r
    comparisons={}
    for candidate,reference in CONTRASTS:
        entries={}
        for block in (20,5,10,40):
            try:entries[str(block)]=development_comparison(records[reference],records[candidate],d,block_sessions=block,repeats=2000)
            except ValueError as error:
                if str(error)!='insufficient_contiguous_date_blocks':raise
                entries[str(block)]=dict(status='unavailable',reason=str(error))
        comparisons[candidate+'-vs-'+reference]=entries
    atomic_json(output/'completion.json',dict(run_id=proposal['run_id'],completed_fits=4,
        result_sha256=hashes,contrasts=comparisons,configuration_selection=False,
        historical_variance_reference=diagnostics(d.historical_variance,d,da),
        final_test_held=True,paid_calls=0))
    return dict(run_id=proposal['run_id'],completed_fits=4,final_test_held=True)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','root','approval'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(execute(json.loads(read_bytes(a.plan,limit=64*1024*1024)),a.root,
                             json.loads(read_bytes(a.approval))),indent=2))

