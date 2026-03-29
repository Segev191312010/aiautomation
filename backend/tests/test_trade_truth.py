"""Stage 9 trade truth layer tests — model, finalize, trade_utils."""
import json
import pytest

from models import Trade
from trade_utils import get_trade_realized_pnl, is_closed_canonical, is_closed_any


# ── S9-01: Trade Model Backward Compat ───────────────────────────────────────

def _old_trade_dict() -> dict:
    """Pre-S9 trade payload with no canonical outcome fields."""
    return {
        "id": "old-trade-1",
        "rule_id": "rule-1",
        "rule_name": "Test Rule",
        "symbol": "AAPL",
        "action": "BUY",
        "asset_type": "STK",
        "quantity": 10,
        "order_type": "MKT",
        "limit_price": None,
        "fill_price": 150.0,
        "status": "FILLED",
        "order_id": 12345,
        "timestamp": "2026-01-15T10:00:00+00:00",
        "source": "rule",
        "metadata": {"pnl": 50.0, "paper": True},
    }


def test_old_trade_payload_deserializes():
    trade = Trade(**_old_trade_dict())
    assert trade.mode is None
    assert trade.decision_id is None
    assert trade.position_id is None
    assert trade.opened_at is None
    assert trade.closed_at is None
    assert trade.entry_price is None
    assert trade.exit_price is None
    assert trade.fees == 0.0
    assert trade.realized_pnl is None
    assert trade.pnl_pct is None
    assert trade.close_reason is None
    assert trade.outcome_quality is None


def test_canonical_fields_round_trip():
    trade = Trade(
        rule_id="r1", rule_name="R", symbol="SPY", action="SELL",
        asset_type="STK", quantity=5, order_type="MKT",
        limit_price=None, fill_price=400.0, status="FILLED",
        timestamp="2026-03-20T10:00:00+00:00",
        mode="LIVE", decision_id="dec-1", position_id="pos-1",
        opened_at="2026-03-19T10:00:00+00:00",
        closed_at="2026-03-20T10:00:00+00:00",
        entry_price=380.0, exit_price=400.0, fees=2.5,
        realized_pnl=97.5, pnl_pct=5.26, close_reason="trailing_stop",
        outcome_quality="canonical",
    )
    blob = trade.model_dump_json()
    reloaded = Trade.model_validate(json.loads(blob))
    assert reloaded.mode == "LIVE"
    assert reloaded.decision_id == "dec-1"
    assert reloaded.position_id == "pos-1"
    assert reloaded.entry_price == 380.0
    assert reloaded.exit_price == 400.0
    assert reloaded.realized_pnl == 97.5
    assert reloaded.pnl_pct == 5.26
    assert reloaded.close_reason == "trailing_stop"
    assert reloaded.outcome_quality == "canonical"


def test_outcome_quality_literal():
    for valid in ("canonical", "legacy_enriched", "legacy_unverified"):
        t = Trade(**{**_old_trade_dict(), "outcome_quality": valid})
        assert t.outcome_quality == valid

    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Trade(**{**_old_trade_dict(), "outcome_quality": "garbage"})


def test_mode_uses_uppercase():
    for valid in ("LIVE", "PAPER", "SIM"):
        t = Trade(**{**_old_trade_dict(), "mode": valid})
        assert t.mode == valid

    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Trade(**{**_old_trade_dict(), "mode": "live"})


# ── S9-07a: trade_utils ──────────────────────────────────────────────────────

def test_get_trade_realized_pnl_prefers_canonical():
    trade = Trade(**{**_old_trade_dict(), "realized_pnl": 50.0})
    trade.metadata["pnl"] = 40.0
    assert get_trade_realized_pnl(trade) == 50.0


def test_get_trade_realized_pnl_falls_back_to_metadata():
    trade = Trade(**_old_trade_dict())
    assert trade.realized_pnl is None
    assert get_trade_realized_pnl(trade) == 50.0  # from metadata["pnl"]


def test_get_trade_realized_pnl_returns_none_when_missing():
    d = _old_trade_dict()
    d["metadata"] = {}
    trade = Trade(**d)
    assert get_trade_realized_pnl(trade) is None


def test_is_closed_canonical():
    d = _old_trade_dict()
    d.update(closed_at="2026-03-20T10:00:00+00:00", realized_pnl=50.0, outcome_quality="canonical")
    trade = Trade(**d)
    assert is_closed_canonical(trade) is True

    # Missing outcome_quality
    d2 = _old_trade_dict()
    d2.update(closed_at="2026-03-20T10:00:00+00:00", realized_pnl=50.0)
    trade2 = Trade(**d2)
    assert is_closed_canonical(trade2) is False


def test_is_closed_any():
    # Canonical closed
    d = _old_trade_dict()
    d.update(closed_at="2026-03-20T10:00:00+00:00", realized_pnl=50.0)
    assert is_closed_any(Trade(**d)) is True

    # Legacy with metadata pnl only
    assert is_closed_any(Trade(**_old_trade_dict())) is True

    # No pnl at all
    d_no_pnl = _old_trade_dict()
    d_no_pnl["metadata"] = {}
    assert is_closed_any(Trade(**d_no_pnl)) is False


# ── S9-02: finalize_trade_outcome ────────────────────────────────────────────

@pytest.fixture
def _patch_db(tmp_path, monkeypatch):
    """Patch DB_PATH to a temp file for test isolation."""
    db_path = str(tmp_path / "test.db")
    import config
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    import database
    monkeypatch.setattr(database, "DB_PATH", db_path)
    return database


