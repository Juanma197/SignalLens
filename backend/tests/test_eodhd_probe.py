from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.eodhd_probe import EODHDCapabilityProbe, ProbeLimits
from app.eodhd_probe_cli import build_parser, execute


FIXTURES = Path(__file__).with_name("fixtures") / "eodhd"
TOKEN = "fixture-secret-never-recorded"


def fixture_transport(statuses: dict[str, int] | None = None) -> httpx.MockTransport:
    statuses = statuses or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("api_token") != TOKEN:
            return httpx.Response(401)
        endpoint = request.url.path.rsplit("/", 2)[-2:]
        name = endpoint[0]
        status = statuses.get(name, 200)
        files = {
            "eod": "eod_aapl_us.json", "exchange-symbol-list": "us_symbols.json",
            "exchanges-list": "exchanges.json",
            "splits": "splits_aapl_us.json", "div": "div_aapl_us.json",
        }
        return httpx.Response(status, json=json.loads((FIXTURES / files[name]).read_text()))

    return httpx.MockTransport(handler)


def test_recorded_fixture_probe_validates_metadata_prices_actions_and_depth(tmp_path: Path) -> None:
    production = tmp_path / "production.duckdb"
    research = tmp_path / "research.duckdb"
    production.write_bytes(b"production bytes")
    research.write_bytes(b"research bytes")
    probe = EODHDCapabilityProbe(
        TOKEN, limits=ProbeLimits(rate_limit_seconds=0), transport=fixture_transport(), sleep=lambda _: None,
    )
    result = probe.run(database_paths=[production, research], include_actions=True)
    prices, exchanges, metadata, splits, dividends = result["endpoints"]
    assert result["status"] == "usable" and result["request_count"] == 5
    assert prices["classification"] == "available" and prices["valid_rows"] == 2
    assert prices["historical_depth"]["earliest"] == "2026-09-21"
    assert "adjusted_close" in prices["usable_fields"]
    assert exchanges["classification"] == "available" and exchanges["records_returned"] == 2
    assert exchanges["usable_fields"] == ["Code", "Country", "Currency", "Name"]
    assert metadata["metadata_valid"] is True and metadata["available_exchanges"] == ["NASDAQ"]
    assert splits["valid_rows"] == dividends["valid_rows"] == 1
    assert result["database_immutability"]["verified"] is True
    assert production.read_bytes() == b"production bytes" and research.read_bytes() == b"research bytes"


@pytest.mark.parametrize(("status", "classification"), [
    (401, "unauthorized"), (403, "restricted"), (429, "rate_limited"), (404, "unsupported"),
])
def test_endpoint_status_classification_is_explicit(status: int, classification: str, tmp_path: Path) -> None:
    probe = EODHDCapabilityProbe(TOKEN, limits=ProbeLimits(max_attempts=1, rate_limit_seconds=0),
                                 transport=fixture_transport({"eod": status}), sleep=lambda _: None)
    result = probe.run(database_paths=[tmp_path / "absent-production", tmp_path / "absent-research"])
    assert result["endpoints"][0]["classification"] == classification
    assert result["status"] == "unusable"
    assert result["database_immutability"]["verified"] is True
    assert not (tmp_path / "absent-production").exists() and not (tmp_path / "absent-research").exists()


def test_request_budget_is_hard_bounded_even_with_retries(tmp_path: Path) -> None:
    probe = EODHDCapabilityProbe(TOKEN, limits=ProbeLimits(max_requests=2, max_attempts=2, rate_limit_seconds=0),
                                 transport=fixture_transport({"eod": 429}), sleep=lambda _: None)
    result = probe.run(database_paths=[tmp_path / "one", tmp_path / "two"], include_actions=True)
    assert result["request_count"] == result["request_limit"] == 2
    assert all(item["classification"] in {"available", "restricted", "unauthorized", "rate_limited", "unsupported", "response_too_large"}
               for item in result["endpoints"])


def test_metadata_can_exceed_default_limit_but_retains_separate_ceiling(tmp_path: Path) -> None:
    body = json.dumps([{"Code": "US", "Name": "United States", "Country": "USA",
                        "Currency": "USD", "padding": "x" * 6000}]).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if "exchanges-list" in request.url.path:
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        return fixture_transport().handle_request(request)

    probe = EODHDCapabilityProbe(TOKEN, limits=ProbeLimits(
        max_response_bytes=5_000, metadata_max_response_bytes=10_000, rate_limit_seconds=0,
    ), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    result = probe.run(database_paths=[tmp_path / "one", tmp_path / "two"])
    assert result["endpoints"][1]["classification"] == "available"


def test_metadata_over_limit_has_distinct_sanitized_classification(tmp_path: Path) -> None:
    body = b"[" + b" " * 6000 + b"]"

    def handler(request: httpx.Request) -> httpx.Response:
        if "exchanges-list" in request.url.path:
            return httpx.Response(200, content=body)
        return fixture_transport().handle_request(request)

    probe = EODHDCapabilityProbe(TOKEN, limits=ProbeLimits(
        metadata_max_response_bytes=5_000, rate_limit_seconds=0,
    ), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    result = probe.run(database_paths=[tmp_path / "one", tmp_path / "two"])
    endpoint = result["endpoints"][1]
    assert endpoint["classification"] == "response_too_large"
    assert endpoint["records_returned"] == 0 and TOKEN not in json.dumps(endpoint)


def test_metadata_limit_has_conservative_hard_maximum() -> None:
    with pytest.raises(ValueError, match="metadata_max_response_bytes"):
        ProbeLimits(metadata_max_response_bytes=16 * 1024 * 1024 + 1)


@pytest.mark.parametrize("payload", [{"not": "a list"}, [{"Code": "US"}], ["invalid"]])
def test_malformed_exchange_list_is_unsupported(payload: object, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "exchanges-list" in request.url.path:
            return httpx.Response(200, json=payload)
        return fixture_transport().handle_request(request)

    probe = EODHDCapabilityProbe(TOKEN, limits=ProbeLimits(rate_limit_seconds=0),
                                 transport=httpx.MockTransport(handler), sleep=lambda _: None)
    endpoint = probe.run(database_paths=[tmp_path / "one", tmp_path / "two"])["endpoints"][1]
    assert endpoint["classification"] == "unsupported" and endpoint["valid_records"] == 0


def test_token_is_absent_from_report_and_cli_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    probe = EODHDCapabilityProbe(TOKEN, limits=ProbeLimits(rate_limit_seconds=0),
                                 transport=fixture_transport(), sleep=lambda _: None)
    report = probe.run(database_paths=[tmp_path / "one", tmp_path / "two"])
    assert TOKEN not in json.dumps(report)
    monkeypatch.setenv("SIGNALLENS_EODHD_API_TOKEN", TOKEN)
    args = build_parser().parse_args(["probe", "--max-requests", "0"])
    with pytest.raises(ValueError):
        execute(args)
    assert TOKEN not in capsys.readouterr().out + capsys.readouterr().err


def test_fixture_files_are_sanitized() -> None:
    combined = b"".join(path.read_bytes() for path in FIXTURES.iterdir())
    assert b"api_token" not in combined and TOKEN.encode() not in combined
