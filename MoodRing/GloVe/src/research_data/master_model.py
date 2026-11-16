"""Small MASTER-inspired research model (Li et al., arXiv:2312.15235, 2023).

This is an independent implementation of the paper's five-stage design, not a
reproduction of its CSI300/800 experiment or a calibrated risk model. Inputs are
masked as-of windows; all observed tokens must precede the prediction cutoff. No ticker
embedding: inter-stock attention remains permutation equivariant.
"""
import math
import torch
from torch import nn


class MarketGuidedStockTransformer(nn.Module):
    """Market gating, intra-stock, inter-stock, temporal pooling, linear prediction.

    x: [prediction dates, stocks, lookback sessions, features].
    market: [prediction dates, market features] known at that date's cutoff.
    Bidirectional attention is confined to the already-observed lookback window;
    batches never mix information across prediction dates. Missing inputs must be
    handled by a train-only preprocessing policy with explicit missingness masks.
    The single output per stock is an uncalibrated regression score, not a
    probability, sentiment label, or an approved five-head risk forecast.
    """
    def __init__(self, features, market_features, width=32, intra_heads=4,
                 inter_heads=2, temperature=5., max_lookback=64, dropout=.1,
                 market_gating=True, inter_stock=True):
        super().__init__()
        if min(features,market_features,width,intra_heads,inter_heads,max_lookback)<=0 or width%2 or width%intra_heads or width%inter_heads or temperature<=0 or not math.isfinite(temperature):
            raise ValueError('invalid_architecture')
        self.features=features;self.market_features=market_features
        self.temperature=temperature;self.market_gating=market_gating;self.inter_stock=inter_stock
        self.gate=nn.Linear(market_features,features);self.encode=nn.Linear(features,width)
        self.normalize=nn.LayerNorm(width)
        self.intra=nn.TransformerEncoderLayer(width,intra_heads,dim_feedforward=width*2,dropout=dropout,batch_first=True)
        self.inter=nn.TransformerEncoderLayer(width,inter_heads,dim_feedforward=width*2,dropout=dropout,batch_first=True)
        self.temporal_query=nn.Linear(width,width,bias=False)
        self.predict=nn.Linear(width,1)
        positions=torch.arange(max_lookback).unsqueeze(1)
        frequency=torch.exp(torch.arange(0,width,2)*(-math.log(10000.)/width))
        pe=torch.zeros(max_lookback,width);pe[:,0::2]=torch.sin(positions*frequency);pe[:,1::2]=torch.cos(positions*frequency)
        self.register_buffer('positions',pe)

    def forward_asof(self,x,market,stock_times,market_times,cutoffs,token_mask):
        """Use the shared time boundary for supplied historical feature windows.

        Upstream must resolve source evidence; timestamps alone are assertions.
        Raw forward remains available for synthetic architecture tests.
        """
        if stock_times.shape!=x.shape[:3] or market_times.shape!=market.shape or cutoffs.shape!=(x.shape[0],):
            raise ValueError('feature_availability_shape')
        if any(v.device!=x.device for v in (market,stock_times,market_times,cutoffs,token_mask)):
            raise ValueError('feature_availability_device')
        verify_batch_times(stock_times,market_times,cutoffs,token_mask)
        return self.forward(x,market,token_mask)

    def forward(self,x,market,token_mask=None):
        """Return raw scores; absent stocks are NaN and masked tokens cannot supply keys/values."""
        score=self.predict(self.representation(x,market,token_mask)).squeeze(-1)
        return score.masked_fill(~token_mask.any(2),float('nan')) if token_mask is not None else score

    def representation(self,x,market,token_mask=None):
        """Date-isolated pooled stock state, before the prediction head.

        Exposes late fusion for separately gated anchor-context experiments. This
        does not broadcast a current anchor summary into earlier feature tokens.
        """
        if x.ndim!=4 or market.ndim!=2 or x.shape[0]!=market.shape[0] or x.shape[-1]!=self.features or market.shape[-1]!=self.market_features:
            raise ValueError('input_shape')
        b,s,t,_=x.shape
        if min(b,s,t)<1 or t>len(self.positions) or not torch.isfinite(market).all():
            raise ValueError('nonfinite_or_unbounded_window')
        if token_mask is None:token_mask=torch.ones((b,s,t),dtype=torch.bool,device=x.device)
        if token_mask.shape!=(b,s,t) or token_mask.dtype!=torch.bool or token_mask.device!=x.device:
            raise ValueError('token_mask_shape_or_type')
        if not token_mask.any(dim=(1,2)).all():raise ValueError('empty_prediction_date')
        if not torch.isfinite(x[token_mask]).all():raise ValueError('nonfinite_observed_feature')
        # Padded values never enter a linear projection or attention key/value.
        x=torch.where(token_mask[:,:,:,None],x,torch.zeros_like(x))
        weights=self.features*torch.softmax(self.gate(market)/self.temperature,dim=-1) if self.market_gating else torch.ones_like(x[:,0,0,:])
        hidden=self.normalize(self.encode(x*weights[:,None,None,:])+self.positions[:t])
        temporal=hidden.reshape(b*s,t,-1);valid=token_mask.reshape(b*s,t)
        active=valid.any(1);encoded=torch.zeros_like(temporal)
        encoded[active]=self.intra(temporal[active],src_key_padding_mask=~valid[active])
        hidden=encoded.reshape(b,s,t,-1).masked_fill(~token_mask[:,:,:,None],0)
        if self.inter_stock:
            across=hidden.permute(0,2,1,3).reshape(b*t,s,-1)
            valid=token_mask.permute(0,2,1).reshape(b*t,s);active=valid.any(1)
            mixed=torch.zeros_like(across)
            mixed[active]=self.inter(across[active],src_key_padding_mask=~valid[active])
            hidden=mixed.reshape(b,t,s,-1).permute(0,2,1,3).masked_fill(~token_mask[:,:,:,None],0)
        # A stock with no feature token has no output, but missing labels do not mask it.
        stock_present=token_mask.any(2)
        last=(torch.arange(t,device=x.device)[None,None,:]*token_mask).max(2).values
        query=self.temporal_query(hidden.gather(2,last[:,:,None,None].expand(-1,-1,1,hidden.shape[-1])).squeeze(2))
        logits=(hidden*query[:,:,None,:]).sum(-1).masked_fill(~token_mask,float('-inf'))
        logits=torch.where(stock_present[:,:,None],logits,torch.zeros_like(logits))
        attention=torch.softmax(logits,dim=-1)*token_mask
        pooled=(hidden*attention[:,:,:,None]).sum(2)
        return pooled.masked_fill(~stock_present[:,:,None],0)

    def predict_variance_asof(self,x,market,stock_times,market_times,cutoffs,token_mask):
        """Report annualized variance on the same positive link used by QLIKE.

        Missing-stock outputs remain NaN. Square roots belong to volatility
        reporting only; never feed those square roots into the variance loss.
        """
        return positive_variance(self.forward_asof(x,market,stock_times,market_times,cutoffs,token_mask))


