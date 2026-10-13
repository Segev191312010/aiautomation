"""The read-only scanner probe: client id, safety boundary, report and fixtures."""
from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import pytest

from tests.fakes.scanner_tws import (
    FIXTURE_DIR,
    FakeScannerTWS,
    load_scanner_fixtures,
    rows_by_scan,
    scanner_parameters_xml,
)
from trading.operations import scanner_probe
from trading.operations.scanner_probe import (
    ALLOWED_CLIENT_CALLS,
    CANDIDATE_SCAN_CODES,
    LINE_RUNS,
    LINE_SYMBOLS,
    PROBE_CLIENT_ID,
    SCAN_VARIANTS,
    ScannerProbeConfigError,
    ScannerProbeError,
    ScannerProbeSettings,
    child_client_ids,
    et_phase,
    fixtures_from_report,
    run_scanner_probe,
    summarize_scanner_parameters,
    write_fixtures,
)

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = BACKEND / "tests" / "fixtures"
# The account-id shapes no fixture, report or log line may carry.
ACCOUNT_PATTERNS = (re.compile(r"DU\d+"), re.compile(r"U\d{6,}"))
FAST = {
    "handshake_timeout_seconds": 2.0,
    "accounts_timeout_seconds": 0.3,
    "parameters_timeout_seconds": 2.0,
    "news_timeout_seconds": 2.0,
    "scan_timeout_seconds": 0.4,
    "line_timeout_seconds": 0.4,
    "scan_start_interval_seconds": 0.0,
    "line_start_interval_seconds": 0.0,
    "line_settle_seconds": 0.05,
    "line_run_pause_seconds": 0.0,
    "deadline_seconds": 30.0,
}
INLINE_ROWS = {
    (code, variant): [
        {"rank": rank, "symbol": symbol, "con_id": 1000 + rank, "exchange": "NASDAQ"}
        for rank, symbol in enumerate(("AAA", "BBB", "CCC"))
    ]
    for code in CANDIDATE_SCAN_CODES
    for variant in SCAN_VARIANTS
}


def _fixture_fake_options() -> dict[str, Any]:
    codes, rows = load_scanner_fixtures()
    return {
        "scan_codes": codes["scan_codes"],
        "filter_codes": codes["filter_codes"],
        "scan_rows": rows_by_scan(rows),
    }


def _run(
    settings: dict[str, Any] | None = None, **fake_options: Any
) -> tuple[dict[str, Any], FakeScannerTWS]:
    fakes: list[FakeScannerTWS] = []
    options = {"scan_codes": CANDIDATE_SCAN_CODES, "scan_rows": INLINE_ROWS, **fake_options}

    def factory(state: Any) -> FakeScannerTWS:
        fake = FakeScannerTWS(state, **options)
        fakes.append(fake)
        return fake

    report = run_scanner_probe(
        ScannerProbeSettings(**{**FAST, **(settings or {})}), client_factory=factory
    )
    assert not fakes[0].callback_errors
    return report, fakes[0]


def _account_like(text: str) -> list[str]:
    return [match for pattern in ACCOUNT_PATTERNS for match in pattern.findall(text)]


# -- client id -------------------------------------------------------------
def test_probe_client_id_differs_from_every_child_bridge_and_assistant_id():
    from trading.operations.screener_service import SCREENER_CLIENT_ID_OFFSET
    from trading.operations.shadow_service import STRATEGY_CLIENT_OFFSET
    from trading.operations.telegram_tools import (
        RESERVED_TWS_CLIENT_IDS,
        TELEGRAM_TWS_CLIENT_ID,
    )
    from trading.research.recorder import RESEARCH_CLIENT_ID

    data, orders = (
        scanner_probe.DOCUMENTED_DATA_CLIENT_ID,
        scanner_probe.DOCUMENTED_ORDER_CLIENT_ID,
    )
    used = (
        {data + offset for offset in STRATEGY_CLIENT_OFFSET.values()}
        | {data + SCREENER_CLIENT_ID_OFFSET, orders, TELEGRAM_TWS_CLIENT_ID, RESEARCH_CLIENT_ID}
        | set(RESERVED_TWS_CLIENT_IDS)
    )
    assert (data, orders) == (17, 18)
    assert PROBE_CLIENT_ID not in used
    assert child_client_ids(data, orders) == used
    # The literal map follows the owning modules for any configured pair.
    for data, orders in ((30, 31), (5, 4)):
        assert child_client_ids(data, orders) == (
            {data + offset for offset in STRATEGY_CLIENT_OFFSET.values()}
            | {data + SCREENER_CLIENT_ID_OFFSET, orders, TELEGRAM_TWS_CLIENT_ID, RESEARCH_CLIENT_ID}
        )


