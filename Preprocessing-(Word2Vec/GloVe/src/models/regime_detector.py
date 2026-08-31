from __future__ import annotations

from typing import Dict, Tuple
import numpy as np


class SoftRegimeDetector:
    """
    Soft Regime Probability Detector.
    Outputs continuous regime probability distribution:
    P(Regime) = [P(Bull), P(Bear), P(Sideways), P(High Volatility), P(Low Volatility)]

    Enables smooth position sizing transitions rather than hard discrete jumps.
    """

    REGIME_NAMES = ["bull_trend", "bear_trend", "sideways", "high_volatility", "low_volatility"]

    DEFAULT_POLICY_MULTIPLIERS = {
        "bull_trend": 1.0,
        "low_volatility": 1.0,
        "sideways": 0.7,
        "bear_trend": 0.5,
        "high_volatility": 0.2,
    }

    def __init__(self, policy_multipliers: Dict[str, float] | None = None) -> None:
        self.policy_multipliers = policy_multipliers or dict(self.DEFAULT_POLICY_MULTIPLIERS)

    def predict_regime_probabilities(
        self,
        garch_vol: float,
        adx: float,
        plus_di: float,
        minus_di: float,
    ) -> Dict[str, float]:
        """
        Computes soft regime probabilities using softmax activation over regime score logits.
        """
        # Logit scores based on empirical indicators
        score_high_vol = max(0.0, (garch_vol - 3.0) * 2.0)
        score_low_vol = max(0.0, (1.5 - garch_vol) * 2.0)
        
        adx_strength = max(0.0, adx - 25.0) / 10.0
        score_bull = adx_strength * max(0.0, plus_di - minus_di) / 10.0
        score_bear = adx_strength * max(0.0, minus_di - plus_di) / 10.0
        score_sideways = max(0.0, (25.0 - adx) / 10.0) + 0.5

        logits = np.array([score_bull, score_bear, score_sideways, score_high_vol, score_low_vol])
        
        # Softmax normalization
        exp_logits = np.exp(logits - np.max(logits))
        probs = exp_logits / np.sum(exp_logits)

        return {name: float(p) for name, p in zip(self.REGIME_NAMES, probs)}

    def compute_regime_multiplier(self, regime_probs: Dict[str, float]) -> Tuple[float, str]:
        """
        Computes continuous regime position multiplier:
        Multiplier = sum(P(Regime_k) * w_k)
        Returns multiplier and dominant regime label for explainability.
        """
        mult = 0.0
        dominant_regime = "sideways"
        max_p = -1.0

        for name, prob in regime_probs.items():
            w = self.policy_multipliers.get(name, 0.7)
            mult += prob * w
            if prob > max_p:
                max_p = prob
                dominant_regime = name

        return float(np.clip(mult, 0.2, 1.0)), dominant_regime
