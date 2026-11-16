"""Purged, expanding training-period folds for a bounded tree comparison.

This study never loads development or test tables. Preparation hashes inputs but
never fits. A separate exact approval is required for the 27 real-data fits.
"""
from dataclasses import replace
from pathlib import Path
from importlib.metadata import version
import json,os,platform
import io
import pandas as pd
import numpy as np
from .archive import digest,json_bytes,read_bytes,file_digest
from .pilot_runner import relative_file,atomic_json
from .sentiment_policy import verified_table
from .forecast_runners import build_dataset,BoostedVariance,FLOOR,_array_digest
from .comparison_protocol import FrozenPreprocessor,segmented_date_interval
from .risk_evaluation import variance_metrics
from .point_in_time import sessions_between,utc
from .matched_comparison import THREADS

SCOPE='tree_training_period_chronological_validation_v1'
BASE=dict(max_leaf_nodes=7,learning_rate=.03,max_iter=100,max_bins=32,min_leaf=5)
CONFIGS={
 'C0_current':dict(BASE), 'C1_leaves3':dict(BASE,max_leaf_nodes=3),
 'C2_leaves11':dict(BASE,max_leaf_nodes=11), 'C3_minleaf10':dict(BASE,min_leaf=10),
 'C4_minleaf20':dict(BASE,min_leaf=20), 'C5_iterations50':dict(BASE,max_iter=50),
 'C6_iterations150':dict(BASE,max_iter=150), 'C7_rate005':dict(BASE,learning_rate=.05),
 'C8_rate001_iter200':dict(BASE,learning_rate=.01,max_iter=200)}
FOLDS=(('F1','2021-07-01','2021-12-31'),('F2','2022-01-03','2022-06-30'),('F3','2022-07-01','2022-12-30'))


def subset(data,indices):
    """Keep source partition train; fold validation is a role, not global dev."""
    indices=np.asarray(indices,dtype=int)
    if not len(indices) or np.any(np.diff(indices)<=0):raise ValueError('ordered_nonempty_subset')
    needed=np.unique(data.window_indices[indices].reshape(-1));mapping={old:new for new,old in enumerate(needed)}
    return replace(data,feature_keys=tuple(data.feature_keys[k] for k in needed),
        feature_cutoffs=tuple(data.feature_cutoffs[k] for k in needed),features=data.features[needed].copy(),
        window_indices=np.asarray([[mapping[k] for k in row] for row in data.window_indices[indices]]),
        keys=tuple(data.keys[k] for k in indices),row_ids=tuple(data.row_ids[k] for k in indices),
        target=data.target[indices].copy(),historical_variance=data.historical_variance[indices].copy())


def folds(data,label_ends,specs=FOLDS):
    """Purge any fitting horizon reaching validation; validation ends inside fold.

    Earlier price context may feed the first validation anchor, since those are
    past observations. It does not enter fitting or preprocessing unless included
    in a fitting anchor's historical window. Purged outcomes are never fit.
    """
    if data.split!='train' or len(label_ends)!=len(data.keys):raise ValueError('training_period_only')
    result=[];used=set()
    for name,start,end in specs:
        fit=[i for i,(day,_) in enumerate(data.keys) if day<start and label_ends[i]<start]
        valid=[i for i,(day,_) in enumerate(data.keys) if start<=day<=end and label_ends[i]<=end]
        purged=[i for i,(day,_) in enumerate(data.keys) if day<start<=label_ends[i]]
        tail=[i for i,(day,_) in enumerate(data.keys) if start<=day<=end<label_ends[i]]
        if used.intersection(valid):raise ValueError('overlapping_validation_rows')
        used.update(valid)
        a,b=subset(data,fit),subset(data,valid)
        if max(label_ends[i] for i in fit)>=start:raise ValueError('unpurged_outcome')
        if max(d for d,_ in a.feature_keys)>=start:raise ValueError('future_fitting_features')
        result.append(dict(name=name,start=start,end=end,train=a,validation=b,
            fit_indices=fit,validation_indices=valid,purged_row_ids=[data.row_ids[i] for i in purged],
            validation_tail_exclusions=[data.row_ids[i] for i in tail]))
    return result