@pytest.mark.parametrize("client_id", [0, -1, 17, 18, 19, 20, 21, 22, 23, 28])
def test_settings_refuse_child_and_manual_order_client_ids(client_id):
    with pytest.raises(ScannerProbeConfigError):
        ScannerProbeSettings(client_id=client_id).validate()


@pytest.mark.parametrize(
    "overrides",
    [
        {"host": "0.0.0.0"},
        {"host": "10.0.0.5"},
        {"port": 7496},
        {"port": 4001},
        {"max_concurrent_scans": 4},
        {"line_runs": (10, 17)},
        {"variants": ("base", "bogus")},
        {"scan_codes": ("HOT BY VOLUME",)},
        {"scan_timeout_seconds": 0.0},
        {"line_start_interval_seconds": -1.0},
    ],
)
def test_settings_refuse_anything_outside_the_paper_boundary(overrides):
    with pytest.raises(ScannerProbeConfigError):
        replace(ScannerProbeSettings(), **overrides).validate()


def test_default_settings_match_the_production_boundary():
    from trading.market import tws_actor

    ScannerProbeSettings().validate()
    assert (scanner_probe.PAPER_HOST, scanner_probe.PAPER_PORT) == (
        tws_actor.PAPER_TWS_HOST,
        tws_actor.PAPER_TWS_PORT,
    )
    # Lines are held exactly as long as the production snapshot holds them.
    assert scanner_probe.LINE_SETTLE_SECONDS == tws_actor.MARKET_SNAPSHOT_SETTLE_SECONDS
    assert scanner_probe.LINE_START_INTERVAL_SECONDS == tws_actor.SNAPSHOT_START_INTERVAL_SECONDS
    assert LINE_RUNS == (10, 16, 10, 16) and len(LINE_SYMBOLS) == 16


