"""Fixed-destination SEC HTTPS leaf, isolated to bound DNS and slow reads.

No provider logging, persistence, redirects, proxy configuration or caller URLs.
The worker accepts only a bounded JSON request over stdin and returns bounded JSON
on stdout. Never invoke this worker/ProcessTransport in CI with a real connection.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
import zlib

HOST = "data.sec.gov"
BODY_CAP = 1048576
IPC_CAP = 2 * BODY_CAP
CHUNK = 8192


class TransportFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def target(identifier):
    if (
        not isinstance(identifier, str)
        or not re.fullmatch(r"[0-9]{10}", identifier)
        or int(identifier) == 0
    ):
        raise TransportFailure("invalid_destination")
    return "/submissions/CIK" + identifier + ".json"


def identified(value):
    # Syntax is not identity verification; the actual operator needs human approval.
    if (
        not isinstance(value, str)
        or not value.isascii()
        or len(value) > 254
        or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9._/-]{2,63} [A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}",
            value,
        )
    ):
        raise TransportFailure("operator_required")
    return value


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes = field(default=b"", repr=False)
    wire_sha256: str | None = None
    encoding: str = "identity"
    retry_delay: float | None = None
    retry_invalid: bool = False


def retry_after(value, now):
    if value is None:
        return None, False
    if not isinstance(value, str) or len(value) > 128 or not value.isascii():
        return None, True
    try:
        if re.fullmatch(r"[0-9]{1,10}", value):
            delay = float(value)
        else:
            at = parsedate_to_datetime(value)
            if at.tzinfo is None:
                raise ValueError
            delay = max(0, (at - now).total_seconds())
        return min(delay, 31), False  # 31 cannot fit a 30-second invocation.
    except (ValueError, TypeError, OverflowError):
        return None, True


def read_body(response, *, deadline, cap, monotonic=time.monotonic):
    """Bound both wire and decoded bytes; read1 never asks for the whole body.

    The outer process deadline also stops a read1/header/DNS call that never returns.
    Gzip output uses max_length, never an unbounded decompress/flush allocation.
    """
    headers = response.getheaders()
    if len(headers) > 50 or sum(len(k) + len(v) for k, v in headers) > 32768:
        raise TransportFailure("headers_limit")
    lengths = [v for k, v in headers if k.lower() == "content-length"]
    encodings = [v for k, v in headers if k.lower() == "content-encoding"]
    if len(lengths) > 1 or len(encodings) > 1:
        raise TransportFailure("invalid_headers")
    declared = None
    if lengths:
        if not re.fullmatch(r"[0-9]{1,12}", lengths[0]):
            raise TransportFailure("invalid_headers")
        declared = int(lengths[0])
        if declared > cap:
            raise TransportFailure("body_limit")
    if lengths and any(k.lower() == "transfer-encoding" for k, _ in headers):
        raise TransportFailure("invalid_headers")
    encoding = encodings[0].strip().lower() if encodings else "identity"
    if encoding not in ("identity", "gzip"):
        raise TransportFailure("unsupported_encoding")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    wire_bytes, body = 0, bytearray()
    digest = hashlib.sha256()
    while True:
        if monotonic() >= deadline:
            raise TransportFailure("deadline_exceeded")
        chunk = response.read1(min(CHUNK, cap - wire_bytes + 1))
        if monotonic() >= deadline:
            raise TransportFailure("deadline_exceeded")
        if not chunk:
            break
        wire_bytes += len(chunk)
        if wire_bytes > cap:
            raise TransportFailure("body_limit")
        digest.update(chunk)
        try:
            decoded = (
                decoder.decompress(chunk, cap - len(body) + 1) if decoder else chunk
            )
        except zlib.error:
            raise TransportFailure("invalid_compression") from None
        body.extend(decoded)
        if len(body) > cap:
            raise TransportFailure("body_limit")
        if decoder and decoder.unused_data:
            raise TransportFailure("invalid_compression")
    if declared is not None and declared != wire_bytes:
        raise TransportFailure("invalid_length")
    if decoder and not decoder.eof:
        raise TransportFailure("invalid_compression")
    return bytes(body), digest.hexdigest(), encoding


def https_attempt(
    identifier, user_agent, *, deadline, timeout, cap, monotonic=time.monotonic
):
    path = target(identifier)
    identified(user_agent)
    if monotonic() >= deadline:
        raise TransportFailure("deadline_exceeded")
    # Native HTTPSConnection never consults HTTP(S)_PROXY / ALL_PROXY / NO_PROXY.
    # Load compiled-in trust locations explicitly, not inherited SSL_CERT_* overrides.
    defaults = ssl.get_default_verify_paths()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(
        cafile=defaults.openssl_cafile, capath=defaults.openssl_capath
    )
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    connection = http.client.HTTPSConnection(
        HOST, port=443, context=context, timeout=min(timeout, deadline - monotonic())
    )
    # Header parsing is also finite in the isolated worker.
    http.client._MAXLINE = 8192
    http.client._MAXHEADERS = 50
    try:
        connection.request(
            "GET",
            path,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if monotonic() >= deadline:
            raise TransportFailure("deadline_exceeded")
        delay, invalid = retry_after(
            response.getheader("Retry-After"), datetime.now(timezone.utc)
        )
        if response.status != 200:
            # Never read an error/redirect body or follow Location.
            return Response(response.status, retry_delay=delay, retry_invalid=invalid)
        body, digest, encoding = read_body(
            response, deadline=deadline, cap=cap, monotonic=monotonic
        )
        return Response(200, body, digest, encoding)
    finally:
        connection.close()


class ProcessTransport:
    """One owned child per attempt; only explicit opt-in can create it.

    communicate(timeout) bounds the WHOLE attempt to at most five seconds,
    including DNS, headers and slow-drip reads (stricter than per-read timeouts).
    A one-second reserve is left for terminate/kill/reap; no subsequent attempt
    is allowed if the child cannot be reaped. No environment proxy/cert overrides.
    """

    def __init__(self, *, enabled=False, user_agent=None):
        self.enabled = enabled is True
        self.user_agent = identified(user_agent) if self.enabled else None

    def fetch(self, identifier, *, deadline, timeout, cap, monotonic=time.monotonic):
        if not self.enabled:
            raise TransportFailure("network_disabled")
        target(identifier)
        started = monotonic()
        remaining = deadline - started - 1
        if remaining <= 0:
            raise TransportFailure("deadline_exceeded")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 5
            or type(cap) is not int
            or not 1 <= cap <= BODY_CAP
        ):
            raise TransportFailure("invalid_transport_limits")
        attempt_deadline = min(deadline - 1, started + timeout)
        message = json.dumps(
            {
                "enabled": True,
                "cik": identifier,
                "user_agent": self.user_agent,
                "deadline": attempt_deadline,
                "timeout": min(timeout, remaining),
                "cap": cap,
            }
        ).encode()
        # A deliberately minimal environment: no inherited proxies, SSL overrides,
        # Python path/startup hooks or private provider credentials.
        env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
        if sys.platform == "win32" and "SystemRoot" in os.environ:
            env["SystemRoot"] = os.environ["SystemRoot"]
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=str(Path(__file__).parent),
        )
        try:
            remaining = attempt_deadline - monotonic()
            if remaining <= 0:
                raise TransportFailure(
                    "deadline_exceeded"
                    if monotonic() >= deadline - 1
                    else "transport_timeout"
                )
            try:
                output, _ = process.communicate(message, timeout=remaining)
            except subprocess.TimeoutExpired:
                raise TransportFailure(
                    "deadline_exceeded"
                    if monotonic() >= deadline - 1
                    else "transport_timeout"
                ) from None
            if monotonic() >= deadline - 1:
                raise TransportFailure("deadline_exceeded")
            if monotonic() >= attempt_deadline:
                raise TransportFailure("transport_timeout")
            if process.returncode != 0 or len(output) > IPC_CAP:
                raise TransportFailure("worker_failed")
            try:
                data = json.loads(output)
                if not isinstance(data, dict):
                    raise ValueError
                if data.get("error"):
                    code = data["error"]
                    if code not in (
                        "transport_timeout",
                        "transport_error",
                        "deadline_exceeded",
                        "body_limit",
                        "headers_limit",
                        "invalid_headers",
                        "unsupported_encoding",
                        "invalid_compression",
                        "invalid_length",
                    ):
                        code = "worker_failed"
                    raise TransportFailure(code)
                if set(data) != {
                    "status",
                    "body",
                    "wire_sha256",
                    "encoding",
                    "retry_delay",
                    "retry_invalid",
                }:
                    raise ValueError
                if type(data["retry_invalid"]) is not bool or data["encoding"] not in (
                    "identity",
                    "gzip",
                ):
                    raise ValueError
                if data["status"] == 200 and (
                    not isinstance(data["wire_sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", data["wire_sha256"])
                ):
                    raise ValueError
                body = base64.b64decode(data["body"], validate=True)
                if len(body) > cap:
                    raise TransportFailure("body_limit")
                status, delay = data["status"], data["retry_delay"]
                if type(status) is not int or not 100 <= status <= 599:
                    raise ValueError
                if delay is not None and (
                    type(delay) not in (int, float)
                    or not math.isfinite(delay)
                    or not 0 <= delay <= 31
                ):
                    raise ValueError
                return Response(
                    status,
                    body,
                    data["wire_sha256"],
                    data["encoding"],
                    delay,
                    bool(data["retry_invalid"]),
                )
            except (ValueError, TypeError, KeyError):
                raise TransportFailure("worker_failed") from None
        finally:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=0.2)
            except (OSError, subprocess.TimeoutExpired):
                raise TransportFailure("worker_shutdown_failed") from None
            finally:
                for stream in (process.stdin, process.stdout):
                    if stream:
                        try:
                            stream.close()
                        except OSError:
                            pass


def _main():
    # Worker protocol is private and does not accept URLs, headers or output paths.
    try:
        data = json.loads(sys.stdin.buffer.read(4097))
        if (
            set(data) != {"enabled", "cik", "user_agent", "deadline", "timeout", "cap"}
            or data["enabled"] is not True
        ):
            raise ValueError
        if not (
            type(data["cap"]) is int
            and 1 <= data["cap"] <= BODY_CAP
            and isinstance(data["timeout"], (int, float))
            and 0 < data["timeout"] <= 5
            and isinstance(data["deadline"], (int, float))
            and math.isfinite(data["deadline"])
            and 0 < data["deadline"] - time.monotonic() <= 30
        ):
            raise ValueError
        response = https_attempt(
            data["cik"],
            data["user_agent"],
            deadline=data["deadline"],
            timeout=data["timeout"],
            cap=data["cap"],
        )
        output = {
            "status": response.status,
            "body": base64.b64encode(response.body).decode(),
            "wire_sha256": response.wire_sha256,
            "encoding": response.encoding,
            "retry_delay": response.retry_delay,
            "retry_invalid": response.retry_invalid,
        }
    except TimeoutError:
        output = {"error": "transport_timeout"}
    except TransportFailure as exc:
        output = {"error": exc.code}
    except Exception:
        output = {"error": "transport_error"}
    sys.stdout.write(json.dumps(output))


if __name__ == "__main__":
    _main()
