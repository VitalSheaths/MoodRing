"""Separately gated positive-output linear and small MASTER experiments.

Only synthetic callers may use low-level fit functions without a release. Real
training is exposed through exact identity/approval checks; preparation never fits.
"""
from pathlib import Path
from datetime import date,timedelta
from importlib.metadata import version
import copy,json,os,platform
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from . import matched_comparison as matched
from .archive import digest,json_bytes,read_bytes
from .pilot_runner import relative_file,atomic_json
from .forecast_master import MasterBatch,prepare_master
from .master_model import MarketGuidedStockTransformer,masked_variance_loss,positive_variance
from .forecast_runners import preprocess,_array_digest,FLOOR,development_comparison
from .risk_evaluation import variance_metrics
from .point_in_time import sessions_between,utc

ARMS=(matched.BASE,*matched.ARMS)
SCOPES=('positive_output_ridge_train_dev','small_master_train_dev')
RIDGE_CONFIGS=[dict(alpha=a,learning_rate=.01,max_steps=1000) for a in (.1,1.,10.,100.)]
MASTER_CONFIGS=[dict(width=16,learning_rate=r,dropout=.1,max_epochs=50,patience=5) for r in (.0001,.001)]


class PositiveRidge(nn.Module):
    """Softplus linear variance, original variance labels, no log-target transform.

    This is nonlinear regularized regression, not closed-form ridge. Optimize
    mean variance squared error + alpha/n * ||w||², unpenalized intercept.
    Positivity does not guarantee calibration or eliminate saturation at the floor.
    """
    def __init__(self,features,mean_target):
        super().__init__()
        self.weight=nn.Parameter(torch.zeros(features,dtype=torch.float64))
        value=max(float(mean_target)-FLOOR,1e-12)
        bias=value+np.log(-np.expm1(-value))
        self.bias=nn.Parameter(torch.tensor(bias,dtype=torch.float64))
    def forward(self,x):return F.softplus(x@self.weight+self.bias)+FLOOR


def fit_positive(x,y,dev,config):
    """Deterministic full-batch Adam; fixed training steps, no dev early stopping."""
    if set(config)!=set(RIDGE_CONFIGS[0]) or config['alpha']<=0 or not 1<=config['max_steps']<=1000 or config['learning_rate']!=.01:
        raise ValueError('positive_ridge_budget')
    x=np.asarray(x,float);y=np.asarray(y,float);dev=np.asarray(dev,float)
    if x.ndim!=2 or y.shape!=(len(x),) or not np.isfinite(x).all() or not np.isfinite(dev).all() or not np.isfinite(y).all() or (y<0).any():
        raise ValueError('positive_ridge_inputs')
    torch.set_num_threads(2);torch.manual_seed(17);torch.use_deterministic_algorithms(True)
    x=torch.tensor(x,dtype=torch.float64);target=torch.tensor(y,dtype=torch.float64)
    model=PositiveRidge(x.shape[1],target.mean());optimizer=torch.optim.Adam(model.parameters(),lr=.01)
    history=[]
    for step in range(config['max_steps']):
        optimizer.zero_grad();pred=model(x)
        loss=((pred-target)**2).mean()+config['alpha']/len(x)*model.weight.square().sum()
        if not torch.isfinite(loss):raise ValueError('nonfinite_positive_objective')
        loss.backward();norm=float(torch.linalg.vector_norm(torch.cat([model.weight.grad,model.bias.grad.reshape(1)])))
        optimizer.step()
        if step%50==0 or step+1==config['max_steps']:history.append(dict(step=step+1,objective_before_update=float(loss.detach()),gradient_norm_before_update=norm))
    with torch.no_grad():prediction=model(torch.tensor(dev,dtype=torch.float64)).numpy()
    return dict(predicted_variance=prediction.tolist(),model_state=dict(kind='softplus_linear_variance',
        weight=model.weight.detach().tolist(),bias=float(model.bias.detach()),floor=FLOOR),
        history=history,steps=config['max_steps'],convergence_claim=False,
        fitting_objective='variance MSE + alpha/n L2; unpenalized intercept',extreme_targets_removed=0)


