"""Prepared small MASTER-inspired runner; excluded from baseline release.

Synthetic tests may exercise this API. Real transformer fitting remains a separate
explicit release, including data bindings and final-test protections.
"""
from dataclasses import dataclass
import copy
import numpy as np
from .comparison_protocol import FrozenPreprocessor
from .point_in_time import sessions_between,utc
from .research_experiment import split_for
from .sentiment_inventory import partition
from .forecast_runners import FLOOR
from .risk_evaluation import variance_metrics


@dataclass
class MasterBatch:
    split:str
    dates:tuple
    tickers:tuple
    contexts:tuple
    feature_names:tuple
    market_names:tuple
    x:np.ndarray
    market:np.ndarray
    token_mask:np.ndarray
    target:np.ndarray
    label_mask:np.ndarray
    label_end_sessions:tuple
    stock_times:np.ndarray
    market_times:np.ndarray
    cutoffs:np.ndarray

    def validate(self):
        d,s,t,f=self.x.shape
        if self.split not in ('train','dev') or any(partition(day)!=self.split for day in self.dates):raise ValueError('final_test_held')
        if t!=8 or len(self.dates)!=d or len(self.tickers)!=s or len(set(self.dates))!=d or len(set(self.tickers))!=s:raise ValueError('master_dimensions')
        if tuple(sorted(self.dates))!=self.dates or f!=len(self.feature_names) or self.market.shape!=(d,len(self.market_names)):raise ValueError('master_feature_contract')
        if self.token_mask.shape!=(d,s,t) or self.token_mask.dtype!=bool or self.label_mask.shape!=(d,s) or self.label_mask.dtype!=bool:raise ValueError('master_masks')
        if self.target.shape!=(d,s) or self.stock_times.shape!=(d,s,t) or self.market_times.shape!=self.market.shape or self.cutoffs.shape!=(d,):raise ValueError('master_times')
        if any(any(word in n for word in ('target','label','annualized_variance','prediction')) for n in self.feature_names+self.market_names):raise ValueError('outcome_in_features')
        if any(a.dtype!=np.int64 for a in (self.stock_times,self.market_times,self.cutoffs)):raise ValueError('epoch_nanoseconds_required')
        if not self.token_mask.any(axis=(1,2)).all() or (self.label_mask & ~self.token_mask.any(2)).any():raise ValueError('missing_stock_label')
        if np.isinf(self.x[self.token_mask]).any() or np.isinf(self.market).any():raise ValueError('infinite_feature')
        if ((self.stock_times<=0)|(self.stock_times>self.cutoffs[:,None,None]))[self.token_mask].any():raise ValueError('future_stock_feature')
        observed=~np.isnan(self.market)
        if ((self.market_times<=0)|(self.market_times>self.cutoffs[:,None]))[observed].any():raise ValueError('future_market_feature')
        if not np.isfinite(self.target[self.label_mask]).all() or (self.target[self.label_mask]<0).any():raise ValueError('invalid_present_target')
        if len(self.contexts)!=d or len(self.label_end_sessions)!=d:raise ValueError('master_context_dates')
        for i,day in enumerate(self.dates):
            if np.datetime64(int(self.cutoffs[i]),'ns').astype('datetime64[D]').astype(str)!=day:raise ValueError('cutoff_date_binding')
            context=list(self.contexts[i])
            if len(context)!=8 or context[-1]!=day or any(partition(k)!=self.split for k in context) or context!=[k for k,_ in sessions_between(context[0],day)]:
                raise ValueError('eight_session_partition_context')
            horizon=sessions_between(day,self.label_end_sessions[i])
            if len(horizon)!=6 or split_for(day+'T00:00:00Z',horizon[-1][1])!=self.split:raise ValueError('five_session_split_purge')
        return self


def _feature_rows(batch):
    unique={}
    for i,context in enumerate(batch.contexts):
        for j,ticker in enumerate(batch.tickers):
            for k,day in enumerate(context):
                if not batch.token_mask[i,j,k]:continue
                key=(day,ticker);value=batch.x[i,j,k]
                if key in unique and not np.array_equal(value,unique[key],equal_nan=True):raise ValueError('inconsistent_duplicate_context_feature')
                unique[key]=value
    keys=sorted(unique)
    return keys,np.asarray([unique[k] for k in keys])