# -- scanner parameters ----------------------------------------------------
REALISTIC_PARAMETERS = """<?xml version="1.0" encoding="UTF-8"?>
<ScanParameterResponse>
  <InstrumentList varName="instrumentList">
    <Instrument>
      <name>US Stocks</name>
      <type>STK</type>
      <filters>PRICE,VOLUME,MKTCAP</filters>
      <group>STK.GLOBAL</group>
      <shortName>US</shortName>
    </Instrument>
    <Instrument><name>US Futures</name><type>FUT.US</type><filters>OPENINTEREST</filters></Instrument>
  </InstrumentList>
  <LocationTree varName="locationTree">
    <Location>
      <displayName>US Stocks</displayName>
      <locationCode>STK.US</locationCode>
      <instruments>STK</instruments>
      <LocationTree varName="locationTree">
        <Location>
          <displayName>Listed/NASDAQ</displayName>
          <locationCode>STK.US.MAJOR</locationCode>
          <instruments>STK</instruments>
        </Location>
      </LocationTree>
    </Location>
  </LocationTree>
  <ScanTypeList varName="scanTypeList">
    <ScanType>
      <displayName>Top % Gainers</displayName>
      <scanCode>TOP_PERC_GAIN</scanCode>
      <instruments>BOND,STK,STOCK.NA,ETF.EQ.US</instruments>
      <absoluteColumns>false</absoluteColumns>
    </ScanType>
    <ScanType>
      <displayName>Hot Contracts by Volume</displayName>
      <scanCode>HOT_BY_VOLUME</scanCode>
      <instruments>STK,FUT.US</instruments>
    </ScanType>
    <ScanType>
      <displayName>Highest Open Interest</displayName>
      <scanCode>HIGH_OPEN_INTEREST</scanCode>
      <instruments>FUT.US,STK.NA</instruments>
    </ScanType>
  </ScanTypeList>
  <FilterList varName="filterList">
    <RangeFilter>
      <id>PRICE</id>
      <category>Price</category>
      <AbstractField type="scanner.field.DoubleField"><code>priceAbove</code></AbstractField>
      <AbstractField type="scanner.field.DoubleField"><code>priceBelow</code></AbstractField>
    </RangeFilter>
    <RangeFilter>
      <id>MKTCAP</id>
      <AbstractField type="scanner.field.DoubleField"><code>marketCapAbove1e6</code></AbstractField>
    </RangeFilter>
    <SimpleFilter>
      <id>VOLUME</id>
      <AbstractField type="scanner.field.IntField"><code>volumeAbove</code></AbstractField>
    </SimpleFilter>
    <RangeFilter>
      <id>OPENINTEREST</id>
      <AbstractField type="scanner.field.IntField"><code>openInterestAbove</code></AbstractField>
    </RangeFilter>
  </FilterList>
</ScanParameterResponse>
"""


def test_scanner_parameters_keep_only_stock_scan_codes_and_filter_codes():
    summary = summarize_scanner_parameters(REALISTIC_PARAMETERS)

    assert summary["parsed"] is True
    assert summary["location_found"] is True
    assert summary["scan_codes"] == ["HOT_BY_VOLUME", "TOP_PERC_GAIN"]
    assert summary["candidates"]["HOT_BY_VOLUME"] is True
    assert summary["candidates"]["HIGH_OPEN_GAP"] is False
    assert summary["filter_scope"] == "STK"
    assert summary["filter_codes"] == [
        "marketCapAbove1e6",
        "priceAbove",
        "priceBelow",
        "volumeAbove",
    ]
    assert "structure" not in summary
    # Trimmed: names only, never the XML itself.
    assert "<" not in json.dumps(summary)


def test_scanner_parameters_report_structure_when_nothing_parses():
    unknown = summarize_scanner_parameters("<Response><Other><x/></Other></Response>")
    assert unknown["parsed"] is True and unknown["scan_code_count"] == 0
    assert unknown["structure"] == {"Response": 1, "Other": 1, "x": 1}
    broken = summarize_scanner_parameters("<Response><unclosed></Response>")
    assert broken["parsed"] is False and broken["error"].startswith("ParseError")


def test_fake_parameters_round_trip_through_the_parser():
    codes, _ = load_scanner_fixtures()
    summary = summarize_scanner_parameters(
        scanner_parameters_xml(codes["scan_codes"], codes["filter_codes"])
    )
    assert summary["scan_codes"] == sorted(codes["scan_codes"])
    assert summary["filter_codes"] == sorted(codes["filter_codes"])