class AnchorMaster(MarketGuidedStockTransformer):
    """Fuse published anchor summaries after temporal pooling, once per stock."""
    def __init__(self,features,market_features,anchor_features,**kwargs):
        super().__init__(features,market_features,**kwargs)
        self.anchor_features=anchor_features
        self.anchor=nn.Linear(anchor_features,self.predict.in_features,bias=False) if anchor_features else None
    def forward(self,x,market,token_mask=None,anchor=None):
        h=self.representation(x,market,token_mask)
        if self.anchor is not None:
            if anchor is None or anchor.shape!=(*x.shape[:2],self.anchor_features):raise ValueError('anchor_shape')
            present=token_mask.any(2) if token_mask is not None else torch.ones(x.shape[:2],dtype=torch.bool,device=x.device)
            if not torch.isfinite(anchor[present]).all():raise ValueError('nonfinite_anchor')
            safe=torch.where(present[:,:,None],anchor,torch.zeros_like(anchor))
            h=h+torch.tanh(self.anchor(safe))
        score=self.predict(h).squeeze(-1)
        return score.masked_fill(~token_mask.any(2),float('nan')) if token_mask is not None else score


def master_batch(data):
    """Map exact matched rows to date/stock tensors without dropping any stock."""
    dates=tuple(sorted({d for d,_ in data.keys}));tickers=tuple(sorted({t for _,t in data.keys}))
    if tuple((d,t) for d in dates for t in tickers)!=data.keys:raise ValueError('complete_ordered_date_stock_grid')
    d,s=len(dates),len(tickers);windows=data.window_indices.reshape(d,s,8)
    x=data.features[windows];times=np.asarray([int(utc(t).timestamp()*1e9) for t in data.feature_cutoffs],dtype=np.int64)[windows]
    contexts=tuple(tuple(data.feature_keys[k][0] for k in row[0]) for row in windows)
    if any(any(tuple(data.feature_keys[k][0] for k in win)!=contexts[i] for win in row) for i,row in enumerate(windows)):
        raise ValueError('stock_context_calendar_mismatch')
    cutoff=times[:,:,7]
    if not (cutoff==cutoff[:,0,None]).all():raise ValueError('stock_cutoff_mismatch')
    market_names=tuple(n for n in data.names if n.startswith('macro_'))
    if not market_names:raise ValueError('macro_gate_required')
    selected=[data.names.index(n) for n in market_names]
    current=x[:,:,7,:][:,:,selected]
    if any(not np.array_equal(current[:,0],current[:,j],equal_nan=True) for j in range(s)):
        raise ValueError('macro_gate_not_shared')
    market=current[:,0];market_times=np.where(np.isnan(market),0,cutoff[:,0,None]).astype(np.int64)
    schedule=[k for k,_ in sessions_between(dates[0],(date.fromisoformat(dates[-1])+timedelta(days=25)).isoformat())]
    pos={day:i for i,day in enumerate(schedule)};ends=tuple(schedule[pos[day]+5] for day in dates)
    return MasterBatch(data.split,dates,tickers,contexts,data.names,market_names,x,market,
        np.ones(x.shape[:3],bool),data.target.reshape(d,s),np.ones((d,s),bool),ends,times,market_times,cutoff[:,0]).validate()


def anchor_blocks(train,dev,ta,da):
    """Training-only shared anchor blocks; identical controls across feature arms."""
    blocks={'coverage':matched._block(train,dev,ta,da,matched.COVERAGE)}
    blocks.update({name:matched._block(train,dev,ta,da,[name]) for name in matched.TONES})
    designs={matched.BASE:(np.empty((len(ta),0)),np.empty((len(da),0)))}
    for arm,tones in matched.ARMS.items():
        chosen=['coverage',*tones]
        designs[arm]=tuple(np.column_stack([blocks[k][i] for k in chosen]) for i in (0,1))
    return designs,{k:v[2] for k,v in blocks.items()}