@pytest.mark.anyio
async def test_finalize_long_exit_pnl(_patch_db):
    db = _patch_db
    await db.init_db()
    trade = Trade(
        rule_id="r1", rule_name="R", symbol="AAPL", action="SELL",
        asset_type="STK", quantity=10, order_type="MKT",
        limit_price=None, fill_price=155.0, status="FILLED",
        timestamp="2026-03-20T14:00:00+00:00", source="rule",
    )
    await db.save_trade(trade)

    result = await db.finalize_trade_outcome(
        trade.id, position_side="BUY", entry_price=150.0, exit_price=155.0,
        fees=0.0, close_reason="trailing_stop", position_id="entry-1",
    )
    assert result is not None
    assert result.realized_pnl == 50.0
    assert abs(result.pnl_pct - 3.33) < 0.01
    assert result.outcome_quality == "canonical"
    assert result.close_reason == "trailing_stop"
    assert result.closed_at is not None
    assert result.position_id == "entry-1"


@pytest.mark.anyio
async def test_finalize_short_exit_pnl(_patch_db):
    db = _patch_db
    await db.init_db()
    trade = Trade(
        rule_id="r1", rule_name="R", symbol="AAPL", action="BUY",
        asset_type="STK", quantity=10, order_type="MKT",
        limit_price=None, fill_price=150.0, status="FILLED",
        timestamp="2026-03-20T14:00:00+00:00", source="rule",
    )
    await db.save_trade(trade)

    result = await db.finalize_trade_outcome(
        trade.id, position_side="SELL", entry_price=155.0, exit_price=150.0,
        fees=0.0, close_reason="hard_stop", position_id="entry-2",
    )
    assert result is not None
    assert result.realized_pnl == 50.0
    assert abs(result.pnl_pct - 3.33) < 0.01  # (155/150 - 1) * 100


@pytest.mark.anyio
async def test_finalize_with_fees(_patch_db):
    db = _patch_db
    await db.init_db()
    trade = Trade(
        rule_id="r1", rule_name="R", symbol="SPY", action="SELL",
        asset_type="STK", quantity=10, order_type="MKT",
        limit_price=None, fill_price=155.0, status="FILLED",
        timestamp="2026-03-20T14:00:00+00:00", source="rule",
    )
    await db.save_trade(trade)

    result = await db.finalize_trade_outcome(
        trade.id, position_side="BUY", entry_price=150.0, exit_price=155.0,
        fees=5.0, close_reason="trailing_stop",
    )
    assert result is not None
    assert result.realized_pnl == 45.0  # 50 - 5
    assert result.fees == 5.0


@pytest.mark.anyio
async def test_finalize_not_found_returns_none(_patch_db):
    db = _patch_db
    await db.init_db()
    result = await db.finalize_trade_outcome(
        "nonexistent", position_side="BUY", entry_price=100.0,
        exit_price=110.0, close_reason="test",
    )
    assert result is None


@pytest.mark.anyio
async def test_finalize_sets_backward_compat_metadata(_patch_db):
    db = _patch_db
    await db.init_db()
    trade = Trade(
        rule_id="r1", rule_name="R", symbol="MSFT", action="SELL",
        asset_type="STK", quantity=5, order_type="MKT",
        limit_price=None, fill_price=400.0, status="FILLED",
        timestamp="2026-03-20T10:00:00+00:00", source="rule",
    )
    await db.save_trade(trade)

    result = await db.finalize_trade_outcome(
        trade.id, position_side="BUY", entry_price=380.0, exit_price=400.0,
        fees=0.0, close_reason="trailing_stop",
    )
    assert result is not None
    assert result.metadata["pnl"] == result.realized_pnl


@pytest.mark.anyio
async def test_update_trade_status_respects_user_scope(_patch_db):
    db = _patch_db
    await db.init_db()
    trade = Trade(
        rule_id="r1", rule_name="R", symbol="QQQ", action="BUY",
        asset_type="STK", quantity=2, order_type="MKT",
        limit_price=None, fill_price=None, status="PENDING",
        timestamp="2026-03-20T10:00:00+00:00", source="rule",
    )
    await db.save_trade(trade, user_id="alice")

    await db.update_trade_status(trade.id, "FILLED", 101.0, user_id="bob")

    loaded = await db.get_trade(trade.id, user_id="alice")
    assert loaded is not None
    assert loaded.status == "PENDING"
    assert loaded.fill_price is None


@pytest.mark.anyio
async def test_finalize_trade_outcome_respects_user_scope(_patch_db):
    db = _patch_db
    await db.init_db()
    trade = Trade(
        rule_id="r1", rule_name="R", symbol="IWM", action="SELL",
        asset_type="STK", quantity=4, order_type="MKT",
        limit_price=None, fill_price=205.0, status="FILLED",
        timestamp="2026-03-20T10:00:00+00:00", source="rule",
    )
    await db.save_trade(trade, user_id="alice")

    result = await db.finalize_trade_outcome(
        trade.id,
        position_side="BUY",
        entry_price=200.0,
        exit_price=205.0,
        close_reason="manual",
        user_id="bob",
    )
    assert result is None

    loaded = await db.get_trade(trade.id, user_id="alice")
    assert loaded is not None
    assert loaded.realized_pnl is None
    assert loaded.closed_at is None


@pytest.mark.anyio
async def test_get_trade_by_id(_patch_db):
    db = _patch_db
    await db.init_db()
    trade = Trade(
        rule_id="r1", rule_name="R", symbol="GOOG", action="BUY",
        asset_type="STK", quantity=3, order_type="MKT",
        limit_price=None, fill_price=100.0, status="FILLED",
        timestamp="2026-03-20T10:00:00+00:00", source="rule",
    )
    await db.save_trade(trade)

    loaded = await db.get_trade(trade.id)
    assert loaded is not None
    assert loaded.id == trade.id
    assert loaded.symbol == "GOOG"

    missing = await db.get_trade("nonexistent")
    assert missing is None
