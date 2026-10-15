from __future__ import annotations

from httpx import ASGITransport, AsyncClient
import pytest

from trading.sendable_identities import SENDABLE_IDENTITIES


def _strategy_payload() -> dict:
    return {
        "name": "Draft VWAP continuation",
        "universe": {"kind": "fixed", "symbols": ["AAPL"]},
        "timeframe": "5m",
        "conditions": [{"feature": "rvol", "operator": "gte", "value": "1.5"}],
        "rationale": "Operator-defined draft strategy.",
    }


@pytest.mark.anyio
async def test_autotrader_routes_are_draft_and_shadow_only(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db
    from main import app

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "autotrader_routes.db"))
    monkeypatch.setattr(cfg, "JWT_BOOTSTRAP_SECRET_FILE", "")
    monkeypatch.setenv("BOOTSTRAP_ALLOW_REMOTE", "1")
    await init_db()
    transport = ASGITransport(app=app)
    headers = {"X-Bootstrap-Secret": cfg.JWT_BOOTSTRAP_SECRET}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        auth = await client.post("/api/auth/token", headers=headers)
        assert auth.status_code == 200
        bearer = {"Authorization": f"Bearer {auth.json()['access_token']}"}

        status = await client.get("/api/engine/v1/status", headers=bearer)
        assert status.status_code == 200
        # The bridge in this checkout is the reviewed one: real PAPER orders
        # are sent for the sendable identities, and the status says so.
        assert status.json()["execution_enabled"] is True
        assert status.json()["paper_order_sender"] == "paper_bridge (pinned)"
        assert status.json()["sendable_identities"] == list(SENDABLE_IDENTITIES)
        assert status.json()["paper_authority"] == "paper_bridge_pinned"
        assert status.json()["live_authority"] == "unsupported"
        assert status.json()["enabled_modes"] == ["backtest", "shadow"]

        created = await client.post("/api/engine/v1/strategies", headers=bearer, json=_strategy_payload())
        assert created.status_code == 201
        strategy_id = created.json()["strategy_id"]

        shadow = await client.post(
            f"/api/engine/v1/strategies/{strategy_id}/versions/1/transition",
            headers=bearer,
            json={
                "expected_revision": 0,
                "target": "backtesting",
                "reason": "Backtest scheduled",
            },
        )
        assert shadow.status_code == 200

        shadow_ready = await client.post(
            f"/api/engine/v1/strategies/{strategy_id}/versions/1/transition",
            headers=bearer,
            json={
                "expected_revision": 1,
                "target": "shadow",
                "reason": "Shadow evidence run",
            },
        )
        assert shadow_ready.status_code == 200

        run_request = await client.post(
            "/api/engine/v1/runs/shadow",
            headers=bearer,
            json={
                "source_sha": "a" * 40,
                "strategy_id": strategy_id,
                "strategy_version": 1,
                "session_date": "2026-08-27",
                "policy_fingerprints": {"risk": "b" * 64},
            },
        )
        assert run_request.status_code == 201
        run_id = run_request.json()["id"]
        assert run_request.json()["execution_enabled"] is False

        runs = await client.get("/api/engine/v1/runs", headers=bearer)
        assert runs.status_code == 200
        assert runs.json()[0]["id"] == run_id
        run_candidates = await client.get(
            f"/api/engine/v1/runs/{run_id}/candidates", headers=bearer
        )
        assert run_candidates.status_code == 200
        decisions = await client.get(
            f"/api/engine/v1/decisions?run_id={run_id}", headers=bearer
        )
        assert decisions.status_code == 200
        evidence = await client.get("/api/engine/v1/evidence", headers=bearer)
        assert evidence.status_code == 200
        assert evidence.json()["status"] == "pending"

        blocked_paper = await client.post(
            f"/api/engine/v1/strategies/{strategy_id}/versions/1/transition",
            headers=bearer,
            json={
                "expected_revision": 2,
                "target": "paper",
                "reason": "Attempt to bypass evidence",
            },
        )
        assert blocked_paper.status_code == 409

        changed = _strategy_payload()
        changed["name"] = "Versioned VWAP continuation"
        published = await client.post(
            f"/api/engine/v1/strategies/{strategy_id}/versions",
            headers=bearer,
            json={"base_version": 1, "spec": changed, "reason": "Freeze revision two"},
        )
        assert published.status_code == 201
        assert published.json()["version"] == 2

        stale = await client.post(
            f"/api/engine/v1/strategies/{strategy_id}/versions",
            headers=bearer,
            json={"base_version": 1, "spec": changed, "reason": "Stale retry"},
        )
        assert stale.status_code == 409

        versions = await client.get(
            f"/api/engine/v1/strategies/{strategy_id}/versions", headers=bearer
        )
        assert versions.status_code == 200
        assert [item["version"] for item in versions.json()] == [1, 2]

        listed = await client.get("/api/engine/v1/strategies", headers=bearer)
        assert listed.status_code == 200
        assert listed.json()[0]["version"] == 2
        assert listed.json()[0]["state"] == "draft"


