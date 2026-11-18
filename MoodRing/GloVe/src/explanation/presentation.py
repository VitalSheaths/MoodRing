"""Local HTML/text presentation with escaped content and visible evidence grades."""
from html import escape

from .contracts import Answer, Card
from .dictionary import ENTRIES


def text_answer(answer: Answer) -> str:
    """Render an inspectable plain-text answer without hiding missing evidence."""
    q = answer.result.query
    lines = [f'{answer.result.data_kind.upper()} | {q.instrument_id} | {q.session}',
             f'Cutoff: {q.cutoff.isoformat()} | Computed: {answer.computed_at.isoformat()}',
             f'Status: {answer.status} | Adapter: {answer.adapter_version}']
    lines.extend(f'[{s.grade}] {s.text} ' + ' '.join(f'[{e}]' for e in s.evidence_ids) for s in answer.statements)
    return '\n'.join(lines)


# Presentation metadata only: formulas describe the versioned tool calculation.
# Labels and rounding must never change the underlying cards or evidence IDs.
# key: (group, label, meaning, calculation, advanced)
CARD_COPY = {
    'return_1': ('Price', 'Daily price change', 'How much the closing price changed since the previous session.',
                 '(Latest close ÷ previous close − 1) × 100%.', False),
    'range_pct': ('Price', 'Daily trading range', 'The distance between the day’s high and low, relative to its closing price.',
                  '(Latest high − latest low) ÷ latest close × 100%.', False),
    'relative_volume_20': ('Volume', 'Trading volume vs usual', '1× is the recent average. A larger value means more shares traded; it does not identify buyers or sellers.',
                           'Latest volume ÷ average volume of the preceding 20 sessions, excluding today.', False),
    'obv_change_20': ('Volume', 'Volume trend · OBV change', 'Volume signed by the direction of daily closing-price moves. This is not measured buying or selling pressure.',
                       'Over 20 changes: add volume on up days, subtract it on down days, and add zero on unchanged days.', False),
    'volume_profile': ('Volume', 'Volume by price level', 'Shows where trading took place across price levels. Daily totals cannot provide this detail.',
                        'Requires intraday trades or suitable volume-at-price data; not calculated from these daily bars.', True),
    'rsi_14': ('Trend', 'Price momentum · RSI', 'A 0–100 measure of recent gains relative to losses. A high reading alone does not predict a reversal.',
                '100 − 100 ÷ (1 + average gain ÷ average loss), with a 14-change arithmetic seed and Wilder smoothing. A flat series is undefined.', False),
    'macd': ('Trend', 'Trend balance · MACD', 'Positive values mean the faster price average is above the slower one. This describes momentum, not a probability.',
              '12-session exponential moving average minus the 26-session average, using recursive averages seeded by the first close.', False),
    'macd_signal': ('Trend', 'MACD signal line', 'A smoother reference for MACD; crossing it is not a validated trade recommendation.',
                     '9-session recursive exponential average of MACD, following the tool’s warm-up convention; requires 34 closes.', True),
    'macd_hist': ('Trend', 'MACD gap', 'The distance between MACD and its signal line.',
                   'MACD minus its 9-session signal line; requires 34 closes.', True),
    'bollinger_pct_b': ('Trend', 'Position within price bands', '0% is the lower band and 100% the upper band. Values can fall outside this range; this is not a probability.',
                         '(Close − lower band) ÷ (upper band − lower band) × 100%. Bands use the 20-close mean ± twice the population standard deviation.', True),
    'bollinger_bandwidth': ('Risk', 'Price-band width', 'Wider bands describe more dispersion in recent closing prices.',
                             'Four times the population standard deviation of 20 closes ÷ their mean × 100%.', True),
    'atr_14_pct': ('Risk', 'Typical trading range · ATR', 'A smoothed range including overnight gaps, expressed as a share of the latest close. It is not a loss forecast.',
                    'True range is the largest of high − low, |high − previous close| and |low − previous close|. Smooth with a 14-range arithmetic seed and Wilder updates, then divide by latest close and multiply by 100%.', False),
    'realized_vol_20': ('Risk', 'Daily price variability', 'How widely daily log returns varied over the latest 20 changes. This is historical daily volatility, not annualized or predicted volatility.',
                        'Sample standard deviation (19 in the denominator) of 20 log(close ÷ previous close) returns, × 100%.', False),
    'garch': ('Risk', 'Volatility forecast · GARCH', 'A fitted model could estimate changing volatility. No fitted result is supplied here.',
               'Requires a specified model, training window, successful convergence and validated forecasts.', True),
    'evt': ('Risk', 'Extreme-loss estimate · EVT', 'A tail model could describe unusually large losses. No fitted tail estimate is supplied here.',
             'Requires a training-only threshold, sufficient tail observations and validated tail estimates.', True),
    'portfolio_risk': ('Risk', 'Portfolio risk', 'The combined risk of several holdings depends on their weights and how returns move together.',
                        'Requires portfolio weights and aligned, eligible histories; a single-stock card cannot supply it.', True),
    'direction': ('Model results', 'Chance of a price rise', 'Proposed probability of a positive return over an accepted horizon.',
                    'Requires accepted labels and a validated, calibrated model run. No probability is calculated here.', False),
    'expected_return': ('Model results', 'Expected price change', 'Proposed average future return over an accepted horizon.',
                          'Requires an accepted return basis and horizon, plus a validated model run.', False),
    'variance': ('Model results', 'Forecast uncertainty', 'Proposed conditional return variance, in squared-return units; not a confidence percentage.',
                   'Requires a validated variance model and checks of forecast interval coverage.', False),
    'trade_success': ('Model results', 'Chance of trade success', 'Proposed probability under a defined entry, target, stop and cost policy.',
                        'Requires accepted trade labels and a validated model. Daily bars cannot tell which level was hit first when both target and stop are touched.', False),
    'drawdown': ('Model results', 'Potential adverse price move', 'The proposed head measures a future adverse move from entry. Its definition still needs human acceptance.',
                   'Requires an accepted entry, horizon and adverse-move definition, plus a validated model run.', False),
}