def verify_batch_times(stock_times,market_times,cutoffs,token_mask):
    """Check latest knowledge time for every valid stock token and market-gate input.

    Integer UTC epoch nanoseconds are required. These checks cannot replace the
    upstream source-version/evidence ledger; unknown valid-token times fail closed.
    """
    if any(x.dtype!=torch.int64 for x in (stock_times,market_times,cutoffs)):
        raise ValueError('utc_nanoseconds_required')
    if token_mask.dtype!=torch.bool or stock_times.shape!=token_mask.shape or cutoffs.ndim!=1 or market_times.ndim!=2 or market_times.shape[0]!=len(cutoffs) or stock_times.shape[0]!=len(cutoffs):
        raise ValueError('availability_shape')
    if (cutoffs<=0).any() or ((stock_times<=0)|(stock_times>cutoffs[:,None,None]))[token_mask].any():
        raise ValueError('unavailable_stock_feature')
    if ((market_times<=0)|(market_times>cutoffs[:,None])).any():raise ValueError('unavailable_market_gate')


def positive_variance(raw_scores):
    """Shared link for variance-scale training and prediction."""
    return torch.nn.functional.softplus(raw_scores)+1e-8


def masked_variance_loss(raw_scores,target_variance,label_mask):
    """QLIKE on positive variance; missing labels do not remove attention context.

    Extreme targets are retained. Mask only absent labels; invalid present labels
    raise. Return None for a date batch with no labels so no optimizer step occurs.
    """
    if raw_scores.shape!=target_variance.shape or label_mask.shape!=raw_scores.shape or label_mask.dtype!=torch.bool:
        raise ValueError('label_mask_shape')
    if not label_mask.any():return None
    scores=raw_scores[label_mask];target=target_variance[label_mask]
    if not torch.isfinite(scores).all() or not torch.isfinite(target).all() or (target<0).any():
        raise ValueError('invalid_present_label_or_prediction')
    variance=positive_variance(scores)
    return (torch.log(variance)+target/variance).mean()