# -- end to end against the fake TWS ---------------------------------------
def test_report_against_the_fixture_tws():
    codes, rows = load_scanner_fixtures()
    expected_rows = rows_by_scan(rows)
    report, fake = _run(**_fixture_fake_options())

    assert fake.connect_args == ("127.0.0.1", 7497, PROBE_CLIENT_ID)
    assert fake.market_data_types == [1]
    connection = report["connection"]
    assert connection["ok"] is True and connection["paper_login"] is True
    assert connection["client_id"] == PROBE_CLIENT_ID
    assert report["read_only"] is True and report["source"] == "injected"

    parameters = report["scanner_parameters"]
    assert parameters["ok"] is True
    assert parameters["scan_codes"] == sorted(codes["scan_codes"])
    assert parameters["filter_codes"] == sorted(codes["filter_codes"])
    assert parameters["xml_bytes"] > 0
    assert report["news_providers"]["providers"] == [
        {"code": "BRFG", "name": "Briefing.com General Market Columns"}
    ]

    scans = report["scans"]
    assert len(scans) == len(CANDIDATE_SCAN_CODES) * len(SCAN_VARIANTS)
    assert [scan["variant"] for scan in scans[: len(CANDIDATE_SCAN_CODES)]] == ["base"] * len(
        CANDIDATE_SCAN_CODES
    )
    for scan in scans:
        served = expected_rows.get((scan["scan_code"], scan["variant"]), [])
        assert scan["rows"] == len(served)
        assert scan["completed"] is True and scan["timed_out"] is False
        assert scan["error_codes"] == []
        assert scan["sample_symbols"] == [row["symbol"] for row in served][:10]
        assert scan["total_ms"] is not None
        assert (scan["first_row_ms"] is None) == (not served)
        if scan["variant"] != "base":
            base = expected_rows.get((scan["scan_code"], "base"), [])
            same = [row["symbol"] for row in served] == [row["symbol"] for row in base]
            assert scan["vs_base"]["same_rows"] is same
    summary = report["scan_summary"]
    assert summary["codes_with_rows"] == [
        code for code in CANDIDATE_SCAN_CODES if expected_rows.get((code, "base"))
    ]

    lines = report["lines"]
    assert [run["lines"] for run in lines["runs"]] == list(LINE_RUNS)
    for run in lines["runs"]:
        assert run["max_concurrent"] == run["lines"]
        assert run["ready"] == len(LINE_SYMBOLS) and run["not_ready"] == []
        assert all(item["data_type"] == "live" for item in run["per_symbol"])
        assert all(item["full_ms"] is not None for item in run["per_symbol"])
    assert lines["market_data_types_observed"] == {"live": len(LINE_SYMBOLS) * len(LINE_RUNS)}
    assert set(lines["comparison"]) == {"10", "16"}

    assert report["requests"]["scanner_subscriptions"] == report["requests"]["scanner_cancels"]
    assert report["requests"]["market_data_lines"] == report["requests"]["market_data_cancels"]
    assert report["system_message_codes"] == {"2104": 1}
    assert report["deadline_reached"] is False
    assert _account_like(json.dumps(report)) == []


def test_every_subscription_is_cancelled_and_at_most_three_scans_run_at_once():
    _, fake = _run()

    assert set(fake.calls) <= ALLOWED_CLIENT_CALLS
    assert sorted(fake.scan_cancels) == sorted(fake.scan_requests)
    assert len(fake.scan_cancels) == len(set(fake.scan_cancels))
    assert fake.max_active_scans == 3
    assert sorted(fake.line_cancels) == sorted(fake.line_requests)
    assert len(fake.line_cancels) == len(set(fake.line_cancels))
    assert fake.max_active_lines == 16
    for subscription in fake.scan_requests.values():
        assert (subscription.instrument, subscription.locationCode) == ("STK", "STK.US.MAJOR")
        assert subscription.stockTypeFilter == "CORP"
        assert (subscription.abovePrice, subscription.belowPrice) == (10.0, 300.0)
    for contract in fake.line_requests.values():
        assert (contract.secType, contract.exchange, contract.currency) == ("STK", "SMART", "USD")


def test_a_ten_line_run_never_holds_more_than_ten_lines():
    report, fake = _run({"line_runs": (10,)})

    assert fake.max_active_lines == 10
    (run,) = report["lines"]["runs"]
    assert run["max_concurrent"] == 10 and run["symbols"] == 16


def test_variants_set_only_the_builtin_subscription_fields():
    _, fake = _run({"scan_codes": ("HOT_BY_VOLUME",)})

    seen = {
        (subscription.aboveVolume, subscription.marketCapAbove)
        for subscription in fake.scan_requests.values()
    }
    unset_volume, unset_cap = 2**31 - 1, 1.7976931348623157e308
    assert seen == {
        (unset_volume, unset_cap),
        (1_000_000, unset_cap),
        (unset_volume, 2_000.0),
        (unset_volume, 2_000_000_000.0),
    }
    assert all(not subscription.scannerSettingPairs for subscription in fake.scan_requests.values())


