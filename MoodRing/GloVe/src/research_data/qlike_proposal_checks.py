"""Numerical primitives for a proposed log-variance QLIKE tree, not a trainer."""
import numpy as np
from .forecast_runners import FLOOR


def derivatives(log_variance,realized_variance):
    """Uncentered QLIKE in log variance: L=f+y*exp(-f), g=1-y/v, H=y/v.

    Bounds are domain checks, never silent target clipping. Zero outcomes are
    valid and have zero curvature. A future optimizer must handle that explicitly.
    """
    f=np.asarray(log_variance,dtype=float);y=np.asarray(realized_variance,dtype=float)
    if f.shape!=y.shape or not f.size or not np.isfinite(f).all() or not np.isfinite(y).all() or (y<0).any():
        raise ValueError('qlike_domain')
    if (f<np.log(FLOOR)).any() or (f>np.log(np.finfo(float).max)).any():raise ValueError('log_variance_domain')
    ratio=np.zeros_like(y);positive=y>0
    with np.errstate(over='raise',invalid='raise',divide='raise',under='ignore'):
        try:ratio[positive]=np.exp(np.log(y[positive])-f[positive])
        except FloatingPointError as e:raise ValueError('nonfinite_ratio') from e
    loss=f+ratio;gradient=1-ratio
    if not np.isfinite(loss).all() or not np.isfinite(gradient).all():raise ValueError('nonfinite_loss')
    return loss,gradient,ratio


def constant_optimum(realized_variance):
    """Positive constant QLIKE optimum equals the arithmetic mean, with floor."""
    y=np.asarray(realized_variance,float)
    if y.ndim!=1 or not len(y) or not np.isfinite(y).all() or (y<0).any():raise ValueError('target_domain')
    # Scale before summing to avoid overflowing a finite nonnegative mean.
    maximum=y.max()
    mean=0. if maximum==0 else maximum*np.mean(y/maximum)
    return float(np.log(max(mean,FLOOR)))


def proposed_leaf_step(log_variance,realized_variance,*,ridge=1.,max_step=1.):
    """Bounded Newton proposal only; a tree must still check actual loss decrease.

    This is not a complete constrained optimizer or a training authorization.
    Positive ridge controls zero/near-zero curvature; the trust bound prevents an
    extreme ratio from producing an unrestricted update. No target is removed.
    """
    if not np.isfinite(ridge) or ridge<=0 or not np.isfinite(max_step) or max_step<=0:raise ValueError('step_controls')
    _,g,h=derivatives(log_variance,realized_variance)
    # Divide sums by a common scale to avoid overflow in G or H.
    scale=max(float(np.max(abs(g))),float(np.max(h)),float(ridge),1.)
    step=-float(np.sum(g/scale))/(float(np.sum(h/scale))+ridge/scale)
    return float(np.clip(step,-max_step,max_step))
