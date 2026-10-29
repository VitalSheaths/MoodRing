"""Issue #22 executable merge gate. All SEC transport is mocked/offline."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import socket

import pytest

from ingestion.sec.access import AccessError, fetch_metadata
from ingestion.sec.contracts import payload_digest
from ingestion.sec.normalization import eligible
from ingestion.sec.transport import ProcessTransport, Response, TransportFailure
from sec_access_support import Clock, MockTransport, UA, inputs, payload, response


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Reject real socket operations so these controls remain entirely offline."""

    def forbidden(*a, **kw):
        """Fail immediately if the test reaches a forbidden network boundary."""
        raise AssertionError("No real SEC network access in tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def run(values=None, ciks=None, **kwargs):
    """Execute the access flow with synthetic responses, fixture inputs and a fake clock."""
    universe, taxonomy = inputs()
    clock = kwargs.pop("timer", Clock())
    transport = MockTransport(clock, [response()] if values is None else values)
    result = fetch_metadata(
        ["123"] if ciks is None else ciks,
        universe,
        taxonomy,
        allow_network=True,
        user_agent=UA,
        transport=transport,
        monotonic=clock.monotonic,
        clock=clock.now,
        sleep=clock.sleep,
        **kwargs
    )
    return result, transport, clock


def context():
    """Build reviewed synthetic publication and amendment evidence for normalization tests."""
    return {
        "0000000123": {
            "availability": {
                "0000000999-26-000001": {
                    "available_at": "2026-10-01T15:00:00Z",
                    "basis": "sec_publication",
                    "evidence": "synthetic:reviewed-publication",
                }
            }
        }
    }


def test_network_is_off_by_default_even_with_injected_transport(monkeypatch):
    """Verify network is off by default even with injected transport."""
    u, t = inputs()

    class Forbidden:
        def fetch(self, *a, **kw):
            """Return the next synthetic response without network access."""
            pytest.fail("transport constructed/called before permission")

    for value in (False, None, 1, "true"):
        with pytest.raises(AccessError, match="^network_disabled$"):
            fetch_metadata(
                ["123"], u, t, allow_network=value, user_agent=UA, transport=Forbidden()
            )
    with pytest.raises(TransportFailure, match="network_disabled"):
        ProcessTransport().fetch("0000000123", deadline=30, timeout=5, cap=1024)


@pytest.mark.parametrize(
    "ua",
    [
        None,
        "",
        "anonymous",
        "x a@b.com",
        "Agent test@example.invalid\r\nX-Header: secret",
        "Agent https://evil.invalid",
        "Agent ü@example.invalid",
    ],
)
def test_operator_required_before_dispatch(ua):
    """Verify operator required before dispatch."""
    u, t = inputs()
    with pytest.raises(AccessError, match="invalid_configuration"):
        fetch_metadata(["123"], u, t, allow_network=True, user_agent=ua)


@pytest.mark.parametrize(
    "ciks",
    [
        [],
        ["123", "456", "789"],
        ["123", "0000000123"],
        ["0"],
        [123],
        [True],
        ["１２３"],
        ["../123"],
        ["https://evil.invalid"],
        ["123?x=secret"],
        ["123:443"],
        [" 123"],
        ["123/anything"],
        ["00000000000"],
    ],
)
def test_invalid_destinations_and_list_bounds(ciks):
    """Verify invalid destinations and list bounds."""
    with pytest.raises(AccessError, match="invalid_configuration"):
        run(ciks=ciks)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_ciks": 3},
        {"max_attempts": 5},
        {"retries_per_cik": 2},
        {"timeout_s": 6},
        {"deadline_s": 31},
        {"body_bytes": 1048577},
        {"rows": 1001},
        {"result_bytes": 4194305},
        {"timeout_s": True},
        {"max_ciks": 1.0},
        {"arbitrary_url": "secret"},
    ],
)
def test_limits_cannot_be_raised_or_coerced(limits):
    """Verify limits cannot be raised or coerced."""
    with pytest.raises(AccessError, match="invalid_configuration"):
        run(limits=limits)