REASONS = {
    'validated_run_required': 'Not available yet — needs a validated model.',
    'intraday_data_required': 'Not available — needs intraday trading data.',
    'fit_and_convergence_evidence_required': 'Not available yet — needs a fitted and checked volatility model.',
    'training_threshold_and_tail_sample_required': 'Not available yet — needs a fitted tail model and enough extreme observations.',
    'weights_and_aligned_histories_required': 'Not available — needs portfolio weights and aligned price histories.',
    'insufficient_history': 'Not available — needs more price history.',
    'missing_volume': 'Not available — some trading-volume data are missing.',
    'zero_volume_baseline': 'Not available — average comparison volume is zero.',
    'flat_series_undefined': 'Not available — RSI is undefined when all price changes are zero.',
    'zero_band_width': 'Not available — the price bands have zero width.',
    'missing_or_ineligible_session': 'Not available — a session is missing or its data were not eligible at the selected cutoff.',
    'instrument_not_in_snapshot': 'Not available — the selected stock is not in this dataset.',
    'session_not_in_calendar': 'Not available — the selected date is not in this dataset’s session calendar.',
    'session_after_cutoff': 'Not available — the selected session had not closed at the cutoff.',
    'nonfinite_derived_value': 'Not available — the calculation did not produce a finite number.',
}


def _copy(card: Card) -> tuple[str, str, str, str, bool]:
    """Keep unfamiliar future cards visible without inventing their methodology."""
    if card.key in ENTRIES:
        e = ENTRIES[card.key]
        return e['group'], e['label'], e['meaning'], e['formula'], e['advanced']
    return CARD_COPY.get(card.key, ('Other indicators', card.key.replace('_', ' '),
                                  'No explanation is available for this indicator yet.',
                                  'Consult the source record; no display formula is registered.', True))


def _number(value: float, places: int = 2) -> str:
    """Round display values without turning a nonzero measurement into zero."""
    if 0 < abs(value) < 10 ** -places:
        return ('−' if value < 0 else '') + f'<{10 ** -places:.{places}f}'
    return f'{value:,.{places}f}'.rstrip('0').rstrip('.') if places else f'{value:,.0f}'


def _value(card: Card) -> str:
    """Convert units for readers while retaining the exact original evidence."""
    if card.status != 'ok' or card.value is None:
        return REASONS.get(card.reason, 'Not available — see source details for the recorded reason.')
    if card.unit in ('fraction', 'daily_log_return_std'):
        return _number(card.value * 100) + '%'
    if card.unit == 'multiple':
        return _number(card.value) + '×'
    if card.unit == 'shares':
        return _number(card.value) + ' shares'
    if card.unit == 'index':
        return _number(card.value) + ' / 100'
    if card.unit == 'price':
        return _number(card.value, 4) + ' price units'
    return _number(card.value) + ' ' + card.unit


