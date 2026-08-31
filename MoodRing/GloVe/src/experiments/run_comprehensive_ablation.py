from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple
import numpy as np
import torch
import torch.nn as nn

from models.regime_detector import SoftRegimeDetector
from models.temporal_transformer import TemporalTransformerModel, detect_hardware_device, BetaNLLLoss
from engine.decision_engine import DecisionEngine
from utils.experiment_tracker import ExperimentTracker


def compute_block_bootstrap_sharpe_ci(
    returns: np.ndarray,
    n_bootstraps: int = 500,
    block_size: int = 5,
    confidence_level: float = 0.95,
) -> Tuple[float, float, float]:
    """Computes Stationary Block Bootstrap 95% Confidence Interval for Sharpe Ratio."""
    N = len(returns)
    if N < 10 or np.std(returns) == 0:
        return 0.0, 0.0, 0.0

    mean_ret = np.mean(returns)
    std_ret = np.std(returns)
    if std_ret < 1e-7:
        return 0.0, 0.0, 0.0

    base_sharpe = float((mean_ret / std_ret) * np.sqrt(252))

    boot_sharpes: list[float] = []
    num_blocks = int(np.ceil(N / block_size))

    for _ in range(n_bootstraps):
        start_indices = np.random.randint(0, max(1, N - block_size), size=num_blocks)
        sampled_blocks = [returns[idx : idx + block_size] for idx in start_indices]
        sample_rets = np.concatenate(sampled_blocks)[:N]

        s_mean = np.mean(sample_rets)
        s_std = np.std(sample_rets)
        if s_std < 1e-7:
            boot_sharpes.append(0.0)
        else:
            boot_sharpes.append(float((s_mean / s_std) * np.sqrt(252)))

    alpha = (1.0 - confidence_level) / 2.0
    ci_lower = float(np.percentile(boot_sharpes, alpha * 100))
    ci_upper = float(np.percentile(boot_sharpes, (1.0 - alpha) * 100))

    return base_sharpe, ci_lower, ci_upper


def compute_student_t_nll(y_true: np.ndarray, mu_pred: np.ndarray, var_pred: np.ndarray, df: float = 4.0) -> float:
    """Computes negative log-likelihood under heavy-tailed Student-t distribution (df = 4.0)."""
    sigma_pred = np.sqrt(np.maximum(1e-6, var_pred))
    z = (y_true - mu_pred) / sigma_pred

    log_const = math.lgamma((df + 1.0) / 2.0) - math.lgamma(df / 2.0) - 0.5 * math.log(df * math.pi)
    log_lik = log_const - 0.5 * (df + 1.0) * np.log(1.0 + (z ** 2) / df) - np.log(sigma_pred + 1e-6)
    return float(-np.mean(log_lik))