def prepare_fold(a,b):
    """Fit means, scales and dropped columns on this fold's fitting rows only."""
    if a.split!='train' or b.split!='train' or a.names!=b.names or max(k[0] for k in a.keys)>=min(k[0] for k in b.keys):
        raise ValueError('fold_chronology')
    scaler=FrozenPreprocessor().fit(a.features,a.feature_cutoffs,a.feature_keys,a.names)
    def transform(d):
        v=scaler.transform(d.features)[d.window_indices]
        return v.reshape(len(v),-1)
    state=dict(names=list(a.names),keep=scaler.keep.tolist(),dropped=scaler.dropped,
        mean=scaler.scaler.mean.tolist(),scale=scaler.scaler.scale.tolist(),
        unique_training_feature_rows=len(a.feature_keys),missingness_masks=True)
    return transform(a),transform(b),state


def load(descriptor,root):
    """Read only the frozen original training partition after binding checks."""
    root=Path(root).absolute()
    for name,sha in descriptor['bindings'].items():
        if file_digest(relative_file(root,name))!=sha:raise ValueError('changed_input:'+name)
    directory=relative_file(root,descriptor['training_dataset'])
    if any(descriptor['training_dataset']+'/'+n+'.parquet' not in descriptor['bindings'] for n in ('features','contexts','labels')):
        raise ValueError('unbound_training_table')
    frames=[verified_table(directory,n)[1].to_pandas(use_threads=False) for n in ('features','contexts','labels')]
    data=build_dataset(*frames,descriptor['feature_names'],'train')
    y=frames[2].set_index(['session','ticker'])
    ends=[y.loc[k,'label_end_session'] for k in data.keys]
    return data,ends,folds(data,ends)


def plan(descriptor,root):
    """Freeze exact rows and budgets; no preprocessing or model fitting."""
    data,ends,parts=load(descriptor,root)
    if len(data.keys)!=4920:raise ValueError('original_training_population_changed')
    if any(os.environ.get(k)!='2' for k in THREADS):raise ValueError('two_threads_required')
    evidence=[]
    for f in parts:
        evidence.append(dict(name=f['name'],validation_start=f['start'],validation_end=f['end'],
            train=f['train'].identity(),validation=f['validation'].identity(),
            purged_row_ids=f['purged_row_ids'],validation_tail_exclusions=f['validation_tail_exclusions']))
    identity=dict(version=SCOPE,scope=SCOPE,descriptor=descriptor,folds=evidence,configs=CONFIGS,
        max_fits=27,max_reserved_attempts=27,max_attempts_per_fit=1,max_boosting_iterations=3000,
        max_leaves_per_tree=11,threads=2,seed=17,automatic_retry=False,automatic_resume=False,
        feature_arm='unchanged price + macro + technical baseline; 59 input columns, eight sessions; no sentiment or coverage controls',
        primary_selection='minimum pooled out-of-fold mean QLIKE; exact ties: fewer leaves, fewer iterations, larger min_leaf, config id',
        secondary_metrics=['volatility_mae','volatility_rmse','floor_count','worst row excess QLIKE','worst five-date block','event-cluster diagnostic'],
        uncertainty='paired date means; 20-session blocks primary, 5/10/40 sensitivity; 2000 draws seed17; no crossing folds or gaps',
        safeguards='unchanged annualized variance targets and floor; no extreme trimming; no permutation CV; no val early stopping',
        development_selection_optimism='training-fold results remain exploratory after earlier research; not a new untouched holdout',
        no_refit_or_development_evaluation=True,final_test_held=True,other_packets_not_authorized=True,
        qlike_tree_training_held=True,real_scores_generated=0,new_acquisitions=0,paid_calls=0,strict_PIT=False,
        code_sha256={p.name:digest(read_bytes(p)) for p in sorted(Path(__file__).parent.glob('*.py'))},
        runtime=dict(python=platform.python_version(),machine=platform.machine(),packages={n:version(n) for n in
            ('numpy','pandas','pyarrow','exchange-calendars')},threads={k:os.environ[k] for k in THREADS}))
    identity=json.loads(json_bytes(identity))
    return dict(run_id=digest(json_bytes(identity)),identity=identity,status='awaiting_exact_training_approval')