def test_missing_publication_is_ineligible_and_current_sic_is_not_history():
    """Verify missing publication is ineligible and current sic is not history."""
    result, _, _ = run()
    item = result.issuers[0]
    assert item.status == "completed_with_gaps"
    assert item.sic.code == "7372" and item.sic.status == "observed"
    assert not hasattr(item.sic, "effective_from")
    assert not hasattr(item.sic, "available_at")
    assert not eligible(item.normalized, item.receipt.received_at)
    summary = item.public_summary()
    assert (
        not summary["complete_history"]
        and summary["history_scope"] == "root_recent_only"
    )
    assert summary["metadata"]["counts"]["quarantined"] == 1


def test_receipt_hash_binding_accepted_context_and_deterministic_replay():
    """Verify receipt hash binding accepted context and deterministic replay."""
    body = payload(ignored_source="retained-in-hash")
    a, transport, _ = run([response(body)], contexts=context())
    b, _, _ = run([response(body)], contexts=context())
    item = a.issuers[0]
    assert a == b and item.status == "completed_subset"
    assert item.receipt.raw_sha256 == payload_digest(body) == item.normalized.raw_sha256
    assert (
        item.normalized.rows[0].envelope.filing.provenance.payload_sha256
        == item.receipt.raw_sha256
    )
    assert eligible(
        item.normalized, item.receipt.received_at, require_local_receipt=True
    )
    assert len(transport.calls) == 1  # linked files and primaryDocument not visited
    changed, _, _ = run(
        [response(payload(ignored_source="changed"))], contexts=context()
    )
    assert changed.issuers[0].receipt.binding_sha256 != item.receipt.binding_sha256
    assert (
        changed.issuers[0].normalized.binding_sha256 != item.normalized.binding_sha256
    )


@pytest.mark.parametrize(
    "change,status",
    [
        (None, "missing"),
        ("", "missing"),
        ("0000", "unsupported"),
        ("9999", "unsupported"),
        (7372, "invalid"),
        ("٧٣٧٢", "invalid"),
        (True, "invalid"),
        ("7372\nsecret", "invalid"),
    ],
)
def test_sic_missing_invalid_unknown_never_fabricated(change, status):
    """Verify sic missing invalid unknown never fabricated."""
    r, _, _ = run([response(payload(sic=change))])
    assert r.issuers[0].sic.status == status
    assert r.issuers[0].sic.code is None
    assert r.issuers[0].status == "completed_with_gaps"


@pytest.mark.parametrize(
    "body",
    [
        b'{"cik":"123","cik":"456"}',
        b"{",
        b"\xff",
        b'{"cik":"123","x":NaN}',
        b'{"cik":"123","x":Infinity}',
        b'{"cik":"123","x":1e999}',
        payload(cik="456"),
        payload(cik=True),
        payload(cik="../secret"),
        payload(cik=None),
    ],
)
def test_structural_and_issuer_failures_are_sanitized(body):
    """Verify structural and issuer failures are sanitized."""
    r, client, _ = run([response(body)])
    item = r.issuers[0]
    assert item.status == "failed" and item.normalized is None
    assert len(client.calls) == 1
    assert item.code in ("invalid_metadata", "wrong_issuer")
    assert "secret" not in json.dumps(r.public_summary())


def test_misaligned_arrays_and_excessive_rows():
    """Verify misaligned arrays and excessive rows."""
    data = json.loads(payload())
    data["filings"]["recent"]["form"].append("10-Q")
    r, _, _ = run([response(json.dumps(data).encode())])
    assert r.issuers[0].code == "invalid_metadata"
    data = json.loads(payload())
    for key in data["filings"]["recent"]:
        data["filings"]["recent"][key] *= 1001
    r, _, _ = run([response(json.dumps(data).encode())])
    assert r.issuers[0].code == "invalid_metadata"


