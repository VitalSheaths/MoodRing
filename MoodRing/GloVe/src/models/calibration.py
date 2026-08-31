from __future__ import annotations

from typing import Dict, Tuple
import numpy as np


def compute_expected_calibration_error(
    probs: np.ndarray,
    targets: np.ndarray,
    num_bins: int = 10,
) -> float:
    """
    Computes Expected Calibration Error (ECE):
    ECE = sum_{m=1}^M (|B_m| / N) * |acc(B_m) - conf(B_m)|
    Measures calibration quality on out-of-sample data.
    """
    bin_boundaries = np.linspace(0.0, 1.0, num_bins + 1)
    ece = 0.0
    total_samples = len(probs)

    for i in range(num_bins):
        bin_lower = bin_boundaries[i]
        bin_upper = bin_boundaries[i + 1]

        in_bin = (probs > bin_lower) & (probs <= bin_upper)
        bin_size = np.sum(in_bin)

        if bin_size > 0:
            bin_acc = np.mean(targets[in_bin])
            bin_conf = np.mean(probs[in_bin])
            ece += (bin_size / total_samples) * np.abs(bin_acc - bin_conf)

    return float(ece)


def compute_brier_score(probs: np.ndarray, targets: np.ndarray) -> float:
    """
    Computes Brier Score:
    Brier = (1/N) * sum((p_i - y_i)^2)
    """
    return float(np.mean((probs - targets) ** 2))


class OutOfSampleProbabilityCalibrator:
    """
    Out-of-Sample Probability Calibrator.
    Fitted strictly on a dedicated out-of-sample calibration validation window.
    Calibrates probabilities so that an output of 0.85 corresponds to approximately 85%
    empirical frequency among sufficiently sized comparable probability bins.
    """

    def __init__(self, method: str = "temperature_scaling") -> None:
        self.method = method
        self.temperature: float = 1.0
        self.is_fitted: bool = False

    def fit_temperature_scaling(self, logits: np.ndarray, targets: np.ndarray) -> float:
        """
        Fits Temperature Scaling parameter T_calib on out-of-sample calibration logits.
        """
        best_t = 1.0
        best_ece = float("inf")

        for t in np.linspace(0.1, 5.0, 100):
            scaled_logits = logits / t
            scaled_probs = 1.0 / (1.0 + np.exp(-scaled_logits))
            ece = compute_expected_calibration_error(scaled_probs, targets)
            if ece < best_ece:
                best_ece = ece
                best_t = t

        self.temperature = float(best_t)
        self.is_fitted = True
        return self.temperature

    def calibrate(self, probs: np.ndarray) -> np.ndarray:
        """Applies fitted temperature scaling to raw output probabilities."""
        if not self.is_fitted or self.temperature == 1.0:
            return np.clip(probs, 1e-4, 1.0 - 1e-4)

        # Convert back to logits, scale by temperature, then sigmoid
        clipped = np.clip(probs, 1e-4, 1.0 - 1e-4)
        logits = np.log(clipped / (1.0 - clipped))
        scaled_logits = logits / self.temperature
        return 1.0 / (1.0 + np.exp(-scaled_logits))

    def evaluate_calibration(
        self,
        probs: np.ndarray,
        targets: np.ndarray,
    ) -> Dict[str, float]:
        """Evaluates ECE and Brier Score on out-of-sample evaluation data."""
        calibrated_probs = self.calibrate(probs)
        ece = compute_expected_calibration_error(calibrated_probs, targets)
        brier = compute_brier_score(calibrated_probs, targets)
        return {
            "ece": ece,
            "brier_score": brier,
            "temperature": self.temperature,
            "method": self.method,
        }
