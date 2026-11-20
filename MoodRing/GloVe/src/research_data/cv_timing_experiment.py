"""Prepare or explicitly authorize the changed-design C/V timing sensitivity.

Reconstruct retained timing inputs on the original matched population. Unsupported
news windows remain unknown, not zero coverage. Preparation never fits; execution
requires exact approval and an absent destination. No provider or final-test path.
"""
from pathlib import Path
from importlib.metadata import version
import io
import json
import os
import platform
import numpy as np
import pandas as pd
from .archive import digest, json_bytes, file_digest, read_bytes
from .pilot_runner import relative_file, atomic_json
from .forecast_runners import build_dataset, _array_digest, preprocess
from .retrospective_panel import join_macro
from . import matched_comparison as matched
from . import variance_peer_experiment as parent
from .sparse_timing_features import context_anchors

SCOPE = 'changed_design_cv_timing_train_dev_v1'
CONDITIONS = ((0,0),(1,0),(5,0),(20,0),(0,1),(0,5))
HORIZONS = parent.HORIZONS
MASKS = tuple(n+'_missing' for n in HORIZONS)
CONTROLS = (*matched.COVERAGE,*MASKS)
ARMS = {'C': (), 'V': HORIZONS}
TREE = dict(parent.TREE)


def coverage_anchors(rows,groups,contexts):
    """Keep all ordered rows; unsupported delay windows have unknown coverage.

    A fully supported window with no articles has observed zero counts. A window
    requiring unavailable pre-partition news has null counts and null daily masks.
    Preprocessing subsequently appends missingness indicators to both arms.
    """
    if not rows.row_id.is_unique or set(rows.split)-{'train','dev'}:
        raise ValueError('ordered_train_dev_rows_required')
    c=contexts.set_index(['row_id','channel'],verify_integrity=True)
    expected=pd.MultiIndex.from_product([rows.row_id,matched.CHANNELS])
    if set(c.index)!=set(expected):raise ValueError('context_population_binding')
    c=c.loc[expected]
    support=c.supported.unstack().reindex(rows.row_id)
    if support.isna().any().any() or support.nunique(axis=1).ne(1).any():
        raise ValueError('inconsistent_channel_support')
    if not np.isin(support.to_numpy(),[True,False]).all():raise ValueError('invalid_support')
    valid=support.iloc[:,0].astype(bool).to_numpy()
    if valid.any():
        rebuilt=context_anchors(rows.loc[valid],groups,contexts).set_index('row_id')
        result=rebuilt.reindex(rows.row_id)[list(matched.COVERAGE)].astype(float)
    else:result=pd.DataFrame(np.nan,index=rows.row_id,columns=matched.COVERAGE)
    if not result.loc[~valid].isna().all().all():raise ValueError('unknown_not_zero')
    if not np.isfinite(result.loc[valid].to_numpy()).all():raise ValueError('supported_coverage_missing')
    return result.reset_index(),valid


def invariant_population(reference,changed):
    """Timing may change macro values, but never keys, outcomes or price inputs."""
    a=reference.identity();b=changed.identity()
    for key in a:
        if key!='features_sha256' and a[key]!=b[key]:raise ValueError('population_changed:'+key)
    price=[i for i,n in enumerate(reference.names) if not n.startswith('macro_')]
    if not np.array_equal(reference.features[:,price],changed.features[:,price],equal_nan=True):
        raise ValueError('nonmacro_features_changed')