def test_scan_errors_timeouts_and_resent_rows_are_recorded_and_scrubbed():
    report, fake = _run(
        scan_errors={
            "HOT_BY_PRICE": (321, "Error validating request: account DU7654321 / U7654321")
        },
        silent_scans=("HIGH_VS_13W_HL",),
        extra_scanner_rows_after_end=True,
        news=(("DJNL", "Dow Jones Newsletters"),),
    )
    base = {scan["scan_code"]: scan for scan in report["scans"] if scan["variant"] == "base"}

    failed = base["HOT_BY_PRICE"]
    assert failed["error_codes"] == [321] and failed["rows"] == 0
    assert "[account]" in failed["errors"][0]["message"]
    silent = base["HIGH_VS_13W_HL"]
    assert silent["timed_out"] is True and silent["completed"] is False
    assert all("LATE" not in scan["sample_symbols"] for scan in report["scans"])
    assert report["scan_summary"]["codes_with_errors"] == {"HOT_BY_PRICE": [321]}
    assert sorted(fake.scan_cancels) == sorted(fake.scan_requests)
    assert _account_like(json.dumps(report)) == []


def test_line_errors_are_recorded_and_every_line_released():
    report, fake = _run(
        {"line_runs": (16,)},
        line_errors={"V": (10197, "No market data during competing live session")},
    )
    (run,) = report["lines"]["runs"]
    by_symbol = {item["symbol"]: item for item in run["per_symbol"]}

    assert by_symbol["V"]["error_codes"] == [10197] and by_symbol["V"]["ready_ms"] is None
    assert run["not_ready"] == ["V"] and run["ready"] == 15
    assert report["lines"]["comparison"]["16"]["error_codes"] == [10197]
    assert sorted(fake.line_cancels) == sorted(fake.line_requests)


def test_delayed_data_never_counts_as_a_ready_quote():
    report, _ = _run({"line_runs": (16,)}, line_data_type=3)
    (run,) = report["lines"]["runs"]

    assert run["ready"] == 0
    assert report["lines"]["market_data_types_observed"] == {"delayed": 16}
    assert all(item["timed_out"] for item in run["per_symbol"])


def test_partial_ticks_are_ready_without_the_full_set():
    report, _ = _run({"line_runs": (10,)}, partial_lines=[symbol for symbol, _ in LINE_SYMBOLS])
    (run,) = report["lines"]["runs"]

    assert run["ready"] == 16
    assert all(item["full_ms"] is None for item in run["per_symbol"])
    assert all("open" not in item["ticks"] for item in run["per_symbol"])


@pytest.mark.parametrize("accounts", ["U1234567", "DU1234567,U7654321", ""])
def test_a_non_paper_login_is_refused_before_any_market_request(accounts):
    report, fake = _run(accounts=accounts)

    assert report["connection"]["ok"] is False
    assert "non-paper" in report["connection"]["error"]
    assert {"reqScannerParameters", "reqScannerSubscription", "reqMktData", "reqNewsProviders"}.isdisjoint(
        fake.calls
    )
    assert "scans" not in report
    assert _account_like(json.dumps(report)) == []


def test_a_missing_account_list_is_requested_once():
    report, fake = _run({"line_runs": (10,), "scan_codes": ("HOT_BY_VOLUME",)}, send_accounts=False)

    assert fake.calls.count("reqManagedAccts") == 1
    assert report["connection"]["ok"] is True


def test_a_client_id_in_use_is_reported_without_requests():
    report, fake = _run(
        refuse_connect=(326, "Unable to connect as the client id is already in use.")
    )

    assert report["connection"]["ok"] is False
    assert "326" in report["connection"]["error"]
    assert set(fake.calls) <= {"connect", "run", "disconnect"}


