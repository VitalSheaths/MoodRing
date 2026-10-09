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
    min_obs: int = 60
    window: int = 252
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
    """One-step forecasts fitted only to returns known at each row's close.

    No full-sample parameter estimates are reused for historical feature rows.
    Values remain in percent-return units, as in the existing volatility pipeline.
    """
    cfg = cfg or ArchConfig()
    if cfg.min_obs < 2 or cfg.window < cfg.min_obs:
        raise ValueError("window must be at least min_obs, and min_obs must be at least two")
    out = df.sort_values("trade_date").copy().reset_index(drop=True)
    records = []
    for i in range(len(out)):
        y = out["log_return_scaled"].iloc[max(0, i + 1 - cfg.window):i + 1].dropna()
        scaled, factor, metric = auto_rescale(y, cfg)
        row = {"arch_scale_before": metric, "arch_scale_factor": factor, "n_obs": len(y)}
        for name, kind in (("garch", "GARCH"), ("egarch", "EGARCH")):
            row.update({f"{name}_vol": np.nan, f"{name}_aic": np.nan, f"{name}_bic": np.nan,
                        f"{name}_fit_ok": False, f"{name}_fit_error": "insufficient_history"})
            if len(y) < cfg.min_obs:
                continue
            try:
                model = arch_model(scaled, mean=cfg.mean, vol=kind, p=cfg.p, q=cfg.q,
                                   dist=cfg.dist, rescale=False)
                result = model.fit(disp="off")
                if result.convergence_flag != 0:
                    raise ValueError("ARCH fit did not converge")
                variance = float(result.forecast(horizon=1, reindex=False).variance.iloc[-1, 0])
                if not np.isfinite(variance) or variance <= 0:
                    raise ValueError("Invalid forecast variance")
                row.update({f"{name}_vol": np.sqrt(variance) / factor,
                            f"{name}_aic": result.aic, f"{name}_bic": result.bic,
                            f"{name}_fit_ok": True, f"{name}_fit_error": ""})
            except Exception as exc:
                row[f"{name}_fit_error"] = str(exc)
        records.append(row)
    for column in (records[0] if records else {}):
        out[column] = [row[column] for row in records]
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
