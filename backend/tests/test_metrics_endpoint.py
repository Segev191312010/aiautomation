"""
Tests for the Prometheus /metrics endpoint (ULTRAPLAN v4).

Verifies that:
- a minimal FastAPI app including the metrics router serves GET /metrics,
- the response uses the Prometheus text exposition content type and parses
  cleanly via the official text parser,
- every metric name in the contract is present, and
- the thin helper functions increment their underlying counters.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client.parser import text_string_to_metric_families

import metrics


# Names without the ``_total`` suffix that prometheus_client strips during
# parsing; we assert on the *base* family names the parser yields.
EXPECTED_METRIC_NAMES = {
    "trading_orders_placed",
    "trading_orders_filled",
    "trading_ibkr_disconnects",
    "trading_bot_cycle_seconds",
    "trading_rate_cap_hits",
    "trading_autopilot_mode",
    "trading_claude_calls",
    "trading_claude_cost_usd",
    "trading_webhook_events",
    "trading_seconds_since_last_accepted_signal",
}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(metrics.router)
    return TestClient(app)


def _families(body: str) -> dict[str, object]:
    """Parse Prometheus text into {family_name: family}."""
    return {fam.name: fam for fam in text_string_to_metric_families(body)}


def _value(body: str, sample_name: str, **labels: str) -> float:
    """Return the value of the sample named ``sample_name`` (the full series
    name, e.g. ``trading_webhook_events_total``) matching ``labels``.

    Matching on the sample name rather than the parsed family name avoids the
    Prometheus text parser's habit of stripping the ``_total`` suffix from
    counter *family* names while keeping it on the underlying samples.
    """
    for fam in text_string_to_metric_families(body):
        for sample in fam.samples:
            if sample.name != sample_name:
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


def _scrape(client: TestClient) -> str:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # media_type may include a charset; the family token must match.
    assert resp.headers["content-type"].split(";")[0] == CONTENT_TYPE_LATEST.split(";")[0]
    return resp.text


def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:
    body = _scrape(client)
    # Body parses cleanly as Prometheus text exposition format.
    families = _families(body)
    assert families, "no metric families parsed from /metrics body"


def test_application_does_not_mount_metrics_without_isolated_profile() -> None:
    """Metrics are default-deny on the public application listener."""
    from main import _register_metrics_router

    production_like_app = FastAPI()
    assert _register_metrics_router(production_like_app, exposure_profile="off") is False
    assert "/metrics" not in {route.path for route in production_like_app.routes}


def test_isolated_monitoring_profile_mounts_metrics() -> None:
    from main import _register_metrics_router

    monitoring_app = FastAPI()
    assert _register_metrics_router(monitoring_app, exposure_profile="isolated") is True
    assert "/metrics" in {route.path for route in monitoring_app.routes}


def test_all_metric_names_present(client: TestClient) -> None:
    # Touch every labelled metric so its series materialises in the output.
    metrics.record_order_placed("test_src", "BUY")
    metrics.record_order_filled("test_src")
    metrics.record_ibkr_disconnect()
    metrics.observe_bot_cycle_seconds(0.5)
    metrics.record_rate_cap_hit("AAPL")
    metrics.set_autopilot_mode("LIVE")
    metrics.record_claude_call("ok")
    metrics.record_claude_cost(0.01)
    metrics.record_webhook_outcome("accepted")
    metrics.set_seconds_since_last_accepted_signal(3.0)

    body = _scrape(client)
    present = set(_families(body).keys())
    missing = EXPECTED_METRIC_NAMES - present
    assert not missing, f"missing metric families: {sorted(missing)}"


def test_helper_increments_counter(client: TestClient) -> None:
    before = _value(_scrape(client), "trading_webhook_events_total", outcome="duplicate")
    metrics.record_webhook_outcome("duplicate")
    after = _value(_scrape(client), "trading_webhook_events_total", outcome="duplicate")
    assert after == before + 1


def test_order_placed_helper_increments_with_labels(client: TestClient) -> None:
    before = _value(
        _scrape(client), "trading_orders_placed_total", source="webhook", action="SELL"
    )
    metrics.record_order_placed("webhook", "SELL")
    after = _value(
        _scrape(client), "trading_orders_placed_total", source="webhook", action="SELL"
    )
    assert after == before + 1


def test_autopilot_mode_gauge_encodes_text_modes(client: TestClient) -> None:
    metrics.set_autopilot_mode("OFF")
    assert _value(_scrape(client), "trading_autopilot_mode") == 0
    metrics.set_autopilot_mode("PAPER")
    assert _value(_scrape(client), "trading_autopilot_mode") == 1
    metrics.set_autopilot_mode("LIVE")
    assert _value(_scrape(client), "trading_autopilot_mode") == 2


def test_unknown_webhook_outcome_is_ignored(client: TestClient) -> None:
    # Bad label values must never raise into a trading path.
    metrics.record_webhook_outcome("not_a_real_outcome")
    body = _scrape(client)
    assert _value(body, "trading_webhook_events_total", outcome="not_a_real_outcome") == 0.0


def test_rate_cap_metric_never_exports_symbol_labels(client: TestClient) -> None:
    metrics.record_rate_cap_hit("PRIVATE-STRATEGY-SYMBOL")
    family = _families(_scrape(client))["trading_rate_cap_hits"]
    assert all(sample.labels == {} for sample in family.samples)
    assert "PRIVATE-STRATEGY-SYMBOL" not in _scrape(client)
