"""Bounded retrospective forecasting runners; real fitting needs exact release.

All file loading/publication is owned by the calling release adapter. The module
does not fetch data, score final test, or turn synthetic tests into authorization.
"""
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from importlib.metadata import version
import platform
import math
import numpy as np
import pandas as pd
from .archive import digest,json_bytes
from .comparison_protocol import FrozenPreprocessor,PROTOCOL,segmented_date_interval
from .point_in_time import sessions_between,utc
from .research_experiment import split_for
from .sentiment_inventory import partition
from .risk_evaluation import variance_metrics

VERSION='numpy-retrospective-forecasters-v1'
FLOOR=1e-8
BASELINE_CONFIGS=[
    dict(family='regularized_regression',alpha=a) for a in (.1,1.,10.,100.)
]+[
    dict(family='boosted_trees',max_leaf_nodes=l,learning_rate=r,max_iter=n,max_bins=32,min_leaf=5)
    for l,r,n in product((7,15),(.03,.1),(100,200))
]
SEEDS=(17,29,43)


def _array_digest(value):
    x=np.asarray(value,float)
    return digest(json_bytes([[None if np.isnan(v) else float(v) for v in row] for row in x.reshape(len(x),-1)]))


@dataclass
class PreparedSplit:
    """Unique feature rows plus exact eight-session references and outcome rows."""
    split:str
    names:tuple
    feature_keys:tuple
    feature_cutoffs:tuple
    features:np.ndarray
    window_indices:np.ndarray
    keys:tuple
    row_ids:tuple
    target:np.ndarray
    historical_variance:np.ndarray

    def identity(self):
        return dict(split=self.split,names=list(self.names),feature_keys=[list(k) for k in self.feature_keys],
            feature_cutoffs=list(self.feature_cutoffs),features_sha256=_array_digest(self.features),
            windows_sha256=_array_digest(self.window_indices),keys=[list(k) for k in self.keys],
            row_ids=list(self.row_ids),targets_sha256=_array_digest(self.target[:,None]),
            benchmark_sha256=_array_digest(self.historical_variance[:,None]))


def build_dataset(features,contexts,labels,feature_names,split):
    """Validate supplied train/dev slices, exact context calendar and label horizon.

    Future labels need maturity by label_asof, not by the earlier feature cutoff.
    These retrospective assertions do not establish strict PIT source availability.
    Caller supplies only the named partition; passing final-test rows fails closed.
    """
    if split not in ('train','dev'):raise ValueError('final_test_held')
    names=tuple(feature_names)
    if not names or len(names)!=len(set(names)):raise ValueError('feature_names')
    if any(any(s in n for s in ('target','label','annualized_variance','prediction')) for n in names):
        raise ValueError('outcome_in_features')
    for frame in (features,contexts,labels):
        if frame.duplicated(['ticker','session']).any():raise ValueError('duplicate_keys')
        if any(partition(s)!=split for s in frame.session):raise ValueError('partition_slice_required')
    f=features.set_index(['session','ticker'],verify_integrity=True)
    y=labels.set_index(['session','ticker'],verify_integrity=True)
    selected=contexts[contexts.primary_matched].sort_values(['session','ticker'])
    if selected.empty:raise ValueError('empty_matched_population')
    # Construct the exchange calendar once per partition, rather than once per
    # stock/window. This is the same session boundary, with explicit end matching.
    schedule=sessions_between(min(features.session.min(),contexts.session.min()),
                              max(features.session.max(),labels.label_end_session.max()))
    positions={day:i for i,(day,_) in enumerate(schedule)}
    def calendar_range(start,end):
        if start not in positions or end not in positions or positions[end]<positions[start]:
            raise ValueError('non_session_horizon_or_context')
        return schedule[positions[start]:positions[end]+1]
    needed=set();windows=[];keys=[];row_ids=[];targets=[];benchmarks=[]
    for r in selected.to_dict('records'):
        key=(r['session'],r['ticker']);days=list(r['context_sessions'])
        if len(days)!=8 or days[-1]!=r['session'] or days!=[d for d,_ in calendar_range(days[0],days[-1])]:
            raise ValueError('eight_contiguous_sessions_required')
        if any(partition(d)!=split for d in days):raise ValueError('cross_split_context')
        if r.get('known_gap_in_context',False):raise ValueError('source_gap_context')
        if r['row_id']!=digest(json_bytes([r['ticker'],r['session']])):raise ValueError('row_id_binding')
        anchor=f.loc[key];target=y.loc[key]
        horizon=calendar_range(r['session'],target['label_end_session'])
        if len(horizon)!=6 or split_for(anchor['cutoff'],horizon[-1][1])!=split:
            raise ValueError('five_session_split_purge')
        if target['status']!='mature_retrospective' or target['split']!=split:raise ValueError('immature_label')
        if utc(target['cutoff'])!=utc(anchor['cutoff']):raise ValueError('label_cutoff_binding')
        if utc(target['label_received_at'])>utc(target['label_asof']) or horizon[-1][1]>utc(target['label_asof']):
            raise ValueError('label_maturity')
        if len(target['source_record_sha256'])!=6:raise ValueError('label_source_horizon')
        value=float(target['annualized_variance'])
        if not math.isfinite(value) or value<0:raise ValueError('invalid_variance_target')
        window=[(d,r['ticker']) for d in days]
        for k in window:
            feature=f.loc[k]
            if utc(feature['cutoff'])>utc(anchor['cutoff']):raise ValueError('future_context_feature')
        needed.update(window);windows.append(window);keys.append(key);row_ids.append(r['row_id']);targets.append(value)
        b=float(anchor['historical_variance_20'])
        if not math.isfinite(b) or b<0:raise ValueError('historical_variance_missing')
        benchmarks.append(max(b,FLOOR))
    feature_keys=tuple(sorted(needed));lookup={k:i for i,k in enumerate(feature_keys)}
    frame=f.loc[list(feature_keys)]
    values=frame[list(names)].to_numpy(float)
    if np.isinf(values).any():raise ValueError('infinite_feature')
    return PreparedSplit(split,names,feature_keys,tuple(frame.cutoff),values,
        np.asarray([[lookup[k] for k in win] for win in windows],dtype=int),
        tuple(keys),tuple(row_ids),np.asarray(targets),np.asarray(benchmarks))


