"""Run the synthetic no-LLM bot, optionally writing a local card view."""
import argparse
from pathlib import Path

from explanation.bot import ExplanationBot
from explanation.demo import demo_query, demo_snapshot
from explanation.presentation import html_answer, text_answer
from explanation.tools import EvidenceTools


def main() -> None:
    """Keep output paths in operator arguments, never in the bot's question tools."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--question', default='Volume was high, so why did the price barely move?')
    parser.add_argument('--html', type=Path, help='Create a new standalone HTML file; refuses overwrite.')
    parser.add_argument('--json', action='store_true', help='Print the full evidence envelope.')
    args = parser.parse_args()
    snapshot = demo_snapshot()
    answer = ExplanationBot(EvidenceTools((snapshot,))).answer('learner', demo_query(snapshot), args.question)
    print(answer.model_dump_json(indent=2) if args.json else text_answer(answer))
    if args.html:
        with args.html.open('x', encoding='utf-8') as output:
            output.write(html_answer(answer))


if __name__ == '__main__':
    main()
