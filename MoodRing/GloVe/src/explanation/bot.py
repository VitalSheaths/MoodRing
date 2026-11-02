"""Deterministic answers with a replaceable, evidence-constrained renderer."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Protocol

from .contracts import Answer, Passage, Query, Record, Statement, ToolResult
from .knowledge import PASSAGES, retrieve
from .tools import EvidenceTools


class AnswerContext(Record):
    """Only the question and already-authorized evidence reach a renderer."""

    question: str
    result: ToolResult
    passages: tuple[Passage, ...]
    statements: tuple[Statement, ...]


class AnswerAdapter(Protocol):
    """Trusted in-process adapter interface; external transports are not installed.

    Models may add explicitly unverified C statements. Authoritative A/B text,
    grades and citations must be retained exactly. Adapter code itself is trusted,
    not sandboxed; it never receives a tool handle or archive access from the bot.
    """

    version: str

    def render(self, context: AnswerContext) -> tuple[Statement, ...]:
        """Return supported statements plus optional unverified commentary."""
        ...


class TemplateAdapter:
    """Default no-LLM renderer; no provider, downloads or inference costs."""

    version = 'template-v1'

    def render(self, context: AnswerContext) -> tuple[Statement, ...]:
        """Keep all grounded facts and explicit missingness unchanged."""
        return context.statements


def _intent(question: str) -> str:
    """Route a small documented question vocabulary, never executable content."""
    words = set(re.findall(r'[a-z]+', question.lower()))
    if words.intersection({'sql', 'shell', 'execute', 'ignore', 'secret', 'file', 'another', 'trade', 'buy', 'sell'}):
        return 'unsupported'
    if words.intersection({'cards', 'indicators', 'overview'}):
        return 'cards'
    if words.intersection({'model', 'prediction', 'predictions', 'compare', 'sentiment', 'forecast'}):
        return 'model'
    if words.intersection({'volume', 'price', 'move', 'obv'}):
        return 'volume'
    if words.intersection({'rsi', 'macd', 'momentum', 'trend'}):
        return 'trend'
    if words.intersection({'volatility', 'atr', 'bollinger', 'bands', 'risk', 'garch', 'evt', 'portfolio', 'tail'}):
        return 'risk'
    if words.intersection({'missing', 'coverage', 'cutoff', 'revision', 'history', 'help'}):
        return 'coverage'
    return 'unsupported'


class ExplanationBot:
    """Keep tools, cache, grades and evidence IDs independent of the model."""

    def __init__(self, tools: EvidenceTools, adapter: AnswerAdapter | None = None):
        """Inject an optional renderer without adding retrieval capabilities."""
        self.tools = tools
        self.adapter = adapter or TemplateAdapter()

    def answer(self, owner_id: str, query: Query, question: str,
               computed_at: datetime | None = None) -> Answer:
        """Compute once, then render with an exact evidence validation boundary."""
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise ValueError('question_length')
        now = computed_at or datetime.now(timezone.utc)
        result = self.tools.cards(owner_id, query, now)  # auth before retrieval/adapter
        intent = _intent(question)
        # A selected context is explicit. Do not silently answer for EXAMPLE when
        # the user asks about another symbol or historical date.
        dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', question)
        symbols = set(re.findall(r'\b[A-Z]{2,8}\b', question))
        if (any(d != query.session.isoformat() for d in dates)
                or symbols - {'EXAMPLE', 'RSI', 'MACD', 'ATR', 'OBV', 'EVT', 'GARCH', 'OHLCV', 'SQL'}):
            intent = 'unsupported'
        passages = retrieve(question)
        coverage = next(p for p in PASSAGES if p.key == 'coverage')
        policy = next(p for p in PASSAGES if p.key == 'policy')
        passages = tuple(dict.fromkeys((*passages, coverage, *((policy,) if intent == 'unsupported' else ()))))
        status = 'unsupported_question' if intent == 'unsupported' else 'answered'
        selected = {
            'cards': {'trend', 'volume', 'volatility', 'tail', 'portfolio', 'model'},
            'volume': {'volume', 'trend', 'volatility'},
            'trend': {'trend'}, 'risk': {'volatility', 'tail', 'portfolio'},
            'model': {'model'}, 'coverage': {'trend', 'volume', 'volatility'},
            'unsupported': set(),
        }[intent]
        statements = []
        for card in result.cards:
            if card.bundle in selected:
                value = f'{card.value:.10g} {card.unit}' if card.status == 'ok' else f'{card.status} ({card.reason})'
                statements.append(Statement(text=f'{card.key}: {value}.', grade='A', evidence_ids=(result.evidence_id,)))
        statements.extend(Statement(text=p.text, grade='B', evidence_ids=(p.evidence_id,)) for p in passages)
        if intent != 'unsupported' and not any(c.status == 'ok' for c in result.cards):
            status = 'unavailable'
        values = {c.key: c.value for c in result.cards if c.status == 'ok'}
        if (intent == 'volume' and 'why' in question.lower()
                and values.get('relative_volume_20', 0) >= 2
                and abs(values.get('return_1', 1)) < .01):
            statements.append(Statement(grade='C', text=(
                'Hypothesis, unverified: offsetting order flow could be consistent with high '
                'volume and a small net move. This is not an inference from these bars; '
                'signed trades and order-book evidence would be needed to test it.')))
        context = AnswerContext(question=question, result=result, passages=passages, statements=tuple(statements))
        try:
            rendered = self.adapter.render(context)
            if not isinstance(rendered, tuple) or len(rendered) > 64:
                raise ValueError('adapter_output_limit')
            # Exact A/B preservation prevents model-generated numbers or citation laundering.
            grounded = tuple(s for s in context.statements if s.grade != 'C')
            if tuple(s for s in rendered if s.grade != 'C') != grounded:
                raise ValueError('adapter_changed_grounded_evidence')
            if any(not isinstance(s, Statement) for s in rendered):
                raise ValueError('adapter_schema')
            if any(s.grade == 'C' and not s.text.startswith(('Hypothesis, unverified:', 'Unverified:')) for s in rendered):
                raise ValueError('adapter_unlabelled_commentary')
        except Exception:
            rendered, status = context.statements, 'adapter_rejected'
        return Answer(status=status, adapter_version=self.adapter.version, computed_at=now,
                      result=result, passages=passages, statements=rendered)
