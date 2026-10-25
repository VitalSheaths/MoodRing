"""Transport controls are executable merge requirements; no real sockets/SEC calls."""
from datetime import datetime, timezone, timedelta
import base64
import gzip
import io
import json
import socket
import ssl
import subprocess
from types import SimpleNamespace

import pytest

from ingestion.sec import transport as wire
from sec_access_support import Clock, UA


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Reject real socket operations so these controls remain entirely offline."""

    def blocked(*a, **kw):
        """Fail immediately if a test attempts a real socket operation."""
        raise AssertionError("real network forbidden")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(wire.http.client, "_MAXLINE", wire.http.client._MAXLINE)
    monkeypatch.setattr(wire.http.client, "_MAXHEADERS", wire.http.client._MAXHEADERS)


class Body:
    status = 200

    def __init__(self, chunks, headers=(), clock=None, drip=0):
        """Initialize bounded synthetic response chunks, headers and optional drip timing."""
        self.chunks = list(chunks)
        self.headers = list(headers)
        self.clock = clock
        self.drip = drip
        self.reads = []

    def getheaders(self):
        """Return synthetic response headers without network access."""
        return self.headers

    def getheader(self, name):
        """Return the first matching synthetic response header, if present."""
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    def read1(self, size):
        """Read a bounded synthetic chunk and optionally advance the fake clock."""
        self.reads.append(size)
        if self.clock:
            self.clock.seconds += self.drip
        if not self.chunks:
            return b""
        item = self.chunks.pop(0)
        if len(item) > size:
            self.chunks.insert(0, item[size:])
            item = item[:size]
        return item


@pytest.mark.parametrize(
    "identifier",
    [
        "https://evil.invalid",
        "123",
        "0000000000",
        "../123",
        "0000000123?x=1",
        "0000000123:443",
        "0000000123/extra",
        123,
        True,
        "１２３４５６７８９０",
    ],
)
def test_destination_generator_only_accepts_canonical_ciks(identifier):
    """Verify destination generator only accepts canonical ciks."""
    with pytest.raises(wire.TransportFailure, match="invalid_destination"):
        wire.target(identifier)


def test_fixed_destination_tls_and_hostile_proxy_variables(monkeypatch, tmp_path):
    """Verify fixed destination tls and hostile proxy variables."""
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.setenv(key, "http://private:secret@127.0.0.1:9/hostile")
    monkeypatch.setenv("SSL_CERT_FILE", "/private/hostile.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/private/hostile")
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "private-keys"))
    calls = []

    class Context:
        verify_mode = None
        check_hostname = False

        def load_verify_locations(self, **kw):
            """Record explicitly selected CA paths without loading certificates."""
            calls.append(("roots", kw))

    monkeypatch.setattr(wire.ssl, "SSLContext", lambda protocol: Context())
    body = Body([b"{}"], [("Content-Length", "2")])

    class HTTPS:
        def __init__(self, host, **kw):
            """Capture or ignore connection inputs without opening a socket."""
            calls.append(("connection", host, kw))

        def request(self, method, path, headers):
            """Record or intercept a synthetic HTTPS request without sending it."""
            calls.append(("request", method, path, headers))

        def getresponse(self):
            """Return the synthetic response without contacting SEC."""
            return body

        def close(self):
            """Record or simulate connection closure without socket operations."""
            calls.append(("close",))

    monkeypatch.setattr(wire.http.client, "HTTPSConnection", HTTPS)
    # Restore global header limits after invoking a worker leaf in this test process.
    monkeypatch.setattr(wire.http.client, "_MAXLINE", wire.http.client._MAXLINE)
    monkeypatch.setattr(wire.http.client, "_MAXHEADERS", wire.http.client._MAXHEADERS)
    r = wire.https_attempt(
        "0000000123", UA, deadline=30, timeout=5, cap=100, monotonic=lambda: 0
    )
    c = next(x for x in calls if x[0] == "connection")
    assert c[1] == "data.sec.gov" and c[2]["port"] == 443 and c[2]["timeout"] == 5
    assert (
        c[2]["context"].verify_mode == ssl.CERT_REQUIRED
        and c[2]["context"].check_hostname
    )
    roots = next(x for x in calls if x[0] == "roots")[1]
    assert all("/private/hostile" not in str(v) for v in roots.values())
    request = next(x for x in calls if x[0] == "request")
    assert request[1:3] == ("GET", "/submissions/CIK0000000123.json")
    assert request[3]["User-Agent"] == UA and "Authorization" not in request[3]
    assert calls[-1] == ("close",) and r.body == b"{}"
    assert not (tmp_path / "private-keys").exists()


@pytest.mark.parametrize(
    "status", [301, 302, 303, 307, 308, 403, 404, 429, 500, 502, 503, 504]
)
def test_redirects_errors_never_read_bodies_or_follow_location(monkeypatch, status):
    """Verify redirects errors never read bodies or follow location."""
    body = Body(
        [b"SECRET"],
        [("Location", "https://evil.invalid/private"), ("Retry-After", "2")],
    )
    body.status = status
    calls = []

    class HTTPS:
        def __init__(self, *a, **kw):
            """Capture or ignore connection inputs without opening a socket."""
            pass

        def request(self, *a, **kw):
            """Record or intercept a synthetic HTTPS request without sending it."""
            calls.append(a)

        def getresponse(self):
            """Return the synthetic response without contacting SEC."""
            return body

        def close(self):
            """Record or simulate connection closure without socket operations."""
            pass

    monkeypatch.setattr(wire.http.client, "HTTPSConnection", HTTPS)
    r = wire.https_attempt(
        "0000000123", UA, deadline=30, timeout=5, cap=100, monotonic=lambda: 0
    )
    assert len(calls) == 1 and not body.reads and r.body == b"" and r.retry_delay == 2


@pytest.mark.parametrize(
    "headers,expected",
    [
        ([("Content-Length", "101")], "body_limit"),
        ([("Content-Length", "no")], "invalid_headers"),
        ([("Content-Length", "-1")], "invalid_headers"),
        ([("Content-Length", "2"), ("content-length", "2")], "invalid_headers"),
        (
            [("Content-Length", "2"), ("Transfer-Encoding", "chunked")],
            "invalid_headers",
        ),
        ([("Content-Encoding", "br")], "unsupported_encoding"),
        (
            [("Content-Encoding", "gzip"), ("Content-Encoding", "identity")],
            "invalid_headers",
        ),
        ([(f"X-{i}", "x") for i in range(51)], "headers_limit"),
        ([("X-Long", "x" * 32769)], "headers_limit"),
    ],
)
def test_header_bounds_reject_before_body_read(headers, expected):
    """Verify header bounds reject before body read."""
    b = Body([b"{}"], headers)
    with pytest.raises(wire.TransportFailure, match=expected):
        wire.read_body(b, deadline=30, cap=100, monotonic=lambda: 0)
    assert not b.reads


@pytest.mark.parametrize(
    "headers", [[], [("Content-Length", "1")], [("Content-Length", "99")]]
)
def test_misleading_or_missing_length_cannot_bypass_counting(headers):
    """Verify misleading or missing length cannot bypass counting."""
    b = Body([b"x" * 101], headers)
    with pytest.raises(wire.TransportFailure, match="body_limit"):
        wire.read_body(b, deadline=30, cap=100, monotonic=lambda: 0)
    assert all(n <= 101 for n in b.reads)


def test_short_body_declared_length_fails():
    """Verify short body declared length fails."""
    with pytest.raises(wire.TransportFailure, match="invalid_length"):
        wire.read_body(
            Body([b"{}"], [("Content-Length", "9")]),
            deadline=30,
            cap=100,
            monotonic=lambda: 0,
        )


def test_gzip_decoded_cap_is_enforced_without_unbounded_flush():
    """Verify gzip decoded cap is enforced without unbounded flush."""
    encoded = gzip.compress(b"x" * 2000000)
    assert len(encoded) < 1048576
    b = Body(
        [encoded], [("Content-Encoding", "gzip"), ("Content-Length", str(len(encoded)))]
    )
    with pytest.raises(wire.TransportFailure, match="body_limit"):
        wire.read_body(b, deadline=30, cap=1048576, monotonic=lambda: 0)


@pytest.mark.parametrize(
    "encoded",
    [
        b"invalid gzip",
        gzip.compress(b"{}")[:-2],
        gzip.compress(b"{}") + b"extra",
        gzip.compress(b"{}") + gzip.compress(b"{}"),
    ],
)
def test_invalid_truncated_or_concatenated_compression_fails(encoded):
    """Verify invalid truncated or concatenated compression fails."""
    with pytest.raises(wire.TransportFailure, match="invalid_compression"):
        wire.read_body(
            Body([encoded], [("Content-Encoding", "gzip")]),
            deadline=30,
            cap=1000,
            monotonic=lambda: 0,
        )


@pytest.mark.parametrize("compressed", [False, True])
def test_slow_drip_deadline_checked_during_identity_and_gzip_reads(compressed):
    """Verify slow drip deadline checked during identity and gzip reads."""
    clock = Clock()
    body = gzip.compress(b"{}") if compressed else b"xxxxxxxxxx"
    chunks = [bytes([b]) for b in body]
    b = Body(
        chunks,
        [("Content-Encoding", "gzip")] if compressed else [],
        clock=clock,
        drip=0.25,
    )
    with pytest.raises(wire.TransportFailure, match="deadline_exceeded"):
        wire.read_body(b, deadline=1, cap=100, monotonic=clock.monotonic)
    assert clock.seconds == 1 and len(b.reads) == 4


def test_exact_decoded_boundary_and_hashes():
    """Verify exact decoded boundary and hashes."""
    body = b"x" * 100
    for encoding in ("identity", "gzip"):
        encoded = gzip.compress(body) if encoding == "gzip" else body
        b = Body(
            [encoded],
            [("Content-Encoding", encoding), ("Content-Length", str(len(encoded)))],
        )
        decoded, digest, result_encoding = wire.read_body(
            b, deadline=30, cap=100, monotonic=lambda: 0
        )
        assert (
            decoded == body
            and digest == wire.hashlib.sha256(encoded).hexdigest()
            and encoding == result_encoding
        )


@pytest.mark.parametrize(
    "header,delay,invalid",
    [
        (None, None, False),
        ("0", 0, False),
        ("2", 2, False),
        ("9999999999", 31, False),
        ("Sun, 25 Oct 2026 00:00:10 GMT", 10, False),
        ("Sun, 25 Oct 2026 00:00:00 GMT", 0, False),
        ("-1", None, True),
        ("private@secret.invalid", None, True),
        ("x" * 129, None, True),
    ],
)
def test_retry_after_is_bounded_numeric_not_disclosed(header, delay, invalid):
    """Verify retry after is bounded numeric not disclosed."""
    assert wire.retry_after(header, datetime(2026, 10, 25, tzinfo=timezone.utc)) == (
        delay,
        invalid,
    )


class FakeProcess:
    def __init__(self, output=None, timeout=False):
        """Initialize fake worker pipes, output and timeout behavior."""
        self.output = output
        self.timeout = timeout
        self.returncode = None
        self.stdin, self.stdout = io.BytesIO(), io.BytesIO()
        self.calls = []

    def communicate(self, message, timeout):
        """Record the private request and return mock output or a simulated timeout."""
        self.calls.append(("communicate", json.loads(message), timeout))
        if self.timeout:
            raise subprocess.TimeoutExpired("private-command", timeout)
        self.returncode = 0
        return self.output, None

    def poll(self):
        """Return the simulated child exit status."""
        return self.returncode

    def terminate(self):
        """Record simulated termination and mark the mock child exited."""
        self.calls.append(("terminate",))
        self.returncode = -15

    def kill(self):
        """Record simulated forced termination and mark the mock child exited."""
        self.calls.append(("kill",))
        self.returncode = -9

    def wait(self, timeout):
        """Simulate the child wait behavior required by this cleanup test."""
        self.calls.append(("wait", timeout))
        return self.returncode


def worker_reply(**changes):
    """Encode a synthetic successful worker reply with optional malformed-field overrides."""
    body = {
        "status": 200,
        "body": base64.b64encode(b"{}").decode(),
        "wire_sha256": wire.hashlib.sha256(b"{}").hexdigest(),
        "encoding": "identity",
        "retry_delay": None,
        "retry_invalid": False,
    }
    body.update(changes)
    return json.dumps(body).encode()


def test_process_ignores_hostile_environment_and_bounds_time(monkeypatch):
    """Verify process ignores hostile environment and bounds time."""
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "SSLKEYLOGFILE",
        "PYTHONPATH",
        "PYTHONSTARTUP",
    ):
        monkeypatch.setenv(key, "private-hostile-value")
    fake = FakeProcess(worker_reply())
    calls = []

    def start(argv, **kw):
        """Construct or return a fake child while recording process startup inputs."""
        calls.append((argv, kw))
        return fake

    monkeypatch.setattr(wire.subprocess, "Popen", start)
    r = wire.ProcessTransport(enabled=True, user_agent=UA).fetch(
        "0000000123", deadline=30, timeout=5, cap=100, monotonic=lambda: 0
    )
    assert r.body == b"{}"
    argv, kw = calls[0]
    assert "-I" in argv and "-B" in argv and kw["stderr"] == subprocess.DEVNULL
    assert kw["env"] == {"PATH": wire.os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
    _, request, budget = fake.calls[0]
    assert (
        budget == 5
        and request["deadline"] == 5
        and request["timeout"] == 5
        and request["enabled"] is True
    )
    assert (
        request["cik"] == "0000000123"
        and "url" not in request
        and "headers" not in request
    )
    assert fake.stdin.closed and fake.stdout.closed


def test_blocked_dns_or_slow_read_is_terminated_by_parent_deadline(monkeypatch):
    """Verify blocked dns or slow read is terminated by parent deadline."""
    fake = FakeProcess(timeout=True)
    monkeypatch.setattr(wire.subprocess, "Popen", lambda *a, **kw: fake)
    with pytest.raises(wire.TransportFailure, match="^transport_timeout$"):
        wire.ProcessTransport(enabled=True, user_agent=UA).fetch(
            "0000000123", deadline=30, timeout=5, cap=100, monotonic=lambda: 0
        )
    assert ("terminate",) in fake.calls and ("wait", 0.2) in fake.calls
    assert fake.stdin.closed and fake.stdout.closed


def test_worker_kill_fallback_and_no_start_after_budget(monkeypatch):
    """Verify worker kill fallback and no start after budget."""

    class Stuck(FakeProcess):
        def wait(self, timeout):
            """Simulate the child wait behavior required by this cleanup test."""
            self.calls.append(("wait", timeout))
            if ("kill",) not in self.calls:
                raise subprocess.TimeoutExpired("private", timeout)
            return -9

    fake = Stuck(timeout=True)
    called = []
    monkeypatch.setattr(
        wire.subprocess, "Popen", lambda *a, **kw: called.append(1) or fake
    )
    with pytest.raises(wire.TransportFailure, match="transport_timeout"):
        wire.ProcessTransport(enabled=True, user_agent=UA).fetch(
            "0000000123", deadline=30, timeout=5, cap=100, monotonic=lambda: 0
        )
    assert ("kill",) in fake.calls
    with pytest.raises(wire.TransportFailure, match="deadline_exceeded"):
        wire.ProcessTransport(enabled=True, user_agent=UA).fetch(
            "0000000123", deadline=30, timeout=5, cap=100, monotonic=lambda: 29
        )
    assert len(called) == 1


@pytest.mark.parametrize(
    "output",
    [
        b"not json",
        b"[]",
        b"null",
        worker_reply(body="notbase64"),
        worker_reply(status="200"),
        worker_reply(retry_delay=float("nan")),
        b"x" * (wire.IPC_CAP + 1),
        b'{"error":"private header/secret"}',
    ],
)
def test_malformed_worker_output_sanitized(monkeypatch, output):
    """Verify malformed worker output sanitized."""
    monkeypatch.setattr(wire.subprocess, "Popen", lambda *a, **kw: FakeProcess(output))
    with pytest.raises(wire.TransportFailure, match="^worker_failed$"):
        wire.ProcessTransport(enabled=True, user_agent=UA).fetch(
            "0000000123", deadline=30, timeout=5, cap=100, monotonic=lambda: 0
        )


def test_worker_rejects_disabled_protocol_without_connection(monkeypatch):
    """Verify worker rejects disabled protocol without connection."""
    monkeypatch.setattr(
        wire.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"enabled":false}'))
    )
    output = io.StringIO()
    monkeypatch.setattr(wire.sys, "stdout", output)
    monkeypatch.setattr(
        wire,
        "https_attempt",
        lambda *a, **kw: pytest.fail("worker connected without opt-in"),
    )
    wire._main()
    assert json.loads(output.getvalue()) == {"error": "transport_error"}


def test_unreapable_worker_blocks_further_dispatch_and_closes_pipes(monkeypatch):
    """Verify unreapable worker blocks further dispatch and closes pipes."""

    class Unreapable(FakeProcess):
        def wait(self, timeout):
            """Simulate the child wait behavior required by this cleanup test."""
            raise subprocess.TimeoutExpired("private-command", timeout)

    fake = Unreapable(timeout=True)
    monkeypatch.setattr(wire.subprocess, "Popen", lambda *a, **kw: fake)
    with pytest.raises(wire.TransportFailure, match="worker_shutdown_failed"):
        wire.ProcessTransport(enabled=True, user_agent=UA).fetch(
            "0000000123", deadline=30, timeout=5, cap=100, monotonic=lambda: 0
        )
    assert fake.stdin.closed and fake.stdout.closed


def test_no_transport_process_spawn_on_disabled_or_invalid_identity(monkeypatch):
    """Verify no transport process spawn on disabled or invalid identity."""
    monkeypatch.setattr(
        wire.subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("spawn before validation"),
    )
    with pytest.raises(wire.TransportFailure, match="network_disabled"):
        wire.ProcessTransport().fetch("0000000123", deadline=30, timeout=5, cap=100)
    with pytest.raises(wire.TransportFailure, match="invalid_destination"):
        wire.ProcessTransport(enabled=True, user_agent=UA).fetch(
            "https://evil.invalid", deadline=30, timeout=5, cap=100
        )


def test_parent_deadline_includes_process_startup_and_elapsed_attempt(monkeypatch):
    """Verify parent deadline includes process startup and elapsed attempt."""
    clock = Clock()
    fake = FakeProcess(worker_reply())

    def start(*a, **kw):
        """Construct or return a fake child while recording process startup inputs."""
        clock.seconds = 6
        return fake

    monkeypatch.setattr(wire.subprocess, "Popen", start)
    with pytest.raises(wire.TransportFailure, match="transport_timeout"):
        wire.ProcessTransport(enabled=True, user_agent=UA).fetch(
            "0000000123", deadline=30, timeout=5, cap=100, monotonic=clock.monotonic
        )
    assert ("terminate",) in fake.calls


@pytest.mark.parametrize(
    "existing", ["directory", "file", "both", "neither", "windows", "none"]
)
def test_missing_trust_paths_fail_closed_before_connection(
    monkeypatch, tmp_path, existing
):
    """Verify missing trust paths fail closed before connection."""
    cafile = tmp_path / "ca.pem"
    capath = tmp_path / "certs"
    if existing in ("file", "both"):
        cafile.touch()
    if existing in ("directory", "both"):
        capath.mkdir()
    monkeypatch.setattr(
        wire.ssl,
        "get_default_verify_paths",
        lambda: SimpleNamespace(
            openssl_cafile=None if existing == "none" else str(cafile),
            openssl_capath=None if existing == "none" else str(capath),
        ),
    )
    monkeypatch.setattr(
        wire.sys, "platform", "win32" if existing == "windows" else "linux"
    )
    calls = []

    class Context:
        verify_mode = None
        check_hostname = False

        def load_verify_locations(self, **kw):
            """Record explicitly selected CA paths without loading certificates."""
            calls.append(kw)

        def load_default_certs(self, purpose):
            """Record Windows system-store selection without consulting host trust roots."""
            calls.append(purpose)

    context = Context()
    monkeypatch.setattr(wire.ssl, "SSLContext", lambda protocol: context)

    def connection(*args, **kwargs):
        """Assert verified TLS setup before intercepting connection construction."""
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        raise RuntimeError("connection reached")

    monkeypatch.setattr(wire.http.client, "HTTPSConnection", connection)
    error = wire.TransportFailure if existing in ("neither", "none") else RuntimeError
    message = (
        "^trust_store_unavailable$"
        if error is wire.TransportFailure
        else "^connection reached$"
    )
    with pytest.raises(error, match=message):
        wire.https_attempt(
            "0000000123", UA, deadline=30, timeout=5, cap=100, monotonic=lambda: 0
        )
    if existing in ("neither", "none"):
        assert calls == []
    elif existing == "windows":
        assert calls == [ssl.Purpose.SERVER_AUTH]
    else:
        assert calls == [
            {
                "cafile": str(cafile) if existing in ("file", "both") else None,
                "capath": str(capath) if existing in ("directory", "both") else None,
            }
        ]


def test_worker_preserves_fixed_trust_store_error(monkeypatch):
    """Verify worker preserves fixed trust store error."""
    fake = FakeProcess(b'{"error":"trust_store_unavailable"}')
    monkeypatch.setattr(wire.subprocess, "Popen", lambda *a, **kw: fake)
    with pytest.raises(wire.TransportFailure, match="^trust_store_unavailable$"):
        wire.ProcessTransport(enabled=True, user_agent=UA).fetch(
            "0000000123", deadline=30, timeout=5, cap=100, monotonic=lambda: 0
        )
