"""Exact, independent approval gates for sparse-channel and timing refits.

Planning verifies frozen artifacts without fitting preprocessing. Only execute,
with a matching human approval, can reserve the one-shot output and fit models.
No search, acquisition, sentiment inference, final test or MASTER is exposed here.
"""
from pathlib import Path
from importlib.metadata import version
import json
import io
import os
import platform
import numpy as np
import pandas as pd
from .archive import digest, json_bytes, read_bytes, file_digest
from .pilot_runner import relative_file, atomic_json
from .forecast_runners import build_dataset, preprocess, BoostedVariance, _array_digest, development_comparison
from .comparison_protocol import PROTOCOL
from .risk_evaluation import variance_metrics
from .point_in_time import utc
from . import matched_comparison as matched
from .sparse_timing_features import SUPPORT_FIELDS, INTENSITY_FIELDS

SCOPES = ('sparse_channel_train_dev', 'predictive_timing_train_dev')
CONDITIONS = ((0,0),(1,0),(5,0),(20,0),(0,1),(0,5))
TREE = dict(learning_rate=.03,max_bins=32,max_iter=100,max_leaf_nodes=7,min_leaf=5)
SUPPORT = tuple(ch+'_'+f for f in SUPPORT_FIELDS for ch in matched.CHANNELS)
WEIGHTED = tuple(ch+'_support_weighted_tone' for ch in matched.CHANNELS)
INTENSITY = tuple(ch+'_'+f for f in INTENSITY_FIELDS for ch in matched.CHANNELS)
SPARSE_ARMS = {'C':(), 'Q':SUPPORT, 'R':SUPPORT+matched.TONES,
               'W':SUPPORT+WEIGHTED, 'I':SUPPORT+INTENSITY,
               'WI':SUPPORT+WEIGHTED+INTENSITY}
TIMING_ARMS = {'coverage':(), 'coverage_and_tone':matched.TONES}


def load(descriptor, root):
    """Hash every dependency before parsing only explicitly bound train/dev files."""
    root = Path(root).absolute()
    for name, sha in descriptor['bindings'].items():
        if file_digest(relative_file(root,name)) != sha:
            raise ValueError('bound_file_changed:'+name)
    def table(name):
        if name not in descriptor['bindings']:raise ValueError('unbound_table')
        data=read_bytes(relative_file(root,name))
        if digest(data)!=descriptor['bindings'][name]:raise ValueError('table_changed_during_load')
        return pd.read_parquet(io.BytesIO(data))
    variants = {}; expected = descriptor['ordered_row_ids']
    for key, spec in descriptor['variants'].items():
        blocks=[]
        for split in ('train','dev'):
            part=spec[split]
            data=build_dataset(*(table(part[k]) for k in ('features','contexts','labels')),
                               descriptor['feature_names'],split)
            if list(data.row_ids)!=expected[split]:raise ValueError('ordered_population_changed')
            anchor=table(part['anchors'])
            if anchor.row_id.tolist()!=expected[split] or not anchor.row_id.is_unique:
                raise ValueError('anchor_row_binding')
            anchor=anchor.drop(columns='row_id').astype(float)
            if np.isinf(anchor.to_numpy()).any():raise ValueError('infinite_anchor')
            blocks.extend([data,anchor])
        variants[key]=tuple(blocks) # train, train anchor, dev, dev anchor
    return variants


def grid(scope):
    """Predeclared six or twelve fits; no per-arm search or deterministic repeats."""
    if scope==SCOPES[0]:return [('m0-n0',arm) for arm in SPARSE_ARMS]
    if scope==SCOPES[1]:return [(f'm{m}-n{n}',arm) for m,n in CONDITIONS for arm in TIMING_ARMS]
    raise ValueError('unknown_scope')


