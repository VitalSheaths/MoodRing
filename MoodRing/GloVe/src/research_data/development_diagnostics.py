"""Read-only development error and source-binding diagnostics; no rule selection."""
import hashlib
import numpy as np
import pandas as pd
from .sentiment_inventory import partition


def errors(rows, predictions):
    """Bind exact ordered development predictions before computing per-row losses.

    A high loss or low coverage is a diagnostic association, not evidence that a
    headline caused an outcome or that a classifier is incorrect. Keep floor rows.
    """
    if rows.row_id.duplicated().any() or any(partition(s)!='dev' for s in rows.session):
        raise ValueError('unique_development_rows_required')
    if predictions['row_ids']!=rows.row_id.tolist():raise ValueError('prediction_row_order')
    if predictions['keys']!=[list(k) for k in zip(rows.session,rows.ticker)]:raise ValueError('prediction_keys')
    p=np.asarray(predictions['predicted_variance'],float);y=rows.annualized_variance.to_numpy(float)
    if p.shape!=y.shape or not np.isfinite(p).all() or not np.isfinite(y).all() or (p<=0).any() or (y<0).any():
        raise ValueError('invalid_variance')
    out=rows.copy();out['predicted_variance']=p;out['qlike']=np.log(p)+y/p
    out['absolute_volatility_error']=np.abs(np.sqrt(p)-np.sqrt(y));out['at_floor']=p==1e-8
    return out


def coverage_summary(row_errors):
    """Report predeclared support bins per ticker/channel without changing rows."""
    result=[]
    for channel in ('direct','indirect_product','indirect_sector_competitor'):
        days=row_errors[channel+'_scored_days']
        if not days.between(0,8).all() or (days!=np.floor(days)).any():raise ValueError('coverage_count')
        frame=row_errors.assign(coverage=np.select([days==0,days.between(1,3),days.between(4,7)],
                                                 ['absent','one_to_three','four_to_seven'],default='eight'))
        for (ticker,coverage),g in frame.groupby(['ticker','coverage']):
            result.append(dict(ticker=ticker,channel=channel,coverage=coverage,rows=len(g),
                mean_qlike=float(g.qlike.mean()),floor_rows=int(g.at_floor.sum()),
                mean_absolute_volatility_error=float(g.absolute_volatility_error.mean())))
    return pd.DataFrame(result)


def verify_headline_trace(records):
    """Verify exact text binding and eligibility; never infer human correctness."""
    for r in records:
        if partition(r['forecast_session'])!='dev' or partition(r['session'])!='dev':
            raise ValueError('development_only')
        if hashlib.sha256(r['title'].encode('utf-8')).hexdigest()!=r['exact_model_input_sha256']:
            raise ValueError('source_title_binding')
        if pd.Timestamp(r['availability_at'])>pd.Timestamp(r['forecast_cutoff']):raise ValueError('future_headline')
    return dict(records=len(records),bindings_verified=True,human_accuracy_established=False,policy_changed=False)
