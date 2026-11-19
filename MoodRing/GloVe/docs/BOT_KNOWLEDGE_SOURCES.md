# Methodology knowledge preparation

The no-LLM bot now includes thirteen curated methodology passages. Five additions
cover macro vintages, probability calibration, financial sentiment, price adjustment
and chronological validation. They are source-based paraphrases, not training-set
solutions. No held-out question answers were read or used for this increment.

The additions are reachable through the existing template adapter, grades and
content-addressed evidence IDs. Grade B identifies methodology; it does not prove a
model result. Grade A still requires the authorized tool. Real archives remain
outside the synthetic `Snapshot` contract, and no live endpoint is exposed to a
question or a future model renderer.

Primary references reviewed on 2026-11-03:

| Topic | Source | Use and limitation |
| --- | --- | --- |
| Macro vintages | [ALFRED](https://fred.stlouisfed.org/docs/api/fred/alfred.html) | Separate observation period, release and revision; date-only evidence is not intraday timing. |
| Probability calibration | [scikit-learn](https://scikit-learn.org/stable/modules/calibration.html) | Reliability plots and probabilistic scoring; lower Brier loss alone does not prove better calibration. |
| Financial sentiment | [Araci, FinBERT](https://arxiv.org/abs/1908.10063) | Financial text classification; a sentiment label is not a stock-return probability. MoodRing evaluation requirements are project policy, not results from this paper. |
| Price basis | [yfinance](https://ranaroussi.github.io/yfinance/advanced/price_adjustment.html) | Separate split-adjusted OHLC and dividend-adjusted close; SDK values are processed evidence. |
| Time-ordered validation | [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) | Chronological split and gap mechanism; overlapping-label purging and held-out corpus exclusion are additional project requirements. |
| MACD | [Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd) | 12/26 EMA difference and 9-period signal; implementation warm-up is documented separately. |
| ATR | [Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr) | True range and smoothing; normalization by close is this project's convention. |
| OBV | [Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/obv) | Close-direction signed volume; not direct buyer/seller order flow. |
| GARCH | [arch](https://arch.readthedocs.io/en/latest/univariate/generated/arch.univariate.GARCH.html) | Conditional variance specification; no fit or convergence evidence is supplied by this bot. |
| EVT distribution | [SciPy generalized Pareto](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.genpareto.html) | Distribution interface; not proof of an appropriate threshold or adequate tail sample. |

The golden development regression now has 37 cases, including source-methodology
questions and a macro-key injection attempt. These are agent-authored tests with
`human_approved=false`, not independent blind evaluation. No weights were trained,
no model was downloaded and no template-versus-model winner is claimed. The same
adapter comparison protocol still applies when a model is added.

CodeRabbit's evaluator exception finding was reproduced and fixed. A normal case
can no longer pass by raising the fallback error string; explicit expected errors
retain their oracle. Function docstrings cover the previously undocumented test
helpers, and HTML values use the same precision as answer statements. External
acquisition remains a separate trust boundary from the read-only bot.

## Research and investor-education supplement

[research_lessons.json](../src/explanation/research_lessons.json) adds twelve
source-linked paraphrases: HAR persistence, proxy-sensitive loss evaluation,
signed variation, ML volatility forecasting, pooled commonality, MASTER,
exogenous variables, survivorship, retrieval, evidence selection, order risk
and diversification/costs. Each identifies its source and applicability limits.
These are AI-curated explanations pending human review, not reproduced paper
results, human-approved answers, held-out solutions or fine-tuning examples.

The no-LLM retriever can serve these passages now. A future Gemini adapter can
consume the same authorized evidence, while numeric tools remain authoritative.
Adding a corpus does not train model weights. No model transport or paid fallback
is introduced here. Compare retrieval and answer quality before choosing a model.
