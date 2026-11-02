"""Local HTML/text presentation with escaped content and visible evidence grades."""
from html import escape

from .contracts import Answer


def text_answer(answer: Answer) -> str:
    """Render an inspectable plain-text answer without hiding missing evidence."""
    q = answer.result.query
    lines = [f'SYNTHETIC historical reconstruction | {q.instrument_id} | {q.session}',
             f'Cutoff: {q.cutoff.isoformat()} | Computed: {answer.computed_at.isoformat()}',
             f'Status: {answer.status} | Adapter: {answer.adapter_version}']
    lines.extend(f'[{s.grade}] {s.text} ' + ' '.join(f'[{e}]' for e in s.evidence_ids) for s in answer.statements)
    return '\n'.join(lines)


def html_answer(answer: Answer) -> str:
    """Render standalone indicator cards; retrieved/model text is never HTML."""
    q = answer.result.query
    cards = ''.join(
        '<article><small>'+escape(c.bundle)+'</small><h2>'+escape(c.key.replace('_', ' '))+'</h2>'
        '<strong>'+(escape(f'{c.value:.6g}') if c.value is not None else escape(c.status))+'</strong>'
        '<p>'+escape(c.unit if c.status == 'ok' else c.reason or '')+'</p></article>'
        for c in answer.result.cards)
    statements = ''.join('<li><b>Grade '+s.grade+'</b> '+escape(s.text)+'<small>'+
                         escape(' · '.join(s.evidence_ids))+'</small></li>' for s in answer.statements)
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>MoodRing · Evidence cards</title><style>
body{font:16px/1.5 system-ui,sans-serif;background:#f3f5f2;color:#17392f;margin:0}
main{max-width:1100px;margin:auto;padding:40px 24px}h1{font-size:36px;margin-bottom:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
article{background:white;border:1px solid #cddbd4;border-radius:12px;padding:20px}
h2{font-size:17px}strong{font-size:25px}small{display:block;color:#526d63;overflow-wrap:anywhere}
li{margin:18px 0}.banner{background:#dfebe2;padding:14px;border-radius:8px}footer{margin-top:24px}
</style><main><small>MOODRING / OFFLINE RESEARCH LESSON</small><h1>Indicator evidence</h1>
<p class="banner">Synthetic historical reconstruction. These are fictional data, not forecasts or investment performance.</p>
<p>'''+escape(f'{q.instrument_id} · {q.session} · cutoff {q.cutoff.isoformat()}')+'''</p>
<p>'''+escape(f'Computed {answer.computed_at.isoformat()} · {answer.adapter_version} · {answer.status}')+'''</p>
<section class="grid">'''+cards+'''</section><h2>Explanation</h2><p>A: computed from the supplied synthetic snapshot · B: curated methodology · C: unverified hypothesis</p><ul>'''+statements+'''</ul>
<footer><small>Tool evidence: '''+escape(answer.result.evidence_id)+'''</small></footer></main></html>'''
