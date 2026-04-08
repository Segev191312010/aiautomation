"""Stage 9 end-to-end consistency tests — golden-path validation of the trade truth layer."""
import pytest
import aiosqlite

from unittest.mock import AsyncMock, patch

from models import Rule, Trade
import database
from database import (
    init_db,
    save_rule,
    save_trade,
    get_rule,
    get_rule_versions,
    finalize_trade_outcome,
    persist_rule_revision,
    save_rule_version,
)
from rule_validation import evaluate_paper_rules, evaluate_promotion_gate, record_validation_result
from autopilot_api import promote_autopilot_rule, PromoteRuleRequest
from performance_ledger import compute_source_performance
from trade_utils import get_trade_realized_pnl
import config


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    """Give each test its own DB file."""
    db_path = str(tmp_path / "e2e.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)


def _make_rule(**overrides) -> Rule:
    defaults = dict(
        name="S9 E2E Test Rule",
        symbol="AAPL",
        enabled=False,
        conditions=[{"indicator": "RSI", "params": {"length": 14}, "operator": "<", "value": 30}],
        logic="AND",
        action={"type": "BUY", "asset_type": "STK", "quantity": 10, "order_type": "MKT"},
        status="paper",
        ai_generated=True,
        created_by="ai",
    )
    defaults.update(overrides)
    return Rule(**defaults)


def _make_entry_trade(rule: Rule, idx: int) -> Trade:
    return Trade(
        rule_id=rule.id,
        rule_name=rule.name,
        symbol="AAPL",
        action="BUY",
        asset_type="STK",
        quantity=10,
        order_type="MKT",
        limit_price=None,
        fill_price=150.0 + idx,
        status="FILLED",
        timestamp=f"2026-03-{10 + idx:02d}T10:00:00+00:00",
        source="rule",
        mode="PAPER",
        opened_at=f"2026-03-{10 + idx:02d}T10:00:00+00:00",
        entry_price=150.0 + idx,
    )


def _make_exit_trade(rule: Rule, entry_trade: Trade, idx: int) -> Trade:
    return Trade(
        rule_id=rule.id,
        rule_name=f"EXIT:{rule.name}",
        symbol="AAPL",
        action="SELL",
        asset_type="STK",
        quantity=10,
        order_type="MKT",
        limit_price=None,
        fill_price=155.0 + idx,
        status="FILLED",
        timestamp=f"2026-03-{10 + idx:02d}T14:00:00+00:00",
        source="rule",
        mode="PAPER",
        position_id=entry_trade.id,
    )


# ── Test 1: Paper rule → closed trades → validation → promotion ─────────────

@pytest.mark.anyio
async def test_golden_path_paper_rule_to_promotion(_isolated_db, anyio_backend):
    """Full lifecycle: create paper rule, finalize 6 trades, validate, promote."""
    await init_db()

    rule = _make_rule()
    await save_rule(rule)

    # Create 6 entry+exit pairs with canonical outcomes
    for i in range(6):
        entry = _make_entry_trade(rule, i)
        entry.position_id = entry.id
        await save_trade(entry)

        exit_trade = _make_exit_trade(rule, entry, i)
        await save_trade(exit_trade)

        # Finalize the exit trade with canonical P&L
        finalized = await finalize_trade_outcome(
            exit_trade.id,
            position_side="BUY",
            entry_price=150.0 + i,
            exit_price=155.0 + i,
            fees=0.0,
            close_reason="trailing_stop",
            position_id=entry.id,
        )
        assert finalized is not None
        assert finalized.outcome_quality == "canonical"
        assert finalized.realized_pnl == 50.0  # (155-150)*10 always

    # Run validation
    results = await evaluate_paper_rules()
    rule_result = next((r for r in results if r["rule_id"] == rule.id), None)
    assert rule_result is not None
    assert rule_result["passed"] is True
    assert rule_result["evaluated_closed_count"] == 6
    assert rule_result["data_quality"] == "canonical"

    # Check promotion gate
    eligible, reasons, latest = await evaluate_promotion_gate(rule)
    assert eligible is True, f"Expected eligible, got reasons: {reasons}"

    # Promote through the REAL endpoint handler (not bare persist_rule_revision)
    original_version = rule.version
    payload = PromoteRuleRequest(reason="Promoted via E2E test")
    # Mock log_ai_action since it touches the audit log table
    with patch("autopilot_api.log_ai_action", new_callable=AsyncMock):
        result = await promote_autopilot_rule(rule.id, payload)

    # Verify promotion happened
    promoted = await get_rule(rule.id)
    assert promoted is not None
    assert promoted.status == "active"
    assert promoted.enabled is True
    assert promoted.version >= 1  # version derived from DB MAX, always positive

    # Version snapshot exists
    versions = await get_rule_versions(rule.id)
    assert any(v["status"] == "active" for v in versions)


# ── Test 2: Version history is append-only ───────────────────────────────────

@pytest.mark.anyio
async def test_version_history_is_append_only(_isolated_db, anyio_backend):
    """Three sequential revisions produce three distinct version snapshots."""
    await init_db()

    rule = _make_rule(name="Version Test Rule")
    await save_rule(rule)
    initial_version = rule.version

    # Three revisions
    rule.ai_reason = "First change"
    await persist_rule_revision(rule, diff_summary="First change", author="ai")
    v1 = rule.version

    rule.status = "paused"
    rule.enabled = False
    rule.ai_reason = "Second change"
    await persist_rule_revision(rule, diff_summary="Second change", author="operator")
    v2 = rule.version

    rule.status = "retired"
    rule.ai_reason = "Third change"
    await persist_rule_revision(rule, diff_summary="Third change", author="operator")
    v3 = rule.version

    # All versions are distinct and increasing (version derived from DB MAX, not object)
    assert v1 < v2 < v3
    assert v2 == v1 + 1
    assert v3 == v2 + 1

    # All three snapshots exist in DB
    versions = await get_rule_versions(rule.id)
    version_numbers = [v["version"] for v in versions]
    assert v1 in version_numbers
    assert v2 in version_numbers
    assert v3 in version_numbers

    # Each snapshot has different status
    statuses = {v["version"]: v.get("status") for v in versions}
    assert statuses[v2] == "paused"
    assert statuses[v3] == "retired"


@pytest.mark.anyio
async def test_rule_version_duplicate_snapshot_raises(_isolated_db, anyio_backend):
    """Snapshots are append-only: duplicate rule/version writes must fail loudly."""
    await init_db()

    rule = _make_rule(name="Duplicate Snapshot Rule")
    await save_rule(rule)

    await save_rule_version(rule, diff_summary="Initial snapshot", author="ai")

    with pytest.raises(aiosqlite.IntegrityError):
        await save_rule_version(rule, diff_summary="Overwrite attempt", author="ai")

    versions = await get_rule_versions(rule.id)
    assert len([v for v in versions if v["version"] == rule.version]) == 1


# ── Test 3: Performance ledger matches canonical P&L ─────────────────────────

@pytest.mark.anyio
async def test_performance_matches_canonical_pnl(_isolated_db, anyio_backend):
    """Performance reader returns the exact realized P&L from finalized trades."""
    await init_db()

    # Create a finalized exit trade
    entry = Trade(
        rule_id="perf-rule", rule_name="Perf Rule", symbol="MSFT",
        action="BUY", asset_type="STK", quantity=5, order_type="MKT",
        limit_price=None, fill_price=380.0, status="FILLED",
        timestamp="2026-03-20T10:00:00+00:00", source="rule",
        mode="PAPER", opened_at="2026-03-20T10:00:00+00:00",
        entry_price=380.0,
    )
    entry.position_id = entry.id
    await save_trade(entry)

    exit_trade = Trade(
        rule_id="perf-rule", rule_name="EXIT:Perf Rule", symbol="MSFT",
        action="SELL", asset_type="STK", quantity=5, order_type="MKT",
        limit_price=None, fill_price=400.0, status="FILLED",
        timestamp="2026-03-20T14:00:00+00:00", source="rule",
        mode="PAPER", position_id=entry.id,
    )
    await save_trade(exit_trade)

    finalized = await finalize_trade_outcome(
        exit_trade.id,
        position_side="BUY",
        entry_price=380.0,
        exit_price=400.0,
        fees=0.0,
        close_reason="trailing_stop",
        position_id=entry.id,
    )
    assert finalized is not None
    assert finalized.realized_pnl == 100.0  # (400-380)*5

    # Performance reader should see this exact P&L
    perfs = await compute_source_performance(days=30)
    rule_perf = next((p for p in perfs if p["source"] == "rule"), None)
    assert rule_perf is not None
    assert rule_perf["realized_pnl"] == 100.0
    assert rule_perf["trades_count"] >= 1

    # Shared extractor agrees
    assert get_trade_realized_pnl(finalized) == 100.0
