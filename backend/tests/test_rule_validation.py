import pytest
from httpx import ASGITransport, AsyncClient

from models import Rule
from rule_validation import evaluate_promotion_gate, evaluate_validation_run, validate_rule_schema


def _make_rule(*, status: str = "paper", enabled: bool = False) -> Rule:
    return Rule(
        name="AI Trend Rule",
        symbol="AAPL",
        enabled=enabled,
        conditions=[{"indicator": "SMA", "params": {"length": 20}, "operator": ">", "value": "PRICE"}],
        logic="AND",
        action={"type": "BUY", "asset_type": "STK", "quantity": 5, "order_type": "MKT"},
        cooldown_minutes=30,
        status=status,  # type: ignore[arg-type]
        ai_generated=True,
        created_by="ai",
    )


def test_validate_rule_schema_requires_conditions():
    rule = Rule(
        name="Broken Rule",
        symbol="MSFT",
        enabled=False,
        conditions=[],
        logic="AND",
        action={"type": "BUY", "asset_type": "STK", "quantity": 1, "order_type": "MKT"},
        cooldown_minutes=15,
        status="paper",
        ai_generated=True,
        created_by="ai",
    )

    ok, errors = validate_rule_schema(rule)
    assert ok is False
    assert "at least one condition" in errors[0]


def test_evaluate_validation_run_rejects_weak_metrics():
    passed, reasons = evaluate_validation_run(
        trades_count=2,
        expectancy=-0.1,
        max_drawdown=22.0,
        overlap_score=0.9,
    )

    assert passed is False
    assert len(reasons) >= 3


@pytest.mark.anyio
async def test_evaluate_promotion_gate_accepts_latest_passing_validation(anyio_backend):
    from database import init_db, save_rule
    from rule_validation import record_validation_result

    await init_db()
    rule = _make_rule(status="paper", enabled=False)
    await save_rule(rule)
    await record_validation_result(
        rule=rule,
        validation_mode="paper",
        trades_count=8,
        hit_rate=0.62,
        net_pnl=145.0,
        expectancy=0.35,
        max_drawdown=8.5,
        overlap_score=0.2,
        passed=True,
        notes="Paper validation passed",
    )

    eligible, reasons, latest = await evaluate_promotion_gate(rule)
    assert eligible is True
    assert reasons == []
    assert latest is not None
    assert latest["validation_mode"] == "paper"


@pytest.mark.anyio
async def test_promotion_readiness_endpoint_exposes_latest_validation(tmp_path, anyio_backend):
    from config import cfg
    import database

    db_path = str(tmp_path / "promotion_readiness.db")
    cfg.DB_PATH = db_path
    database.DB_PATH = db_path

    from database import init_db, save_rule
    from main import app
    from rule_validation import record_validation_result

    await init_db()
    rule = _make_rule(status="paper", enabled=False)
    await save_rule(rule)
    await record_validation_result(
        rule=rule,
        validation_mode="paper",
        trades_count=7,
        hit_rate=0.57,
        net_pnl=92.0,
        expectancy=0.18,
        max_drawdown=7.5,
        overlap_score=0.2,
        passed=True,
        notes="Promotion gate satisfied",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/autopilot/rules/{rule.id}/promotion-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["rule_id"] == rule.id
    assert body["status"] == "paper"
    assert body["eligible"] is True
    assert body["latest_validation"]["validation_mode"] == "paper"
