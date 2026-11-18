"""Reader-facing units, missingness and inspectable evidence in the static page."""
from html.parser import HTMLParser

from explanation.contracts import Answer
from explanation.evaluation import scenario
from explanation.presentation import html_answer
from test_explanation import answer, edit_row, replace_snapshot
from explanation.demo import demo_snapshot


class PageText(HTMLParser):
    """Collect initially visible text and the escaped full evidence record."""

    def __init__(self, page):
        """Parse locally without executing scripts or loading browser resources."""
        super().__init__()
        self.details = 0
        self.in_pre = False
        self.in_body = False
        self.visible = []
        self.record = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        """Track collapsed disclosures and the serialized evidence block."""
        if tag == 'details':
            self.details += 1
        if tag == 'pre':
            self.in_pre = True
        if tag == 'body':
            self.in_body = True

    def handle_endtag(self, tag):
        """Leave the current disclosure or evidence block."""
        if tag == 'details':
            self.details -= 1
        if tag == 'pre':
            self.in_pre = False
        if tag == 'body':
            self.in_body = False

    def handle_data(self, data):
        """Retain only text in the two views being checked."""
        if self.in_body and not self.details:
            self.visible.append(data)
        if self.in_pre:
            self.record.append(data)


def test_display_units_and_evidence_survive_round_trip():
    """Readable rounding must not alter tool results or hide their provenance."""
    original = answer()
    before = original.model_dump_json()
    page = PageText(html_answer(original))
    visible = ' '.join(page.visible)
    assert 'The closing price was unchanged' in visible
    assert '0%' in visible and '3×' in visible and '2.9%' in visible
    assert '0.18%' in visible  # daily, not annualized log-return volatility
    assert '40 eligible daily observations used' in visible
    assert '4 indicators unavailable; 5 model results unavailable' in visible
    assert original.result.evidence_id not in visible
    assert 'validated_run_required' not in visible
    assert 'Not available yet — needs a validated model.' in visible
    assert 'This card view does not load forecasting results.' in visible
    restored = Answer.model_validate_json(''.join(page.record))
    assert restored == original
    for statement in restored.statements:
        for evidence_id in statement.evidence_ids:
            assert restored.resolve_evidence(evidence_id) is not None
    assert original.model_dump_json() == before


def test_unavailable_data_does_not_read_as_a_zero_return():
    """Missing sessions must change the summary and never produce numeric highlights."""
    snapshot, query, _ = scenario('gap')
    visible = ' '.join(PageText(html_answer(answer(snapshot, query))).visible)
    assert 'daily closing-price change is not available' in visible
    assert 'The volume comparison is not available' in visible
    assert '0 eligible daily observations used' in visible
    assert '0%' not in visible and 'unchanged from the previous session' not in visible
    assert 'a session is missing' in visible


def test_tiny_measured_change_is_not_labelled_unchanged():
    """A rounded small return must remain distinguishable from an actual zero."""
    snapshot = demo_snapshot()
    rows = snapshot.observations[:-1] + (edit_row(snapshot.observations[-1], close='138.0001'),)
    visible = ' '.join(PageText(html_answer(answer(replace_snapshot(snapshot, rows)))).visible)
    assert 'rose <0.01%' in visible
    assert 'closing price was unchanged' not in visible