def test_a_silent_api_handshake_is_abandoned_in_time():
    report, fake = _run({"handshake_timeout_seconds": 0.3}, hang_connect=True)

    assert report["connection"]["ok"] is False
    assert "handshake" in report["connection"]["error"]
    assert set(fake.calls) <= {"connect", "disconnect"}


def test_a_connection_closed_mid_run_stops_new_requests():
    report, fake = _run(close_after_scan_requests=2)

    skipped = [scan for scan in report["scans"] if "skipped" in scan]
    assert skipped and {scan["skipped"] for scan in skipped} == {"TWS closed the connection"}
    assert {run["skipped"] for run in report["lines"]["runs"]} == {"TWS closed the connection"}
    assert fake.line_requests == {}
    assert sorted(fake.scan_cancels) == sorted(fake.scan_requests)
    assert report["tws_closed_connection"] is True


def test_the_official_client_forwards_callbacks_and_contains_probe_bugs():
    import logging

    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper

    state = scanner_probe._ProbeState()
    client = scanner_probe._official_client_factory(state)

    assert isinstance(client, EClient) and isinstance(client, EWrapper)
    assert client.isConnected() is False
    assert logging.getLogger("ibapi.decoder").getEffectiveLevel() >= logging.WARNING
    client.nextValidId(7)
    client.managedAccounts("DU1234567")
    client.error(-1, 2104, "Market data farm connection is OK:usfarm")
    assert (state.next_valid_id, state.paper_login, state.system_codes[2104]) == (7, True, 1)
    # A malformed callback is recorded, never raised into TWS's reader thread.
    client.scannerData("not-a-request-id", 0, None, "", "", "", "")
    assert state.callback_failures and "scannerData" in state.callback_failures[0]
    assert "DU1234567" not in repr(vars(state))


def test_the_deadline_skips_what_is_left_and_still_cancels():
    report, fake = _run(
        {"deadline_seconds": 0.3, "scan_timeout_seconds": 5.0},
        silent_scans=CANDIDATE_SCAN_CODES,
    )

    assert report["deadline_reached"] is True
    assert any("skipped" in scan for scan in report["scans"])
    assert all("skipped" in run for run in report["lines"]["runs"])
    assert sorted(fake.scan_cancels) == sorted(fake.scan_requests)
    assert fake.line_requests == {}


@pytest.mark.parametrize(
    ("moment", "phase"),
    [
        (datetime(2026, 10, 13, 9, 15, tzinfo=timezone.utc), "pre-market"),
        (datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc), "regular"),
        (datetime(2026, 10, 13, 21, 0, tzinfo=timezone.utc), "after-hours"),
        (datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc), "overnight"),
        (datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc), "weekend"),
        (datetime(2026, 12, 1, 14, 45, tzinfo=timezone.utc), "regular"),
    ],
)
def test_et_phase_follows_the_new_york_clock(moment, phase):
    assert et_phase(moment) == phase


# -- fixtures ----------------------------------------------------------------
def test_scanner_fixtures_are_trimmed_public_data():
    codes, rows = load_scanner_fixtures()
    meta = {"source", "captured_at_utc", "et_phase", "probe_version"}

    assert set(codes) == meta | {
        "instrument",
        "location",
        "location_found",
        "scan_codes",
        "filter_scope",
        "filter_codes",
    }
    assert codes["instrument"] == "STK" and codes["location"] == "STK.US.MAJOR"
    assert codes["scan_codes"] == sorted(set(codes["scan_codes"]))
    assert set(CANDIDATE_SCAN_CODES) <= set(codes["scan_codes"])
    assert set(rows) == meta | {"rank_base", "scans"}
    assert rows["rank_base"] == 0
    for scan in rows["scans"]:
        assert set(scan) == {"scan_code", "variant", "rows"}
        assert scan["variant"] in SCAN_VARIANTS
        assert len(scan["rows"]) <= 10
        ranks = [row["rank"] for row in scan["rows"]]
        assert ranks == sorted(ranks) and all(rank >= 0 for rank in ranks)
        for row in scan["rows"]:
            assert set(row) == {"rank", "symbol", "con_id", "exchange"}
            assert isinstance(row["con_id"], int) and row["con_id"] > 0
            assert row["symbol"] and row["symbol"] == row["symbol"].upper()
            assert row["exchange"]