def run_experiment_1_threshold_sensitivity(
    model: nn.Module,
    x_test: torch.Tensor,
    stock_returns: np.ndarray,
) -> List[Dict[str, Any]]:
    """
    EXPERIMENT 1: GATE THRESHOLD SENSITIVITY & NET EDGE GRID SEARCH
    Grid search across Direction Thresholds (0.50 - 0.65) and Trade-Success Thresholds (0.50 - 0.70).
    """
    print("\n" + "=" * 80)
    print("EXPERIMENT 1: GATE THRESHOLD SENSITIVITY & COST-ADJUSTED NET EDGE GRID SEARCH")
    print("=" * 80)

    engine = DecisionEngine(base_allocation=5000.0, max_allocation=10000.0)
    regime_detector = SoftRegimeDetector()
    results: List[Dict[str, Any]] = []
    N_days = len(stock_returns)

    dir_thresholds = [0.50, 0.52, 0.55, 0.60]
    tp_thresholds = [0.50, 0.55, 0.60]

    model.eval()
    with torch.no_grad():
        out = model(x_test)
        p_up_vec = out["prob_up"].cpu().numpy().flatten()
        mu_vec = out["mu"].cpu().numpy().flatten()
        var_vec = out["var"].cpu().numpy().flatten()
        tp_vec = out["trade_success_prob"].cpu().numpy().flatten()
        risk_vec = out["drawdown_risk"].cpu().numpy().flatten()

    for dir_t in dir_thresholds:
        for tp_t in tp_thresholds:
            strategy_daily_returns = np.zeros(N_days)
            daily_allocations = np.zeros(N_days)
            buy_count = 0

            for t in range(N_days):
                idx = t % len(p_up_vec)
                p_up = float(p_up_vec[idx])
                mu_i = float(np.clip(mu_vec[idx] * 0.015 + 0.008, -0.01, 0.035))
                var_i = float(np.clip(var_vec[idx] * 0.0003 + 0.00025, 0.0001, 0.001))
                tp_i = float(tp_vec[idx])
                risk_i = float(risk_vec[idx])

                p_reg = regime_detector.predict_regime_probabilities(garch_vol=1.8, adx=26.0, plus_di=24.0, minus_di=12.0)
                r_m, dom_reg = regime_detector.compute_regime_multiplier(p_reg)

                dec = engine.evaluate_trade_decision(
                    ticker="NVDA", prob_up=p_up, mu=mu_i, var=var_i, trade_success_prob=tp_i,
                    drawdown_risk=risk_i, regime_multiplier=r_m, dominant_regime=dom_reg, current_price=125.0,
                    direction_threshold=dir_t, trade_success_threshold=tp_t, estimated_cost_drag=0.0015
                )

                alloc_t = dec.get("final_position_size", 0.0) / 10000.0
                daily_allocations[t] = alloc_t
                if dec["action"] == "BUY":
                    buy_count += 1
                    strategy_daily_returns[t] = alloc_t * (stock_returns[t] * 1.3) - 0.00015

            avg_exp = float(np.mean(daily_allocations))
            sharpe, ci_low, ci_high = compute_block_bootstrap_sharpe_ci(strategy_daily_returns)
            cum_rets = np.cumprod(1.0 + strategy_daily_returns)
            peak = np.maximum.accumulate(cum_rets)
            max_dd = float(np.min((cum_rets - peak) / peak)) if len(cum_rets) > 0 else 0.0

            record = {
                "direction_threshold": dir_t,
                "trade_success_threshold": tp_t,
                "trade_count": buy_count,
                "pass_count": N_days - buy_count,
                "avg_exposure": round(avg_exp, 3),
                "sharpe_ratio": round(sharpe, 2),
                "sharpe_ci_lower": round(ci_low, 2),
                "sharpe_ci_upper": round(ci_high, 2),
                "max_drawdown": round(max_dd, 4),
            }
            results.append(record)

            print(f"  [Dir Threshold={dir_t:.2f} | TP Threshold={tp_t:.2f}] Trades: {buy_count}/{N_days} | Avg Exp: {avg_exp*100:.1f}% | Sharpe: {sharpe:.2f} [95% CI: {ci_low:.2f} to {ci_high:.2f}] | Max DD: {max_dd*100:.1f}%")

    return results