def preprocess(train,dev):
    """Fit once on unique training observations; never on overlapping windows."""
    if train.split!='train' or dev.split!='dev' or train.names!=dev.names:raise ValueError('train_dev_feature_contract')
    scaler=FrozenPreprocessor().fit(train.features,train.feature_cutoffs,train.feature_keys,train.names)
    tx=scaler.transform(train.features)[train.window_indices]
    dx=scaler.transform(dev.features)[dev.window_indices]
    state=dict(names=list(train.names),kept=[n for n,k in zip(train.names,scaler.keep) if k],
        dropped=scaler.dropped,mean=scaler.scaler.mean.tolist(),scale=scaler.scaler.scale.tolist(),
        training_unique_feature_rows=len(train.feature_keys),missingness_masks_appended=True,
        context_flattening='oldest-to-newest eight sessions; transformed features then masks per session')
    return tx.reshape(len(tx),-1),dx.reshape(len(dx),-1),state


class RidgeVariance:
    """Ridge MSE + alpha*L2, unpenalized intercept, no target clipping."""
    def __init__(self,alpha):
        if not math.isfinite(alpha) or alpha<=0:raise ValueError('ridge_alpha')
        self.alpha=alpha
    def fit(self,x,y):
        x,y=_training_arrays(x,y);self.mean=x.mean(0);self.intercept=float(y.mean())
        a=x-self.mean;b=y-self.intercept
        self.coef=np.linalg.solve(a.T@a+self.alpha*np.eye(a.shape[1]),a.T@b)
        return self
    def predict(self,x):
        p=(np.asarray(x,float)-self.mean)@self.coef+self.intercept
        if not np.isfinite(p).all():raise ValueError('invalid_prediction')
        return np.maximum(p,FLOOR)


def _training_arrays(x,y):
    x=np.asarray(x,float);y=np.asarray(y,float)
    if x.ndim!=2 or y.shape!=(len(x),) or not len(x) or not np.isfinite(x).all() or not np.isfinite(y).all() or (y<0).any():
        raise ValueError('training_arrays')
    return x,y