def _summary(cards: dict[str, Card]) -> str:
    """Describe only available measurements; daily bars do not explain causes."""
    change, volume = cards.get('return_1'), cards.get('relative_volume_20')
    if change is None or change.value is None or change.status != 'ok':
        price_text = 'The daily closing-price change is not available for this selection.'
    elif change.value == 0:
        price_text = 'The closing price was unchanged from the previous session.'
    else:
        price_text = f'The closing price {"rose" if change.value > 0 else "fell"} {_number(abs(change.value) * 100)}% from the previous session.'
    volume_text = (f'Trading volume was {_value(volume)} its average over the preceding 20 sessions.'
                   if volume and volume.status == 'ok' else 'The volume comparison is not available.')
    return price_text + ' ' + volume_text + ' These daily observations cannot tell us why or predict what happens next.'


def _card_html(card: Card) -> str:
    """Render escaped copy and a keyboard-accessible calculation disclosure."""
    _, label, meaning, calculation, _ = _copy(card)
    value_class = 'value' if card.status == 'ok' else 'missing'
    return (f'<article><h3>{escape(label)}</h3><p class="{value_class}">{escape(_value(card))}</p>'
            f'<p><b>What this means</b><br>{escape(meaning)}</p>'
            f'<details><summary>How it is calculated</summary><p>{escape(calculation)}</p></details></article>')


