"""Accepted primary risk objective; no fitting or real-data eligibility is implied."""
import math
from .point_in_time import utc,sessions_between,price_history,source_id,available_at,latest_versions
from .research_experiment import split_for,verify_window

VERSION='five-session-total-return-rv-v2'
CONTRACT={
 'target':'five-session realized volatility', 'horizon_sessions':5,
 'return_basis':'dividend-inclusive total-return wealth, split-consistent',
 'definition':'sqrt((252/5) * sum(log(W[t+k]/W[t+k-1])**2 for k in 1..5))',
 'annualization':252,'demean_returns':False,'unit':'annualized fraction',
 'forecast_quantity':'positive annualized variance; display square root as volatility',
 'loss':'QLIKE: log(predicted_variance) + realized_variance/predicted_variance',
 'variance_floor':1e-8,'primary_metric':'mean QLIKE; paired differences grouped by prediction date',
 'secondary_metrics':['MAE annualized volatility','RMSE annualized volatility','extreme-loss-period errors'],
 'extreme_outcomes':'preserved; no target clipping, winsorization or DropExtremeLabel',
 'missing_labels':'mask loss and metrics only; keep valid stock features in attention context',
 'cutoff':'20 minutes after actual exchange close, with separately evidenced input availability',
 'execution':'forecast statistic; not an executable same-close return or calibrated probability',
 'input_readiness':'Historical feature versions must be evidenced by prediction cutoff.',
 'outcome_readiness':'Complete consistently adjusted anchor plus five future observations; available by explicit label_asof, not prediction cutoff.',
 'benchmark':'Trailing 20-session mean squared eligible total-return log returns, annualized by 252.',
 'training_authorized':False,
}


def realized_target(cutoff,records,*,label_asof):
    """Use anchor plus five consecutive sessions of verified total-return wealth.

    Targets live separately from features. Future target evidence is permitted
    only for labels. label_asof is the outcome snapshot cutoff, not prediction time.
    Horizon close controls split purging; label availability controls maturation.
    Missing/unverified outcomes remain absent. Do not derive these records from
    today's adjusted history and mark them verified.
    """
    if len(records)!=6:return None
    times=[r['session'] for r in records]
    if times!=[s for s,_ in sessions_between(times[0],times[-1])]:raise ValueError('nonconsecutive_target_sessions')
    if len({r['ticker'] for r in records})!=1:raise ValueError('mixed_target_issuer')
    close=dict(sessions_between(times[0],times[-1]));cutoff=utc(cutoff)
    if not close[times[0]]<cutoff<close[times[1]]:raise ValueError('target_anchor_cutoff')
    if any(r.get('total_return_verified') is not True or not r.get('source_ids') or not r.get('available_at') or not r.get('label_basis_id') for r in records):return None
    if len({r['label_basis_id'] for r in records})!=1:raise ValueError('inconsistent_outcome_adjustment_basis')
    if any(utc(r['available_at'])<close[r['session']] for r in records):raise ValueError('outcome_before_close')
    label_available=max(utc(r['available_at']) for r in records)
    if label_available>utc(label_asof):return None
    split=split_for(cutoff,close[times[-1]])
    if split is None:return None
    wealth=[float(r['total_return_wealth']) for r in records]
    if any(not math.isfinite(x) or x<=0 for x in wealth):raise ValueError('invalid_total_return_wealth')
    logs=[math.log(b/a) for a,b in zip(wealth,wealth[1:])]
    variance=252/5*sum(r*r for r in logs)
    return dict(ticker=records[0]['ticker'],cutoff=cutoff.isoformat(),variance=variance,
                volatility=math.sqrt(variance),label_end_session=times[-1],
                label_end_at=close[times[-1]].isoformat(),label_available_at=label_available.isoformat(),
                label_asof=utc(label_asof).isoformat(),split=split,label_basis_id=records[0]['label_basis_id'],
                source_ids=[s for r in records for s in r['source_ids']],target_version=VERSION)


def reconstruct_outcome(cutoff,bars,actions,coverage,evidence,*,label_asof):
    """Build a bounded label from six raw sessions and actions known by label_asof.

    This separate price-history invocation must never supply feature values at the
    earlier prediction cutoff. Future splits/dividends are part of outcomes only.
    Evidence here can be a later observed exact-version receipt; it need not prove
    availability at prediction time. Adjustment coverage must still be verified.
    """
    history=price_history(bars,actions,coverage,label_asof,evidence)
    if len(history)!=6:return None
    selected_bars=latest_versions(bars,label_asof,evidence,('ticker','session'))
    selected_actions=[a for a in latest_versions(actions,label_asof,evidence,('ticker','action_id'))
                      if a['ticker']==history[0]['ticker'] and history[0]['session']<=a['effective_session']<=history[-1]['session']]
    sources=selected_bars+selected_actions+[coverage]
    ids=[source_id(r) for r in sources]
    from .archive import digest,json_bytes
    basis=digest(json_bytes(ids))
    stamps=[available_at(r,evidence) for r in sources]
    if any(s is None or s>utc(label_asof) for s in stamps):return None
    receipt=max(stamps).isoformat();wealth=1.;records=[]
    for i,row in enumerate(history):
        if i:wealth*=1+row['total_return']
        records.append(dict(ticker=row['ticker'],session=row['session'],total_return_wealth=wealth,
            total_return_verified=True,label_basis_id=basis,source_ids=ids,available_at=receipt))
    return realized_target(cutoff,records,label_asof=label_asof)


def historical_variance(cutoff,returns,lookback=20):
    """Simple trailing mean-square log-return benchmark, from verified features.

    Returns must be the last consecutive sessions ending on the prediction session;
    no future outcome input or target clipping enters this baseline.
    """
    if not isinstance(lookback,int) or lookback<2:raise ValueError('lookback_required')
    if len(returns)<lookback:return None
    rows=returns[-lookback:];verify_window(cutoff,rows)
    if any(r.get('value') is None for r in rows):return None
    days=[r['session'] for r in rows]
    expected=dict(sessions_between(days[0],days[-1]))
    if days!=list(expected) or days[-1]!=utc(cutoff).date().isoformat():raise ValueError('benchmark_session_gap')
    if any(expected[r['session']]>utc(cutoff) for r in rows):raise ValueError('future_benchmark_return')
    values=[float(r['value']) for r in rows]
    if any(not math.isfinite(x) for x in values):raise ValueError('invalid_benchmark_return')
    variance=max(252*sum(x*x for x in values)/lookback,CONTRACT['variance_floor'])
    return dict(variance=variance,volatility=math.sqrt(variance),lookback_sessions=lookback)
