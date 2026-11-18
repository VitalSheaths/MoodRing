"""Versioned calculation dictionary shared by code review, cards and retrieval.

The JSON is application code, never a model-generated answer or held-out solution.
Descriptions bind to the actual indicator convention, including warm-up and units.
"""
import json
from pathlib import Path
import re

from .contracts import Passage

DICTIONARY = json.loads(Path(__file__).with_name('indicator_dictionary.json').read_text())
ENTRIES = DICTIONARY['indicators']
TECHNICAL_KEYS = tuple(k for k, entry in ENTRIES.items() if entry['status'] == 'calculated')


def passages_for(question):
    """Retrieve at most three exact indicator topics, not arbitrary prompt instructions."""
    words = set(re.findall(r'[a-z0-9_]+', question.lower()))
    aliases = {'rsi':'rsi_14', 'atr':'atr_14_pct', 'obv':'obv_change_20',
               'bollinger':'bollinger_pct_b', 'volatility':'realized_vol_20',
               'variance':'historical_variance_20', 'log':'log_return_1',
               'garch':'garch', 'evt':'evt'}
    keys = [k for k in ENTRIES if k in words]
    keys += [k for a,k in aliases.items() if a in words and k not in keys]
    result = []
    for key in keys[:3]:
        e = ENTRIES[key]
        text = f"{e['label']}: {e['meaning']} Calculation: {e['formula']} Unit: {e['unit']}. {e['limitations']}"
        result.append(Passage(key='dictionary:'+key, text=text, keywords=(key,)))
    return tuple(result)