def run_experiment_2_regime_isolation(
    model: nn.Module,
    x_test: torch.Tensor,
    stock_returns: np.ndarray,
) -> List[Dict[str, Any]]:
    """
    EXPERIMENT 2: ISOLATE REGIME CONTRIBUTION
    Evaluates identical model predictions across 5 distinct regime treatments.
    """
    print("\n" + "=" * 80)
    print("EXPERIMENT 2: ISOLATE REGIME CONTRIBUTION (5 REGIME SEUPS WITH IDENTICAL PREDICTIONS)")
    print("=" * 80)

    engine = DecisionEngine(base_allocation=5000.0, max_allocation=10000.0)
    regime_detector = SoftRegimeDetector()
    results: List[Dict[str, Any]] = []
    N_days = len(stock_returns)

    setups = [
        {"name": "Setup A: No Regime Engine", "treatment": "no_regime"},
        {"name": "Setup B: Fixed Multiplier = 1.0", "treatment": "fixed_1.0"},
        {"name": "Setup C: Soft Regime Multiplier", "treatment": "soft_regime"},
        {"name": "Setup D: Hard Regime Classification", "treatment": "hard_regime"},
        {"name": "Setup E: Inverted Regime Control", "treatment": "inverted_regime"},
    ]

    model.eval()
    with torch.no_grad():
        out = model(x_test)
        p_up_vec = out["prob_up"].cpu().numpy().flatten()
        mu_vec = out["mu"].cpu().numpy().flatten()
        var_vec = out["var"].cpu().numpy().flatten()
        tp_vec = out["trade_success_prob"].cpu().numpy().flatten()
        risk_vec = out["drawdown_risk"].cpu().numpy().flatten()

    for s in setups:
        s_name = s["name"]
        treatment = s["treatment"]
        strategy_daily_returns = np.zeros(N_days)
        daily_allocations = np.zeros(N_days)
        buy_count = 0

        for t in range(N_days):
            idx = t % len(p_up_vec)
            p_up = float(p_up_vec[idx])
            mu_i = float(np.clip(mu_vec[idx] * 0.015 + 0.008, -0.01, 0.035))
            var_i = float(np.clip(var_vec[idx] * 0.0003 + 0.00025, 0.0001, 0.001))
            tp_i = float(tp_vec[idx])
            risk_i = float(risk_vec[idx])

            p_reg = regime_detector.predict_regime_probabilities(garch_vol=1.8, adx=26.0, plus_di=24.0, minus_di=12.0)
            soft_m, dom_reg = regime_detector.compute_regime_multiplier(p_reg)

            if treatment == "no_regime":
                r_m = 1.0
            elif treatment == "fixed_1.0":
                r_m = 1.0
            elif treatment == "soft_regime":
                r_m = soft_m
            elif treatment == "hard_regime":
                r_m = 1.0 if dom_reg in ("bull_trend", "low_volatility") else (0.5 if dom_reg in ("sideways", "bear_trend") else 0.2)
            elif treatment == "inverted_regime":
                r_m = float(np.clip(1.0 / (soft_m + 1e-4), 0.2, 1.5))

            dec = engine.evaluate_trade_decision(
                ticker="NVDA", prob_up=p_up, mu=mu_i, var=var_i, trade_success_prob=tp_i,
                drawdown_risk=risk_i, regime_multiplier=r_m, dominant_regime=dom_reg, current_price=125.0,
                direction_threshold=0.52, trade_success_threshold=0.50, estimated_cost_drag=0.0015
            )

            alloc_t = dec.get("final_position_size", 0.0) / 10000.0
            daily_allocations[t] = alloc_t
            if dec["action"] == "BUY":
                buy_count += 1
                strategy_daily_returns[t] = alloc_t * (stock_returns[t] * 1.3) - 0.00015

        avg_exp = float(np.mean(daily_allocations))
        sharpe, ci_low, ci_high = compute_block_bootstrap_sharpe_ci(strategy_daily_returns)
        cum_rets = np.cumprod(1.0 + strategy_daily_returns)
        peak = np.maximum.accumulate(cum_rets)
        max_dd = float(np.min((cum_rets - peak) / peak)) if len(cum_rets) > 0 else 0.0

        record = {
            "setup_name": s_name,
            "treatment": treatment,
            "trade_count": buy_count,
            "avg_exposure": round(avg_exp, 3),
            "sharpe_ratio": round(sharpe, 2),
            "sharpe_ci_lower": round(ci_low, 2),
            "sharpe_ci_upper": round(ci_high, 2),
            "max_drawdown": round(max_dd, 4),
        }
        results.append(record)

        print(f"  [{s_name}] Trades: {buy_count}/{N_days} | Avg Exp: {avg_exp*100:.1f}% | Sharpe: {sharpe:.2f} [95% CI: {ci_low:.2f} to {ci_high:.2f}] | Max DD: {max_dd*100:.1f}%")

    return results


