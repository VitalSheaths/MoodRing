"""Synthetic checks for temporal isolation, missingness and signed risk proxies."""
import numpy as np
import pandas as pd
import pytest
from research_data.volatility_structure import build_features, FEATURES


def fixture():
    sessions=pd.bdate_range('2023-01-03',periods=30).strftime('%Y-%m-%d').tolist()
    symbols=['AAA','BBB','CCC']
    rows=[dict(ticker=t,session=s,cutoff=s+'T21:20:00Z',log_return_1=(-1)**i*.01)
          for t in symbols for i,s in enumerate(sessions)]
    return pd.DataFrame(rows),sessions,symbols


def test_variance_decomposition_and_observed_zero():
    f,s,u=fixture();a=build_features(f,s,u,min_peers=2)
    last=a[a.session==s[-1]]
    assert np.allclose(last.proxy_variance_22,.0252)
    assert np.allclose(last.negative_variance_22+last.positive_variance_22,last.proxy_variance_22)
    f.log_return_1=0;b=build_features(f,s,u,min_peers=2)
    assert b[b.session==s[-1]].peer_variance_22.eq(0).all()
    assert not b[b.session==s[-1]].peer_variance_22_missing.any()


def test_future_mutation_cannot_change_past_features():
    f,s,u=fixture();a=build_features(f,s,u,min_peers=2)
    f.loc[f.session>s[24],'log_return_1']=100
    b=build_features(f,s,u,min_peers=2)
    pd.testing.assert_frame_equal(a[a.session<=s[24]],b[b.session<=s[24]])


def test_missing_session_not_compressed_or_filled():
    f,s,u=fixture();f=f[~((f.ticker=='AAA')&(f.session==s[15]))]
    a=build_features(f,s,u,min_peers=2)
    assert a[(a.ticker=='AAA')&(a.session==s[-1])].proxy_variance_22.isna().all()
    assert a[(a.ticker=='BBB')&(a.session==s[-1])].peer_variance_22.isna().all()
    assert a[(a.ticker=='BBB')&(a.session==s[-1])].peer_count.item()==1


def test_peer_cutoffs_and_own_return_excluded():
    f,s,u=fixture();f.loc[(f.ticker=='AAA')&(f.session==s[-1]),'log_return_1']=10
    a=build_features(f,s,u,min_peers=2)
    assert a[(a.ticker=='AAA')&(a.session==s[-1])].peer_variance_22.item()==pytest.approx(.0252)
    f.loc[(f.ticker=='BBB')&(f.session==s[-1]),'cutoff']=s[-1]+'T22:20:00Z'
    b=build_features(f,s,u,min_peers=2)
    assert b[(b.ticker=='AAA')&(b.session==s[-1])].peer_variance_22.isna().all()


def test_partition_and_duplicate_rejection():
    f,s,u=fixture()
    with pytest.raises(ValueError):build_features(pd.concat([f,f.iloc[:1]]),s,u,2)
    with pytest.raises(ValueError):build_features(f,[*s,'2025-01-02'],u,2)
    with pytest.raises(ValueError):build_features(f,['2022-12-30',*s],u,2)
