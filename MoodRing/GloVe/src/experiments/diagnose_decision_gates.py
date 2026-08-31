from __future__ import annotations

import math
from typing import Dict, List, Any, Tuple
import numpy as np
import torch
import scipy.stats as stats

from models.temporal_transformer import TemporalTransformerModel, detect_hardware_device, BetaNLLLoss
from models.regime_detector import SoftRegimeDetector
from engine.decision_engine import DecisionEngine


def compute_standardized_residuals(y_true: np.ndarray, mu_pred: np.ndarray, var_pred: np.ndarray) -> Dict[str, float]:
    """
    Calculates standardized residuals z = (y - mu) / sigma and computes:
    - Residual Mean
    - Residual Standard Deviation
    - Skewness
    - Kurtosis (Excess Kurtosis)
    """
    sigma_pred = np.sqrt(np.maximum(1e-6, var_pred))
    z = (y_true - mu_pred) / sigma_pred

    res_mean = float(np.mean(z))
    res_std = float(np.std(z))
    res_skew = float(stats.skew(z.flatten()))
    res_kurtosis = float(stats.kurtosis(z.flatten()))  # Excess kurtosis (0 for Gaussian)

    return {
        "residual_mean": res_mean,
        "residual_std": res_std,
        "residual_skewness": res_skew,
        "residual_kurtosis": res_kurtosis,
    }


def compute_student_t_nll(y_true: np.ndarray, mu_pred: np.ndarray, var_pred: np.ndarray, df: float = 4.0) -> float:
    """
    Computes negative log-likelihood under heavy-tailed Student-t distribution (df = 4.0).
    """
    sigma_pred = np.sqrt(np.maximum(1e-6, var_pred))
    z = (y_true - mu_pred) / sigma_pred

    # Student-t Log Likelihood: log C - (df + 1)/2 * log(1 + z^2 / df) - log(sigma)
    log_const = math.lgamma((df + 1.0) / 2.0) - math.lgamma(df / 2.0) - 0.5 * math.log(df * math.pi)
    log_lik = log_const - 0.5 * (df + 1.0) * np.log(1.0 + (z ** 2) / df) - np.log(sigma_pred + 1e-6)
    return float(-np.mean(log_lik))


def instrument_decision_engine(
    models_dict: Dict[str, Any],
    x_test: torch.Tensor,
    y_test: torch.Tensor,
    device: torch.device,
) -> Dict[str, Any]:
    """
    Instruments every gate in DecisionEngine to log exact PASS reasons:
    - Gate 1: CIRCUIT_BREAKER_STALE_DATA
    - Gate 2: NEGATIVE_EXPECTED_RETURN_GUARDRAIL (mu <= 0 or prob_up < 0.52)
    - Gate 3: BELOW_MINIMUM_ALLOCATION_THRESHOLD (Calc size < $100)
    - Gate 4: ACTIVE_BUY (Passed all gates)
    """
    engine = DecisionEngine(base_allocation=5000.0, max_allocation=10000.0)
    regime_detector = SoftRegimeDetector()
    diagnostic_summary: Dict[str, Any] = {}

    print("\n" + "=" * 80)
    print("DECISION ENGINE GATE INSTRUMENTATION & GATE DIAGNOSTICS")
    print("=" * 80)

    for group_name, model in models_dict.items():
        gate_counts = {
            "STALE_DATA": 0,
            "NEGATIVE_RETURN_GUARDRAIL": 0,
            "BELOW_MIN_THRESHOLD": 0,
            "ACTIVE_BUY": 0,
        }
        calculated_sizes: List[float] = []

        model.eval()
        with torch.no_grad():
            out = model(x_test)
            p_up_vec = out["prob_up"].cpu().numpy().flatten()
            mu_vec = out["mu"].cpu().numpy().flatten()
            var_vec = out["var"].cpu().numpy().flatten()
            tp_vec = out["trade_success_prob"].cpu().numpy().flatten()
            risk_vec = out["drawdown_risk"].cpu().numpy().flatten()

        N = len(p_up_vec)
        for i in range(N):
            # Scale mu and var to realistic decimal return scale
            p_up = float(p_up_vec[i])
            mu_i = float(np.clip(mu_vec[i] * 0.015 + 0.005, -0.01, 0.035))
            var_i = float(np.clip(var_vec[i] * 0.0003 + 0.00025, 0.0001, 0.001))
            tp_i = float(tp_vec[i])
            risk_i = float(risk_vec[i])

            p_reg = regime_detector.predict_regime_probabilities(garch_vol=1.8, adx=26.0, plus_di=24.0, minus_di=12.0)
            r_m, dom_reg = regime_detector.compute_regime_multiplier(p_reg)

            dec = engine.evaluate_trade_decision(
                ticker="NVDA",
                prob_up=p_up,
                mu=mu_i,
                var=var_i,
                trade_success_prob=tp_i,
                drawdown_risk=risk_i,
                regime_multiplier=r_m,
                dominant_regime=dom_reg,
                current_price=125.0,
                feature_metadata={"is_stale": False, "hours_since_update": 1.0, "missing_ratio": 0.0},
            )

            calc_s = dec.get("calculated_position_size", 0.0)
            calculated_sizes.append(calc_s)

            if dec["action"] == "BUY":
                gate_counts["ACTIVE_BUY"] += 1
            else:
                reason = dec.get("reason", "")
                if "STALE" in reason:
                    gate_counts["STALE_DATA"] += 1
                elif "NEGATIVE" in reason:
                    gate_counts["NEGATIVE_RETURN_GUARDRAIL"] += 1
                elif "BELOW" in reason:
                    gate_counts["BELOW_MIN_THRESHOLD"] += 1

        avg_calc_size = float(np.mean(calculated_sizes))
        diagnostic_summary[group_name] = {
            "gate_counts": gate_counts,
            "avg_calculated_size": avg_calc_size,
            "total_evaluations": N,
        }

        print(f"\n  [{group_name}] (N={N})")
        print(f"    Avg Raw Calculated Size: ${avg_calc_size:.2f}")
        print(f"    Gate Breakdown:")
        print(f"      - Gate 1 (Stale Data):           {gate_counts['STALE_DATA']} / {N}")
        print(f"      - Gate 2 (Negative Return μ<=0): {gate_counts['NEGATIVE_RETURN_GUARDRAIL']} / {N}")
        print(f"      - Gate 3 (Below Min Size <$100): {gate_counts['BELOW_MIN_THRESHOLD']} / {N}")
        print(f"      - PASSED ALL GATES (Active Buy): {gate_counts['ACTIVE_BUY']} / {N}")

    return diagnostic_summary


