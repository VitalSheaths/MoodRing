"""Manually invoked SEC metadata/SIC access. No persistence or downstream use.

The transport seam is trusted injected code for tests, never caller URLs/config.
Unknown source publication stays unknown; receipt is NOT filing availability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ingestion.sec.contracts import UniverseManifest, cik, explicit_time, payload_digest
from ingestion.sec.normalization import (
    Context,
    Limits as NormalizationLimits,
    NormalizationError,
    Result,
    _decode,
    normalize,
)
from ingestion.universe.identity import SicTaxonomy, sic_code
from ingestion.sec.transport import (
    ProcessTransport,
    TransportFailure,
    identified,
    target,
)


class AccessError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    max_ciks: int = Field(default=2, ge=1, le=2)
    max_attempts: int = Field(default=4, ge=1, le=4)
    retries_per_cik: int = Field(default=1, ge=0, le=1)
    timeout_s: int = Field(default=5, ge=1, le=5)
    deadline_s: int = Field(default=30, ge=1, le=30)
    body_bytes: int = Field(default=1048576, ge=1, le=1048576)
    rows: int = Field(default=1000, ge=1, le=1000)
    result_bytes: int = Field(default=4194304, ge=1, le=4194304)


@dataclass(frozen=True)
class Receipt:
    cik: str
    request_at: datetime
    received_at: datetime
    raw_sha256: str
    wire_sha256: str
    encoding: str
    binding_sha256: str


@dataclass(frozen=True)
class CurrentSic:
    code: str | None
    status: Literal["observed", "missing", "invalid", "unsupported"]
    observed_at: datetime
    taxonomy_version: str
    payload_sha256: str
    # No effective date, historical availability or SicAssignment is invented.


@dataclass(frozen=True)
class IssuerResult:
    ordinal: int
    status: str
    code: str
    attempts: int
    receipt: Receipt | None = field(default=None, repr=False)
    sic: CurrentSic | None = field(default=None, repr=False)
    normalized: Result | None = field(default=None, repr=False)

    def public_summary(self):
        return {
            "issuer_ordinal": self.ordinal,
            "status": self.status,
            "code": self.code,
            "attempts": self.attempts,
            "sic_status": self.sic.status if self.sic else "unavailable",
            "metadata": self.normalized.public_summary() if self.normalized else None,
            "complete_history": False,
            "history_scope": "root_recent_only",
        }


@dataclass(frozen=True)
class RunResult:
    issuers: tuple[IssuerResult, ...]

    def public_summary(self):
        return {
            "attempts": sum(r.attempts for r in self.issuers),
            "issuers": [r.public_summary() for r in self.issuers],
            "durable_writes": 0,
            "downstream_use": False,
        }


def _now():
    return datetime.now(timezone.utc)


def _sic(data, taxonomy, receipt):
    value = data.get("sic")
    code, status = None, "missing"
    if value is not None and value != "":
        try:
            code = sic_code(value)
            status = (
                "observed"
                if code in {s.code for s in taxonomy.codes}
                else "unsupported"
            )
        except (TypeError, ValueError):
            status = "invalid"
    return CurrentSic(
        code if status == "observed" else None,
        status,
        receipt.received_at,
        taxonomy.taxonomy_version,
        receipt.raw_sha256,
    )


def _receipt(identifier, requested_at, received_at, response):
    raw_hash = payload_digest(response.body)
    wire_hash = response.wire_sha256
    if (
        not isinstance(wire_hash, str)
        or len(wire_hash) != 64
        or any(c not in "0123456789abcdef" for c in wire_hash)
    ):
        raise AccessError("invalid_transport_result")
    binding = {
        "cik": identifier,
        "url": "https://data.sec.gov" + target(identifier),
        "request_at": requested_at.isoformat(),
        "received_at": received_at.isoformat(),
        "raw_sha256": raw_hash,
        "wire_sha256": wire_hash,
        "encoding": response.encoding,
    }
    return Receipt(
        identifier,
        requested_at,
        received_at,
        raw_hash,
        wire_hash,
        response.encoding,
        hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest(),
    )


def fetch_metadata(
    ciks,
    universe,
    taxonomy,
    *,
    allow_network=False,
    user_agent=None,
    limits=None,
    transport=None,
    monotonic=time.monotonic,
    sleep=time.sleep,
    clock=_now,
    contexts=None,
):
    """Bounded one-shot access; no network by default, even with an injected mock.

    contexts may supply reviewed availability/amendment evidence per issuer. Actual
    receipt supplies ingested_at. No caller time or provider acceptance replaces it.
    """
    start = monotonic()
    if allow_network is not True:
        raise AccessError("network_disabled")
    try:
        identified(user_agent)
        limits = Limits.model_validate(limits or {})
        if not isinstance(ciks, (list, tuple)) or not 1 <= len(ciks) <= limits.max_ciks:
            raise ValueError
        identifiers = tuple(cik(c) for c in ciks)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError
        universe = UniverseManifest.model_validate(universe)
        taxonomy = SicTaxonomy.model_validate(taxonomy)
        if any(
            not any(m.cik == identifier for m in universe.members)
            for identifier in identifiers
        ):
            raise ValueError
        if taxonomy.captured_at > explicit_time(clock()):
            raise ValueError
        contexts = contexts or {}
        if not isinstance(contexts, dict) or any(
            k not in identifiers for k in contexts
        ):
            raise ValueError
        for ctx in contexts.values():
            if not isinstance(ctx, dict) or set(ctx) - {"availability", "amendments"}:
                raise ValueError
            # Validate and bound caller context now, not after a network operation.
            Context.model_validate({"ingested_at": explicit_time(clock()), **ctx})
    except (ValueError, TypeError, ValidationError, TransportFailure):
        raise AccessError("invalid_configuration") from None
    deadline = start + limits.deadline_s
    client = (
        transport
        if transport is not None
        else ProcessTransport(enabled=True, user_agent=user_agent)
    )
    nlimits = NormalizationLimits(
        max_input_bytes=limits.body_bytes,
        max_rows=limits.rows,
        max_result_bytes=limits.result_bytes,
    )
    outcomes, total, last_finished = [], 0, None
    fatal = False
    for ordinal, identifier in enumerate(identifiers, 1):
        attempts, result = 0, None
        for retry in range(limits.retries_per_cik + 1):
            if fatal or monotonic() >= deadline:
                result = IssuerResult(
                    ordinal, "deferred", "deadline_or_worker_unavailable", attempts
                )
                break
            if total >= limits.max_attempts:
                result = IssuerResult(ordinal, "deferred", "attempt_limit", attempts)
                break
            if last_finished is not None:
                wait = max(0, 1 - (monotonic() - last_finished))
                if monotonic() + wait >= deadline - 1:
                    result = IssuerResult(
                        ordinal, "deferred", "deadline_exceeded", attempts
                    )
                    break
                if wait:
                    sleep(wait)
            if monotonic() >= deadline:
                result = IssuerResult(
                    ordinal, "deferred", "deadline_exceeded", attempts
                )
                break
            requested_at = explicit_time(clock())
            total += 1
            attempts += 1
            response, code = None, None
            try:
                response = client.fetch(
                    identifier,
                    deadline=deadline,
                    timeout=min(limits.timeout_s, deadline - monotonic()),
                    cap=limits.body_bytes,
                    monotonic=monotonic,
                )
            except TransportFailure as exc:
                # Only a fixed allowlist of transport failures can be disclosed/retried.
                code = (
                    exc.code
                    if exc.code
                    in {
                        "transport_timeout",
                        "deadline_exceeded",
                        "body_limit",
                        "headers_limit",
                        "invalid_headers",
                        "invalid_length",
                        "invalid_compression",
                        "unsupported_encoding",
                        "worker_shutdown_failed",
                    }
                    else "transport_unavailable"
                )
                fatal = code == "worker_shutdown_failed"
            except Exception:
                code = "transport_unavailable"
            # Pace from completion, not process launch: variable child startup
            # cannot compress the actual HTTP dispatch interval below one second.
            last_finished = monotonic()
            if monotonic() >= deadline or code == "deadline_exceeded":
                result = IssuerResult(
                    ordinal, "deferred", "deadline_exceeded", attempts
                )
                break
            retryable = code == "transport_timeout"
            if response is not None:
                retryable = response.status in (429, 500, 502, 503, 504)
                if 300 <= response.status < 400:
                    code = "redirect_rejected"
                elif response.status == 403:
                    code = "access_denied"
                elif response.status == 404:
                    code = "not_found"
                elif response.status != 200:
                    code = "retryable_http" if retryable else "http_rejected"
            if response is not None and response.status == 200:
                try:
                    if (
                        not isinstance(response.body, bytes)
                        or len(response.body) > limits.body_bytes
                    ):
                        raise AccessError("body_limit")
                    received_at = explicit_time(clock())
                    if received_at.tzinfo is None or requested_at > received_at:
                        raise AccessError("invalid_receipt")
                    data = _decode(response.body, nlimits)
                    if (
                        not isinstance(data, dict)
                        or isinstance(data.get("cik"), bool)
                        or not isinstance(data.get("cik"), (str, int))
                    ):
                        raise AccessError("wrong_issuer")
                    if cik(str(data["cik"])) != identifier:
                        raise AccessError("wrong_issuer")
                    receipt = _receipt(identifier, requested_at, received_at, response)
                    context = {
                        "ingested_at": received_at,
                        **contexts.get(identifier, {}),
                    }
                    normalized = normalize(
                        response.body, context, universe, limits=nlimits
                    )
                    if normalized.raw_sha256 != receipt.raw_sha256:
                        raise AccessError("invalid_receipt")
                    sic = _sic(data, taxonomy, receipt)
                    if monotonic() >= deadline:
                        raise AccessError("deadline_exceeded")
                    status = (
                        "deferred"
                        if any(e.status == "retryable" for e in normalized.events)
                        else "completed_with_gaps"
                        if sic.status != "observed"
                        or any(e.status == "quarantined" for e in normalized.events)
                        else "completed_subset"
                    )
                    result = IssuerResult(
                        ordinal,
                        status,
                        "root_subset_only",
                        attempts,
                        receipt,
                        sic,
                        normalized,
                    )
                except AccessError as exc:
                    result = IssuerResult(
                        ordinal,
                        "deferred" if exc.code == "deadline_exceeded" else "failed",
                        exc.code,
                        attempts,
                    )
                except (NormalizationError, ValueError, TypeError, ValidationError):
                    result = IssuerResult(
                        ordinal, "failed", "invalid_metadata", attempts
                    )
                break
            if retryable:
                if response is not None and response.retry_invalid:
                    result = IssuerResult(
                        ordinal, "deferred", "retry_after_invalid", attempts
                    )
                    break
                if retry >= limits.retries_per_cik or total >= limits.max_attempts:
                    result = IssuerResult(
                        ordinal, "deferred", "retry_budget_exhausted", attempts
                    )
                    break
                delay = (
                    response.retry_delay
                    if response is not None and response.retry_delay is not None
                    else 1
                )
                wait = max(delay, max(0, 1 - (monotonic() - last_finished)))
                if monotonic() + wait >= deadline - 1:
                    result = IssuerResult(
                        ordinal, "deferred", "retry_after_budget", attempts
                    )
                    break
                if wait:
                    sleep(wait)
                continue
            result = IssuerResult(
                ordinal, "failed", code or "transport_unavailable", attempts
            )
            break
        outcomes.append(result)
    return RunResult(tuple(outcomes))