def test_invalid_offset_and_amendment_do_not_infer_evidence():
    """Verify invalid offset and amendment do not infer evidence."""
    data = json.loads(payload())
    data["filings"]["recent"]["acceptanceDateTime"] = ["2026-10-01T14:00:00"]
    r, _, _ = run([response(json.dumps(data).encode())], contexts=context())
    assert r.issuers[0].normalized.public_summary()["counts"]["quarantined"] == 1
    data["filings"]["recent"]["form"] = ["10-K/A"]
    r, _, _ = run([response(json.dumps(data).encode())])
    assert r.issuers[0].status == "deferred"
    assert r.issuers[0].normalized.checkpoint.next_line == 0


def test_partial_failure_visible_for_each_issuer():
    """Verify partial failure visible for each issuer."""
    r, client, _ = run(
        [Response(403), response(payload(cik="456"))], ciks=["123", "456"]
    )
    assert [i.status for i in r.issuers] == ["failed", "completed_with_gaps"]
    assert [i.ordinal for i in r.issuers] == [1, 2]
    assert [i.attempts for i in r.issuers] == [1, 1]
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "status,code",
    [
        (301, "redirect_rejected"),
        (302, "redirect_rejected"),
        (307, "redirect_rejected"),
        (308, "redirect_rejected"),
        (403, "access_denied"),
        (404, "not_found"),
        (400, "http_rejected"),
    ],
)
def test_nonretryable_http_failures(status, code):
    """Verify nonretryable http failures."""
    r, t, _ = run([Response(status, body=b"PRIVATE BODY")])
    assert r.issuers[0].code == code and r.issuers[0].attempts == 1
    assert "PRIVATE" not in repr(r) and "PRIVATE" not in json.dumps(r.public_summary())


@pytest.mark.parametrize(
    "first",
    [
        Response(429),
        Response(500),
        Response(502),
        Response(503),
        Response(504),
        TransportFailure("transport_timeout"),
    ],
)
def test_one_retry_per_issuer_pacing_and_five_second_limits(first):
    """Verify one retry per issuer pacing and five second limits."""
    r, t, clock = run([first, response()])
    assert r.issuers[0].attempts == 2
    assert [c[1] for c in t.calls] == [0, 1]
    assert t.max_active == 1
    assert all(
        c[2]["timeout"] == 5 and c[2]["cap"] == 1048576 and c[2]["deadline"] == 30
        for c in t.calls
    )


def test_four_total_attempts_at_most_two_issuers():
    """Verify four total attempts at most two issuers."""
    r, t, _ = run(
        [
            Response(503),
            response(),
            TransportFailure("transport_timeout"),
            response(payload(cik="456")),
        ],
        ciks=["123", "456"],
    )
    assert r.public_summary()["attempts"] == 4 and t.max_active == 1
    assert [c[1] for c in t.calls] == [0, 1, 2, 3]
    assert [i.attempts for i in r.issuers] == [2, 2]


def test_reduced_attempt_limits_no_hidden_retry():
    """Verify reduced attempt limits no hidden retry."""
    r, t, _ = run(
        [Response(503), Response(503)], ciks=["123", "456"], limits={"max_attempts": 1}
    )
    assert len(t.calls) == 1 and r.issuers[1].attempts == 0
    r, t, _ = run([Response(503)], limits={"retries_per_cik": 0})
    assert len(t.calls) == 1 and r.issuers[0].status == "deferred"


def test_retry_after_cannot_fit_does_not_sleep_or_retry():
    """Verify retry after cannot fit does not sleep or retry."""
    r, t, clock = run([Response(429, retry_delay=31)])
    assert r.issuers[0].code == "retry_after_budget"
    assert len(t.calls) == 1 and not clock.sleeps
    r, t, _ = run([Response(429, retry_invalid=True)])
    assert r.issuers[0].code == "retry_after_invalid" and len(t.calls) == 1


