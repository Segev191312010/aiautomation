"""Preparation checks cannot masquerade as broker verification."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from trading.operations import readiness
from trading.market.tws_actor import DataLineCapacityReport, TWSPreflightReport


def test_reachable_socket_does_not_authorize_orders(monkeypatch):
    monkeypatch.setattr(readiness, "load_shadow_runtime_config", lambda: SimpleNamespace(data_client_id=17, order_client_id=18))
    calls = []

    def connect(address, timeout):
        calls.append((address, timeout))
        return nullcontext()

    monkeypatch.setattr(readiness.socket, "create_connection", connect)
    report = readiness.check_local_preparation()
    assert calls == [(("127.0.0.1", 7497), 2)]
    assert report["ready"] is False
    assert report["orders_enabled"] is False
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["tws_socket"]["status"] == "passed"
    assert checks["paper_account"]["status"] == "unverified"


def test_failures_are_sanitized(monkeypatch):
    def bad_config():
        raise readiness.ShadowSettingsError("private-account-secret")

    def bad_socket(*args, **kwargs):
        raise OSError("private-connection-secret")

    monkeypatch.setattr(readiness, "load_shadow_runtime_config", bad_config)
    monkeypatch.setattr(readiness.socket, "create_connection", bad_socket)
    report = readiness.check_local_preparation()
    assert "private-" not in str(report)
    assert [check["status"] for check in report["checks"][:2]] == ["blocked", "blocked"]


def test_cli_preflight_does_not_initialize_database(monkeypatch, capsys):
    from trading import cli

    async def forbidden():
        raise AssertionError("preparation must not initialize a database")

    monkeypatch.setattr(cli, "init_db", forbidden)
    async def preflight():
        return {"ready": False, "orders_enabled": False, "checks": []}

    monkeypatch.setattr(readiness, "check_shadow_preflight", preflight)
    assert cli.main(["shadow", "preflight", "--json"]) == 2
    assert '"orders_enabled": false' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_broker_preflight_performs_official_handshake_without_authorizing(monkeypatch):
    config = SimpleNamespace(
        paper_account_id="DU_TEST", data_client_id=17, order_client_id=18, max_candidates=20
    )
    monkeypatch.setattr(readiness, "load_shadow_runtime_config", lambda: config)
    monkeypatch.setattr(readiness, "check_local_preparation", lambda: {
        "kind": "local_preparation_only",
        "ready": False,
        "orders_enabled": False,
        "checks": [
            {"name": "configuration", "status": "passed", "detail": "valid"},
            {"name": "tws_socket", "status": "passed", "detail": "reachable"},
            {"name": "paper_account", "status": "unverified", "detail": "pending"},
            {"name": "client_identity", "status": "unverified", "detail": "pending"},
            {"name": "realtime_entitlements", "status": "unverified", "detail": "pending"},
        ],
    })

    class Actor:
        def __init__(self, actor_config):
            assert actor_config.account == "DU_TEST"
            assert actor_config.client_id == 17
            self.disconnected = False

        async def connect(self):
            return None

        async def preflight(self, *, required_data_lines):
            assert required_data_lines == 20
            return TWSPreflightReport(
                host="127.0.0.1",
                port=7497,
                account="DU_TEST",
                client_id=17,
                connected=True,
                account_verified=True,
                scanner_supported=True,
                data_line_capacity=DataLineCapacityReport(
                    required_lines=20, available_lines=None, passed=False,
                    reason="not independently verified",
                ),
                ready=False,
            )

        async def disconnect(self):
            self.disconnected = True

    report = await readiness.check_shadow_preflight(actor_factory=Actor)
    checks = {item["name"]: item for item in report["checks"]}
    assert report["ready"] is False
    assert report["orders_enabled"] is False
    assert checks["paper_account"]["status"] == "passed"
    assert checks["scanner_capability"]["status"] == "passed"
    assert checks["realtime_entitlements"]["status"] == "unverified"
