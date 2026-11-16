"""Private, offline FinBERT headline scoring after auditable relevance filtering.

No download, remote inference, database write, label tuning or article crawling is
performed. A separately downloaded pinned safetensors model is required. Outputs
are retrospective uncalibrated text sentiment, never stock-return probabilities.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path

import pyarrow as pa

from .archive import digest, publish, read_bytes, verified_tables
from .news_relevance import VERSION, curate
from .panel_scope import UNIVERSE

MODEL_ID = 'ProsusAI/finbert'
MODEL_REVISION = '0bc2ac10dda6929bcb1f77ccc43c7ec5c424f031'
MODEL_FILES = ('config.json', 'model.safetensors', 'vocab.txt',
               'tokenizer_config.json', 'special_tokens_map.json', 'README.md')
# Pinned official revision; weights hash also verified against the model file page.
MODEL_SHA256 = {
    'model.safetensors': 'e5897858ff819aad7629b96ce521ae5477952d03634ef5dd30ed2d76357a9f00',
    'config.json': 'f6449ddda85eb726207a40be59c0cd3bd4b142ccb27298d5e45f9ae3396b1abe',
    'vocab.txt': '07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3',
    'tokenizer_config.json': 'ffc7913f6084b138dad68de5a0c2f0ee25ac983709260901e01f53be5796b9d9',
    'special_tokens_map.json': '303df45a03609e4ead04bc3dc1536d0ab19b5358db685b6f3da123d05ec200e3',
    'README.md': '32d38310f6343e42bb827b47b8342babf6173cfb47f4d28b8b557b4d92f80cb1',
}
MAX_HEADLINES = 5000


def model_provenance(path: Path) -> dict:
    """Hash local non-symlink model files with a fixed aggregate size limit."""
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('model_symlink')
    hashes, total = {}, 0
    for name in MODEL_FILES:
        file = path / name
        if file.is_symlink() or not file.is_file():
            raise ValueError('model_file_missing_or_symlink')
        total += file.stat().st_size
        if total > 512 * 1024**2:
            raise ValueError('model_size_bound')
        h = hashlib.sha256()
        with file.open('rb') as source:
            for block in iter(lambda: source.read(1024**2), b''):
                h.update(block)
        hashes[name] = h.hexdigest()
    if hashes != MODEL_SHA256:
        raise ValueError('sentiment_model_checksum_mismatch')
    config = json.loads(read_bytes(path / 'config.json'))
    if config.get('model_type') != 'bert' or set(config.get('id2label', {}).values()) != {
            'positive', 'negative', 'neutral'}:
        raise ValueError('unexpected_sentiment_model_config')
    return {'model_id': MODEL_ID, 'intended_revision': MODEL_REVISION,
            'local_file_sha256': hashes, 'license': 'CC-BY-NC-SA-4.0',
            'packages': {p: version(p) for p in
                         ('torch', 'transformers', 'langdetect', 'pyarrow', 'exchange-calendars',
                          'pandas', 'numpy')},
            'input': 'English headline only', 'device': 'cpu',
            'score_definition': 'positive_softmax_minus_negative_softmax',
            'calibration': 'not_calibrated', 'training_performed': False}


class LocalFinBERT:
    """Load only local safetensors with remote code disabled and bounded CPU batches."""

    def __init__(self, model_path: Path):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.provenance = model_provenance(model_path)
        torch.set_num_threads(2)
        torch.manual_seed(0)
        torch.use_deterministic_algorithms(True)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path, local_files_only=True, trust_remote_code=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_path, local_files_only=True, trust_remote_code=False,
            use_safetensors=True).to('cpu').eval()

    def score(self, titles: list[str]) -> list[dict | None]:
        """Return all label probabilities; overlength text is missing, never truncated."""
        if len(titles) > MAX_HEADLINES:
            raise ValueError('sentiment_headline_bound')
        result = [None] * len(titles)
        eligible = [i for i, title in enumerate(titles) if
                    len(self.tokenizer.encode(title, truncation=False)) <= 512]
        for begin in range(0, len(eligible), 16):
            indices = eligible[begin:begin+16]
            inputs = self.tokenizer([titles[i] for i in indices], padding=True,
                                    truncation=False, return_tensors='pt')
            with self.torch.inference_mode():
                probabilities = self.model(**inputs).logits.softmax(dim=-1).tolist()
            for index, probs in zip(indices, probabilities):
                result[index] = {self.model.config.id2label[i]: float(p)
                                 for i, p in enumerate(probs)}
        return result


def attach_scores(items: list[dict], predictions: list[dict | None], created_at: str) -> list[dict]:
    """Validate classifier output and preserve nulls rather than inventing neutral scores."""
    if len(items) != len(predictions):
        raise ValueError('sentiment_prediction_count')
    output = []
    for item, prediction in zip(items, predictions):
        result = dict(item, feature_created_at=created_at, model_id=MODEL_ID,
                      model_revision=MODEL_REVISION, sentiment_score=None,
                      positive_probability=None, negative_probability=None,
                      neutral_probability=None, label=None,
                      sentiment_status='not_scored_token_limit',
                      sentiment_scope='headline_tone_for_single_named_company',
                      sentiment_calibrated=False)
        if prediction is not None:
            if set(prediction) != {'positive', 'negative', 'neutral'} or any(
                    not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1
                    for p in prediction.values()) or abs(sum(prediction.values())-1) > 1e-5:
                raise ValueError('invalid_sentiment_probabilities')
            result.update({label+'_probability': float(p) for label, p in prediction.items()})
            result['sentiment_score'] = prediction['positive']-prediction['negative']
            result['label'] = max(prediction, key=prediction.get)
            result['sentiment_status'] = 'scored_provisional'
        output.append(result)
    return output


def aggregate(rows: list[dict], sessions: list[tuple[str, datetime]]) -> list[dict]:
    """Equal-weight headline clusters at the first exchange close after their time proxy.

