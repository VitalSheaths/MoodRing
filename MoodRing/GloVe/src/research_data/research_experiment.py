"""Chronological comparison safeguards for a proposed MASTER adaptation.

No training runner is exposed until a verified PIT panel and accepted target
contract exist. These helpers do not approve evidence or claim model accuracy.
"""
from datetime import datetime,timezone
import numpy as np
from .point_in_time import utc

SPLITS=(('train','2020-01-01','2023-01-01'),('dev','2023-01-01','2025-01-01'),('test','2025-01-01','2027-01-01'))


def split_for(cutoff, label_end):
    """Purge outcomes crossing split boundaries; right-censored targets stay absent."""
    if label_end is None:return None
    cutoff=utc(cutoff);end=utc(label_end)
    if end<=cutoff:raise ValueError('future_target_required')
    for name,start,finish in SPLITS:
        start=datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        finish=datetime.fromisoformat(finish).replace(tzinfo=timezone.utc)
        if start<=cutoff<finish:return name if end<finish else None
    return None


def verify_window(cutoff, observations):
    """Require every nonmissing feature version to have eligible availability evidence.

    Use only records already resolved by point_in_time primitives. Missing values
    remain represented as missing; this helper never invents an availability time.
    """
    cutoff=utc(cutoff)
    for item in observations:
        value=item.get('value');stamp=item.get('available_at')
        if value is None:continue
        if stamp is None or item.get('evidence_resolved') is not True or utc(stamp)>cutoff:
            raise ValueError('unavailable_feature_in_window')


def matched_keys(arms):
    """Return a shared comparison population and explicit exclusions by arm.

    Keys are (session, ticker); no model may independently choose evaluation rows.
    All complete arms are intersected once, before fitting or metric calculation.
    """
    if not arms:raise ValueError('comparison_arms_required')
    sets={name:set(keys) for name,keys in arms.items()}
    common=set.intersection(*sets.values())
    return sorted(common),{name:dict(available=len(keys),excluded_from_matched=len(keys-common)) for name,keys in sets.items()}


class TrainOnlyStandardizer:
    """Fit feature-wise moments on train windows only; append missingness indicators.

    Repeated windows are weighted as supplied; callers should fit on unique
    training-date feature observations to avoid overweighting overlapping windows.
    Never call fit again for a dev/test arm. All-missing training features fail
    closed and need an explicitly revised feature contract.
    """
    def fit(self, values, cutoffs):
        x=np.asarray(values,dtype=float)
        if hasattr(self,'mean'):raise ValueError('preprocessing_already_frozen')
        if x.ndim!=2 or len(x)!=len(cutoffs) or len(x)==0:raise ValueError('training_shape')
        start=utc('2020-01-01T00:00:00Z');end=utc('2023-01-01T00:00:00Z')
        if any(not start<=utc(t)<end for t in cutoffs):raise ValueError('train_only_fit')
        if np.isinf(x).any() or np.isnan(x).all(axis=0).any():raise ValueError('invalid_training_feature')
        self.mean=np.nanmean(x,axis=0);scale=np.nanstd(x,axis=0)
        self.scale=np.where(scale>0,scale,1.)
        return self

    def transform(self,values):
        if not hasattr(self,'mean'):raise ValueError('preprocessing_not_fitted')
        x=np.asarray(values,dtype=float)
        if x.ndim!=2 or x.shape[1]!=len(self.mean) or np.isinf(x).any():raise ValueError('feature_shape_or_values')
        missing=np.isnan(x)
        scaled=(np.where(missing,self.mean,x)-self.mean)/self.scale
        return np.concatenate([scaled,missing.astype(float)],axis=1)