class BoostedVariance:
    """Deterministic squared-error boosting with best-first bounded CART leaves.

    NumPy implementation, <=32 candidate thresholds per feature/node. No external
    estimator or dependency is implied. Seed is recorded; fitting is deterministic.
    """
    def __init__(self,max_leaf_nodes=7,learning_rate=.03,max_iter=100,max_bins=32,min_leaf=5):
        if type(max_leaf_nodes) is not int or not 2<=max_leaf_nodes<=15 or type(max_iter) is not int or not 1<=max_iter<=200:
            raise ValueError('tree_bounds')
        if not 0<learning_rate<=1 or type(max_bins) is not int or not 2<=max_bins<=32 or type(min_leaf) is not int or min_leaf<1:
            raise ValueError('tree_parameters')
        self.leaves=max_leaf_nodes;self.rate=learning_rate;self.iterations=max_iter;self.bins=max_bins;self.min_leaf=min_leaf
    def _tree(self,x,y):
        root=dict(value=float(y.mean()));leaves=[(root,np.arange(len(x)))]
        def candidate(indices):
            if len(indices)<2*self.min_leaf:return None
            yy=y[indices];base=float(np.sum((yy-yy.mean())**2));best=None
            for j in range(x.shape[1]):
                order=np.argsort(x[indices,j],kind='stable');values=x[indices[order],j]
                edges=np.flatnonzero(values[:-1]<values[1:])+1
                edges=edges[(edges>=self.min_leaf)&(edges<=len(values)-self.min_leaf)]
                if not len(edges):continue
                edges=edges[np.unique(np.linspace(0,len(edges)-1,min(self.bins,len(edges))).astype(int))]
                ordered=yy[order];sums=np.cumsum(ordered);squares=np.cumsum(ordered**2)
                n=edges;left=sums[n-1];right=sums[-1]-left
                errors=squares[-1]-left**2/n-right**2/(len(values)-n)
                gains=base-errors;k=int(np.argmax(gains));gain=float(gains[k]);n=int(n[k])
                if gain>1e-15 and (best is None or gain>best[0]):
                    cut=float(values[n-1]) # Exact training partition even for adjacent floats.
                    best=(gain,j,cut,indices[order[:n]],indices[order[n:]])
            return best
        candidates=[candidate(leaves[0][1])]
        while len(leaves)<self.leaves:
            usable=[i for i,c in enumerate(candidates) if c is not None]
            if not usable:break
            i=max(usable,key=lambda n:candidates[n][0]);node,_=leaves.pop(i);_,j,cut,a,b=candidates.pop(i)
            left=dict(value=float(y[a].mean()));right=dict(value=float(y[b].mean()))
            node.update(feature=j,threshold=cut,left=left,right=right)
            leaves.extend([(left,a),(right,b)]);candidates.extend([candidate(a),candidate(b)])
        return root
    @staticmethod
    def _predict_tree(tree,x):
        out=np.empty(len(x))
        def visit(node,indices):
            if 'feature' not in node:out[indices]=node['value'];return
            keep=x[indices,node['feature']]<=node['threshold']
            visit(node['left'],indices[keep]);visit(node['right'],indices[~keep])
        visit(tree,np.arange(len(x)));return out
    def fit(self,x,y):
        x,y=_training_arrays(x,y);self.intercept=float(y.mean());p=np.full(len(y),self.intercept);self.trees=[]
        for _ in range(self.iterations):
            tree=self._tree(x,y-p);self.trees.append(tree);p+=self.rate*self._predict_tree(tree,x)
        return self
    def predict(self,x):
        x=np.asarray(x,float)
        if x.ndim!=2 or not np.isfinite(x).all():raise ValueError('tree_predict_features')
        p=np.full(len(x),self.intercept)
        for tree in self.trees:p+=self.rate*self._predict_tree(tree,x)
        if not np.isfinite(p).all():raise ValueError('invalid_prediction')
        return np.maximum(p,FLOOR)


def fit_baseline(train,dev,family,config,seed=17):
    """Fit supplied arrays only; release orchestrator must gate real data."""
    if train.split!='train' or dev.split!='dev':raise ValueError('final_test_held')
    if seed not in SEEDS:raise ValueError('seed_contract')
    if family=='historical_variance_20':
        prediction=dev.historical_variance.copy();state=None;loss='no fitting';model_state=dict(kind='historical_variance_20')
    else:
        tx,dx,state=preprocess(train,dev)
        if family=='regularized_regression':model=RidgeVariance(**config)
        elif family=='boosted_trees':model=BoostedVariance(**config)
        else:raise ValueError('baseline_family')
        prediction=model.fit(tx,train.target).predict(dx);loss='MSE'
        model_state=(dict(kind='ridge',alpha=model.alpha,mean=model.mean.tolist(),coef=model.coef.tolist(),intercept=model.intercept)
            if family=='regularized_regression' else dict(kind='numpy_cart_boosting',intercept=model.intercept,
                learning_rate=model.rate,trees=model.trees,config=dict(config)))
    return dict(family=family,config=config,seed=seed,keys=list(dev.keys),row_ids=list(dev.row_ids),
        predicted_variance=prediction,metrics=variance_metrics(prediction,dev.target),preprocessing=state,
        fitting_objective=loss,selection_split='dev',training_rows=len(train.keys),
        development_rows=len(dev.keys),extreme_targets_removed=0,final_test_evaluated=False,model_state=model_state)


