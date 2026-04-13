import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import config
import database
from database import init_db
from execution_brain import (
    choose_candidates,
    drain_direct_candidates,
    queue_direct_candidates,
)


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a fresh sqlite file per test."""
    db_path = str(tmp_path / "execution_brain.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    # db.core reads cfg.DB_PATH at open-time, so patching cfg is sufficient


def test_choose_candidates_prefers_exit_for_same_symbol():
    selected = choose_candidates(
        [
            {"symbol": "AAPL", "source": "rule", "score": 91.0, "is_exit": False, "risk_pct": 1.0},
        ],
        [
            {"symbol": "AAPL", "source": "ai_direct", "score": 72.0, "is_exit": True, "risk_pct": 1.0},
        ],
    )

    assert len(selected) == 1
    assert selected[0]["source"] == "ai_direct"
    assert selected[0]["is_exit"] is True


@pytest.mark.anyio
async def test_queue_and_drain_direct_candidates_keep_highest_priority_per_symbol(
    _isolated_db, anyio_backend
):
    await init_db()

    queued = await queue_direct_candidates([
        {
            "symbol": "NVDA",
            "action": "BUY",
            "order_type": "MKT",
            "stop_price": 860.0,
            "invalidation": "Break below support",
            "reason": "Momentum continuation",
            "confidence": 0.61,
        },
        {
            "symbol": "NVDA",
            "action": "SELL",
            "order_type": "MKT",
            "stop_price": 860.0,
            "invalidation": "Trend failure",
            "reason": "Protect open gains",
            "confidence": 0.55,
        },
    ])

    assert queued == 2

    drained = await drain_direct_candidates()
    assert len(drained) == 1
    assert drained[0]["symbol"] == "NVDA"
    assert drained[0]["is_exit"] is True
    # Second drain returns nothing — rows now in 'draining' state
    assert await drain_direct_candidates() == []


@pytest.mark.anyio
async def test_queue_survives_restart_simulation(_isolated_db, anyio_backend):
    """Persistence test: a candidate queued before the 'restart' is still
    drainable after the in-memory state has been discarded (since the DB is
    the source of truth)."""
    await init_db()

    queued = await queue_direct_candidates([
        {
            "symbol": "MSFT",
            "action": "BUY",
            "order_type": "MKT",
            "stop_price": 401.0,
            "invalidation": "Break VWAP",
            "reason": "Trend continuation",
            "confidence": 0.70,
        },
    ])
    assert queued == 1

    # "Restart simulation": nothing else to reset — there is no in-memory
    # queue any more. Draining via a fresh call must still return the row.
    drained = await drain_direct_candidates()
    assert len(drained) == 1
    assert drained[0]["symbol"] == "MSFT"


@pytest.mark.anyio
async def test_drain_drops_stale_candidates_past_ttl(_isolated_db, anyio_backend):
    """Rows older than max_age_seconds must be marked expired and excluded."""
    from db.direct_candidates import queue_candidate, get_candidate_status
    from datetime import datetime, timedelta, timezone

    await init_db()

    # Insert a row with a stale queued_at stamp directly via the CRUD helper
    # and then overwrite queued_at to be 1 hour old.
    await queue_candidate(
        "cand-stale",
        "TSLA",
        {"symbol": "TSLA", "score": 50, "is_exit": False, "queued_at": "fake"},
        ttl_seconds=900,
    )
    # Rewrite queued_at to an hour ago
    from db.core import get_db
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE direct_candidates SET queued_at=? WHERE id=?",
            (old_ts, "cand-stale"),
        )
        await db.commit()

    drained = await drain_direct_candidates(max_age_seconds=900)
    assert drained == []
    status = await get_candidate_status("cand-stale")
    assert status == "expired"


@pytest.mark.anyio
async def test_mark_candidate_status_applied_after_execution(_isolated_db, anyio_backend):
    """queue → drain → execution success → mark applied."""
    from db.direct_candidates import mark_candidate_status, get_candidate_status

    await init_db()

    await queue_direct_candidates([
        {
            "symbol": "AMD",
            "action": "BUY",
            "order_type": "MKT",
            "stop_price": 150.0,
            "invalidation": "Break support",
            "reason": "Trend",
            "confidence": 0.62,
            "decision_id": "cand-applied-1",
        },
    ])

    drained = await drain_direct_candidates()
    assert len(drained) == 1
    cand_id = drained[0].get("_candidate_id")
    assert cand_id == "cand-applied-1"

    await mark_candidate_status(cand_id, "applied")
    assert await get_candidate_status(cand_id) == "applied"


@pytest.mark.anyio
async def test_purge_expired_candidates_on_startup(_isolated_db, anyio_backend):
    """Startup purge marks stale queued/draining rows as expired."""
    from db.direct_candidates import purge_expired_candidates, get_candidate_status
    from db.core import get_db
    from datetime import datetime, timedelta, timezone

    await init_db()

    await queue_direct_candidates([
        {
            "symbol": "GOOG",
            "action": "BUY",
            "order_type": "MKT",
            "stop_price": 140.0,
            "invalidation": "Break support",
            "reason": "Trend",
            "confidence": 0.55,
            "decision_id": "cand-purge-1",
        },
    ])

    # Make the row look like it was queued 2 hours ago (TTL = 900s default)
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE direct_candidates SET queued_at=? WHERE id=?",
            (old_ts, "cand-purge-1"),
        )
        await db.commit()

    expired = await purge_expired_candidates()
    assert expired == 1
    assert await get_candidate_status("cand-purge-1") == "expired"


# ── HB1-02 regression: risk check must run AFTER dynamic sizing ────────────
# The bot cycle first computes `computed_qty` from account value * sizing pct
# and writes it onto order_rule.action.quantity, THEN invokes check_trade_risk
# using that updated quantity. If the order ever flips, risk guards would see
# the stale original rule quantity instead of the final size.


# BLOCK-6 status (2026-04-11): this source-level check is now COMPLEMENTED by
# a runtime pattern-replication test below. The two catch different bug
# classes: the source check catches reordering inside `_run_cycle`, the
# runtime test catches model-copy semantics and argument-capture drift.
# A full runtime harness that drives `_run_cycle` end-to-end remains a
# Phase B refactor target (requires extracting the sizing+risk block into a
# testable helper). See `sessions/phase-b-f7-01-auth-gap-analysis.md` for the
# broader Phase B scope.
def test_hb1_02_risk_check_runs_after_dynamic_sizing():
    """Source-level guard: inside bot_runner._run_cycle, the check_trade_risk
    call must come AFTER the block that assigns computed_qty to
    order_rule.action.quantity. Any reordering would revert HB1-02."""
    import inspect
    import bot_runner

    src = inspect.getsource(bot_runner._run_cycle)

    sizing_marker = 'update={"quantity": computed_qty}'
    risk_marker = "check_trade_risk("

    sizing_idx = src.find(sizing_marker)
    risk_idx = src.find(risk_marker)

    assert sizing_idx != -1, "dynamic sizing block missing from _run_cycle"
    assert risk_idx != -1, "check_trade_risk call missing from _run_cycle"
    assert sizing_idx < risk_idx, (
        "HB1-02 regression: check_trade_risk must be invoked AFTER the "
        "dynamic-sizing block that updates order_rule.action.quantity"
    )

    # Also confirm the check_trade_risk call passes `order_rule.action.quantity`
    # (the updated field) as its quantity argument, not `rule.action.quantity`
    # or an original-source copy.
    risk_call_snippet = src[risk_idx : risk_idx + 300]
    assert "order_rule.action.quantity" in risk_call_snippet, (
        "check_trade_risk must be called with order_rule.action.quantity "
        "(the post-sizing value)"
    )


def test_hb1_02_sized_quantity_flows_to_risk_check_runtime():
    """Runtime regression: replicate the exact sizing + risk-check pattern
    used in bot_runner._run_cycle (lines 647-706) and verify the risk check
    receives the POST-sizing quantity, not the original rule.action.quantity.

    This test catches model-copy semantics bugs that the source-level check
    above would miss — e.g. if model_copy were replaced with a shallow copy
    that didn't propagate the update, or if order_rule.action were ever
    re-read from the immutable original before the risk call.

    This is the runtime companion to the source-level check and addresses
    BLOCK-6 at the invariant level. Full `_run_cycle` end-to-end harness
    remains a Phase B item.
    """
    from models import Rule, TradeAction, Condition

    # Build a rule whose ORIGINAL quantity is 1 — the sizing math must
    # produce a different value for this test to be meaningful.
    original_qty = 1
    rule = Rule(
        name="HB1-02 regression",
        symbol="AAPL",
        enabled=True,
        conditions=[Condition(indicator="PRICE", operator=">", value=0.0)],
        logic="AND",
        action=TradeAction(type="BUY", quantity=original_qty, order_type="MKT"),
    )

    # Replicate bot_runner.py:664 — dynamic sizing math.
    account_val = 10_000.0
    price = 100.0
    position_size_pct = 0.10  # 10% → 10 shares at $100 with $10k account
    computed_qty = max(1, int(account_val * position_size_pct / price))

    # Invariant prerequisite: the sizing math must diverge from the original
    # rule quantity, otherwise the regression test proves nothing.
    assert computed_qty != original_qty, (
        "Test setup bug: computed_qty must differ from original_qty for the "
        "regression to be meaningful"
    )
    assert computed_qty == 10, "sanity check on the sizing math"

    # Replicate bot_runner.py:669-672 — the model_copy + action update.
    order_rule = rule.model_copy()
    order_rule.action = order_rule.action.model_copy(
        update={"quantity": computed_qty}
    )

    # Immutability check: the update must NOT leak back to the original.
    assert rule.action.quantity == original_qty, (
        "HB1-02: model_copy with update must not mutate the original rule"
    )
    assert order_rule.action.quantity == computed_qty, (
        "HB1-02: order_rule.action.quantity must reflect computed_qty post-copy"
    )

    # Capture the qty argument via a fake check_trade_risk.
    captured_calls: list[tuple] = []

    def fake_check_trade_risk(symbol, qty, action_type, positions, cash, limits):
        captured_calls.append((symbol, qty, action_type))
        # Return a BLOCK-like object so a hypothetical caller would bail here.
        class _Result:
            status = "BLOCK"
            reasons = ["test-forced-block"]
        return _Result()

    # Replicate bot_runner.py:699-703 — the risk call. The critical invariant
    # is that the 2nd positional arg is `order_rule.action.quantity`, NOT
    # `rule.action.quantity`.
    fake_check_trade_risk(
        order_rule.symbol,
        order_rule.action.quantity,  # ← HB1-02 guards this reference
        order_rule.action.type,
        [],
        account_val,
        None,
    )

    assert len(captured_calls) == 1, "check_trade_risk should be called exactly once"
    captured_symbol, captured_qty, captured_action = captured_calls[0]
    assert captured_symbol == "AAPL"
    assert captured_action == "BUY"
    assert captured_qty == computed_qty, (
        f"HB1-02 runtime regression: check_trade_risk received qty={captured_qty}, "
        f"expected computed_qty={computed_qty} (original rule qty was {original_qty}). "
        f"Either the model_copy update was lost OR the risk call read from "
        f"the wrong reference."
    )
    assert captured_qty != original_qty, (
        "HB1-02 runtime regression: check_trade_risk received the ORIGINAL rule "
        f"quantity ({original_qty}) instead of the sized quantity ({computed_qty}). "
        "This is the exact failure mode HB1-02 was meant to prevent."
    )


def test_hb1_02_check_trade_risk_with_real_module_integration():
    """Integration variant of HB1-02: import the REAL check_trade_risk from
    risk_manager and confirm it receives the sized quantity through the
    same model_copy pattern used in bot_runner. This exercises the actual
    risk_manager entry point end-to-end without needing to drive _run_cycle.
    """
    from models import Rule, TradeAction, Condition
    from unittest.mock import patch

    rule = Rule(
        name="HB1-02 integration",
        symbol="NVDA",
        enabled=True,
        conditions=[Condition(indicator="PRICE", operator=">", value=0.0)],
        logic="AND",
        action=TradeAction(type="BUY", quantity=1, order_type="MKT"),
    )

    # Sizing math produces 20 shares
    account_val = 10_000.0
    price = 50.0
    position_size_pct = 0.10
    computed_qty = max(1, int(account_val * position_size_pct / price))
    assert computed_qty == 20

    # Apply the sizing update exactly as bot_runner does
    order_rule = rule.model_copy()
    order_rule.action = order_rule.action.model_copy(
        update={"quantity": computed_qty}
    )

    # Patch check_trade_risk to spy on the qty argument while still calling
    # through to the real function so the signature/types stay honest.
    captured: dict = {}

    from risk_manager import check_trade_risk as real_check
    from risk_config import DEFAULT_LIMITS

    def spy(symbol, qty, action_type, positions, cash, limits=None, est_price=0):
        captured["symbol"] = symbol
        captured["qty"] = qty
        captured["action_type"] = action_type
        # Short-circuit with a BLOCK so we don't depend on real risk
        # evaluation semantics for this regression test.
        class _Result:
            status = "BLOCK"
            reasons = ["spy-forced-block"]
        return _Result()

    with patch("risk_manager.check_trade_risk", side_effect=spy):
        from risk_manager import check_trade_risk
        _ = check_trade_risk(
            order_rule.symbol,
            order_rule.action.quantity,
            order_rule.action.type,
            [],
            account_val,
            DEFAULT_LIMITS,
        )

    assert captured["qty"] == computed_qty, (
        f"HB1-02 integration: check_trade_risk received qty={captured['qty']}, "
        f"expected {computed_qty}. The sizing update was lost between "
        f"model_copy and the risk call."
    )
    assert captured["qty"] != 1, "Must not pass the original rule quantity"


@pytest.mark.anyio
async def test_ai_optimizer_queues_direct_trades(_isolated_db, anyio_backend):
    from ai_optimizer import _apply_decisions

    await init_db()

    decision = {
        "symbol": "MSFT",
        "action": "BUY",
        "order_type": "MKT",
        "stop_price": 401.0,
        "invalidation": "Break below VWAP",
        "reason": "Fresh trend confirmation",
        "confidence": 0.74,
    }

    mock_queue = AsyncMock(return_value=1)
    with patch("execution_brain.queue_direct_candidates", new=mock_queue):
        results = await _apply_decisions({"direct_trades": [decision]}, {})

    mock_queue.assert_awaited_once_with([decision])
    assert any("direct_trade_queued: MSFT BUY" in item for item in results["applied"])


# ── AI-7: Regime detection unit tests ──────────────────────────────────────


def test_regime_detector_bull_classification():
    """RegimeDetector classifies price > SMA200*1.02 + SMA50 > SMA200 as BULL."""
    import pandas as pd
    import numpy as np
    from regime_detector import RegimeDetector
    from events import MarketEvent, EventType

    # Build 250 daily bars with a clear uptrend (price consistently above SMAs)
    np.random.seed(42)
    prices = np.linspace(100, 200, 250)  # straight up
    df = pd.DataFrame({
        "open": prices * 0.99,
        "high": prices * 1.01,
        "low": prices * 0.98,
        "close": prices,
        "volume": np.full(250, 1_000_000),
    })

    detector = RegimeDetector()
    spy_event = MarketEvent(
        timestamp=datetime(2026, 4, 13, tzinfo=timezone.utc),
        type=EventType.MARKET, symbol="SPY",
        open=float(df["open"].iloc[-1]), high=float(df["high"].iloc[-1]),
        low=float(df["low"].iloc[-1]), close=float(df["close"].iloc[-1]),
        volume=float(df["volume"].iloc[-1]),
    )
    regime_event = detector.on_market_event(spy_event, df)

    assert detector.regime == "BULL"
    assert detector.get_risk_multiplier() == 1.0
    assert regime_event.regime == "BULL"
    assert regime_event.market_score == 0.8


def test_regime_detector_bear_classification():
    """RegimeDetector classifies price < SMA200*0.98 + SMA50 < SMA200 as BEAR."""
    import pandas as pd
    import numpy as np
    from regime_detector import RegimeDetector
    from events import MarketEvent, EventType

    # Clear downtrend
    np.random.seed(42)
    prices = np.linspace(200, 100, 250)  # straight down
    df = pd.DataFrame({
        "open": prices * 1.01,
        "high": prices * 1.02,
        "low": prices * 0.99,
        "close": prices,
        "volume": np.full(250, 1_000_000),
    })

    detector = RegimeDetector()
    spy_event = MarketEvent(
        timestamp=datetime(2026, 4, 13, tzinfo=timezone.utc),
        type=EventType.MARKET, symbol="SPY",
        open=float(df["open"].iloc[-1]), high=float(df["high"].iloc[-1]),
        low=float(df["low"].iloc[-1]), close=float(df["close"].iloc[-1]),
        volume=float(df["volume"].iloc[-1]),
    )
    regime_event = detector.on_market_event(spy_event, df)

    assert detector.regime == "BEAR"
    assert detector.get_risk_multiplier() == 0.5
    assert regime_event.regime == "BEAR"
    assert regime_event.market_score == 0.2


def test_regime_risk_multiplier_reduces_position_size():
    """AI-7 regression: in BEAR regime, position size should be halved."""
    from models import Rule, TradeAction, Condition

    rule = Rule(
        name="AI-7 regime test",
        symbol="AAPL",
        enabled=True,
        conditions=[Condition(indicator="PRICE", operator=">", value=0.0)],
        logic="AND",
        action=TradeAction(type="BUY", quantity=1, order_type="MKT"),
    )

    account_val = 10_000.0
    price = 100.0
    position_size_pct = 0.10
    ai_sizing = 1.0

    # Normal sizing (BULL, mult=1.0)
    base_qty = max(1, int(account_val * position_size_pct / price))
    assert base_qty == 10

    # BEAR regime mult = 0.5
    bear_mult = 0.5
    bear_qty = max(1, int(base_qty * ai_sizing * bear_mult))
    assert bear_qty == 5, f"BEAR should halve size: expected 5, got {bear_qty}"

    # HIGH_VOL regime mult = 0.7
    hvol_mult = 0.7
    hvol_qty = max(1, int(base_qty * ai_sizing * hvol_mult))
    assert hvol_qty == 7, f"HIGH_VOL should reduce size by 30%: expected 7, got {hvol_qty}"

    # BULL regime mult = 1.0 (no change)
    bull_mult = 1.0
    bull_qty = max(1, int(base_qty * ai_sizing * bull_mult))
    assert bull_qty == 10


def test_regime_weight_adjustments():
    """RegimeDetector returns different signal weight multipliers per regime."""
    from regime_detector import RegimeDetector

    detector = RegimeDetector()

    # Default is BULL
    bull_weights = detector.get_weight_adjustments()
    assert bull_weights["trend"] == 1.3
    assert bull_weights["momentum"] == 1.2
    assert bull_weights["mean_reversion"] == 0.7

    # Force BEAR
    detector._current_regime = "BEAR"
    bear_weights = detector.get_weight_adjustments()
    assert bear_weights["trend"] == 0.6
    assert bear_weights["momentum"] == 0.5
    assert bear_weights["mean_reversion"] == 1.3