def load(descriptor,root):
    """Verify source hashes and independently reconstruct each prepared variant."""
    root=Path(root).absolute()
    for path,sha in descriptor['bindings'].items():
        if file_digest(relative_file(root,path))!=sha:raise ValueError('changed_input:'+path)
    def table(path):
        if path not in descriptor['bindings']:raise ValueError('unbound_input')
        raw=read_bytes(relative_file(root,path),limit=512*1024*1024)
        if digest(raw)!=descriptor['bindings'][path]:raise ValueError('changed_during_read')
        return pd.read_parquet(io.BytesIO(raw))
    pdsc=descriptor['parent_descriptor']
    if any(descriptor['bindings'].get(p)!=h for p,h in pdsc['bindings'].items()):
        raise ValueError('parent_binding_missing')
    t,ta,d,da=parent.load(pdsc,root)
    rows=table(descriptor['ordered_rows'])
    if list(rows.row_id)!=list(t.row_ids)+list(d.row_ids):raise ValueError('row_order_changed')
    for original in (t,d):
        r=rows[rows.split==original.split]
        if list(r.row_id)!=list(original.row_ids) or list(zip(r.session,r.ticker))!=list(original.keys):
            raise ValueError('context_key_binding')
        windows=[[original.feature_keys[j][0] for j in win] for win in original.window_indices]
        if [list(x) for x in r.context_sessions]!=windows:raise ValueError('context_membership_changed')
    macro=table(descriptor['macro'])
    if set(macro.extra_session_lag)!={0,1,5,20}:raise ValueError('macro_variants')
    if any(str(s)[:4]>'2024' for s in macro.session):raise ValueError('final_test_held')
    coverage={}
    for lag in (0,1,5):
        part=descriptor['news'][str(lag)]
        groups=table(part['groups'])
        from .sentiment_inventory import partition
        if any(partition(s) not in ('train','dev') for s in groups.effective_session.unique()):
            raise ValueError('final_test_news_held')
        coverage[lag]=coverage_anchors(rows,groups,table(part['contexts']))
    expected_names={f'm{m}-n{n}' for m,n in CONDITIONS}
    if set(descriptor['variants'])!=expected_names:raise ValueError('fixed_timing_grid')
    result={}
    for ml,nl in CONDITIONS:
        condition=f'm{ml}-n{nl}';items=[]
        if set(descriptor['variants'][condition])!={'train','dev'}:raise ValueError('train_dev_only')
        for original,anchors in ((t,ta),(d,da)):
            split=original.split;src=pdsc['datasets'][split]
            part=descriptor['variants'][condition][split]
            source=table(src['features'])
            rebuilt,_=join_macro(source.drop(columns=[n for n in source if n.startswith('macro_')]),macro[macro.extra_session_lag==ml])
            f=table(part['features'])
            data=build_dataset(f,table(src['contexts']),table(src['labels']),pdsc['feature_names'],split)
            check=build_dataset(rebuilt,table(src['contexts']),table(src['labels']),pdsc['feature_names'],split)
            invariant_population(original,data)
            if not np.allclose(data.features,check.features,equal_nan=True,rtol=0,atol=1e-12):
                raise ValueError('macro_reconstruction')
            if ml==0 and not np.allclose(original.features,data.features,equal_nan=True,rtol=0,atol=1e-12):
                raise ValueError('zero_macro_changed')
            a=table(part['anchors'])
            if list(a.row_id)!=list(original.row_ids):raise ValueError('anchor_order')
            if list(a.columns)!=['row_id',*CONTROLS,*HORIZONS]:raise ValueError('extra_peer_signed_or_tone_controls')
            a=a.drop(columns='row_id')
            cov,valid=coverage[nl]
            cov=cov.set_index('row_id').loc[list(original.row_ids),list(matched.COVERAGE)]
            if not np.allclose(a[list(matched.COVERAGE)],cov,equal_nan=True,rtol=0,atol=1e-12):
                raise ValueError('coverage_reconstruction')
            if nl==0 and not np.allclose(cov,anchors[list(matched.COVERAGE)],equal_nan=True,rtol=0,atol=1e-12):
                raise ValueError('zero_news_changed')
            for n in (*HORIZONS,*MASKS):
                if not np.array_equal(a[n],anchors[n],equal_nan=True):raise ValueError('variance_or_mask_changed')
            if int(a.proxy_variance_22.isna().sum())!=140:raise ValueError('monthly_null_policy')
            items.extend((data,a))
        result[condition]=tuple(items)
    return result


def designs(t,ta,d,da):
    """Training-only preprocessing; three variance masks shared by C and V."""
    for a in (ta,da):
        if tuple(a.columns)!=(*CONTROLS,*HORIZONS):raise ValueError('frozen_anchor_layout')
    x,z,base=preprocess(t,d)
    common=matched._block(t,d,ta,da,CONTROLS)
    values={}
    for n in HORIZONS:
        a,b,state=matched._block(t,d,ta,da,[n]);kept=len(state['kept'])
        values[n]=(a[:,:kept],b[:,:kept],state)
    matrices={arm:tuple(np.column_stack([v,common[i]]+[values[n][i] for n in cols])
              for i,v in enumerate((x,z))) for arm,cols in ARMS.items()}
    return matrices,dict(baseline=base,common=common[2],values={k:v[2] for k,v in values.items()},
        value_mask_policy='three explicit variance masks shared; no duplicate per-arm variance masks')