def html_answer(answer: Answer) -> str:
    """Render a readable synthetic lesson, leaving contracts and evidence intact."""
    q = answer.result.query
    retrospective = answer.result.data_kind == 'retrospective_research'
    banner = ('<b>Retrospective research snapshot.</b> Real retained vendor-adjusted data. '
              'Historical data versions are unverified; this is not a live forecast.' if retrospective else
              '<b>Synthetic historical reconstruction.</b> These are fictional data, not real stock prices, forecasts or investment performance.')
    coverage_text = (f'One approved feature row; {len(answer.result.context_sessions)} matched context sessions. Indicator warm-up uses earlier retained history.'
                     if retrospective else f'{len(answer.result.source_revision_ids)} eligible daily observations used.')
    cards = {c.key: c for c in answer.result.cards}
    highlights = ''.join(f'<article><h3>{escape(_copy(cards[key])[1])}</h3>'
                         f'<p class="{"value" if cards[key].status == "ok" else "missing"}">{escape(_value(cards[key]))}</p></article>'
                         for key in ('return_1', 'relative_volume_20', 'range_pct') if key in cards)
    groups = ['Price', 'Volume', 'Trend', 'Risk', 'Model results']
    if any(_copy(c)[0] not in groups for c in cards.values()):
        groups.append('Other indicators')
    sections = []
    for group in groups:
        selected = [c for c in cards.values() if _copy(c)[0] == group]
        primary = ''.join(_card_html(c) for c in selected if not _copy(c)[4])
        advanced = ''.join(_card_html(c) for c in selected if _copy(c)[4])
        introduction = ('<p>Five-session realized volatility is the approved research objective. Other model heads remain proposed. No model predictions are supplied on this page.</p>'
                        if group == 'Model results' else '')
        sections.append(f'<section><h2>{escape(group)}</h2>{introduction}<div class="grid">{primary}</div>'
                        + (f'<details class="advanced"><summary>Show details · {escape(group)} ({sum(_copy(c)[4] for c in selected)})</summary><div class="grid">{advanced}</div></details>' if advanced else '') + '</section>')
    grades = {'A': 'Calculated fact', 'B': 'Methodology', 'C': 'Unverified hypothesis'}
    facts = {f'{c.key}: {c.value:.10g} {c.unit}.' if c.status == 'ok'
             else f'{c.key}: {c.status} ({c.reason}).': c for c in cards.values()}
    statements = []
    for statement in answer.statements:
        card = facts.get(statement.text) if statement.grade == 'A' else None
        display = _copy(card)[1] + ': ' + _value(card) if card else statement.text
        statements.append(f'<li><span class="badge">{grades[statement.grade]}</span><p>{escape(display)}</p>'
                          '<details><summary>Source details</summary><p>Original statement: '
                          + escape(statement.text) + '</p><p>Evidence IDs: '
                          + escape(' · '.join(statement.evidence_ids) or 'None — this is not verified evidence.') + '</p></details></li>')
    missing = sum(c.status != 'ok' for c in cards.values() if c.bundle != 'model')
    model_missing = sum(c.status != 'ok' for c in cards.values() if c.bundle == 'model')
    status = {'answered': '', 'unavailable': 'The selected data cannot support indicator calculations. See the missing-data messages below.',
              'unsupported_question': 'This question is not supported yet. The cards below describe only the selected data.',
              'adapter_rejected': 'The generated explanation could not be verified. The checked template answer is shown instead.'}[answer.status]
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>MoodRing · Understand the day</title><style>
*{box-sizing:border-box}body{font:16px/1.6 system-ui,sans-serif;background:#f4f6f3;color:#18392f;margin:0}
main{max-width:1120px;margin:auto;padding:36px 24px 64px}h1{font-size:clamp(30px,5vw,44px);line-height:1.2;margin:12px 0 20px}
h2{font-size:25px;margin:0 0 14px}h3{font-size:16px;margin:0 0 10px}p{margin:10px 0}section{margin-top:36px}
.eyebrow{font-size:12px;font-weight:700;letter-spacing:.13em}.intro{font-size:20px;max-width:850px}
.banner{background:#e4eee6;border-left:4px solid #477860;border-radius:4px;padding:12px 16px;margin:20px 0}
.context{display:flex;flex-wrap:wrap;gap:12px 36px;margin:24px 0}.context div{min-width:0}.context dt,small{font-size:13px;color:#52675c}.context dd{margin:0;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,270px),1fr));gap:16px}
article{background:white;border:1px solid #d5dfd6;border-radius:12px;padding:22px;min-width:0}
.highlights article{background:#193e32;color:#fff;border:0}.value{font-size:29px;font-weight:650;line-height:1.3;margin:14px 0 20px}.highlights .value{font-size:36px;margin-bottom:0}
.missing{font-size:17px;font-weight:600;color:#70502a}.highlights .missing{color:#fff}
article p{font-size:14px}article b{font-size:13px}summary{cursor:pointer;font-size:14px;font-weight:600;padding:9px 0;text-underline-offset:4px}summary:hover{text-decoration:underline}
summary:focus-visible,a:focus-visible{outline:3px solid #9a671a;outline-offset:4px}details p{font-size:14px}.advanced{margin-top:16px}.advanced>.grid{margin-top:10px}
.badge{display:inline-block;font-size:12px;font-weight:700;background:#e4eee6;padding:3px 9px;border-radius:20px}
li{margin:20px 0;padding-bottom:18px;border-bottom:1px solid #d5dfd6}ul{padding-left:22px}pre{white-space:pre-wrap;font-size:12px;background:#fff;padding:16px;border:1px solid #d5dfd6}
main,article,dd,li,pre{overflow-wrap:anywhere}footer{margin-top:32px;border-top:1px solid #d5dfd6;padding-top:16px;color:#52675c;font-size:13px}
@media(max-width:480px){main{padding:24px 16px 40px}.intro{font-size:18px}article{padding:18px}}
</style></head><body><main><div class="eyebrow">MOODRING / INDICATOR LESSON</div><h1>Understand the day</h1>
<p class="intro">'''+escape(_summary(cards))+'''</p><div class="grid highlights" aria-label="Three key measurements">'''+highlights+'''</div>
<p class="banner">'''+banner+'''</p>
<dl class="context"><div><dt>Selected stock</dt><dd>'''+escape('EXAMPLE · fictional company' if q.instrument_id == 'stock:example' else q.instrument_id)+'''</dd></div>
<div><dt>Session date</dt><dd>'''+escape(q.session.isoformat())+'''</dd></div><div><dt>Historical reference cutoff</dt><dd>'''+escape(q.cutoff.isoformat().replace('T', ' '))+'''</dd></div></dl>
<p>Data coverage: '''+escape(coverage_text)+''' '''+str(missing)+''' indicators unavailable; '''+str(model_missing)+''' model results unavailable. Availability is shown on each card.</p>
'''+('<p class="banner">'+escape(status)+'</p>' if status else '')+''.join(sections)+'''
<section><h2>Baseline and sentiment · separate research</h2><p>The research will compare <b>prices + macro + technicals</b> with the <b>same baseline + sentiment</b>, using the same eligible stocks, dates and targets.</p>
<p>This card view does not load forecasting results. Separate research comparisons do not authorize live predictions or calibrated probabilities here.</p></section>
<section><h2>Understand the evidence</h2><p><span class="badge">Calculated fact</span> Calculated or imported from the declared dataset; this grade does not mean predictive accuracy or historical availability is verified.
<span class="badge">Methodology</span> An explanation from the curated reference library.
<span class="badge">Unverified hypothesis</span> A possible explanation, not an established fact.</p>
<p>Card values are rounded for readability. Source details preserve the original values and identifiers.</p>
<details><summary>View statements and sources ('''+str(len(statements))+''')</summary><ul>'''+''.join(statements)+'''</ul></details>
<details><summary>Full evidence record</summary><p>This record includes the original tool result, source revision IDs, reference passages, statements and their citations.</p><pre>'''+escape(answer.model_dump_json(indent=2))+'''</pre></details></section>
<footer>Read-only research lesson. These indicators describe the supplied history; they do not establish causes or recommend trades.</footer></main></body></html>'''
