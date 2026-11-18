"""Small curated corpus: methodology is context, never price evidence."""
import re

from .contracts import Passage

PASSAGES = (
    Passage(key='volume', keywords=('volume', 'price', 'move', 'obv', 'flow'), text=(
        'Total volume is unsigned. High volume can coexist with a small close-to-close '
        'return. Daily OHLCV alone cannot establish order imbalance, intent or causation.')),
    Passage(key='rsi', keywords=('rsi', 'momentum', 'overbought', 'oversold'), text=(
        'RSI uses fourteen close changes, an arithmetic seed and Wilder smoothing. '
        'It describes a state, not a forecast. Warm-up and an entirely flat series remain missing.')),
    Passage(key='macd', keywords=('macd', 'trend', 'signal'), text=(
        'MACD reuses the technical helper: recursive EMAs with spans 12 and 26, '
        'and a 9-session signal. The signal needs 34 closes; these lagging values are not forecasts.')),
    Passage(key='volatility', keywords=('volatility', 'atr', 'bollinger', 'bands', 'risk'), text=(
        'ATR uses fourteen true ranges and Wilder smoothing, divided by the current close. '
        'Bollinger bands use twenty closes and two population standard deviations; '
        'they are not probability limits. Realized volatility is the sample standard '
        'deviation of twenty daily log returns, without annualization.')),
    Passage(key='coverage', keywords=('missing', 'coverage', 'cutoff', 'history', 'revision'), text=(
        'Calculations require the supplied calendar sessions through the selected session. '
        'Unknown availability is ineligible, gaps are not compressed, and only revisions '
        'available at the selected cutoff are used. This demonstration is synthetic historical '
        'reconstruction computed now, not a recorded past prediction or operational replay.')),
    Passage(key='models', keywords=('prediction', 'model', 'compare', 'sentiment', 'drawdown', 'forecast'), text=(
        'No validated model run is attached to this demonstration. Direction, expected return, '
        'variance, trade success and drawdown remain not_evaluated. Model attribution explains '
        'model behavior, not market causation. No calibrated loss probability or risk score is supplied.')),
    Passage(key='advanced', keywords=('garch', 'evt', 'portfolio', 'profile', 'tail'), text=(
        'Volume profile requires intraday observations. GARCH requires a fitted model with '
        'convergence evidence; EVT needs a training-only threshold and enough tail samples. '
        'Portfolio risk needs explicit weights and aligned histories. These are not evaluated here.')),
    Passage(key='policy', keywords=('help', 'question'), text=(
        'This offline assistant explains indicator cards and methodology for the explicitly '
        'selected instrument, session and cutoff. Other questions receive an explicit unsupported '
        'response. It cannot execute commands, retrieve private files, place trades or establish a market cause.')),
    Passage(key='macro_vintages', keywords=('macro', 'macroeconomics', 'cpi', 'alfred', 'fred', 'vintage'), text=(
        'Macro observations have a period date and a release/revision history. ALFRED retains '
        'real-time vintage dates. A revised value downloaded today must not be treated as '
        'known at an earlier prediction cutoff. A vintage date alone does not establish '
        'an intraday release timestamp. This bot has no attached validated macro model run. '
        'Reference: https://fred.stlouisfed.org/docs/api/fred/alfred.html')),
    Passage(key='calibration', keywords=('confidence', 'calibration', 'calibrated', 'brier', 'probability'), text=(
        'A calibrated predicted probability should agree with observed event frequency across '
        'comparable predictions. A larger probability after adding sentiment is not evidence '
        'of better forecasting. Compare matched out-of-time outcomes, reliability plots, '
        'Brier score and log loss; the scores combine calibration with other aspects of quality. '
        'No measured confidence improvement is attached here. '
        'Reference: https://scikit-learn.org/stable/modules/calibration.html')),
    Passage(key='sentiment_method', keywords=('finbert', 'sentiment', 'headline', 'headlines', 'tone'), text=(
        'FinBERT is a financial-language sentiment model. A text sentiment label describes '
        'the language, not a calibrated probability of a stock rising. MoodRing must first '
        'validate company relevance, publication timing, duplicate articles and the chosen '
        'model version. Missing news is missing coverage, not neutral sentiment. Adding '
        'sentiment may improve or worsen an otherwise identical forecasting experiment. '
        'Reference: https://arxiv.org/abs/1908.10063')),
    Passage(key='price_basis', keywords=('adjustment', 'adjusted', 'dividend', 'split', 'yfinance', 'ohlc'), text=(
        'With yfinance auto_adjust=False, OHLC is Yahoo\'s split-adjusted view and Adj Close '
        'also reflects dividends. These fields have different meanings; they are not '
        'original as-traded prices with proven historical adjustment vintages. Record the '
        'chosen return basis and preserve the separate fields. '
        'Reference: https://ranaroussi.github.io/yfinance/advanced/price_adjustment.html')),
    Passage(key='chronological_validation', keywords=('validation', 'heldout', 'holdout', 'leakage', 'chronological'), text=(
        'Time-ordered validation trains on earlier observations and evaluates later ones. '
        'Fit preprocessing and select parameters on development data only. Keep calibration '
        'separate from final testing, and remove training labels whose outcome windows '
        'overlap validation. A fixed gap is only sufficient when it covers the label horizon. '
        'MoodRing also prohibits deriving knowledge cards from held-out solutions. '
        'Reference: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html')),
)


def retrieve(question: str, limit: int = 3) -> tuple[Passage, ...]:
    """Deterministic bounded keyword lookup; no external or untrusted instructions."""
    if not 1 <= limit <= 3:
        raise ValueError('retrieval_limit')
    words = set(re.findall(r'[a-z]+', question.lower()))
    ranked = sorted(enumerate(PASSAGES), key=lambda item: (
        -len(words.intersection(item[1].keywords)), item[0]))
    return tuple(p for _, p in ranked if words.intersection(p.keywords))[:limit]
