> Implementation status (10 October 2026): the persistent stock paper-trading API,
> independent scenarios, recorded orders/fills, quote polling and chronological
> replay are now implemented. See [the implementation guide](../docs/PAPER_TRADING.md)
> and [change notes](../docs/IMPLEMENTATION_CHANGES.md). The historical metrics below
> remain research notes; they are not results from this paper-trading release.

# System Architecture & Technical Specifications: Multimodal Feature Store, Multi-Head Risk Modeling & Paper Trading Engine

## Executive Summary & System Objectives

This repository implements a risk-aware **Medallion Data Architecture (Bronze -> Silver -> Gold)**, a **Hierarchical Multimodal Feature Store**, a **5-Head Temporal Transformer Neural Network** with a **Post-Hoc Explainability Layer**, a **Soft Probabilistic Regime Classifier**, a **Strict 3-Split Calibration Layer**, a **4-Tier Drift & Quality Monitoring Engine**, an **Explainable Decision Engine**, and a dedicated **Experiment Tracking Module**.

---

## 1. End-to-End System Architecture

```
                 MULTIMODAL FEATURES
                        │
                        ▼
              TEMPORAL TRANSFORMER
                        │
       ┌────────────────┼────────────────┐
       ▼                ▼                ▼
    P(UP)              μ, σ²           P(TP)
       │                │                │
       ▼                ▼                ▼
 Probability       Uncertainty       Trade-success
 Calibration       Calibration       Calibration
       │                │                │
       └────────────────┼────────────────┘
                        ▼
                  REGIME ENGINE
                        │
                        ▼
               EXPECTED NET EDGE (μ - cost_drag)
                        │
                        ▼
                DECISION WATERFALL
                        │
                 ┌──────┴──────┐
                 ▼             ▼
                BUY           PASS
                 │
                 ▼
            POSITION SIZE (BaseAlloc * P(TP) * (μ_eff/σ²_bounded) * w_regime)
                 │
                 ▼
        COST / SLIPPAGE MODEL
                 │
                 ▼
           PAPER PORTFOLIO
```

---

## 2. Stage A: Research Mode (Pure Predictive Signal Evaluation)

Evaluating predictive signal quality independently of position-sizing or risk-policy gates:

| Model Group | Direction AUC | Directional Accuracy | Out-of-Sample RMSE | Gaussian NLL | Student-$t$ NLL ($df=4$) | 95% Interval Coverage |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Buy & Hold Baseline** | `0.5000` | `50.0%` | `0.015000` | `1.5000` | `0.8000` | `95.0%` |
| **Technical Only** | `0.5410` | `55.0%` | `0.057795` | `-1.4292` | `-1.0887` | `66.7%` |
| **FinBERT Only** | `0.4949` | `55.0%` | `0.052739` | `-2.0033` | **`-1.3849`** | `71.7%` |
| **Tech + FinBERT** | `0.4029` | `50.0%` | `0.090469` | `1.8830` | **`-0.1383`** | `41.7%` |
| **Full (No Regime)** | **`0.5701`** | `53.3%` | `0.200019` | `24.7780` | **`2.7964`** | `11.7%` |
| **Full Model** | `0.3771` | `40.0%` | `0.115404` | `5.2904` | **`0.3876`** | `40.0%` |

---

## 3. Experiment 1: Gate Threshold Sensitivity & Cost-Adjusted Net Edge

Grid search across Direction Thresholds ($\tau_{\text{direction}} \in [0.50, 0.60]$) and Trade-Success Thresholds ($\tau_{\text{success}} \in [0.50, 0.60]$):

| Direction Threshold ($\tau_{\text{dir}}$) | Trade-Success Threshold ($\tau_{\text{TP}}$) | Active BUY Trades | Avg Exposure % | Sharpe Ratio [95% CI] | Max Drawdown |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`0.50`** | **`0.50`** | **13 / 60** | **21.7%** | `-1.06` [-4.94 to 2.25] | `-9.3%` |
| **`0.52`** | **`0.50`** | **5 / 60** | **8.3%** | `-1.52` [-5.24 to -2.07] | **`-4.6%`** |
| `0.52` | `0.55` | 2 / 60 | 3.3% | `-2.93` [-4.79 to 0.00] | `-1.5%` |
| `0.55` | `0.50` | 2 / 60 | 3.3% | `-2.83` [-4.69 to 0.00] | `-1.0%` |
| `0.60+` | `0.60+` | 0 / 60 | 0.0% | `0.00` [0.00 to 0.00] | `0.0%` |

---

## 4. Experiment 2: Regime Contribution Isolation

Holding identical model predictions constant across 5 regime treatments:

| Regime Setup | Multiplier Treatment ($w_{\text{regime}}$) | Trades | Avg Exposure | Sharpe Ratio [95% CI] | Max Drawdown |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Setup A: No Regime Engine** | $w = 1.0$ | 5 / 60 | 8.3% | `-1.52` [-5.27 to -2.07] | `-4.6%` |
| **Setup B: Fixed Multiplier** | $w = 1.0$ | 5 / 60 | 8.3% | `-1.52` [-5.30 to -2.07] | `-4.6%` |
| **Setup C: Soft Regime Multiplier** | $w \in [0.2, 1.0]$ | 5 / 60 | 8.3% | `-1.52` [-5.29 to -2.07] | `-4.6%` |
| **Setup D: Hard Regime Step** | Step ($1.0 / 0.5 / 0.2$) | 5 / 60 | 8.3% | `-1.52` [-5.28 to -2.07] | `-4.6%` |
| **Setup E: Inverted Regime** | $1.0 / w$ | 5 / 60 | 8.3% | `-1.52` [-5.23 to -2.07] | `-4.6%` |

---

## 5. Experiment 3: Head Contribution Decomposition

Decomposing the model's 5 heads:

| Head Configuration | Component Included | Active Trades | Avg Exposure | Sharpe Ratio [95% CI] | Max Drawdown |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Direction $P(\text{Up})$ Only** | Head 1 | 7 / 60 | 11.7% | `-2.45` [-6.27 to -2.92] | `-6.4%` |
| **2. Expected $\mu$ Only** | Head 2 | 7 / 60 | 11.7% | `-2.45` [-6.11 to -2.86] | `-6.4%` |
| **3. $P(\text{TP before SL})$ Only** | Head 4 | 5 / 60 | 8.3% | `-1.52` [-5.17 to -2.07] | **`-4.6%`** |
| **4. Expected $\mu$ + $P(\text{TP})$** | Head 2 + Head 4 | 5 / 60 | 8.3% | `-1.52` [-5.20 to -2.07] | **`-4.6%`** |
| **5. Full Model** | All Heads + Regime | 5 / 60 | 8.3% | `-1.52` [-5.21 to -2.07] | **`-4.6%`** |

*Core Research Takeaway*: Head 4 ($P(\text{TP before SL})$) provides the primary risk-filtering mechanism, reducing Max Drawdown from `-6.4%` down to `-4.6%` by filtering out low-probability trade setups.

---

## 6. Sample Decision Waterfall & Net Edge Log

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
