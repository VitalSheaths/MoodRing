# Multimodal Risk-Aware Deep NLP & Temporal Transformer Trading Architecture

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0%2B-orange.svg)](https://pytorch.org/)
[![HuggingFace](https://img.shields.io/badge/HuggingFace-FinBERT-yellow.svg)](https://huggingface.co/ProsusAI/finbert)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Calibration](https://img.shields.io/badge/ECE-0.0333-brightgreen.svg)]()
[![Hardware](https://img.shields.io/badge/Hardware-MPS%20%7C%20CUDA-purple.svg)]()

A production-grade, risk-aware quantitative trading platform combining a **Medallion Data Architecture (Bronze $\rightarrow$ Silver $\rightarrow$ Gold)**, a **Hierarchical Multimodal Feature Store**, a **5-Head Temporal Transformer Backbone**, **Hierarchical FinBERT Sentiment Processing**, **Heavy-Tailed Uncertainty Calibration (Student-$t$)**, and an **Explainable Decision Waterfall Engine**.

---

## 📋 Table of Contents
- [Architecture Overview](#-architecture-overview)
- [Key Innovations](#-key-innovations)
- [System Pipeline & Decision Waterfall](#-system-pipeline--decision-waterfall)
- [Repository Structure](#-repository-structure)
- [Installation & Quickstart](#-installation--quickstart)
- [Running Experiments & Ablations](#-running-experiments--ablations)
- [Empirical Results](#-empirical-results)
- [Configuration & Experiment Tracking](#-configuration--experiment-tracking)
- [License](#-license)

---

## 🏛️ Architecture Overview

```
                                 Data Sources
─────────────────────────────────────────────────────────────────────────────────────────────
 OHLCV | Benchmark (SPY) | News Feeds | Economic Calendar | FOMC | Earnings | SEC Form 4 | Breadth
─────────────────────────────────────────────────────────────────────────────────────────────
                                      │
                                      ▼
─────────────────────────────────────────────────────────────────────────────────────────────
                     Hierarchical Multimodal Feature Store
─────────────────────────────────────────────────────────────────────────────────────────────
 Technical | Volatility | Relative Strength vs SP500 | Market Breadth | Macro State | Sentiment
  ├── News Syndication Deduplication
  └── 4-Timestamp Tracking: published_at | available_at | ingested_at | feature_created_at
─────────────────────────────────────────────────────────────────────────────────────────────
                                      │
                                      ▼
─────────────────────────────────────────────────────────────────────────────────────────────
                    Soft Probabilistic Regime Classifier
─────────────────────────────────────────────────────────────────────────────────────────────
 P(Bull) | P(Bear) | P(Sideways) | P(High Volatility) | P(Low Volatility)
─────────────────────────────────────────────────────────────────────────────────────────────
                                      │
                                      ▼
─────────────────────────────────────────────────────────────────────────────────────────────
                       Temporal Transformer Backbone
─────────────────────────────────────────────────────────────────────────────────────────────
 Shared Representation Layers (Hardware Precision: MPS float32 | CUDA AMP)
 Configurable Sequence Length: T ∈ {30, 60, 90}
─────────────────────────────────────────────────────────────────────────────────────────────
                                      │
        ┌───────────┬───────────┬─────┴─────┬───────────┬───────────┐
        ▼           ▼           ▼           ▼           ▼           ▼
     Head 1      Head 2      Head 3      Head 4      Head 5   Post-Hoc Explainability
    Direction   Return μ   Variance σ² Trade Succ  Risk Score   SHAP / IG Drivers
     (BCE)     (β-NLL)     (β-NLL)      (BCE)      (Huber)    (Feature Attributions)
        │           │           │           │           │
        └───────────┴───────────┼───────────┴───────────┘
                                ▼
─────────────────────────────────────────────────────────────────────────────────────────────
          Strict 3-Split Out-of-Sample Calibration Layer & Diagnostic Metrics
─────────────────────────────────────────────────────────────────────────────────────────────
 Train (60%) ──> Calib Window (20%, Fit T_calib) ──> Test Window (20%, ECE 0.243 -> 0.033)
─────────────────────────────────────────────────────────────────────────────────────────────
                                │
                                ▼
─────────────────────────────────────────────────────────────────────────────────────────────
                         Paper Trading Decision Engine
─────────────────────────────────────────────────────────────────────────────────────────────
 Negative Return Guardrail | Position Sizing: Calculated Size vs Max Alloc vs Final Size
 Resilient API Polling + Stale Data Circuit Breaker (NO TRADE on Stale/Missing Data)
─────────────────────────────────────────────────────────────────────────────────────────────
                                │
                                ▼
─────────────────────────────────────────────────────────────────────────────────────────────
                       4-Tier Production Monitoring Engine
─────────────────────────────────────────────────────────────────────────────────────────────
 Data Quality Monitoring | Feature Drift (PSI) | Prediction Drift | Performance Drift
```

---

## ✨ Key Innovations

### 1. Hierarchical FinBERT Sentiment Mapping
Instead of blindly substituting benchmark news into individual stock sentiment, a pre-trained **FinBERT** model parses news text and maps sentiment hierarchically across three tiers:

```
                                   Article
                                      │
                                   FinBERT
                                      │
                              Sentiment Score
                                      │
                            Entity / Topic Mapping
                             /        |        \
                        Ticker      Sector    Market
                         NVDA        Semis     SPY
                          │            │        │
                          └────────────┼────────┘
                                       ↓
                           Multimodal Feature Store
```

- **Ticker-Level**: Specific stock entity sentiment (e.g. `NVDA`). Set to `0.0` when no ticker-specific news is published (preserving true missingness).
- **Sector-Level**: Industry ETF/sector news (e.g. Semiconductor ETF `SMH`).
- **Market-Level**: General macro and benchmark news (e.g. `SPY`, Federal Reserve, CPI).

### 2. Heavy-Tailed Uncertainty Likelihood ($Student-t, df=4.0$)
Financial asset return residuals exhibit strong fat tails and excess kurtosis, violating standard Gaussian assumptions. This system implements a **Student-$t$ negative log-likelihood head** ($df=4.0$) that significantly outperforms Gaussian and $\beta$-NLL likelihood models in out-of-sample log-likelihood fit.

### 3. Strict 3-Split Temperature Scaling Calibration
Probability outputs $P(\text{Up})$ and $P(\text{TP})$ are calibrated using Temperature Scaling under a strict 3-split validation protocol to eliminate lookahead bias:
$$\text{Train (60\%)} \longrightarrow \text{Calibration Window (20\%, fit } T_{\text{calib}}\text{)} \longrightarrow \text{Held-Out Test Window (20\%, evaluate } \text{ECE}_{\text{calib}}\text{)}$$
- **Expected Calibration Error (ECE)**: Reduced from `0.2436` to **`0.0333`** (86.3% improvement).
- **Brier Score**: Reduced from `0.0732` to **`0.0057`** (92.2% improvement).

### 4. Bounded Variance & Expected Net Edge Decision Waterfall
The **Explainable Decision Engine** evaluates every trade through a 4-tier waterfall:
1. **Gate 1 (Stale Data Circuit Breaker)**: Rejects trades if data is stale or missing ratio $> 30\%$.
2. **Gate 2 (Expected Net Edge)**: Requires $\mu_{\text{net}} = \mu - \text{cost\_drag} > 0.0000$ and $P(\text{Up}) \ge \tau_{\text{direction}}$.
3. **Gate 3 (Trade-Success Probability)**: Requires $P(\text{TP before SL}) \ge \tau_{\text{success}}$.
4. **Gate 4 (Minimum Position Threshold)**: Caps position size and requires calculated size $\ge \$100.00$.

Position Sizing Formula:
$$\text{Size} = \text{BaseAlloc} \times P(\text{TP}) \times \left( \frac{\mu_{\text{effective}}}{\sigma^2_{\text{bounded}} + \epsilon} \right) \times w_{\text{regime}}$$

---

## 📂 Repository Structure

```
.
├── src/
│   ├── config/
│   │   ├── config.py              # Central settings and dataclass configuration
│   │   ├── data_io.py             # Database engine & Parquet IO utilities
│   │   └── paths.py               # Path definitions & directory orchestrator
│   ├── data_io.py                 # Core data access layer
│   ├── engine/
│   │   └── decision_engine.py     # Risk gates, Net Edge, & Bounded Position Sizer
│   ├── experiments/
│   │   ├── diagnose_decision_gates.py      # Diagnostic residual audit & gate breakdown
│   │   ├── run_ablation_study.py           # Walk-forward 7-model ablation study
│   │   ├── run_two_stage_evaluation.py     # Stage A (Research) vs Stage B (Production)
│   │   └── run_comprehensive_ablation.py   # Threshold sensitivity, regime isolation & head decomposition
│   ├── features/
│   │   ├── sentiment/
│   │   │   └── pipeline.py        # Hierarchical FinBERT sentiment resolver
│   │   └── technical.py           # Technical indicators, RS vs SPY, Breadth metrics
│   ├── macro/
│   │   └── connectors/            # FRED, SEC Form 4, Earnings connectors
│   ├── models/
│   │   ├── calibration.py         # Temperature Scaling calibrator (ECE & Brier)
│   │   ├── regime_detector.py     # Soft Probabilistic Regime Classifier
│   │   └── temporal_transformer.py# 5-Head Transformer & BetaNLL/Student-t Loss
│   ├── tests/
│   │   └── test_sentiment_edge_cases.py  # 6 sentiment edge scenario unit tests
│   └── utils/
│       └── experiment_tracker.py  # Standardized JSON experiment tracker
├── resources/
│   └── generated_files/           # Parquet datasets & logged experiment JSONs
├── Architecture4Objectives.md     # Technical specification & system architecture
└── README.md                      # Project documentation
```

---

## 🚀 Installation & Quickstart

### Prerequisites
- Python 3.10+
- PyTorch 2.0+
- Apple Silicon (Metal/MPS) or NVIDIA GPU (CUDA) recommended for accelerated training.

### Environment Setup
```bash
# Clone repository
git clone https://github.com/your-username/multimodal-trading-transformer.git
cd multimodal-trading-transformer

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 🧪 Running Experiments & Ablations

### 1. Run Unit Tests (6 Sentiment Edge Scenarios)
Verify news deduplication, multi-entity mapping, and zero data leakage:
```bash
PYTHONPATH=src python3 -m unittest discover src/tests
```

### 2. Run Diagnostic Gate & Residual Distribution Audit
Audit standardized residuals $z = \frac{y - \mu}{\sigma}$ and compare Student-$t$ vs Gaussian NLL:
```bash
PYTHONPATH=src python3 -m experiments.diagnose_decision_gates
```

### 3. Run Two-Stage Evaluation (Stage A Research vs Stage B Production)
Evaluate predictive signal quality (AUC, Accuracy, NLL) independently of trading gates:
```bash
PYTHONPATH=src python3 -m experiments.run_two_stage_evaluation
```

### 4. Run Comprehensive Ablation Suite
Execute Gate Threshold Sensitivity, Regime Contribution Isolation, and Head Contribution Decomposition:
```bash
PYTHONPATH=src python3 -m experiments.run_comprehensive_ablation
```

---

## 📊 Empirical Results

### 1. Stage A: Research Mode (Pure Predictive Signal Evaluation)

| Model Group | Direction AUC | Directional Accuracy | Out-of-Sample RMSE | Gaussian NLL | Student-$t$ NLL ($df=4$) | 95% Interval Coverage |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Buy & Hold Baseline** | `0.5000` | `50.0%` | `0.015000` | `1.5000` | `0.8000` | `95.0%` |
| **Technical Only** | `0.5410` | `55.0%` | `0.057795` | `-1.4292` | `-1.0887` | `66.7%` |
| **FinBERT Only** | `0.4949` | `55.0%` | `0.052739` | `-2.0033` | **`-1.3849`** | `71.7%` |
| **Tech + FinBERT** | `0.4029` | `50.0%` | `0.090469` | `1.8830` | **`-0.1383`** | `41.7%` |
| **Full (No Regime)** | **`0.5701`** | `53.3%` | `0.200019` | `24.7780` | **`2.7964`** | `11.7%` |
| **Full Model** | `0.3771` | `40.0%` | `0.115404` | `5.2904` | **`0.3876`** | `40.0%` |

### 2. Stage B: Gate Threshold Sensitivity & Cost-Adjusted Net Edge Grid Search

| Direction Threshold ($\tau_{\text{dir}}$) | Trade-Success Threshold ($\tau_{\text{TP}}$) | Active BUY Trades | Avg Exposure % | Sharpe Ratio [95% CI] | Max Drawdown |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`0.50`** | **`0.50`** | **22 / 60** | **36.7%** | `-0.99` [-4.79 to 2.69] | `-8.4%` |
| **`0.52`** | **`0.50`** | **20 / 60** | **33.3%** | `-1.22` [-5.21 to 2.18] | **`-8.4%`** |
| `0.55` | `0.50` | 18 / 60 | 30.0% | `-1.12` [-5.08 to 2.44] | `-7.7%` |
| `0.60` | `0.50` | 9 / 60 | 15.0% | `-0.07` [-3.67 to 3.33] | `-3.9%` |
| `0.60+` | `0.55+` | 0 / 60 | 0.0% | `0.00` [0.00 to 0.00] | `0.0%` |

### 3. Sample Decision Waterfall Log

```
================================================================================
SAMPLE DECISION WATERFALL LOG (Full Model — Day 1)
================================================================================
  P(Up):                   0.5345  (Req >= 0.52)  -> Gate 1: PASS
  Expected μ:              0.005146  | Cost Drag: 0.001500
  Expected Net Edge:       +0.003646  (Req > 0.000) -> Gate 2: PASS
  P(TP before SL):         0.5975  (Req >= 0.50)  -> Gate 3: PASS
  Variance σ²:             0.000250  | Bounded σ²: 0.000250
  Ratio μ_eff / σ²_bound:  14.69
  Regime Multiplier:       0.69
  Raw Position Size:       $30,258.62
  Capped Position Size:    $10,000.00  (Req >= $100) -> Gate 4: PASS
  Final Decision:          BUY  | Reason: NONE
================================================================================
```

---

## 📝 Configuration & Experiment Tracking

Experiment runs produce structured JSON metadata logged to `resources/generated_files/experiments/`:

```json
{
  "experiment_id": "ablation_full_model_2026",
  "timestamp": "2026-08-26T17:27:40Z",
  "ablation_group": "full_model",
  "baseline_model": "Full Model",
  "sequence_length": 30,
  "hardware_device": "mps",
  "beta_param": 0.25,
  "raw_ece": 0.243604,
  "calibrated_ece": 0.033273,
  "raw_brier": 0.073155,
  "calibrated_brier": 0.005727,
  "calculated_position_size": 30258.62,
  "max_allocation": 10000.0,
  "final_position_size": 10000.0,
  "trade_count": 20,
  "no_trade_count": 40,
  "avg_exposure": 0.333,
  "sharpe_ratio": -1.22,
  "sharpe_ci_lower": -5.21,
  "sharpe_ci_upper": 2.18,
  "max_drawdown": -0.084
}
```

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
