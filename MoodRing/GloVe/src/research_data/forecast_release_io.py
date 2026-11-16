"""File-bound baseline training release; train/dev only, explicit approval."""
from pathlib import Path
import json
from .archive import read_bytes,digest,json_bytes
from .sentiment_policy import verified_table
from .pilot_runner import relative_file,atomic_json
from .forecast_runners import build_dataset,make_release_plan,run_baseline_release


def load_inputs(descriptor,root):
    """Verify source and prepared artifact bytes before reading train/dev tables."""
    root=Path(root).absolute()
    for name,sha in descriptor['bindings'].items():
        if digest(read_bytes(relative_file(root,name)))!=sha:raise ValueError('baseline_file_changed:'+name)
    result=[]
    for split in ('train','dev'):
        directory=relative_file(root,descriptor['datasets'][split])
        frames=[verified_table(directory,name)[1].to_pandas(use_threads=False) for name in ('features','contexts','labels')]
        result.append(build_dataset(*frames,descriptor['feature_names'],split))
    return tuple(result)


def execute(proposal,descriptor,root,approval):
    """Single approved baseline release; incomplete runs require review, not retries."""
    if approval.get('granted') is not True or approval.get('scope')!='baseline_train_dev_only' or approval.get('run_id')!=proposal['run_id']:
        raise PermissionError('exact_baseline_training_approval_required')
    if proposal['identity']['input_bindings']!=descriptor['bindings']:raise ValueError('baseline_descriptor_bindings')
    train,dev=load_inputs(descriptor,root)
    if make_release_plan(train,dev,descriptor['bindings'])!=proposal:raise ValueError('baseline_plan_changed')
    output=relative_file(Path(root).absolute(),descriptor['output_relative_path'])
    # An immutable receipt binds this exact output directory in the input bindings.
    scope_path=relative_file(Path(root).absolute(),descriptor['scope_receipt'])
    if descriptor['scope_receipt'] not in descriptor['bindings']:raise ValueError('unbound_output_scope')
    scope=json.loads(read_bytes(scope_path))
    if scope['output_relative_path']!=descriptor['output_relative_path']:raise ValueError('output_scope_changed')
    if output.exists():raise ValueError('baseline_output_exists_review_before_retry')
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    atomic_json(output/'started.json',dict(run_id=proposal['run_id'],max_fits=36,automatic_retry=False))
    result=run_baseline_release(proposal,train,dev,approval)
    def serial(value):
        if hasattr(value,'tolist'):return value.tolist()
        if isinstance(value,dict):return {k:serial(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [serial(v) for v in value]
        return value
    hashes={}
    for index,record in enumerate(result['results']):
        path=output/f'result-{index:04d}.json'
        atomic_json(path,serial(record));hashes[path.name]=digest(read_bytes(path))
    summary=serial({k:v for k,v in result.items() if k!='results'})
    atomic_json(output/'completion.json',dict(summary,result_sha256=hashes))
    return dict(run_id=proposal['run_id'],completed_fits=36,benchmark_computed=True,final_test_held=True,
                sentiment_comparison_held=True,aggregation_release_held=True,master_training_held=True)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('proposal','descriptor','root','approval'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(execute(json.loads(read_bytes(a.proposal)),json.loads(read_bytes(a.descriptor)),
                             a.root,json.loads(read_bytes(a.approval))),indent=2))