def test_retry_sleep_and_overall_deadline_cannot_be_extended():
    """Verify retry sleep and overall deadline cannot be extended."""
    clock = Clock()

    def slow():
        """Advance the fake clock to exercise timeout accounting."""
        clock.seconds = 30
        return response()

    r, t, _ = run([slow], ciks=["123", "456"], timer=clock)
    assert r.issuers[0].code == "deadline_exceeded" and r.issuers[0].normalized is None
    assert r.issuers[1].attempts == 0 and len(t.calls) == 1
    clock = Clock()

    def oversleep(s):
        """Advance beyond the requested delay to exercise deadline rechecks."""
        clock.seconds += 31

    u, tx = inputs()
    tr = MockTransport(clock, [Response(429), response()])
    r = fetch_metadata(
        ["123"],
        u,
        tx,
        allow_network=True,
        user_agent=UA,
        transport=tr,
        monotonic=clock.monotonic,
        sleep=oversleep,
        clock=clock.now,
    )
    assert len(tr.calls) == 1 and r.issuers[0].status == "deferred"


def test_unexpected_provider_error_and_shutdown_stop_are_safe():
    """Verify unexpected provider error and shutdown stop are safe."""
    r, t, _ = run(
        [RuntimeError("contact@secret.invalid Authorization: abc /private/file")]
    )
    assert r.issuers[0].code == "transport_unavailable" and len(t.calls) == 1
    r, t, _ = run([TransportFailure("worker_shutdown_failed")], ciks=["123", "456"])
    assert len(t.calls) == 1 and r.issuers[1].attempts == 0


def test_body_and_result_limits_fail_closed_at_adapter_boundary():
    """Verify body and result limits fail closed at adapter boundary."""
    r, _, _ = run([response(b"x" * 100)], limits={"body_bytes": 99})
    assert r.issuers[0].code == "body_limit"
    r, _, _ = run(limits={"result_bytes": 1})
    assert r.issuers[0].code == "invalid_metadata"
    r, _, _ = run(limits={"rows": 1})
    assert r.issuers[0].normalized.total_rows == 1


def test_no_durable_writes_or_disclosure(monkeypatch, caplog, capsys):
    """Verify no durable writes or disclosure."""
    import builtins

    u, t = inputs()
    clock = Clock()
    raw = payload(secret="CONTACT private@secret.invalid AUTH header /private/location")
    import os
    import sqlalchemy

    original_os_open = os.open

    def guarded_os_open(path, flags, *args, **kwargs):
        """Reject low-level write opens while allowing read-only fixture access."""
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            pytest.fail("adapter attempted a low-level durable write")
        return original_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(
        sqlalchemy,
        "create_engine",
        lambda *a, **kw: pytest.fail("database integration"),
    )
    original_open = builtins.open

    def guarded_open(file, mode="r", *a, **kw):
        """Reject file write modes while allowing read-only fixture access."""
        if any(c in mode for c in "wax+"):
            raise AssertionError("adapter attempted a durable write")
        return original_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", guarded_open)
    for method in ("write_text", "write_bytes", "mkdir", "touch"):
        monkeypatch.setattr(Path, method, lambda *a, **kw: pytest.fail("durable write"))
    tr = MockTransport(clock, [response(raw)])
    result = fetch_metadata(
        ["123"],
        u,
        t,
        allow_network=True,
        user_agent=UA,
        transport=tr,
        monotonic=clock.monotonic,
        clock=clock.now,
        sleep=clock.sleep,
    )
    summary = json.dumps(result.public_summary())
    assert all(
        secret not in summary + repr(result) + caplog.text + capsys.readouterr().out
        for secret in (
            "CONTACT",
            "secret.invalid",
            "AUTH",
            "/private/",
            "0000000123",
            UA,
        )
    )
    assert result.public_summary()["durable_writes"] == 0