def development_comparison(reference,candidate,dev,block_sessions=20,repeats=2000):
    """Paired date-block uncertainty after exact row reconciliation, no gap bridging."""
    if dev.split!='dev' or reference['keys']!=list(dev.keys) or candidate['keys']!=list(dev.keys):raise ValueError('matched_development_rows_required')
    def losses(result):
        p=np.asarray(result['predicted_variance'],float)
        variance_metrics(p,dev.target)
        return np.log(p)+dev.target/p
    return segmented_date_interval([d for d,_ in dev.keys],[t for _,t in dev.keys],
        losses(candidate),losses(reference),block_sessions=block_sessions,repeats=repeats,seed=17)


def make_release_plan(train,dev,input_bindings):
    """Freeze a baseline-only 36-fit proposal; this function never authorizes it."""
    if train.split!='train' or dev.split!='dev':raise ValueError('final_test_held')
    if any(any(word in n for word in ('sentiment','tone','probability','headline','observed_count')) for n in train.names):
        raise ValueError('baseline_features_only')
    identity=dict(version=VERSION,scope='baseline_train_dev_only',train=train.identity(),dev=dev.identity(),
        input_bindings=input_bindings,configs=BASELINE_CONFIGS,seeds=list(SEEDS),max_fits=36,
        benchmark='historical_variance_20',context_features='flattened eight-session baseline only',
        final_test_held=True,master_training_held=True,aggregation_release_held=True,
        primary_metric='annualized variance QLIKE',loss_confound='ridge/tree MSE; future MASTER QLIKE',
        code_sha256=digest(Path(__file__).read_bytes()),
        dependency_code_sha256={name:digest(Path(__file__).with_name(name).read_bytes()) for name in
            ('archive.py','comparison_protocol.py','research_experiment.py','point_in_time.py','risk_evaluation.py','sentiment_inventory.py','sentiment_policy.py')},
        protocol_sha256=digest(json_bytes(PROTOCOL)),
        runtime=dict(python=platform.python_version(),machine=platform.machine(),
            packages={name:version(name) for name in ('numpy','pandas','exchange-calendars')}))
    return dict(identity=identity,run_id=digest(json_bytes(identity)),status='awaiting_exact_training_approval')


def run_baseline_release(plan,train,dev,approval):
    """Exact approval, data and code binding before any real fit; never final test."""
    expected=make_release_plan(train,dev,plan['identity']['input_bindings'])
    if plan['identity']!=expected['identity'] or plan['run_id']!=expected['run_id']:raise ValueError('baseline_release_changed')
    if approval.get('granted') is not True or approval.get('scope')!='baseline_train_dev_only' or approval.get('run_id')!=plan['run_id']:
        raise PermissionError('exact_baseline_training_approval_required')
    if not approval.get('approved_by'):raise PermissionError('operator_required')
    utc(approval.get('approved_at'))
    results=[fit_baseline(train,dev,'historical_variance_20',{})]
    for candidate in BASELINE_CONFIGS:
        cfg=dict(candidate);family=cfg.pop('family')
        for seed in SEEDS:results.append(fit_baseline(train,dev,family,cfg,seed))
    assert len(results)==37
    # Deterministic ridge/tree seeds are repetitions, not independent uncertainty.
    best={}
    for family in ('regularized_regression','boosted_trees'):
        options=[r for r in results if r['family']==family]
        best[family]=min(options,key=lambda r:(r['metrics']['qlike'],json_bytes(r['config']),r['seed']))['config']
    intervals={}
    for family,config in best.items():
        chosen=next(r for r in results if r['family']==family and r['config']==config and r['seed']==17)
        intervals[family]={}
        for block in (20,5,10,40):
            try:interval=development_comparison(results[0],chosen,dev,block_sessions=block,repeats=2000)
            except ValueError as error:
                if str(error)!='insufficient_contiguous_date_blocks':raise
                interval=dict(status='unavailable',reason=str(error))
            intervals[family][str(block)]=interval
    return dict(run_id=plan['run_id'],results=results,selected_on_development=best,
        development_intervals=intervals,intervals_descriptive_after_development_selection=True,
        deterministic_seed_repetitions=True,final_test_held=True,master_training_held=True,
        sentiment_comparison_held=True,aggregation_release_held=True)