Zero stories produces a null mean. A partial one-day collection is never marked
complete. Computation today is not evidence of model availability at historical
cutoffs, so outputs remain ineligible for a PIT performance claim.
"""
    cutoffs = [cutoff for _, cutoff in sessions]
    buckets = defaultdict(list)
    seen = set()
    for row in sorted(rows, key=lambda r: (r.get('alignment_proxy_at') or r['gdelt_seen_at'], r['source_record_sha256'])):
        if row['sentiment_status'] != 'scored_provisional':
            continue
        key = (row['ticker'], row['headline_cluster_id'])
        if key in seen:
            continue
        seen.add(key)
        instant = datetime.fromisoformat(row.get('alignment_proxy_at') or row['gdelt_seen_at'])
        if instant.tzinfo is None:
            raise ValueError('news_observation_timezone_required')
        index = bisect_left(cutoffs, instant)
        if index == len(sessions):
            raise ValueError('news_session_grid_incomplete')
        buckets[(row['ticker'], sessions[index][0])].append(row)
    results = []
    for ticker in UNIVERSE:
        for session, cutoff in sessions:
            observations = buckets[(ticker, session)]
            scores = [r['sentiment_score'] for r in observations]
            results.append({'ticker': ticker, 'session': session, 'cutoff': cutoff.isoformat(),
                            'headline_count': len(scores),
                            'sentiment_mean': sum(scores)/len(scores) if scores else None,
                            'positive_count': sum(r['label']=='positive' for r in observations),
                            'negative_count': sum(r['label']=='negative' for r in observations),
                            'neutral_count': sum(r['label']=='neutral' for r in observations),
                            'source_record_sha256s': [r['source_record_sha256'] for r in observations],
                            'coverage_complete': False, 'human_approved': False,
                            'missing_reason': None if scores else 'no_eligible_scored_headlines',
                            'timing_policy': 'first_exchange_close_after_source_time_proxy',
                            'source_timing_policies': sorted({r.get('timing_policy','gdelt_observation_not_publication') for r in observations}),
                            'historical_prediction_eligible': False})
    return results


def run(snapshot: Path, output: Path, model_path: Path) -> dict:
    """Publish an immutable audit, headline scores and daily summaries privately."""
    manifest, tables = verified_tables(snapshot)
    if 'news' not in tables or tables['news'].num_rows > 50000:
        raise ValueError('news_input_bound')
    rows = tables['news'].to_pylist()
    if any(r['provider'] != 'gdelt_gkg' for r in rows):
        raise ValueError('gdelt_snapshot_required')
    audit, items = curate(rows)
    if len(items) > MAX_HEADLINES:
        raise ValueError('sentiment_headline_bound')
    # Use the source query window plus seven calendar days solely for exchange-close
    # alignment of after-close/weekend records; no extra news is inferred.
    from datetime import date, timedelta
    import exchange_calendars as xcals
    start = manifest['query_plan']['start']
    end = (date.fromisoformat(manifest['query_plan']['end_exclusive']) + timedelta(days=7)).isoformat()
    calendar = xcals.get_calendar('XNYS', start=start, end=end)
    latest = max(datetime.fromisoformat(r['gdelt_seen_at']) for r in rows if r['gdelt_seen_at'])
    sessions = []
    for session in calendar.sessions:
        close = calendar.session_close(session).to_pydatetime()
        sessions.append((session.date().isoformat(), close))
        if close >= latest:
            break
    classifier = LocalFinBERT(model_path)
    scores = attach_scores(items, classifier.score([r['title'] for r in items]),
                           datetime.now(timezone.utc).isoformat())
    daily = aggregate(scores, sessions)
    record_counts = Counter(r['gdelt_record_id'] for r in rows)
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
              'source_manifest_sha256': digest(read_bytes(snapshot/'manifest.json')),
              'source_export_sha256': manifest['source_export_sha256'],
              'source_query_window': {k:manifest['query_plan'][k] for k in ('start','end_exclusive')},
              'input_rows': len(rows), 'unique_gdelt_ids': len(record_counts),
              'repeated_gdelt_ids': sum(n>1 for n in record_counts.values()),
              'status_counts': dict(Counter(r['status'] for r in audit)),
              'relevance_version': VERSION, 'model': classifier.provenance,
              'split_status': 'development_only_relevance_rules_reviewed_on_this_pilot',
              'eligible_unique_headlines': len(items),
              'scored_headlines': sum(r['sentiment_status']=='scored_provisional' for r in scores),
              'counts_by_ticker': {t:sum(r['ticker']==t and r['sentiment_status']=='scored_provisional'
                                        for r in scores) for t in UNIVERSE},
              'human_approved': False, 'pit_eligible': False,
              'historical_archive_complete': False, 'paid_api_calls': 0,
              'article_bodies_fetched': 0, 'model_training_performed': False}
    # Typed empty table permits honest zero-coverage outputs without a fake row.
    scored_table = pa.Table.from_pylist(scores) if scores else pa.table({
        'ticker': pa.array([], type=pa.string()), 'sentiment_score': pa.array([], type=pa.float64())})
    publish(output, {'relevance_audit': pa.Table.from_pylist(audit),
                     'headline_sentiment': scored_table, 'daily_sentiment': pa.Table.from_pylist(daily)}, report)
    verified_tables(output)
    return report


def main():
    """Require explicit local snapshot, output and model paths; never fetch a model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model-path', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output, args.model_path), indent=2))


if __name__ == '__main__':
    main()