def prepare_master(train,dev):
    """Fit stock and market scaling on unique training dates/observations only."""
    train.validate();dev.validate()
    if not train.label_mask.any():raise ValueError('missing_training_labels')
    if train.split!='train' or dev.split!='dev' or train.feature_names!=dev.feature_names or train.market_names!=dev.market_names or train.tickers!=dev.tickers:
        raise ValueError('master_train_dev_contract')
    keys,values=_feature_rows(train)
    scaler=FrozenPreprocessor().fit(values,[d+'T00:00:00Z' for d,_ in keys],keys,train.feature_names)
    market=FrozenPreprocessor().fit(train.market,[d+'T00:00:00Z' for d in train.dates],list(train.dates),train.market_names)
    def convert(batch):
        shape=batch.x.shape;result=np.zeros((*shape[:3],2*int(scaler.keep.sum())))
        result[batch.token_mask]=scaler.transform(batch.x[batch.token_mask])
        return dict(x=result,market=market.transform(batch.market),token_mask=batch.token_mask.copy(),
            target=batch.target.copy(),label_mask=batch.label_mask.copy())
    return convert(train),convert(dev),dict(
        stock_mean=scaler.scaler.mean.tolist(),stock_scale=scaler.scaler.scale.tolist(),stock_dropped=scaler.dropped,
        market_mean=market.scaler.mean.tolist(),market_scale=market.scaler.scale.tolist(),market_dropped=market.dropped,
        unique_training_stock_features=len(keys),unique_training_market_dates=len(train.dates),
        missing_labels_remove_attention_context=False,missingness_masks_appended=True)


def fit_master_synthetic_or_released(train,dev,*,width=16,learning_rate=.0001,dropout=.1,max_epochs=50,patience=5,seed=17):
    """CPU date-batch QLIKE loop. Calling real-data adapter must require approval.

    Each batch is one prediction date containing all available stocks. Missing
    labels mask loss only, not stock attention. No training-data extreme filtering.
    """
    if width not in (16,32) or learning_rate not in (.0001,.001) or dropout not in (.1,.2) or type(max_epochs) is not int or not 1<=max_epochs<=50 or type(patience) is not int or not 1<=patience<=5 or seed not in (17,29,43):
        raise ValueError('master_config_budget')
    a,b,state=prepare_master(train,dev)
    import torch
    from .master_model import MarketGuidedStockTransformer,masked_variance_loss,positive_variance
    torch.set_num_threads(2);torch.manual_seed(seed);torch.use_deterministic_algorithms(True)
    def tensors(data):
        return {k:torch.as_tensor(v,dtype=torch.bool if k.endswith('mask') else torch.float32) for k,v in data.items()}
    a,b=tensors(a),tensors(b)
    model=MarketGuidedStockTransformer(a['x'].shape[-1],a['market'].shape[-1],width=width,dropout=dropout,max_lookback=8)
    optimizer=torch.optim.Adam(model.parameters(),lr=learning_rate)
    best=None;best_loss=float('inf');stale=0;history=[]
    for epoch in range(max_epochs):
        model.train();losses=[]
        for i in range(len(train.dates)):
            optimizer.zero_grad()
            raw=model(a['x'][i:i+1],a['market'][i:i+1],a['token_mask'][i:i+1])
            loss=masked_variance_loss(raw,a['target'][i:i+1],a['label_mask'][i:i+1])
            if loss is None:continue
            if not torch.isfinite(loss):raise ValueError('nonfinite_training_loss')
            loss.backward();optimizer.step();losses.append(float(loss.detach()))
        model.eval()
        with torch.no_grad():
            raw=model(b['x'],b['market'],b['token_mask'])
            devloss=masked_variance_loss(raw,b['target'],b['label_mask'])
            if devloss is None or not torch.isfinite(devloss):raise ValueError('missing_dev_labels')
            score=float(devloss)
        history.append(dict(epoch=epoch+1,training_date_losses=losses,development_qlike=score))
        if score<best_loss:
            best_loss=score;best=copy.deepcopy(model.state_dict());stale=0
        else:stale+=1
        if stale>=patience:break
    if best is None:raise ValueError('no_master_fit')
    model.load_state_dict(best);model.eval()
    with torch.no_grad():
        prediction=positive_variance(model(b['x'],b['market'],b['token_mask'])).numpy()
    mask=dev.label_mask
    return dict(predicted_variance=prediction,metrics=variance_metrics(prediction[mask],dev.target[mask]),
        preprocessing=state,history=history,epochs=len(history),fitting_objective='QLIKE annualized variance',
        seed=seed,extreme_targets_removed=0,final_test_evaluated=False,model=model)