def plan(scope, descriptor, root):
    """Produce a reproducible run identity without any real fit or inference."""
    fits=grid(scope);variants=load(descriptor,root)
    review_path=descriptor['review_overlay']
    if review_path not in descriptor['bindings']:raise ValueError('unbound_review_overlay')
    review=json.loads(read_bytes(relative_file(Path(root).absolute(),review_path)))
    if review.get('human_provenance',{}).get('human_completed') is not True or review.get('original_scores_changed') is not False:
        raise ValueError('review_overlay_provenance')
    if set(variants)!={c for c,_ in fits}:raise ValueError('condition_grid')
    if any(os.environ.get(k)!='2' for k in matched.THREADS):raise ValueError('two_threads_required')
    names = (*matched.COVERAGE, *matched.TONES, *SUPPORT, *WEIGHTED, *INTENSITY)
    expected_counts=(4920,4900) if scope==SCOPES[0] else (4870,4850)
    reference=None; identities={}
    for condition,(t,ta,d,da) in variants.items():
        if (len(t.keys),len(d.keys))!=expected_counts:raise ValueError('population_count')
        if tuple(ta.columns)!=names or tuple(da.columns)!=names:raise ValueError('feature_schema')
        if not np.isfinite(ta[list(matched.COVERAGE)]).all().all() or not np.isfinite(da[list(matched.COVERAGE)]).all().all():
            raise ValueError('coverage_must_be_explicit')
        signature=[(data.keys,data.row_ids,_array_digest(data.target),_array_digest(data.historical_variance)) for data in (t,d)]
        if reference is not None and signature!=reference:raise ValueError('unmatched_conditions')
        reference=signature
        identities[condition]=dict(train=t.identity(),dev=d.identity(),anchor_names=list(ta.columns),
            anchor_sha256=dict(train=_array_digest(ta.to_numpy()),dev=_array_digest(da.to_numpy())))
    identity=dict(version='sparse-timing-v1',scope=scope,descriptor=descriptor,conditions=identities,
        grid=[dict(condition=c,arm=a) for c,a in fits],configuration=TREE,seed=17,
        max_fits=len(fits),max_reserved_attempts=len(fits),attempts_per_fit=1,
        max_boosting_iterations=len(fits)*TREE['max_iter'],max_leaves_per_tree=7,threads=2,
        automatic_retry=False,automatic_resume=False,configuration_selection=False,
        sparse_columns={a:list(matched.COVERAGE+cols) for a,cols in SPARSE_ARMS.items()},
        timing_columns={a:list(matched.COVERAGE+cols) for a,cols in TIMING_ARMS.items()},
        support_k=5,support_k_calibrated=False,protocol=PROTOCOL,
        loss='variance MSE; positive prediction floor 1e-8; QLIKE evaluation',
        contrasts=['Q-C','R-Q','W-R','I-Q','WI-W'] if scope==SCOPES[0] else
            ['tone minus coverage within each condition','each delayed condition minus common-row zero-delay within each arm'],
        uncertainty=dict(block_sessions=[20,5,10,40],repeats=2000,seed=17,unit='paired date means; all stocks together; no gap bridging'),
        frozen_policy='base unchanged; bound human-reviewed exact development quarantines applied to candidate only; generic tone never reversed',
        review_gate='approval accepts this exact reviewed candidate; any source or review change requires a new identity',
        limitations=['retrospective adjusted prices, not historical versions','macro vintage date proxy, not receipt proof',
            'news observation proxy, not exact historical title versions','one-at-a-time timing, not full interaction grid',
            'development-selected exploratory evidence; fixed current universe'],
        full_scoring_held=True,final_test_held=True,master_training_held=True,
        aggregation_publication_held=True,strict_PIT_eligible=False,extremes_preserved=True,
        original_artifacts_immutable=True,paid_calls=0,new_acquisitions=0,
        code_sha256={p.name:digest(read_bytes(p)) for p in sorted(Path(__file__).parent.glob('*.py'))},
        runtime=dict(python=platform.python_version(),machine=platform.machine(),packages={n:version(n) for n in
            ('numpy','pandas','pyarrow','exchange-calendars')},threads={k:os.environ[k] for k in matched.THREADS}))
    identity=json.loads(json_bytes(identity))  # Approval survives JSON round trips (tuple/list normalization).
    return dict(run_id=digest(json_bytes(identity)),identity=identity,status='awaiting_exact_training_approval')


def designs(t,d,ta,da,scope):
    """Fit each shared block on training only, retaining masks and dropped names."""
    x,y,base=preprocess(t,d)
    arms=SPARSE_ARMS if scope==SCOPES[0] else TIMING_ARMS
    blocks={'coverage':matched._block(t,d,ta,da,matched.COVERAGE)}
    for name in dict.fromkeys(n for cols in arms.values() for n in cols):
        blocks[name]=matched._block(t,d,ta,da,[name])
    result={a:tuple(np.column_stack([v]+[blocks[k][i] for k in ('coverage',*cols)])
        for i,v in enumerate((x,y))) for a,cols in arms.items()}
    return result,dict(base=base,anchors={k:v[2] for k,v in blocks.items()})


