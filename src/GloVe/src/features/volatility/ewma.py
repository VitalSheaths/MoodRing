from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar


@dataclass
class EWMAMLEConfig:
    lb: float = 0.80
    ub: float = 0.999
    eps: float = 1e-8
    xatol: float = 1e-4
    default_lambda: float = 0.94
    init_window: int = 10


class EWMAVolatilityEstimator:
    def __init__(self, config: EWMAMLEConfig | None = None):
        self.cfg = config or EWMAMLEConfig()

    def ewma_var_series(self, s_shifted: np.ndarray, lam: float) -> np.ndarray:
        n = len(s_shifted)
        var = np.empty(n, dtype=float)
        var[:] = np.nan

        if n == 0:
            return var

        init = np.nanvar(s_shifted[: min(self.cfg.init_window, n)])
        mean_val = np.nanmean(s_shifted)
        if not (np.isfinite(init) and init > 0):
            init = mean_val if np.isfinite(mean_val) and mean_val > 0 else 1e-6

        var[0] = init
        for i in range(1, n):
            var[i] = lam * var[i - 1] + (1.0 - lam) * s_shifted[i]

        return var

    def mle_lambda_for_group(self, df: pd.DataFrame) -> pd.DataFrame:
        s_shifted = df["r2"].shift(1).fillna(0.0).to_numpy()
        r2 = df["r2"].to_numpy()

        def neg_loglik(lam: float) -> float:
            var = self.ewma_var_series(s_shifted, lam)
            var = np.where(var <= 0, self.cfg.eps, var)
            ll = -0.5 * np.nansum(np.log(var) + r2 / var)
            return -ll

        res = minimize_scalar(
            neg_loglik,
            bounds=(self.cfg.lb, self.cfg.ub),
            method="bounded",
            options={"xatol": self.cfg.xatol},
        )
        lam_mle = float(res.x) if res.success else self.cfg.default_lambda

        var = self.ewma_var_series(s_shifted, lam_mle)
        var = np.where(var <= 0, self.cfg.eps, var)

        out = df.copy()
        out["ewma_var_mle"] = var
        out["ewma_vol_mle"] = np.sqrt(var)
        out["lambda_mle"] = lam_mle
        out["n_obs"] = len(out)
        return out
