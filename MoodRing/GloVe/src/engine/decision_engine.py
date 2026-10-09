from __future__ import annotations

import math
from typing import Any, Dict, List, Optional
import numpy as np


class DecisionEngine:
    """
    Explainable Risk-Aware Paper Trading Decision Engine.
    
    Includes:
    - Negative Return Guardrail: mu_effective = max(0, mu)
    - Single Bounded Position Sizing: min(Max Allocation, Calculated Size)
    - Stale Data Circuit Breaker: Action = PASS (NO TRADE) on stale/missing data
    - Real Transaction Costs, Slippage & Turnover Drag
    - Post-Hoc Explainable Driver Trade Cards (SHAP / IG Drivers + Regime)
    """

    def __init__(
        self,
        base_allocation: float = 5000.0,
        max_allocation: float = 10000.0,
        commission_bps: float = 10.0,  # 10 bps transaction fee
        slippage_bps: float = 5.0,     # 5 bps bid-ask spread/slippage
        stale_threshold_hours: float = 24.0,
    ) -> None:
        self.base_allocation = base_allocation
        self.max_allocation = max_allocation
        self.commission_rate = commission_bps / 10000.0
        self.slippage_rate = slippage_bps / 10000.0
        self.stale_threshold_hours = stale_threshold_hours

    def check_stale_data_circuit_breaker(self, feature_metadata: Dict[str, Any]) -> bool:
        """
        Stale Data Circuit Breaker:
        Returns True if feature data is stale, incomplete, or missing.
        Triggers immediate NO TRADE (PASS) decision.
        """
        if not feature_metadata or feature_metadata.get("is_stale", False):
            return True
        
        hours_old = feature_metadata.get("hours_since_update", 0.0)
        if hours_old > self.stale_threshold_hours:
            return True

        if feature_metadata.get("missing_ratio", 0.0) > 0.3:
            return True

        return False

    def calculate_net_transaction_cost(self, position_size: float) -> float:
        """Computes brokerage commission + bid-ask spread slippage cost."""
        total_bps_rate = self.commission_rate + self.slippage_rate
        return position_size * total_bps_rate

    def evaluate_trade_decision(
        self,
        ticker: str,
        prob_up: float,
        mu: float,
        var: float,
        trade_success_prob: float,
        drawdown_risk: float,
        regime_multiplier: float = 1.0,
        dominant_regime: str = "bull_trend",
        current_price: float = 100.0,
        feature_metadata: Optional[Dict[str, Any]] = None,
        direction_threshold: float = 0.52,
        trade_success_threshold: float = 0.50,
        estimated_cost_drag: float = 0.0015,
    ) -> Dict[str, Any]:
        """
        Evaluates risk gates, expected net edge, and bounded position sizing.
        """
        top_drivers = feature_metadata.get("top_drivers", []) if feature_metadata else []

        # 1. Stale Data Circuit Breaker
        if self.check_stale_data_circuit_breaker(feature_metadata):
            return {
                "ticker": ticker,
                "action": "PASS",
                "reason": "CIRCUIT_BREAKER_STALE_DATA",
                "calculated_position_size": 0.0,
                "max_allocation": self.max_allocation,
                "final_position_size": 0.0,
                "position_size": 0.0,
                "confidence": float(prob_up),
                "market_regime": dominant_regime,
                "regime_multiplier": float(regime_multiplier),
                "reason_codes": top_drivers,
                "trade_card": f"PASS | Circuit Breaker: Stale Data | Regime: {dominant_regime}",
            }

        values = (prob_up, mu, var, trade_success_prob, drawdown_risk, regime_multiplier, current_price)
        if (not all(math.isfinite(float(value)) for value in values)
                or not 0 <= prob_up <= 1 or not 0 <= trade_success_prob <= 1
                or var <= 0 or current_price <= 0 or regime_multiplier < 0):
            return {"ticker": ticker, "action": "PASS", "reason": "INVALID_PREDICTION",
                    "position_size": 0.0, "reason_codes": top_drivers}

        # 2. Expected Net Edge & Direction Gate
        expected_net_edge = float(mu - estimated_cost_drag)
        if expected_net_edge <= 0.0 or prob_up < direction_threshold or trade_success_prob < trade_success_threshold:
            pass_reason = "NEGATIVE_EXPECTED_NET_EDGE" if expected_net_edge <= 0.0 else (
                "LOW_DIRECTIONAL_CONFIDENCE" if prob_up < direction_threshold else "LOW_TRADE_SUCCESS_PROBABILITY"
            )
            return {
                "ticker": ticker,
                "action": "PASS",
                "reason": pass_reason,
                "calculated_position_size": 0.0,
                "max_allocation": self.max_allocation,
                "final_position_size": 0.0,
                "position_size": 0.0,
                "confidence": float(prob_up),
                "expected_return": float(mu),
                "expected_net_edge": expected_net_edge,
                "estimated_cost_drag": estimated_cost_drag,
                "market_regime": dominant_regime,
                "regime_multiplier": float(regime_multiplier),
                "reason_codes": top_drivers,
                "trade_card": f"PASS | {pass_reason} (Net Edge: +{expected_net_edge*100:.2f}%) | Regime: {dominant_regime}",
            }

        mu_effective = float(np.maximum(0.0, mu))
        # Bounded variance clamping to prevent division by absurdly small values
        bounded_var = float(np.clip(var, 0.0001, 0.01))
        sigma = float(np.sqrt(bounded_var))

        # 3. Single Bounded Position Sizing Formula
        raw_scaled_size = self.base_allocation * (trade_success_prob * mu_effective) / (bounded_var + 1e-4)
        calculated_size = float(raw_scaled_size * regime_multiplier)
        final_position_size = float(np.clip(calculated_size, 0.0, self.max_allocation))

        if final_position_size < 100.0:
            return {
                "ticker": ticker,
                "action": "PASS",
                "reason": "BELOW_MINIMUM_ALLOCATION_THRESHOLD",
                "calculated_position_size": calculated_size,
                "max_allocation": self.max_allocation,
                "final_position_size": 0.0,
                "position_size": 0.0,
                "confidence": float(prob_up),
                "expected_net_edge": expected_net_edge,
                "market_regime": dominant_regime,
                "reason_codes": top_drivers,
                "trade_card": f"PASS | Below Allocation Threshold (Calc: ${calculated_size:.2f}) | Regime: {dominant_regime}",
            }

        # 4. Parametric Risk Metrics (VaR & CVaR)
        # VaR_95 = mu - 1.645 * sigma (Valid under Gaussian residual assumptions)
        var_95 = float(mu - 1.645 * sigma)
        # CVaR_95 = mu - sigma * (phi(1.645) / 0.05)
        cvar_95 = float(mu - sigma * (0.1031 / 0.05))

        # 5. Stop Loss & Take Profit Bounds
        stop_loss_price = float(current_price * (1.0 - np.maximum(0.015, 2.0 * sigma)))
        take_profit_price = float(current_price * (1.0 + np.maximum(0.03, mu_effective * 1.5)))

        transaction_cost = self.calculate_net_transaction_cost(final_position_size)

        return {
            "ticker": ticker,
            "action": "BUY",
            "confidence": float(prob_up),
            "raw_mu": float(mu),
            "effective_mu": float(mu_effective),
            "expected_return": float(mu),
            "predicted_sigma": float(sigma),
            "predicted_variance": float(var),
            "expected_variance": float(var),
            "trade_success_probability": float(trade_success_prob),
            "trade_success_prob": float(trade_success_prob),
            "var_95": var_95,
            "cvar_95": cvar_95,
            "market_regime": dominant_regime,
            "regime_multiplier": float(regime_multiplier),
            "calculated_position_size": calculated_size,
            "max_allocation": self.max_allocation,
            "final_position_size": final_position_size,
            "position_size": final_position_size,
            "estimated_transaction_cost": transaction_cost,
            "current_price": current_price,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "reason_codes": top_drivers,
            "trade_card": (
                f"BUY | Confidence: {prob_up*100:.1f}% | Expected Return: +{mu*100:.2f}% | "
                f"Risk: {drawdown_risk:.2f} | Regime: {dominant_regime} | "
                f"Calc Size: ${calculated_size:.2f} | Max Alloc: ${self.max_allocation:.2f} | Final Size: ${final_position_size:.2f}"
            ),
        }
