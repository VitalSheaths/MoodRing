"""Loopback-only no-LLM educational view over a pinned local descriptor.

No browser-supplied path, HTTP fetch, LLM call, label read or trading operation.
This is a local preview, not a publicly authenticated service.
"""
import argparse
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .bot import ExplanationBot
from .contracts import Query
from .dictionary import DICTIONARY
from .presentation import html_answer
from .research import load_snapshot, ResearchEvidenceTools
from research_data.panel_scope import UNIVERSE


def render_page(bot, snapshots, ticker, session, question):
    """All selectors resolve against preloaded approved anchors, never raw paths."""
    snapshot = snapshots[ticker]
    row = next(r for r in snapshot.rows if r.session.isoformat() == session)
    query = Query(snapshot_id=snapshot.snapshot_id,instrument_id='stock:'+ticker,
                  session=row.session,cutoff=row.cutoff)
    answer = bot.answer(snapshot.owner_id,query,question)
    options = ''.join(f'<option {"selected" if t==ticker else ""}>{escape(t)}</option>' for t in snapshots)
    dates = ''.join(f'<option {"selected" if r.session.isoformat()==session else ""}>{r.session}</option>' for r in snapshot.rows)
    form = ('<section><h2>Ask about the selected evidence</h2><form method="get" action="/">'
        '<label>Stock <select name="ticker">'+options+'</select></label> '
        '<label>Session <select name="session">'+dates+'</select></label>'
        '<p><label>Question <input name="question" maxlength="1000" size="45" value="'+escape(question,quote=True)+'"></label></p>'
        '<button type="submit">Explain with checked templates</button></form>'
        '<p>Try “Explain RSI”, “How is ATR calculated?”, or “What does log_return_1 mean?”. '
        'Exact indicator names are supported. Unrecognized questions receive bounded help. '
        'No free-form model is connected.</p></section>')
    if answer.status == 'unsupported_question':
        response = '<p>This question is outside the supported scope. Select the matching stock and session, then ask about an indicator or its limitations. No trading decision or future outcome is inferred.</p>'
    else:
        selected = [p for p in answer.passages if p.key.startswith(('dictionary:', 'research:'))]
        selected = selected or list(answer.passages[:2])
        response = ''.join('<p>'+escape(p.text)+'</p>' for p in selected[:3])
    form = form[:-10] + '<div role="status"><h3>Template answer</h3>'+response+'</div></section>'
    glossary = '<section><h2>Indicator dictionary</h2><p>Formula conventions match the code. These descriptions do not establish forecasting value.</p>'
    for key,e in DICTIONARY['indicators'].items():
        glossary += ('<details><summary>'+escape(e['label']+' · '+key)+'</summary><p>'+escape(e['meaning'])+
            '</p><p><b>Calculation:</b> '+escape(e['formula'])+'</p><p><b>Unit:</b> '+escape(e['unit'])+
            '</p><p><b>Minimum observations:</b> '+escape(str(e['minimum_observations'] or 'Not implemented'))+
            '</p><p><b>Code:</b> '+escape(e['implementation'])+'</p><p>'+escape(e['limitations'])+'</p></details>')
    glossary += '<h3>Terms</h3><dl>'+''.join('<dt><b>'+escape(k)+'</b></dt><dd>'+escape(v)+'</dd>' for k,v in DICTIONARY['terms'].items())+'</dl></section>'
    page=html_answer(answer).replace("style-src 'unsafe-inline'", "style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
    page=page.replace('</style>','input,select,button{font:inherit;padding:8px;max-width:100%;border:1px solid #9aafa0;border-radius:5px}button{background:#193e32;color:white}form{padding:18px;background:white;border-radius:10px}</style>')
    return page.replace('<section><h2>Price</h2>',form+'<section><h2>Price</h2>',1).replace('<footer>',glossary+'<footer>'), answer


def handler_for(bot,snapshots,port):
    """Reject cross-site requests and rebinding hosts; do not log question text."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            """Serve only approved selections with explicit host and request bounds."""
            allowed = {f'127.0.0.1:{port}',f'localhost:{port}'}
            if self.headers.get('Host') not in allowed or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.send_error(403);return
            if len(self.path)>4096 or urlsplit(self.path).path != '/':
                self.send_error(404);return
            try:
                params=parse_qs(urlsplit(self.path).query,max_num_fields=3,keep_blank_values=True)
                if set(params)-{'ticker','session','question'} or any(len(v)!=1 for v in params.values()):
                    raise ValueError('unknown_or_repeated_parameter')
                ticker=params.get('ticker',['AAPL'])[0]
                snapshot=snapshots[ticker]
                session=params.get('session',[snapshot.rows[-1].session.isoformat()])[0]
                question=params.get('question',['Explain the risk indicators'])[0]
                html,_=render_page(bot,snapshots,ticker,session,question)
            except (KeyError,ValueError,StopIteration):
                self.send_error(400,'Select an approved stock/session and a supported question');return
            content=html.encode()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.end_headers();self.wfile.write(content)
        def log_message(self,*args):
            """Suppress request logs so question text is not persisted."""
            pass
    return Handler


def main():
    """Verify local sources before binding a loopback-only preview server."""
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--descriptor',required=True)
    p.add_argument('--descriptor-sha256',required=True)
    p.add_argument('--port',type=int,default=8018)
    args=p.parse_args()
    now=datetime.now(timezone.utc)
    snapshots={t:load_snapshot(args.root,args.descriptor,args.descriptor_sha256,
                              'local-research-owner',t,now) for t in UNIVERSE}
    bot=ExplanationBot(ResearchEvidenceTools(tuple(snapshots.values())))
    server=HTTPServer(('127.0.0.1',args.port),handler_for(bot,snapshots,args.port))
    print(f'Local preview: http://127.0.0.1:{args.port}/',flush=True)
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':main()
