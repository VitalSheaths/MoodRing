"""Fixed source-linked methodology; no live scraping or training at answer time."""
import json
import re
from pathlib import Path
from .contracts import Passage

CORPUS=json.loads(Path(__file__).with_name('research_lessons.json').read_text())
LESSONS=tuple(Passage(key='research:'+r['key'],keywords=tuple(r['keywords']),
    text=r['text']+' Reference: '+r['url']) for r in CORPUS['entries'])


def retrieve_lessons(question,limit=2):
    """Retrieve bounded curated passages, preserving explicit applicability limits."""
    if not 1<=limit<=3:raise ValueError('lesson_limit')
    words=set(re.findall(r'[a-z]+',question.lower()))
    ranked=sorted(enumerate(LESSONS),key=lambda x:(-len(words.intersection(x[1].keywords)),x[0]))
    return tuple(p for _,p in ranked if words.intersection(p.keywords))[:limit]
