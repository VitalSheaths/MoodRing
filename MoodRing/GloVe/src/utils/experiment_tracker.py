from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from config.paths import PARQUET_ROOT


class ExperimentTracker:
    """
    Dedicated Experiment Tracking Module.
    Records complete metadata for every training run, backtest, and paper trading iteration.
    """

    def __init__(self, storage_dir: Optional[Path | str] = None) -> None:
        self.storage_dir = Path(storage_dir) if storage_dir else PARQUET_ROOT.parent / "experiments"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def log_experiment(self, experiment_data: Dict[str, Any]) -> Path:
        """Logs experiment run parameters and performance metrics to JSON."""
        exp_id = experiment_data.get("experiment_id") or f"exp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        
        # Mandatory schema defaults
        full_record = {
            "experiment_id": exp_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ablation_group": experiment_data.get("ablation_group", "full_model"),
            "baseline_model": experiment_data.get("baseline_model", "none"),
            "sequence_length": experiment_data.get("sequence_length", 30),
            "optimizer": experiment_data.get("optimizer", "AdamW"),
            "sentiment_model_version": experiment_data.get("sentiment_model_version", "finbert_v1.0"),
            "regime_model_version": experiment_data.get("regime_model_version", "soft_probabilistic_v1.0"),
            "calibration_dataset_version": experiment_data.get("calibration_dataset_version", "calib_v1.0"),
            "beta_param": experiment_data.get("beta_param", 0.5),
            "raw_ece": experiment_data.get("raw_ece", None),
            "calibrated_ece": experiment_data.get("calibrated_ece", None),
            "raw_brier": experiment_data.get("raw_brier", None),
            "calibrated_brier": experiment_data.get("calibrated_brier", None),
            "calculated_position_size": experiment_data.get("calculated_position_size", None),
            "final_position_size": experiment_data.get("final_position_size", None),
            "max_allocation": experiment_data.get("max_allocation", 10000.0),
            "turnover": experiment_data.get("turnover", 0.0),
            "commission_cost": experiment_data.get("commission_cost", 0.0),
            "slippage_cost": experiment_data.get("slippage_cost", 0.0),
            "feature_missing_rate": experiment_data.get("feature_missing_rate", 0.0),
            "news_coverage_rate": experiment_data.get("news_coverage_rate", 1.0),
            "stale_data_events": experiment_data.get("stale_data_events", 0),
        }
        full_record.update(experiment_data)

        out_path = self.storage_dir / f"{exp_id}.json"
        out_path.write_text(json.dumps(full_record, indent=2), encoding="utf-8")
        print(f"[experiment_tracker] Experiment ({full_record['ablation_group']}) logged to {out_path}")
        return out_path

    def list_experiments(self) -> List[Dict[str, Any]]:
        """List all stored experiment runs."""
        experiments: List[Dict[str, Any]] = []
        for p in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                experiments.append(data)
            except Exception:
                continue
        return sorted(experiments, key=lambda x: str(x.get("timestamp")), reverse=True)
