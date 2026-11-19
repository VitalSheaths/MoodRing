"""Unfitted daily-return candidates motivated by HAR and signed-variation research.

These are daily squared-return proxies, not high-frequency realized variance.
The supplied grid preserves missing sessions; values never cross split boundaries.
No targets, feature selection, model fitting or final-test loader is provided.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VERSION = 'daily-volatility-structure-v1'
FEATURES = ('proxy_variance_1', 'proxy_variance_5', 'proxy_variance_22',
            'negative_variance_22', 'positive_variance_22',
            'peer_variance_22', 'peer_count', 'peer_coverage')


def build_features(frame, sessions, universe, min_peers=5):
    """Return every stock/session with past-only proxies, nulls and peer support.

    Caller supplies one partition, its full session grid and fixed universe. A
    stock's peer value uses only other rows with cutoff <= that stock's cutoff.
    Missing days occupy calendar slots; zeros are observed values, never fill-ins.
    """
    required = {'ticker','session','cutoff','log_return_1'}
    if not required <= set(frame) or frame.duplicated(['ticker','session']).any():
        raise ValueError('feature_schema_or_duplicate')
    if not sessions or list(sessions) != sorted(set(sessions)):
        raise ValueError('ordered_session_grid_required')
    if len(set(universe)) != len(universe) or not 1 <= min_peers < len(universe):
        raise ValueError('peer_scope')
    years = {str(s)[:4] for s in sessions}
    if not (years <= {'2020','2021','2022'} or years <= {'2023','2024'}):
        raise ValueError('train_or_development_partition_only')
    f = frame[list(required)].copy()
    if not set(f.ticker) <= set(universe) or not set(f.session) <= set(sessions):
        raise ValueError('unknown_key')
    if any(pd.Timestamp(x).tzinfo is None for x in f.cutoff):
        raise ValueError('explicit_cutoff_timezone_required')
    f['cutoff'] = pd.to_datetime(f.cutoff,utc=True)
    if f.cutoff.isna().any() or any(d.strftime('%Y-%m-%d') != s for d,s in zip(f.cutoff,f.session)):
        raise ValueError('cutoff_session_mismatch')
    values=pd.to_numeric(f.log_return_1,errors='raise')
    if np.isinf(values.to_numpy(dtype=float)).any():raise ValueError('nonfinite_return')
    f['log_return_1']=values
    index=pd.MultiIndex.from_product([universe,sessions],names=['ticker','session'])
    out=f.set_index(['ticker','session']).reindex(index)
    chunks=[]
    for ticker in universe:
        g=out.loc[ticker].copy();r=g.log_return_1;squared=r.pow(2)
        for n in (1,5,22):g['proxy_variance_'+str(n)]=252*squared.rolling(n,min_periods=n).mean()
        g['negative_variance_22']=252*r.clip(upper=0).pow(2).rolling(22,min_periods=22).mean()
        g['positive_variance_22']=252*r.clip(lower=0).pow(2).rolling(22,min_periods=22).mean()
        g['ticker']=ticker;chunks.append(g.reset_index())
    result=pd.concat(chunks,ignore_index=True)
    result['peer_variance_22']=np.nan;result['peer_count']=np.nan;result['peer_coverage']=np.nan
    for _,group in result.groupby('session',sort=False):
        for idx,row in group.iterrows():
            if pd.isna(row.cutoff):continue
            valid=group[(group.ticker!=row.ticker)&group.proxy_variance_22.notna()&
                        group.cutoff.notna()&(group.cutoff<=row.cutoff)]
            count=len(valid);result.at[idx,'peer_count']=count
            result.at[idx,'peer_coverage']=count/(len(universe)-1)
            if count>=min_peers:result.at[idx,'peer_variance_22']=valid.proxy_variance_22.mean()
    if np.isinf(result[list(FEATURES)].to_numpy(dtype=float)).any():
        raise ValueError('derived_numeric_overflow')
    for name in FEATURES:
        result[name+'_missing']=result[name].isna()
    return result[['ticker','session','cutoff',*FEATURES,*(n+'_missing' for n in FEATURES)]]
