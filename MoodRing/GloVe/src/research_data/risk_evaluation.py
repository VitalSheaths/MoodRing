"""Variance-scale metrics and date-block uncertainty; no fitting or data approval."""
import numpy as np
from .point_in_time import sessions_between,utc
from .research_experiment import verify_window


def variance_metrics(predicted_variance,realized_variance):
    """QLIKE uses annualized variance; MAE/RMSE use square-root volatility."""
    p=np.asarray(predicted_variance,dtype=float);y=np.asarray(realized_variance,dtype=float)
    if p.ndim!=1 or p.shape!=y.shape or not len(p):raise ValueError('metric_shape')
    if not np.isfinite(p).all() or not np.isfinite(y).all() or (p<=0).any() or (y<0).any():raise ValueError('variance_values')
    errors=np.sqrt(p)-np.sqrt(y)
    return dict(qlike=float(np.mean(np.log(p)+y/p)),volatility_mae=float(np.mean(abs(errors))),
                volatility_rmse=float(np.sqrt(np.mean(errors**2))))


def paired_date_interval(dates,tickers,loss_a,loss_b,*,block_sessions=20,repeats=1000,seed=17):
    """Resample paired date means in moving blocks, keeping stocks together.

    Inputs must share exact ordered date/ticker keys. Missing dates cannot be
    silently bridged by blocks. Minimum two blocks and five-session blocks prevent
    treating overlapping five-session outcomes as independent rows. Estimates are
    approximate uncertainty summaries, not a guarantee of independence.
    """
    a=np.asarray(loss_a,dtype=float);b=np.asarray(loss_b,dtype=float)
    if a.ndim!=1 or a.shape!=b.shape or len(a)!=len(dates) or len(a)!=len(tickers):raise ValueError('paired_shape')
    if not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('paired_missing_loss')
    if len(set(zip(dates,tickers)))!=len(dates):raise ValueError('duplicate_evaluation_key')
    unique=sorted(set(dates))
    if not isinstance(block_sessions,int) or block_sessions<5 or len(unique)<2*block_sessions:raise ValueError('insufficient_date_blocks')
    if not isinstance(repeats,int) or not 100<=repeats<=100000:raise ValueError('bootstrap_bound')
    if unique!=[d for d,_ in sessions_between(unique[0],unique[-1])]:raise ValueError('date_gap_requires_explicit_policy')
    delta=a-b;means=np.array([delta[np.array(dates)==d].mean() for d in unique])
    rng=np.random.default_rng(seed);samples=[]
    for _ in range(repeats):
        starts=rng.integers(0,len(means)-block_sessions+1,size=int(np.ceil(len(means)/block_sessions)))
        sampled=np.concatenate([means[s:s+block_sessions] for s in starts])[:len(means)]
        samples.append(sampled.mean())
    lo,hi=np.quantile(samples,[.025,.975])
    return dict(mean_paired_difference=float(means.mean()),lower_95=float(lo),upper_95=float(hi),
                prediction_dates=len(unique),stock_rows=len(a),block_sessions=block_sessions,repeats=repeats,seed=seed)


def readiness(cutoff,feature_records,target):
    """Separate historical feature eligibility from later verified outcome readiness."""
    feature_reason=None
    try:
        if not feature_records or any(r.get('value') is None for r in feature_records):
            raise ValueError('required_feature_missing')
        verify_window(cutoff,feature_records)
    except ValueError as error:feature_reason=str(error)
    if target is not None and utc(target['cutoff'])!=utc(cutoff):raise ValueError('target_cutoff_mismatch')
    outcome_ready=target is not None
    return dict(input_ready=feature_reason is None,input_reason=feature_reason,
                outcome_ready=outcome_ready,outcome_reason=None if outcome_ready else 'missing_incomplete_unverified_unmatured_or_cross_split_outcome',
                trainable=feature_reason is None and outcome_ready)