def run_diagnostics_pipeline() -> None:
    device = detect_hardware_device()
    print("\n" + "=" * 80)
    print("RUNNING RETURN DISTRIBUTION AUDIT & UNCERTAINTY LIKELIHOOD COMPARISON")
    print("=" * 80)

    np.random.seed(42)
    N_samples = 300
    seq_len = 30

    x_full = torch.randn(N_samples, seq_len, 16, device=device)
    y_full = torch.from_numpy(np.random.normal(0.001, 0.018, size=(N_samples, 1))).float().to(device)

    idx_train = int(N_samples * 0.6)
    idx_val = int(N_samples * 0.8)

    x_train, y_train = x_full[:idx_train], y_full[:idx_train]
    x_val, y_val = x_full[idx_train:idx_val], y_full[idx_train:idx_val]
    x_test, y_test = x_full[idx_val:], y_full[idx_val:]

    # 1. Train Model
    model = TemporalTransformerModel(input_dim=16, d_model=32, sequence_length=seq_len).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
    loss_fn = BetaNLLLoss(beta=0.25)

    model.train()
    for ep in range(5):
        optimizer.zero_grad()
        out = model(x_train)
        loss = loss_fn(out["mu"], out["var"], y_train)
        loss.backward()
        optimizer.step()

    model.eval()
    with torch.no_grad():
        out_te = model(x_test)
        mu_pred = out_te["mu"].cpu().numpy().flatten()
        var_pred = out_te["var"].cpu().numpy().flatten()
        y_true = y_test.cpu().numpy().flatten()

    # 2. Standardized Residual Audit
    residual_stats = compute_standardized_residuals(y_true, mu_pred, var_pred)
    print("\n1. Standardized Residual Audit (z = (y - mu) / sigma):")
    print(f"   Residual Mean z:     {residual_stats['residual_mean']:.6f}  (Target: 0.0)")
    print(f"   Residual Std z:      {residual_stats['residual_std']:.6f}  (Target: 1.0)")
    print(f"   Residual Skewness:   {residual_stats['residual_skewness']:.6f}  (Target: 0.0)")
    print(f"   Excess Kurtosis:     {residual_stats['residual_kurtosis']:.6f}  (Target: 0.0 for Gaussian, >0 heavy tails)")

    # 3. Compare Uncertainty Models (Gaussian vs Beta-NLL vs Student-t)
    gaussian_nll = float(np.mean(0.5 * (((y_true - mu_pred) ** 2) / (var_pred + 1e-6) + np.log(var_pred + 1e-6))))
    student_t_nll = compute_student_t_nll(y_true, mu_pred, var_pred, df=4.0)

    print("\n2. Uncertainty Model Likelihood Comparison:")
    print(f"   - Gaussian NLL (beta=0.00):      {gaussian_nll:.6f}")
    print(f"   - Beta-NLL (beta=0.25):          {loss_fn(torch.tensor(mu_pred), torch.tensor(var_pred), torch.tensor(y_true)).item():.6f}")
    print(f"   - Student-t NLL (df=4, Heavy):   {student_t_nll:.6f}")

    # 4. Instrument Decision Engine Gates
    models_dict = {
        "Full Model": model,
    }
    instrument_decision_engine(models_dict, x_test, y_test, device)


if __name__ == "__main__":
    run_diagnostics_pipeline()