def test_no_fixture_file_holds_an_account_id():
    files = [path for path in FIXTURES_ROOT.rglob("*") if path.is_file()]
    assert any(path.parent == FIXTURE_DIR for path in files)
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert _account_like(text) == [], path


def test_fixtures_round_trip_through_the_fake_tws():
    codes, rows = load_scanner_fixtures()
    report, _ = _run(**_fixture_fake_options())
    rebuilt = fixtures_from_report(report)

    rebuilt_codes = rebuilt[scanner_probe.FIXTURE_SCAN_CODES]
    for key in ("instrument", "location", "location_found", "scan_codes", "filter_scope", "filter_codes"):
        assert rebuilt_codes[key] == codes[key]
    rebuilt_rows = {
        (scan["scan_code"], scan["variant"]): scan["rows"]
        for scan in rebuilt[scanner_probe.FIXTURE_SCAN_ROWS]["scans"]
    }
    for key, served in rows_by_scan(rows).items():
        assert rebuilt_rows[key] == served
    assert all(not served for key, served in rebuilt_rows.items() if key not in rows_by_scan(rows))


def test_fixture_writing_refuses_account_like_ids(tmp_path):
    report, _ = _run({"line_runs": (10,), "scan_codes": ("HOT_BY_VOLUME",)})
    report["scans"][0]["sample_rows"][0]["symbol"] = "DU1234567"

    with pytest.raises(ScannerProbeError):
        write_fixtures(report, tmp_path / "fixtures")
    assert not (tmp_path / "fixtures").exists()


# -- static safety -----------------------------------------------------------
FORBIDDEN_CALLS = (
    "placeOrder",
    "cancelOrder",
    "reqGlobalCancel",
    "reqAutoOpenOrders",
    "reqOpenOrders",
    "reqAllOpenOrders",
    "reqCompletedOrders",
    "exerciseOptions",
    "reqIds",
    "reqAccountUpdates",
    "reqAccountUpdatesMulti",
    "reqAccountSummary",
    "reqPositions",
    "reqPositionsMulti",
    "reqExecutions",
    "reqPnL",
    "reqPnLSingle",
    "requestFA",
    "replaceFA",
)


