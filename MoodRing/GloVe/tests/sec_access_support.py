"""Synthetic fixtures and deterministic transport/clock; never a live SEC response."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from ingestion.sec.contracts import UniverseManifest, payload_digest
from ingestion.sec.transport import Response
from ingestion.universe.identity import SicTaxonomy

ROOT = Path(__file__).parent / "fixtures/sec"
UA = "SyntheticLesson fixture@example.invalid"


class Clock:
    def __init__(self):
        self.seconds = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.seconds

    def now(self):
        return datetime(2026, 10, 25, tzinfo=timezone.utc) + timedelta(
            seconds=self.seconds
        )

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.seconds += seconds


def inputs():
    return (
        UniverseManifest.model_validate_json((ROOT / "universe.json").read_bytes()),
        SicTaxonomy.model_validate_json((ROOT / "access/taxonomy.json").read_bytes()),
    )


def payload(**updates):
    data = json.loads((ROOT / "access/metadata.json").read_bytes())
    data.update(updates)
    return json.dumps(data, separators=(",", ":")).encode()


def response(body=None, **kwargs):
    body = payload() if body is None else body
    return Response(200, body, payload_digest(body), **kwargs)


class MockTransport:
    def __init__(self, clock, responses):
        self.clock, self.responses = clock, list(responses)
        self.calls = []
        self.active, self.max_active = 0, 0

    def fetch(self, identifier, **kwargs):
        self.calls.append((identifier, self.clock.seconds, kwargs))
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        try:
            value = self.responses.pop(0)
            if callable(value):
                value = value()
            if isinstance(value, Exception):
                raise value
            return value
        finally:
            self.active -= 1
