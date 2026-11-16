"""Synthetic mathematical checks; no QLIKE forecasting tree is implemented."""
import numpy as np
import pytest
from research_data.qlike_proposal_checks import derivatives,constant_optimum,proposed_leaf_step
from research_data.forecast_runners import FLOOR


def test_log_link_gradient_and_hessian_against_finite_differences():
    f=np.log(np.array([.002,.04,2.]));y=np.array([0.,.1,20.]);h=1e-5
    loss,g,H=derivatives(f,y)
    lp,gp,_=derivatives(f+h,y);lm,gm,_=derivatives(f-h,y)
    np.testing.assert_allclose((lp-lm)/(2*h),g,rtol=1e-8,atol=1e-8)
    np.testing.assert_allclose((gp-gm)/(2*h),H,rtol=1e-8,atol=1e-8)
    assert (H>=0).all() and H[0]==0


def test_constant_optimum_arithmetic_mean_and_zero_outcomes():
    y=np.array([0.,.01,100.]);f=constant_optimum(y)
    assert np.exp(f)==pytest.approx(y.mean())
    assert abs(derivatives(np.full(3,f),y)[1].sum())<1e-12
    assert constant_optimum([0.,0.])==np.log(FLOOR)
    assert proposed_leaf_step(np.zeros(2),np.zeros(2))==-1.


def test_scaling_changes_loss_by_constant_not_gradients_away_from_floor():
    y=np.array([.1,20.]);f=np.log([.05,4.]);a=derivatives(f,y);b=derivatives(f+np.log(252),y*252)
    np.testing.assert_allclose(b[0]-a[0],np.log(252));np.testing.assert_allclose(a[1],b[1]);np.testing.assert_allclose(a[2],b[2])


def test_underprediction_extreme_is_preserved_and_step_bounded():
    y=np.array([.04,1000.]);before=y.copy();f=np.log([FLOOR,FLOOR])
    l,g,h=derivatives(f,y);assert l.max()>1e10
    assert abs(proposed_leaf_step(f,y))<=1
    np.testing.assert_equal(y,before)


@pytest.mark.parametrize('f,y',[([0.],[-1.]),([np.nan],[1.]),([-1000.],[1.]),([1000.],[1.]),([np.log(FLOOR)],[1e308])])
def test_invalid_or_overflowing_domain_fails_without_silent_clipping(f,y):
    with pytest.raises(ValueError):derivatives(f,y)


def test_decreasing_small_step_on_synthetic_positive_leaf():
    f=np.log(np.array([.01,.02,.04]));y=np.array([.1,.2,.4])
    step=proposed_leaf_step(f,y)
    assert derivatives(f+.03*step,y)[0].sum()<derivatives(f,y)[0].sum()