# The PAPER bridge sends for stored MOMO_HOD names; AP_ names are
# autopilot-managed. Code alone makes both, so the API refuses every variant,
# the one sendable identity included.
RESERVED_NAMES = (
    "MOMO_HOD_V2",
    "MOMO_HOD_V1",
    " momo_hod_v3",
    "AP_MOMO_HOD_V1",
    "ap_scanner",
)


@pytest.mark.anyio
async def test_api_never_creates_or_publishes_a_reserved_strategy_name(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db
    from main import app

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "reserved_names.db"))
    monkeypatch.setattr(cfg, "JWT_BOOTSTRAP_SECRET_FILE", "")
    monkeypatch.setenv("BOOTSTRAP_ALLOW_REMOTE", "1")
    await init_db()
    transport = ASGITransport(app=app)
    headers = {"X-Bootstrap-Secret": cfg.JWT_BOOTSTRAP_SECRET}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        auth = await client.post("/api/auth/token", headers=headers)
        assert auth.status_code == 200
        bearer = {"Authorization": f"Bearer {auth.json()['access_token']}"}

        for name in RESERVED_NAMES:
            refused = await client.post(
                "/api/engine/v1/strategies",
                headers=bearer,
                json={**_strategy_payload(), "name": name},
            )
            assert refused.status_code == 422, name
            assert refused.json()["detail"]["code"] == "reserved_strategy_name"
            assert "MOMO_HOD or AP_" in refused.json()["detail"]["message"]
        listed = await client.get("/api/engine/v1/strategies", headers=bearer)
        assert listed.status_code == 200 and listed.json() == []

        created = await client.post(
            "/api/engine/v1/strategies", headers=bearer, json=_strategy_payload()
        )
        assert created.status_code == 201
        strategy_id = created.json()["strategy_id"]
        for name in RESERVED_NAMES:
            refused = await client.post(
                f"/api/engine/v1/strategies/{strategy_id}/versions",
                headers=bearer,
                json={
                    "base_version": 1,
                    "spec": {**_strategy_payload(), "name": name},
                    "reason": "Rename into a reserved family",
                },
            )
            assert refused.status_code == 422, name
            assert refused.json()["detail"]["code"] == "reserved_strategy_name"
        versions = await client.get(
            f"/api/engine/v1/strategies/{strategy_id}/versions", headers=bearer
        )
        assert [item["version"] for item in versions.json()] == [1]

        # Names that merely contain the text are ordinary drafts.
        ordinary = await client.post(
            f"/api/engine/v1/strategies/{strategy_id}/versions",
            headers=bearer,
            json={
                "base_version": 1,
                "spec": {**_strategy_payload(), "name": "Draft MOMO_HOD copy"},
                "reason": "Ordinary rename",
            },
        )
        assert ordinary.status_code == 201 and ordinary.json()["version"] == 2


def test_engine_status_is_truthful_about_the_bridge_on_disk(tmp_path):
    from routers.autotrader_routes import paper_execution_status
    from trading.operations.supervisor import BACKEND_DIR, REVIEWED_BRIDGE_SHA256
    from trading.sendable_identities import SENDABLE_IDENTITIES

    pinned = paper_execution_status(BACKEND_DIR)
    assert pinned["execution_enabled"] is True
    assert pinned["paper_order_sender"] == "paper_bridge (pinned)"
    assert pinned["paper_bridge_sha256"] == REVIEWED_BRIDGE_SHA256
    assert pinned["sendable_identities"] == list(SENDABLE_IDENTITIES) == list(SENDABLE_IDENTITIES)
    assert "reason" not in pinned

    # No bridge file: the supervisor would refuse to start it, so nothing sends.
    missing = paper_execution_status(tmp_path)
    assert missing["execution_enabled"] is False
    assert missing["paper_order_sender"] is None
    assert missing["sendable_identities"] == []
    assert missing["paper_authority"] == "blocked_bridge_unreadable"
    assert "unreadable" in missing["reason"]

    # An edited bridge: the same refusal, named.
    (tmp_path / "scripts_local").mkdir()
    (tmp_path / "scripts_local" / "paper_bridge.py").write_text("print('edited')\n")
    edited = paper_execution_status(tmp_path)
    assert edited["execution_enabled"] is False
    assert edited["paper_authority"] == "blocked_bridge_unreviewed"
    assert f"not the reviewed {REVIEWED_BRIDGE_SHA256[:12]}" in edited["reason"]