def cli_module():
    """Load the manual CLI module for isolated offline invocation tests."""
    spec = importlib.util.spec_from_file_location(
        "sec_metadata_cli", Path(__file__).parents[1] / "examples/sec_metadata.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_default_off_mock_demo_and_sanitized_arguments(monkeypatch, capsys):
    """Verify cli default off mock demo and sanitized arguments."""
    cli = cli_module()
    monkeypatch.setenv("SEC_USER_AGENT", "secret@secret.invalid\nAuthorization: bad")
    assert cli.main([]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "network_disabled"
    assert cli.main(["--offline-demo"]) == 0
    output = capsys.readouterr().out
    assert "secret" not in output and json.loads(output)["durable_writes"] == 0
    assert cli.main(["--unknown-private-secret"]) == 2
    assert "unknown-private-secret" not in capsys.readouterr().out
    assert (
        cli.main(
            [
                "--allow-network",
                "--cik",
                "123",
                "--universe",
                "/private/secret",
                "--taxonomy",
                "/private/secret",
            ]
        )
        == 2
    )
    assert "/private/" not in capsys.readouterr().out


def test_unreviewed_issuer_and_future_taxonomy_fail_before_dispatch():
    """Verify unreviewed issuer and future taxonomy fail before dispatch."""
    with pytest.raises(AccessError, match="invalid_configuration"):
        run(ciks=["789"])
    u, t = inputs()
    t = t.model_copy(update={"captured_at": Clock().now() + timedelta(days=1)})
    with pytest.raises(AccessError, match="invalid_configuration"):
        fetch_metadata(
            ["123"], u, t, allow_network=True, user_agent=UA, clock=Clock().now
        )


def test_no_public_contact_body_header_stdout_or_stderr(caplog, capsys):
    """Verify no public contact body header stdout or stderr."""
    r, _, _ = run(
        [
            RuntimeError(
                "TOPSECRET Contact: private@example.invalid Authorization: bearer"
            )
        ]
    )
    output = capsys.readouterr()
    assert not output.out and not output.err and not caplog.text
    assert "TOPSECRET" not in repr(r) + json.dumps(r.public_summary())


def test_linked_source_paths_are_never_traversed():
    """Verify linked source paths are never traversed."""
    data = json.loads(payload())
    data["filings"]["files"] = [{"name": "https://evil.invalid?Authorization=private"}]
    data["filings"]["recent"]["primaryDocument"] = ["https://evil.invalid/private"]
    r, t, _ = run([response(json.dumps(data).encode())])
    assert len(t.calls) == 1 and t.calls[0][0] == "0000000123"
    assert r.issuers[0].normalized.public_summary()["counts"]["quarantined"] == 1
    assert "evil.invalid" not in json.dumps(r.public_summary())


def test_cli_one_request_policy_is_exact_and_mocked(monkeypatch, capsys):
    """Verify cli one request policy is exact and mocked."""
    from ingestion.sec.access import RunResult

    cli = cli_module()
    u, t = inputs()
    calls = []
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    monkeypatch.setattr(
        cli,
        "read_input",
        lambda path: u.model_dump_json().encode()
        if path == "universe"
        else t.model_dump_json().encode(),
    )
    monkeypatch.setattr(
        cli, "fetch_metadata", lambda *a, **kw: calls.append((a, kw)) or RunResult(())
    )
    args = [
        "--allow-network",
        "--one-request",
        "--cik",
        "123",
        "--universe",
        "universe",
        "--taxonomy",
        "taxonomy",
    ]
    assert cli.main(args) == 0
    assert calls[0][1]["limits"] == {
        "max_ciks": 1,
        "max_attempts": 1,
        "retries_per_cik": 0,
    }
    assert calls[0][0][0] == ["123"] and UA not in capsys.readouterr().out
    assert cli.main(args + ["--cik", "456"]) == 2 and len(calls) == 1


def test_cli_bad_cik_cannot_read_input_or_start_transport(monkeypatch, capsys):
    """Verify cli bad cik cannot read input or start transport."""
    cli = cli_module()
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    monkeypatch.setattr(
        cli, "read_input", lambda *a: pytest.fail("read before validation")
    )
    monkeypatch.setattr(
        cli,
        "fetch_metadata",
        lambda *a, **kw: pytest.fail("dispatch before validation"),
    )
    assert (
        cli.main(
            [
                "--allow-network",
                "--cik",
                "https://evil.invalid/private",
                "--universe",
                "private",
                "--taxonomy",
                "private",
            ]
        )
        == 2
    )
    assert "evil" not in capsys.readouterr().out


def test_pacing_from_completion_covers_variable_process_startup():
    """Verify pacing from completion covers variable process startup."""
    clock = Clock()

    def slow_response():
        """Simulate transport startup delay before returning a synthetic response."""
        clock.seconds += 0.75
        return response()

    r, t, _ = run(
        [slow_response, response(payload(cik="456"))], ciks=["123", "456"], timer=clock
    )
    assert [c[1] for c in t.calls] == [0, 1.75]
    r, t, clock = run([Response(429, retry_delay=29)])
    assert (
        r.issuers[0].code == "retry_after_budget"
        and len(t.calls) == 1
        and not clock.sleeps
    )


def test_trust_store_error_is_sanitized_and_not_retried():
    """Verify trust store error is sanitized and not retried."""
    result, transport, _ = run([TransportFailure("trust_store_unavailable")])
    assert "trust_store_unavailable" in str(result.public_summary())
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "reason",
    [
        "limit_rows",
        "invalid_schema",
        "misaligned_arrays",
        "invalid_json",
        "duplicate_json_key",
        "limit_result_bytes",
    ],
)
def test_allowlisted_validation_reason_survives_without_retry(monkeypatch, reason):
    """Preserve only approved reason codes while retaining failed, nonretryable outcomes."""
    from ingestion.sec import access
    from ingestion.sec.normalization import NormalizationError

    def reject(*args, **kwargs):
        """Simulate a fixed internal normalizer failure without provider content."""
        raise NormalizationError(reason)

    monkeypatch.setattr(access, "normalize", reject)
    result, transport, _ = run()
    summary = result.public_summary()["issuers"][0]
    assert summary["code"] == "invalid_metadata"
    assert summary["validation_reason"] == reason
    assert summary["status"] == "failed" and summary["metadata"] is None
    assert len(transport.calls) == 1
    assert result.public_summary()["durable_writes"] == 0


@pytest.mark.parametrize("failure", ["normalizer", "value"])
def test_unknown_validation_details_are_not_disclosed(
    monkeypatch, failure, caplog, capsys
):
    """Never expose exception text or unapproved internal codes through diagnostics."""
    from ingestion.sec import access
    from ingestion.sec.normalization import NormalizationError

    secret = "PRIVATE contact@example.invalid /private/path Authorization signed-url"

    def reject(*args, **kwargs):
        """Inject private exception content to verify the public disclosure boundary."""
        raise NormalizationError(secret) if failure == "normalizer" else ValueError(
            secret
        )

    monkeypatch.setattr(access, "normalize", reject)
    result, transport, _ = run()
    assert result.public_summary()["issuers"][0]["validation_reason"] is None
    assert secret not in json.dumps(result.public_summary()) + repr(
        result
    ) + caplog.text + str(capsys.readouterr())
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "body,reason",
    [
        (b"{", "invalid_json"),
        (b'{"cik":"123","cik":"123"}', "duplicate_json_key"),
        (b'{"cik":"123"}', "invalid_schema"),
    ],
)
def test_actual_parser_failures_have_fixed_diagnostics(body, reason):
    """Exercise decoder and schema failures with synthetic bytes only."""
    result, transport, _ = run([response(body)])
    assert result.public_summary()["issuers"][0]["validation_reason"] == reason
    assert len(transport.calls) == 1
