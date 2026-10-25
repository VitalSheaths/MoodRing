"""Manual bounded SEC metadata CLI. No requests by default.

--offline-demo uses only committed synthetic bytes and a mocked transport.
--allow-network is for a later separately approved exact-CIK/operator smoke only.
"""
import argparse
import json
import os
from pathlib import Path
import sys

from ingestion.sec.access import AccessError, fetch_metadata
from ingestion.sec.contracts import UniverseManifest, payload_digest
from ingestion.sec.transport import Response
from ingestion.universe.identity import SicTaxonomy

ROOT = Path(__file__).resolve().parents[1]


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default can echo contact details, arbitrary arguments or paths.
        raise AccessError("invalid_cli_arguments")


def read_input(path):
    with Path(path).open("rb") as stream:
        body = stream.read(1048577)
    if len(body) > 1048576:
        raise AccessError("input_limit")
    return body


def main(argv=None):
    parser = SafeParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline-demo", action="store_true")
    mode.add_argument("--allow-network", action="store_true")
    parser.add_argument("--cik", action="append")
    parser.add_argument(
        "--one-request",
        action="store_true",
        help="Exactly one CIK and no retry; still requires separate live smoke approval",
    )
    parser.add_argument("--universe")
    parser.add_argument("--taxonomy")
    try:
        args = parser.parse_args(argv)
        if not args.offline_demo and not args.allow_network:
            raise AccessError("network_disabled")
        if args.offline_demo:
            if args.cik or args.universe or args.taxonomy or args.one_request:
                raise AccessError("invalid_cli_arguments")
            fixture = ROOT / "tests/fixtures/sec"
            body = read_input(fixture / "access/metadata.json")
            universe = UniverseManifest.model_validate_json(
                read_input(fixture / "universe.json")
            )
            taxonomy = SicTaxonomy.model_validate_json(
                read_input(fixture / "access/taxonomy.json")
            )

            class MockTransport:
                def fetch(self, identifier, **kwargs):
                    return Response(200, body, payload_digest(body))

            result = fetch_metadata(
                ["123"],
                universe,
                taxonomy,
                allow_network=True,
                user_agent="SyntheticLesson fixture@example.invalid",
                transport=MockTransport(),
            )
        else:
            # Validate operator/CIKs before opening caller paths or constructing transport.
            from ingestion.sec.contracts import cik
            from ingestion.sec.transport import identified

            user_agent = identified(os.getenv("SEC_USER_AGENT"))
            if (
                not args.cik
                or len(args.cik) > 2
                or len({cik(c) for c in args.cik}) != len(args.cik)
                or not args.universe
                or not args.taxonomy
            ):
                raise AccessError("invalid_cli_arguments")
            if args.one_request and len(args.cik) != 1:
                raise AccessError("invalid_cli_arguments")
            universe = UniverseManifest.model_validate_json(read_input(args.universe))
            taxonomy = SicTaxonomy.model_validate_json(read_input(args.taxonomy))
            result = fetch_metadata(
                args.cik,
                universe,
                taxonomy,
                allow_network=True,
                user_agent=user_agent,
                limits={"max_ciks": 1, "max_attempts": 1, "retries_per_cik": 0}
                if args.one_request
                else None,
            )
        summary = result.public_summary()
        print(json.dumps(summary, sort_keys=True))
        return (
            1 if any(r.status in ("failed", "deferred") for r in result.issuers) else 0
        )
    except AccessError as exc:
        print(json.dumps({"status": "blocked", "code": exc.code}))
        return 2
    except Exception:
        print(
            json.dumps({"status": "blocked", "code": "invalid_configuration_or_input"})
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