def fit_anchor_master(train,dev,a,b,anchors,config,seed):
    """Date-batched CPU QLIKE; retain the best development epoch in memory."""
    train.validate();dev.validate()
    if (set(config)!=set(MASTER_CONFIGS[0]) or config['width']!=16 or config['learning_rate'] not in (.0001,.001)
        or config['dropout']!=.1 or not 1<=config['max_epochs']<=50 or not 1<=config['patience']<=5
        or seed not in (17,29,43)):raise ValueError('master_budget')
    torch.set_num_threads(2);torch.manual_seed(seed);torch.use_deterministic_algorithms(True)
    def tensors(data,extra):
        values={k:torch.tensor(v,dtype=torch.bool if k.endswith('mask') else torch.float32) for k,v in data.items()}
        values['anchor']=torch.tensor(extra.reshape(*values['target'].shape,-1),dtype=torch.float32)
        return values
    a,b=tensors(a,anchors[0]),tensors(b,anchors[1])
    model=AnchorMaster(a['x'].shape[-1],a['market'].shape[-1],a['anchor'].shape[-1],
        width=config['width'],dropout=config['dropout'],max_lookback=8)
    optimizer=torch.optim.Adam(model.parameters(),lr=config['learning_rate']);best=None;score=float('inf');stale=0;history=[]
    for epoch in range(config['max_epochs']):
        model.train();losses=[]
        for i in range(len(train.dates)):
            optimizer.zero_grad()
            raw=model(a['x'][i:i+1],a['market'][i:i+1],a['token_mask'][i:i+1],a['anchor'][i:i+1])
            loss=masked_variance_loss(raw,a['target'][i:i+1],a['label_mask'][i:i+1])
            if loss is None:continue
            if not torch.isfinite(loss):raise ValueError('nonfinite_master_loss')
            loss.backward();optimizer.step();losses.append(float(loss.detach()))
        model.eval()
        with torch.no_grad():
            raw=torch.cat([model(b['x'][i:i+32],b['market'][i:i+32],b['token_mask'][i:i+32],b['anchor'][i:i+32]) for i in range(0,len(dev.dates),32)])
            loss=masked_variance_loss(raw,b['target'],b['label_mask'])
        if loss is None or not torch.isfinite(loss):raise ValueError('invalid_master_validation')
        history.append(dict(epoch=epoch+1,train_mean_date_qlike=float(np.mean(losses)),dev_qlike=float(loss)))
        if float(loss)<score:score=float(loss);best=copy.deepcopy(model.state_dict());stale=0
        else:stale+=1
        if stale>=config['patience']:break
    model.load_state_dict(best);model.eval()
    with torch.no_grad():
        pred=torch.cat([positive_variance(model(b['x'][i:i+32],b['market'][i:i+32],b['token_mask'][i:i+32],b['anchor'][i:i+32])) for i in range(0,len(dev.dates),32)]).numpy()
    return dict(predicted_variance=pred[dev.label_mask].tolist(),model_state={k:v.tolist() for k,v in best.items()},
        history=history,epochs=len(history),fitting_objective='QLIKE annualized variance',extreme_targets_removed=0)


def plan(scope,descriptor,root):
    """Prepare exact separate proposals without fitting a scaler or model."""
    if scope not in SCOPES:raise ValueError('experiment_scope')
    t,d,ta,da,_,_=matched.load_comparison(descriptor,root)
    mt,md=master_batch(t),master_batch(d) # Also verifies tensor/label/cutoff mapping.
    if any(os.environ.get(n)!='2' for n in matched.THREADS):raise ValueError('two_thread_runtime_required')
    configs=RIDGE_CONFIGS if scope==SCOPES[0] else MASTER_CONFIGS
    seeds=[17] if scope==SCOPES[0] else [17,29,43]
    identity=dict(version='positive-master-v1',scope=scope,descriptor=descriptor,
        train=t.identity(),dev=d.identity(),anchor_names=list(ta.columns),
        anchor_sha256=dict(train=_array_digest(ta.to_numpy()),dev=_array_digest(da.to_numpy())),
        configs=configs,seeds=seeds,arms=list(ARMS),max_fits=len(ARMS)*len(configs)*len(seeds),
        max_optimizer_updates=20000 if scope==SCOPES[0] else 30*50*len(mt.dates),
        max_attempts_per_fit=1,automatic_retry=False,automatic_resume=False,
        market_gate_names=list(mt.market_names),market_gate='anchor macro values only; shared across stocks; same cutoff assumptions',
        master_features='baseline temporal tokens; anchor coverage/tone late fusion after pooling, never copied into past tokens',
        master_shape=dict(train=list(mt.x.shape),dev=list(md.x.shape)),
        matching='exact original 4920 training and 4900 development rows; no tone-based selection',
        positive_loss='mean variance MSE + alpha/n L2 of latent coefficients; intercept unpenalized; softplus(z)+1e-8',
        positive_initialization='zero weights; inverse-softplus(training mean variance minus floor); 1000 full-batch Adam steps',
        positive_caveat='nonlinear regularized linear model, not closed-form ridge; positivity does not prove calibration or cure floor saturation',
        selection='mean development QLIKE across declared seeds, config JSON tie break; primary fixed new-baseline configuration, secondary per-arm tuning; never select a lucky MASTER seed',
        contrasts='within-family coverage vs baseline and direct/indirect/both vs coverage; no fixed equivalent of old ridge alpha',
        uncertainty='paired date-block means, 20 primary / 5,10,40 sensitivity; 2000 replicates, seed17; intervals descriptive after dev selection',
        architecture_loss_confound='MASTER QLIKE versus tree variance squared error; not an architecture-only comparison',
        timing='published zero-extra-delay only; timing variants require separate releases',
        final_test_held=True,strict_PIT_eligible=False,extremes_preserved=True,
        original_results_immutable=True,code_sha256={p.name:digest(read_bytes(p)) for p in sorted(Path(__file__).parent.glob('*.py'))},
        runtime=dict(python=platform.python_version(),machine=platform.machine(),
            packages={n:version(n) for n in ('torch','numpy','pandas','pyarrow','exchange-calendars')},threads={n:os.environ[n] for n in matched.THREADS}))
    return dict(run_id=digest(json_bytes(identity)),identity=identity,status='awaiting_exact_training_approval')