def plan(descriptor,root):
    """Freeze twelve proposed fits without fitting preprocessing or a model."""
    if any(os.environ.get(k)!='2' for k in matched.THREADS):raise ValueError('two_threads_required')
    variants=load(descriptor,root)
    if any((len(t.keys),len(d.keys))!=(4920,4900) for t,_,d,_ in variants.values()):
        raise ValueError('approved_population')
    identities={k:{s:dict(dataset=data.identity(),anchor_sha256=_array_digest(a.to_numpy()),
        nulls={n:int(a[n].isna().sum()) for n in a}) for s,data,a in [('train',t,ta),('dev',d,da)]}
        for k,(t,ta,d,da) in variants.items()}
    contrasts=[(k+'/V',k+'/C') for k in variants]+[(k+'/'+a,'m0-n0/'+a) for k in variants if k!='m0-n0' for a in ARMS]
    package=Path(__file__).parent
    identity=dict(scope=SCOPE,descriptor=descriptor,variants=identities,
        conditions=[list(c) for c in CONDITIONS],arms={k:list(v) for k,v in ARMS.items()},
        common_controls=list(CONTROLS),configuration=TREE,
        grid=[k+'/'+a for k in variants for a in ARMS],contrasts=contrasts,
        max_fits=12,max_reserved_attempts=12,attempts_per_fit=1,max_boosting_iterations=600,
        automatic_retry=False,automatic_resume=False,configuration_selection=False,
        threads=2,seed=17,deterministic=True,forecast_floor=parent.FLOOR,
        fitting_loss='annualized variance squared error; NumPy bounded CART boosting',
        primary_metric='mean QLIKE = log(p)+y/p',secondary_metrics=['volatility MAE','volatility RMSE','floor count','ticker/coverage diagnostics','worst row losses'],
        uncertainty=dict(primary_block=20,sensitivity_blocks=[5,10,40],replicates=2000,seed=17,unit='paired date means; all stocks together; no gap bridging'),
        timing='extra exchange-session delay on reconstructed macro vintage selection OR news eligibility; no joint delays; price variance unchanged',
        missingness='retain all rows; unsupported news windows null, observed no-news counts zero; training-only imputation and appended masks; partition-local 140 monthly nulls per split',
        preprocessing='baseline unique daily training rows; coverage and variance training anchors; independently fitted per timing variant, shared C/V blocks',
        target='unchanged 252/5 sum next five vendor-adjusted log returns squared; label_asof maturity and split purge unchanged',
        design_change='remove peer/support and signed controls from prior C/V; retain news coverage and three shared variance masks; not a direct rerun',
        interpretation='reused development data, exploratory changed-design sensitivity; delays are assumptions, not historical receipt evidence',
        extremes_preserved=True,strict_PIT_eligible=False,final_test_held=True,paid_calls=0,new_acquisitions=0,
        sentiment_values_as_features=False,peer_features=False,signed_features=False,
        code_sha256={str(p.relative_to(package.parent)):file_digest(p) for p in [*sorted(package.glob('*.py')),package.parent/'utils/research_lineage.py',package.parent/'utils/experiment_tracker.py']},
        runtime=dict(python=platform.python_version(),machine=platform.machine(),packages={n:version(n) for n in ('numpy','pandas','pyarrow','exchange-calendars')},threads={k:os.environ[k] for k in matched.THREADS}))
    identity=json.loads(json_bytes(identity))
    output=relative_file(Path(root).absolute(),descriptor['output'])
    if output.exists():raise FileExistsError('execution_directory_must_be_absent')
    return dict(run_id=digest(json_bytes(identity)),identity=identity,status='awaiting_exact_training_approval')


def diagnostics(prediction,data,anchors):
    """Separate unknown delayed coverage from observed no-scored-news coverage."""
    from .risk_evaluation import variance_metrics
    result=parent.diagnostics(prediction,data,anchors)
    unknown=anchors[list(matched.COVERAGE)].isna().any(axis=1).to_numpy()
    result['subsets']['unknown_delayed_news_coverage']=dict(rows=int(unknown.sum()),
        metrics=variance_metrics(np.asarray(prediction)[unknown],data.target[unknown]) if unknown.any() else None)
    result['coverage_nulls']={n:int(anchors[n].isna().sum()) for n in matched.COVERAGE}
    return result


