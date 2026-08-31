from dataclasses import dataclass
import numpy as np
import pandas as pd
from arch import arch_model


@dataclass
class ArchConfig:
    p: int = 1
    q: int = 1
    dist: str = "t"
    mean: str = "Zero"
    eps: float = 1e-8
    min_scale: float = 1.0
    max_scale: float = 1000.0
    scale_step: float = 0.1  # multiply y by this until in range


def _robust_scale_metric(y: np.ndarray) -> float:
    # same spirit as arch warning: keep magnitude in optimizer-friendly range
    v = np.nanvar(y)
    if not np.isfinite(v) or v <= 0:
        return np.nanstd(y)
    return np.sqrt(v)


def auto_rescale(y: pd.Series, cfg: ArchConfig) -> tuple[np.ndarray, float, float]:
    arr = y.astype(float).to_numpy()
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

    metric_before = _robust_scale_metric(arr)
    if not np.isfinite(metric_before) or metric_before <= 0:
        return arr, 1.0, metric_before

    factor = 1.0
    metric = metric_before
    # downscale if too large
    while metric > cfg.max_scale:
        factor *= cfg.scale_step
        metric *= cfg.scale_step
    # upscale if too small (optional, keeps optimizer stable)
    while metric < cfg.min_scale and metric > 0:
        factor /= cfg.scale_step
        metric /= cfg.scale_step

    return arr * factor, factor, metric_before


def fit_vol_models_for_group(df: pd.DataFrame, cfg: ArchConfig | None = None) -> pd.DataFrame:
    cfg = cfg or ArchConfig()
    out = df.copy().reset_index(drop=True)

    y_raw = out["log_return_scaled"].fillna(0.0)
    y_scaled, scale_factor, metric_before = auto_rescale(y_raw, cfg)

    # diagnostics defaults
    garch_aic = garch_bic = egarch_aic = egarch_bic = np.nan
    garch_vol = pd.Series(np.nan, index=out.index, dtype=float)
    egarch_vol = pd.Series(np.nan, index=out.index, dtype=float)
    garch_ok = egarch_ok = False
    garch_err = egarch_err = ""

    # GARCH(1,1)
    try:
        am_g = arch_model(
            y_scaled, mean=cfg.mean, vol="GARCH", p=cfg.p, q=cfg.q,
            dist=cfg.dist, rescale=False
        )
        res_g = am_g.fit(disp="off")
        garch_ok = True
        garch_aic, garch_bic = res_g.aic, res_g.bic
        # convert back to original units
        garch_vol = pd.Series(np.asarray(res_g.conditional_volatility) / scale_factor, index=out.index)
    except Exception as e:
        garch_err = str(e)

    # EGARCH(1,1)
    try:
        am_e = arch_model(
            y_scaled, mean=cfg.mean, vol="EGARCH", p=cfg.p, q=cfg.q,
            power=1.0, dist=cfg.dist, rescale=False
        )
        res_e = am_e.fit(disp="off")
        egarch_ok = True
        egarch_aic, egarch_bic = res_e.aic, res_e.bic
        egarch_vol = pd.Series(np.asarray(res_e.conditional_volatility) / scale_factor, index=out.index)
    except Exception as e:
        egarch_err = str(e)

    out["garch_vol"] = garch_vol.to_numpy()
    out["egarch_vol"] = egarch_vol.to_numpy()

    out["garch_aic"] = garch_aic
    out["garch_bic"] = garch_bic
    out["egarch_aic"] = egarch_aic
    out["egarch_bic"] = egarch_bic

    out["arch_scale_before"] = metric_before
    out["arch_scale_factor"] = scale_factor
    out["garch_fit_ok"] = garch_ok
    out["egarch_fit_ok"] = egarch_ok
    out["garch_fit_error"] = garch_err
    out["egarch_fit_error"] = egarch_err
    out["n_obs"] = len(out)

    return out


def build_model_diagnostics(df: pd.DataFrame) -> pd.DataFrame:
    # one-row per ticker diagnostics
    return (
        df.groupby("ticker", as_index=False)
        .agg(
            garch_aic=("garch_aic", "first"),
            egarch_aic=("egarch_aic", "first"),
            garch_bic=("garch_bic", "first"),
            egarch_bic=("egarch_bic", "first"),
            arch_scale_before=("arch_scale_before", "first"),
            arch_scale_factor=("arch_scale_factor", "first"),
            garch_fit_ok=("garch_fit_ok", "first"),
            egarch_fit_ok=("egarch_fit_ok", "first"),
            n_obs=("n_obs", "first"),
        )
    )