def test_the_probe_never_names_an_order_or_account_call():
    from ibapi.client import EClient

    source = inspect.getsource(scanner_probe)
    for forbidden in FORBIDDEN_CALLS:
        assert forbidden not in source, forbidden
    client_methods = {name for name in dir(EClient) if not name.startswith("_")}
    assert ALLOWED_CLIENT_CALLS <= client_methods
    assert not set(FORBIDDEN_CALLS) & ALLOWED_CLIENT_CALLS
    tree = ast.parse(source)
    referenced = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    referenced |= {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert referenced & client_methods <= ALLOWED_CLIENT_CALLS


def test_the_probe_imports_no_database_keychain_or_claude_code():
    tree = ast.parse(inspect.getsource(scanner_probe))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    project = {name for name in imported if name.split(".")[0] in {"trading", "db", "config"}}
    assert project == {"trading.operations.paths"}
    third_party = {
        name
        for name in imported
        if name.split(".")[0] not in sys.stdlib_module_names and name not in project
    }
    assert third_party == {"ibapi.client", "ibapi.wrapper", "ibapi.scanner", "ibapi.contract"}
    check = (
        "import sys, trading.operations.scanner_probe\n"
        "bad = sorted(n for n in sys.modules if n.split('.')[0] in "
        "{'aiosqlite', 'anthropic', 'httpx', 'db', 'config', 'ibapi'} or "
        "n.startswith(('trading.storage', 'trading.intelligence')) or "
        "n == 'trading.operations.keychain')\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", check], cwd=BACKEND, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


# -- CLI ---------------------------------------------------------------------
@pytest.fixture
def fake_cli_probe(monkeypatch):
    """``run_cli`` on the fake TWS at test speed; config, DB and Keychain fail loudly."""
    from trading.operations import keychain, settings

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the scanner probe must not touch config, database or Keychain")

    monkeypatch.setattr(settings, "load_shadow_runtime_config", forbidden)
    monkeypatch.setattr(keychain, "read_anthropic_api_key", forbidden)
    monkeypatch.setattr("aiosqlite.connect", forbidden)
    fakes: list[FakeScannerTWS] = []
    real = scanner_probe.run_scanner_probe

    def fast(settings_: ScannerProbeSettings, **_: Any) -> dict[str, Any]:
        def factory(state: Any) -> FakeScannerTWS:
            fake = FakeScannerTWS(state, **_fixture_fake_options())
            fakes.append(fake)
            return fake

        return real(replace(settings_, **FAST), client_factory=factory)

    monkeypatch.setattr(scanner_probe, "run_scanner_probe", fast)
    return fakes


def test_cli_scanner_only_needs_no_config_database_or_keychain(fake_cli_probe, tmp_path, capsys):
    from trading.cli import main

    out = tmp_path / "probes" / "scanner.json"
    fixtures = tmp_path / "fixtures"
    code = main(
        ["shadow", "probe", "--scanner-only", "--out", str(out), "--fixtures-dir", str(fixtures)]
    )

    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["connection"]["client_id"] == PROBE_CLIENT_ID
    assert fake_cli_probe[0].connect_args == ("127.0.0.1", 7497, PROBE_CLIENT_ID)
    assert out.stat().st_mode & 0o077 == 0
    assert sorted(path.name for path in fixtures.iterdir()) == ["scan_codes.json", "scan_rows.json"]
    printed = capsys.readouterr().out
    assert "report:" in printed and "HOT_BY_VOLUME [base]" in printed
    assert _account_like(out.read_text(encoding="utf-8") + printed) == []


@pytest.mark.parametrize(
    "arguments",
    [["--client-id", "17"], ["--client-id", "18"], ["--port", "7496"], ["--host", "10.0.0.5"]],
)
def test_cli_scanner_only_refuses_child_ids_and_non_paper_endpoints(fake_cli_probe, arguments, capsys):
    from trading.cli import main

    assert main(["shadow", "probe", "--scanner-only", *arguments]) == 2
    assert fake_cli_probe == []
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["shadow", "probe", "--out", "report.json"],
        ["shadow", "probe", "--client-id", "29"],
        ["shadow", "probe", "--scanner-only", "--con-id", "5"],
        ["shadow", "probe", "--scanner-only", "--with-claude"],
    ],
)
def test_cli_scanner_options_belong_to_scanner_only(arguments):
    from trading.cli import main

    with pytest.raises(SystemExit) as raised:
        main(arguments)
    assert raised.value.code == 2


def test_cli_rebuilds_fixtures_from_a_saved_report(fake_cli_probe, tmp_path):
    from trading.cli import main

    report_path = tmp_path / "report.json"
    assert main(["shadow", "probe", "--scanner-only", "--out", str(report_path)]) == 0
    fixtures = tmp_path / "rebuilt"

    assert main(["shadow", "probe", "--scanner-only", "--from-report", str(report_path)]) == 2
    assert (
        main(
            [
                "shadow",
                "probe",
                "--scanner-only",
                "--from-report",
                str(report_path),
                "--fixtures-dir",
                str(fixtures),
            ]
        )
        == 0
    )
    assert len(fake_cli_probe) == 1
    expected = fixtures_from_report(json.loads(report_path.read_text(encoding="utf-8")))
    for name, payload in expected.items():
        assert json.loads((fixtures / name).read_text(encoding="utf-8")) == payload