def execute(proposal,root,approval):
    """Twelve one-shot fits only after revalidation; preserve reservations on failure.

    Metadata lineage references artifacts rather than embedding their payloads.
    Existing output, interruption or a failed attempt requires a new reviewed run;
    there is no automatic retry, resume or promotion of results.
    """
    from datetime import datetime,timezone
    from .point_in_time import utc
    from .forecast_runners import BoostedVariance,development_comparison
    from utils.research_lineage import ResearchLineage,make_record,artifact_reference
    if (approval.get('granted') is not True or approval.get('scope')!=SCOPE or
        approval.get('run_id')!=proposal.get('run_id') or not approval.get('approved_by')):
        raise PermissionError('exact_cv_timing_approval_required')
    utc(approval.get('approved_at'))
    timestamp=datetime.fromisoformat(approval['approved_at'].replace('Z','+00:00'))
    if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds()!=0:
        raise ValueError('approval_timestamp_must_be_UTC')
    i=proposal['identity'];root=Path(root).absolute()
    if plan(i['descriptor'],root)!=proposal:raise ValueError('experiment_changed')
    variants=load(i['descriptor'],root)
    output=relative_file(root,i['descriptor']['output'])
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    atomic_json(output/'proposal.json',proposal);atomic_json(output/'approval.json',approval)
    atomic_json(output/'started.json',dict(run_id=proposal['run_id'],max_fits=12,approval=approval))
    store=ResearchLineage(output/'lineage',read_only=False)
    approval_id=digest(json_bytes(approval))
    def event(kind,status,refs,parents=(),index=None):
        return store.append(make_record(kind,proposal['run_id'],'development_only',status,
            datetime.now(timezone.utc).isoformat(),parents=parents,
            artifacts=[artifact_reference(root,str((output/name).relative_to(root)),role) for role,name in refs],
            code_commit=i['descriptor'].get('code_revision'),configuration_commit=i['descriptor'].get('configuration_revision'),
            approval_id=approval_id,attempt_index=index))
    prep=event('preparation','prepared',[('packet','proposal.json')])
    auth=event('approval','authorized',[('approval','approval.json')],[prep])
    run=event('run','reserved',[('receipt','started.json')],[auth])
    hashes={};records={};parents=[]
    try:
        for condition,(t,ta,d,da) in variants.items():
            matrices,state=designs(t,ta,d,da)
            statefile='preprocessing-'+condition+'.json';atomic_json(output/statefile,state)
            pre=event('preprocessing','completed',[('preprocessing',statefile)],[run])
            for arm in ARMS:
                idx=len(records);key=condition+'/'+arm
                attempt=f'attempt-{idx:04d}.json'
                atomic_json(output/attempt,dict(index=idx,condition=condition,arm=arm,status='reserved',attempt=1))
                reservation=event('attempt','reserved',[('reservation',attempt)],[pre],idx)
                x,z=matrices[arm];model=BoostedVariance(**i['configuration']).fit(x,t.target)
                prediction=model.predict(z)
                record=dict(condition=condition,arm=arm,configuration=i['configuration'],keys=list(d.keys),row_ids=list(d.row_ids),
                    predicted_variance=prediction.tolist(),training_rows=len(t.keys),development_rows=len(d.keys),
                    diagnostics=diagnostics(prediction,d,da),extreme_targets_removed=0,
                    model_state=dict(kind='numpy_cart_boosting',intercept=model.intercept,rate=model.rate,trees=model.trees))
                name=f'result-{idx:04d}.json';atomic_json(output/name,record)
                hashes[name]=file_digest(output/name);records[key]=record
                model_id=event('model','completed',[('model',name)],[reservation])
                predictions=event('predictions','completed',[('predictions',name)],[model_id])
                parents.append(event('metrics','completed',[('metrics',name)],[predictions]))
        contrasts={}
        for candidate,reference in i['contrasts']:
            entries={}
            for block in (20,5,10,40):
                try:entries[str(block)]=development_comparison(records[reference],records[candidate],variants['m0-n0'][2],block_sessions=block,repeats=2000)
                except ValueError as error:
                    if str(error)!='insufficient_contiguous_date_blocks':raise
                    entries[str(block)]=dict(status='unavailable',reason=str(error))
            contrasts[candidate+'-vs-'+reference]=entries
        atomic_json(output/'contrasts.json',contrasts)
        contrast=event('contrast','completed',[('contrast','contrasts.json')],parents)
        atomic_json(output/'completion.json',dict(run_id=proposal['run_id'],completed_fits=len(records),reserved_attempts=len(records),
            result_sha256=hashes,configuration_selection=False,contrasts_sha256=file_digest(output/'contrasts.json'),
            historical_variance_reference={c:diagnostics(v[2].historical_variance,v[2],v[3]) for c,v in variants.items()},
            final_test_held=True,paid_calls=0,acquisitions=0,audit_release_pending=True))
        event('run','completed',[('receipt','completion.json')],[run,contrast])
    except BaseException as error:
        atomic_json(output/'failure.json',dict(run_id=proposal['run_id'],completed_fits=len(records),error_type=type(error).__name__,automatic_resume=False))
        event('run','failed',[('receipt','failure.json')],[run])
        raise
    return dict(run_id=proposal['run_id'],completed_fits=len(records),audit_release_pending=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','root','approval'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(execute(json.loads(read_bytes(args.plan,limit=64*1024*1024)),args.root,
        json.loads(read_bytes(args.approval))),indent=2))