def row_losses(predictions,y):
    """Raw QLIKE for ranking; floor-constrained excess loss for concentration only."""
    p=np.asarray(predictions,float);y=np.asarray(y,float)
    variance_metrics(p,y)
    oracle=np.maximum(y,FLOOR)
    raw=np.log(p)+y/p
    return raw,np.maximum(raw-(np.log(oracle)+y/oracle),0.)



def tail_diagnostics(data,predictions,events):
    """Attribute losses to dominant realized-return dates, never to inferred news.

    Each outcome has one diagnostic dominant date. That assignment does not make
    overlapping windows independent, and no event or extreme row is discarded.
    """
    event=events.set_index('row_id',verify_integrity=True).loc[list(data.row_ids)]
    if not np.allclose(event.annualized_variance,data.target,atol=1e-12,rtol=1e-12):
        raise ValueError('event_target_binding')
    raw,excess=row_losses(predictions,data.target)
    frame=pd.DataFrame(dict(row_id=data.row_ids,date=[k[0] for k in data.keys],ticker=[k[1] for k in data.keys],
        dominant_session=event.dominant_session.to_numpy(),excess_qlike=excess,qlike=raw))
    groups=frame.groupby(['ticker','dominant_session']).agg(rows=('row_id','size'),total_excess_qlike=('excess_qlike','sum'),
        mean_excess_qlike=('excess_qlike','mean')).reset_index().sort_values(['total_excess_qlike','ticker','dominant_session'],ascending=[False,True,True])
    total=float(excess.sum());dates=sorted(set(frame.date));order={d:i//5 for i,d in enumerate(dates)}
    blocks=frame.assign(block=frame.date.map(order)).groupby('block').agg(first_date=('date','min'),last_date=('date','max'),
        dates=('date','nunique'),mean_excess_qlike=('excess_qlike','mean'),rows=('row_id','size')).reset_index()
    return dict(event_unit='ticker and dominant squared-return date; not a causal/scheduled event',
        worst_events=groups.head(10).to_dict('records'),top_five_event_excess_share=float(groups.head(5).total_excess_qlike.sum()/total) if total else 0.,
        worst_five_date_blocks=blocks.sort_values('mean_excess_qlike',ascending=False).head(5).to_dict('records'),
        worst_rows=frame.nlargest(10,'excess_qlike').to_dict('records'),extremes_removed=0)

def summarize(records,parts,events):
    """Pool identical out-of-fold rows, retaining event/tail diagnostics separately."""
    expected={(f['name'],c) for f in parts for c in CONFIGS}
    if len(records)!=len(expected) or {(r['fold'],r['configuration_id']) for r in records}!=expected:
        raise ValueError('incomplete_comparison_grid')
    for r in records:
        data=next(f['validation'] for f in parts if f['name']==r['fold'])
        if r['row_ids']!=list(data.row_ids) or r['keys']!=[list(k) for k in data.keys]:
            raise ValueError('prediction_population_order')
    scores={};contrasts={}
    for name in CONFIGS:
        chosen=[next(r for r in records if r['configuration_id']==name and r['fold']==f['name']) for f in parts]
        y=np.concatenate([f['validation'].target for f in parts]);p=np.concatenate([r['predicted_variance'] for r in chosen])
        raw,excess=row_losses(p,y)
        scores[name]=dict(metrics=variance_metrics(p,y),floor_count=int((p==FLOOR).sum()),
            worst_row_excess_qlike=float(excess.max()),total_excess_qlike=float(excess.sum()),
            validation_rows=len(y),per_fold=[dict(fold=r['fold'],metrics=r['metrics'],tail_diagnostics=r['tail_diagnostics']) for r in chosen])
        if name=='C0_current':continue
        by_fold={}
        for f,r in zip(parts,chosen):
            ref=next(v for v in records if v['fold']==f['name'] and v['configuration_id']=='C0_current')
            a,_=row_losses(r['predicted_variance'],f['validation'].target)
            b,_=row_losses(ref['predicted_variance'],f['validation'].target)
            dates,tickers=zip(*f['validation'].keys);intervals={}
            for block in (20,5,10,40):
                try:intervals[str(block)]=segmented_date_interval(dates,tickers,a,b,block_sessions=block,repeats=2000)
                except ValueError as error:
                    if str(error)!='insufficient_contiguous_date_blocks':raise
                    intervals[str(block)]=dict(status='unavailable',reason=str(error))
            by_fold[f['name']]=intervals
        contrasts[name]=by_fold
    winner=min(CONFIGS,key=lambda n:(scores[n]['metrics']['qlike'],CONFIGS[n]['max_leaf_nodes'],
        CONFIGS[n]['max_iter'],-CONFIGS[n]['min_leaf'],n))
    return dict(scores=scores,selected_by_pooled_qlike=winner,paired_fold_intervals=contrasts,
                intervals_descriptive_after_selection=True,refit_authorized=False)


def execute(proposal,root,approval):
    """Reserve every fit before execution; incomplete output cannot auto-resume."""
    if (approval.get('granted') is not True or approval.get('scope')!=SCOPE or
        approval.get('run_id')!=proposal['run_id'] or not approval.get('approved_by')):
        raise PermissionError('exact_chronological_study_approval_required')
    utc(approval.get('approved_at'));i=proposal['identity']
    if plan(i['descriptor'],root)!=proposal:raise ValueError('study_changed')
    data,ends,parts=load(i['descriptor'],root)
    event_path=i['descriptor']['event_labels']
    if event_path not in i['descriptor']['bindings']:raise ValueError('unbound_event_labels')
    blob=read_bytes(relative_file(Path(root).absolute(),event_path))
    if digest(blob)!=i['descriptor']['bindings'][event_path]:raise ValueError('event_labels_changed')
    events=pd.read_parquet(io.BytesIO(blob))
    if events.row_id.tolist()!=list(data.row_ids):raise ValueError('event_label_population')
    output=relative_file(Path(root).absolute(),i['descriptor']['output']);output.mkdir(parents=True,exist_ok=False,mode=0o700)
    atomic_json(output/'started.json',dict(run_id=proposal['run_id'],approval=approval,max_fits=27))
    records=[];hashes={};index=0
    for f in parts:
        a,b=f['train'],f['validation'];x,z,state=prepare_fold(a,b)
        atomic_json(output/(f['name']+'-preprocessing.json'),state)
        for name,cfg in CONFIGS.items():
            atomic_json(output/f'attempt-{index:03d}.json',dict(index=index,fold=f['name'],configuration_id=name,status='reserved',attempt=1))
            model=BoostedVariance(**cfg).fit(x,a.target);p=model.predict(z)
            record=dict(fold=f['name'],configuration_id=name,config=cfg,training_rows=len(a.keys),
                row_ids=list(b.row_ids),keys=[list(k) for k in b.keys],predicted_variance=p.tolist(),metrics=variance_metrics(p,b.target),tail_diagnostics=tail_diagnostics(b,p,events),
                historical_variance_metrics=variance_metrics(b.historical_variance,b.target),
                model_state=dict(intercept=model.intercept,rate=model.rate,trees=model.trees),extremes_removed=0)
            path=output/f'result-{index:03d}.json';atomic_json(path,record);hashes[path.name]=digest(read_bytes(path))
            records.append(record);index+=1
    atomic_json(output/'completion.json',dict(run_id=proposal['run_id'],completed_fits=index,results_sha256=hashes,
        **summarize(records,parts,events),development_evaluated=False,final_test_held=True,paid_calls=0))
    return dict(run_id=proposal['run_id'],completed_fits=index,final_test_held=True)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','root','approval'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(execute(json.loads(read_bytes(a.plan,limit=64*1024*1024)),a.root,json.loads(read_bytes(a.approval))),indent=2))
