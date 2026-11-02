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
)


def retrieve(question: str, limit: int = 3) -> tuple[Passage, ...]:
    """Deterministic bounded keyword lookup; no external or untrusted instructions."""
    if not 1 <= limit <= 3:
        raise ValueError('retrieval_limit')
    words = set(re.findall(r'[a-z]+', question.lower()))
    ranked = sorted(enumerate(PASSAGES), key=lambda item: (
        -len(words.intersection(item[1].keywords)), item[0]))
    return tuple(p for _, p in ranked if words.intersection(p.keywords))[:limit]