def summarize(results,dev,configs,seeds):
    """Separate baseline-fixed feature effects from per-arm development tuning."""
    selected={arm:min(configs,key=lambda cfg:(np.mean([r['metrics']['qlike'] for r in results if r['arm']==arm and r['config']==cfg]),json_bytes(cfg))) for arm in ARMS}
    contrasts={}
    for mode in ('fixed_new_baseline_configuration','per_arm_development_tuning'):
        contrasts[mode]={}
        for candidate,reference in [(ARMS[1],ARMS[0])]+[(a,ARMS[1]) for a in ARMS[2:]]:
            contrasts[mode][candidate+'-vs-'+reference]={}
            for seed in seeds:
                get=lambda arm:next(r for r in results if r['arm']==arm and r['config']==selected[ARMS[0] if mode=='fixed_new_baseline_configuration' else arm] and r['seed']==seed)
                contrasts[mode][candidate+'-vs-'+reference][str(seed)]={str(block):development_comparison(get(reference),get(candidate),dev,block_sessions=block,repeats=2000) for block in (20,5,10,40)}
    return dict(selected_by_mean_development_qlike=selected,fixed_new_baseline_configuration=selected[ARMS[0]],per_seed_descriptive_intervals=contrasts)


def execute(proposal,root,approval):
    """Exclusive one-shot release; no training from preparation or old approvals."""
    i=proposal['identity'];scope=i['scope']
    if approval.get('granted') is not True or approval.get('scope')!=scope or approval.get('run_id')!=proposal['run_id'] or not approval.get('approved_by'):
        raise PermissionError('new_exact_experiment_approval_required')
    utc(approval.get('approved_at'))
    if plan(scope,i['descriptor'],root)!=proposal:raise ValueError('experiment_changed')
    t,d,ta,da,_,_=matched.load_comparison(i['descriptor'],root)
    output=relative_file(Path(root).absolute(),i['descriptor']['experiment_outputs'][scope])
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    atomic_json(output/'started.json',dict(run_id=proposal['run_id'],approval=approval,max_fits=i['max_fits']))
    extra,anchor_state=anchor_blocks(t,d,ta,da)
    if scope==SCOPES[0]:
        x,y,state=preprocess(t,d);designs={arm:(np.column_stack([x,e[0]]),np.column_stack([y,e[1]])) for arm,e in extra.items()}
    else:
        mt,md=master_batch(t),master_batch(d);a,b,state=prepare_master(mt,md)
    atomic_json(output/'preprocessing.json',dict(baseline_or_master=state,anchor_blocks=anchor_state))
    results=[];hashes={};index=0
    for arm in ARMS:
        for cfg in i['configs']:
            for seed in i['seeds']:
                if index>=i['max_fits']:raise ValueError('fit_budget')
                atomic_json(output/f'attempt-{index:04d}.json',dict(arm=arm,config=cfg,seed=seed,index=index))
                r=fit_positive(designs[arm][0],t.target,designs[arm][1],cfg) if scope==SCOPES[0] else fit_anchor_master(mt,md,a,b,extra[arm],cfg,seed)
                r.update(arm=arm,config=cfg,seed=seed,row_ids=list(d.row_ids),keys=list(d.keys),
                    metrics=variance_metrics(r['predicted_variance'],d.target),final_test_evaluated=False)
                name=f'result-{index:04d}.json';atomic_json(output/name,r);hashes[name]=digest(read_bytes(output/name));results.append(r);index+=1
    if index!=i['max_fits']:raise ValueError('incomplete_experiment_grid')
    summary=summarize(results,d,i['configs'],i['seeds'])
    atomic_json(output/'completion.json',dict(run_id=proposal['run_id'],completed_fits=index,result_sha256=hashes,
        **summary,
        final_test_held=True,other_experiments_held=True,strict_PIT_eligible=False))
    return dict(run_id=proposal['run_id'],completed_fits=index)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('plan','root','approval'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();print(json.dumps(execute(json.loads(read_bytes(a.plan)),a.root,json.loads(read_bytes(a.approval))),indent=2))