def execute(proposal,root,approval):
    """Exclusive bounded execution; reserve each attempt before calling fit.

    A failed or interrupted directory cannot resume automatically. Existing files
    are immutable and any further attempt requires a new reviewed run packet.
    """
    i=proposal['identity'];scope=i['scope']
    if (approval.get('granted') is not True or approval.get('scope')!=scope or
        approval.get('run_id')!=proposal['run_id'] or not approval.get('approved_by') or
        approval.get('accept_reviewed_candidate') is not True):
        raise PermissionError('exact_training_and_reviewed_candidate_approval_required')
    utc(approval.get('approved_at'))
    if plan(scope,i['descriptor'],root)!=proposal:raise ValueError('experiment_changed')
    variants=load(i['descriptor'],root)
    output=relative_file(Path(root).absolute(),i['descriptor']['output'])
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    atomic_json(output/'started.json',dict(run_id=proposal['run_id'],approval=approval,max_fits=i['max_fits']))
    prepared={};records={};hashes={}
    for index,item in enumerate(i['grid']):
        condition,arm=item['condition'],item['arm'];t,ta,d,da=variants[condition]
        if condition not in prepared:
            prepared[condition],state=designs(t,d,ta,da,scope)
            atomic_json(output/f'preprocessing-{condition}.json',state)
        x,y=prepared[condition][arm]
        atomic_json(output/f'attempt-{index:04d}.json',dict(index=index,condition=condition,arm=arm,status='reserved',attempt=1))
        model=BoostedVariance(**i['configuration']).fit(x,t.target)
        prediction=model.predict(y)
        r=dict(condition=condition,arm=arm,config=i['configuration'],seed=17,
            row_ids=list(d.row_ids),keys=list(d.keys),predicted_variance=prediction.tolist(),
            metrics=variance_metrics(prediction,d.target),floor_count=int((prediction==1e-8).sum()),
            training_rows=len(t.keys),development_rows=len(d.keys),extreme_targets_removed=0,
            model_state=dict(kind='numpy_cart_boosting',intercept=model.intercept,rate=model.rate,trees=model.trees))
        path=output/f'result-{index:04d}.json';atomic_json(path,r);hashes[path.name]=digest(read_bytes(path))
        records[(condition,arm)]=r
    pairs=[]
    if scope==SCOPES[0]:
        pairs=[(('m0-n0',r),('m0-n0',c)) for r,c in [('C','Q'),('Q','R'),('R','W'),('Q','I'),('W','WI')]]
    else:
        pairs=[((c,'coverage'),(c,'coverage_and_tone')) for c in variants]
        pairs += [(('m0-n0',a),(c,a)) for c in variants if c!='m0-n0' for a in TIMING_ARMS]
    comparisons={}
    for ref,cand in pairs:
        comparison={}
        for block in (20,5,10,40):
            try:comparison[str(block)]=development_comparison(records[ref],records[cand],d,block_sessions=block,repeats=2000)
            except ValueError as error:
                if str(error)!='insufficient_contiguous_date_blocks':raise
                comparison[str(block)]=dict(status='unavailable',reason=str(error))
        comparisons['/'.join(cand)+'-vs-'+'/'.join(ref)]=comparison
    atomic_json(output/'completion.json',dict(run_id=proposal['run_id'],completed_fits=len(records),result_sha256=hashes,
        contrasts=comparisons,historical_variance_reference=variance_metrics(d.historical_variance,d.target),
        final_test_held=True,master_training_held=True,original_results_unchanged=True,paid_calls=0))
    return dict(run_id=proposal['run_id'],completed_fits=len(records),final_test_held=True)


def read_proposal(path):
    """Read a bounded packet containing all six exact timing populations.

    These repeated identities exceed the smaller per-table metadata limit. The
    64 MiB cap applies only to the proposal, not to arbitrary table reads.
    """
    return json.loads(read_bytes(path, limit=64*1024*1024))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('plan','root','approval'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(execute(read_proposal(args.plan),args.root,json.loads(read_bytes(args.approval))),indent=2))