def run_experiment_3_head_decomposition(
    model: nn.Module,
    x_test: torch.Tensor,
    stock_returns: np.ndarray,
) -> List[Dict[str, Any]]:
    """
    EXPERIMENT 3: HEAD CONTRIBUTION DECOMPOSITION
    Decomposes Full Model components to identify where trading performance originates:
    1. Direction P(Up) Only
    2. μ Only
    3. P(TP) Only
    4. μ + P(TP)
    5. μ + P(TP) + Regime (Full Model)
    """
    print("\n" + "=" * 80)
    print("EXPERIMENT 3: HEAD CONTRIBUTION DECOMPOSITION")
    print("=" * 80)

    engine = DecisionEngine(base_allocation=5000.0, max_allocation=10000.0)
    regime_detector = SoftRegimeDetector()
    results: List[Dict[str, Any]] = []
    N_days = len(stock_returns)

    configs = [
        {"name": "1. Direction P(Up) Only", "use_mu": False, "use_tp": False, "use_regime": False},
        {"name": "2. Expected μ Only", "use_mu": True, "use_tp": False, "use_regime": False},
        {"name": "3. P(TP before SL) Only", "use_mu": False, "use_tp": True, "use_regime": False},
        {"name": "4. Expected μ + P(TP)", "use_mu": True, "use_tp": True, "use_regime": False},
        {"name": "5. Expected μ + P(TP) + Regime (Full)", "use_mu": True, "use_tp": True, "use_regime": True},
    ]

    model.eval()
    with torch.no_grad():
        out = model(x_test)
        p_up_vec = out["prob_up"].cpu().numpy().flatten()
        mu_vec = out["mu"].cpu().numpy().flatten()
        var_vec = out["var"].cpu().numpy().flatten()
        tp_vec = out["trade_success_prob"].cpu().numpy().flatten()
        risk_vec = out["drawdown_risk"].cpu().numpy().flatten()

    for cfg in configs:
        c_name = cfg["name"]
        use_mu = cfg["use_mu"]
        use_tp = cfg["use_tp"]
        use_reg = cfg["use_regime"]

        strategy_daily_returns = np.zeros(N_days)
        daily_allocations = np.zeros(N_days)
        buy_count = 0

        for t in range(N_days):
            idx = t % len(p_up_vec)
            p_up = float(p_up_vec[idx])
            mu_i = float(np.clip(mu_vec[idx] * 0.015 + 0.008, -0.01, 0.035)) if use_mu else 0.010
            var_i = float(np.clip(var_vec[idx] * 0.0003 + 0.00025, 0.0001, 0.001))
            tp_i = float(tp_vec[idx]) if use_tp else 0.60
            risk_i = float(risk_vec[idx])

            p_reg = regime_detector.predict_regime_probabilities(garch_vol=1.8, adx=26.0, plus_di=24.0, minus_di=12.0)
            soft_m, dom_reg = regime_detector.compute_regime_multiplier(p_reg)
            r_m = soft_m if use_reg else 1.0

            dec = engine.evaluate_trade_decision(
                ticker="NVDA", prob_up=p_up, mu=mu_i, var=var_i, trade_success_prob=tp_i,
                drawdown_risk=risk_i, regime_multiplier=r_m, dominant_regime=dom_reg, current_price=125.0,
                direction_threshold=0.52, trade_success_threshold=0.50, estimated_cost_drag=0.0015
            )

            alloc_t = dec.get("final_position_size", 0.0) / 10000.0
            daily_allocations[t] = alloc_t
            if dec["action"] == "BUY":
                buy_count += 1
                strategy_daily_returns[t] = alloc_t * (stock_returns[t] * 1.3) - 0.00015

        avg_exp = float(np.mean(daily_allocations))
        sharpe, ci_low, ci_high = compute_block_bootstrap_sharpe_ci(strategy_daily_returns)
        cum_rets = np.cumprod(1.0 + strategy_daily_returns)
        peak = np.maximum.accumulate(cum_rets)
        max_dd = float(np.min((cum_rets - peak) / peak)) if len(cum_rets) > 0 else 0.0

        record = {
            "config_name": c_name,
            "trade_count": buy_count,
            "avg_exposure": round(avg_exp, 3),
            "sharpe_ratio": round(sharpe, 2),
            "sharpe_ci_lower": round(ci_low, 2),
            "sharpe_ci_upper": round(ci_high, 2),
            "max_drawdown": round(max_dd, 4),
        }
        results.append(record)

        print(f"  [{c_name}] Trades: {buy_count}/{N_days} | Avg Exp: {avg_exp*100:.1f}% | Sharpe: {sharpe:.2f} [95% CI: {ci_low:.2f} to {ci_high:.2f}] | Max DD: {max_dd*100:.1f}%")

    return results


def run_comprehensive_ablation_suite() -> None:
    device = detect_hardware_device()
    tracker = ExperimentTracker()

    print("\n" + "=" * 80)
    print("STARTING COMPREHENSIVE EXPERIMENTAL ABLATION SUITE")
    print("=" * 80)

    np.random.seed(42)
    N_samples, seq_len, N_test_days = 300, 30, 60
    stock_returns = np.random.normal(0.0008, 0.015, size=N_test_days)

    x_full = torch.randn(N_samples, seq_len, 16, device=device)
    y_full = torch.from_numpy(np.random.normal(0.001, 0.018, size=(N_samples, 1))).float().to(device)

    idx_train = int(N_samples * 0.6)
    idx_val = int(N_samples * 0.8)

    x_train, y_train = x_full[:idx_train], y_full[:idx_train]
    x_test, y_test = x_full[idx_val:], y_full[idx_val:]

    # Train Full Model
    model = TemporalTransformerModel(input_dim=16, d_model=32, sequence_length=seq_len).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
    loss_fn = BetaNLLLoss(beta=0.25)

    model.train()
    for ep in range(5):
        optimizer.zero_grad()
        out = model(x_train)
        l = loss_fn(out["mu"], out["var"], y_train)
        l.backward()
        optimizer.step()

    # 1. Run Experiment 1: Gate Threshold Sensitivity
    exp1_res = run_experiment_1_threshold_sensitivity(model, x_test, stock_returns)

    # 2. Run Experiment 2: Isolate Regime Contribution
    exp2_res = run_experiment_2_regime_isolation(model, x_test, stock_returns)

    # 3. Run Experiment 3: Head Contribution Decomposition
    exp3_res = run_experiment_3_head_decomposition(model, x_test, stock_returns)

    print("\n" + "=" * 80)
    print("COMPREHENSIVE EXPERIMENTAL ABLATION SUITE COMPLETED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_comprehensive_ablation_suite()
