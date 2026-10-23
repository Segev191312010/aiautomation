"""The v7b golden: the recorded PAPER quantities under the owner's identity multipliers.

v7b adds data only: four sendable identities, their caps rows and their size
multipliers. This file pins those rows to the owner's tables (plan rev 3,
2026-10-15) and applies each multiplier to every ``shadow_accepted`` decision
the PAPER ledger had recorded by 2026-10-15 (``tests/fixtures/
v7b_recorded_accepted_decisions.json``, identifier-free). No ORB_V2, VWAP_PB_V2
or GAP_GO_V2 decision had been accepted yet (2026-10-14 produced only
``ai_abstained`` and ``rule_rejected`` outcomes for them), so the recorded
quantities are the MOMO and V1 ones; the arithmetic the identities inherit is
the same ``scaled_quantity`` (floor, never up) and the same
``size_scaled_to_zero`` terminal code.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
import json
import math
from pathlib import Path
import sys
from types import ModuleType

import pytest

from trading.contracts import AIReview, PortfolioSnapshot, RiskPolicy, RuleSetup
from trading.risk.engine import SIZE_SCALED_TO_ZERO, scaled_quantity, size_and_evaluate_trade
from trading.risk.identity_sizing import (
    IDENTITY_SIZE_MULTIPLIER,
    NO_LIMIT_TEST_SIZE_MULTIPLIER,
    V7C_SIZE_MULTIPLIER,
)
from trading.sendable_identities import SENDABLE_IDENTITIES

FIXTURE = Path(__file__).parent / "fixtures" / "v7b_recorded_accepted_decisions.json"
BRIDGE_PATH = Path(__file__).resolve().parents[1] / "scripts_local" / "paper_bridge.py"

# The owner's tables (plan rev 3): multiplier, max positions, max entries/day, last entry offset.
# The multipliers are v7c's (V7C_SIZE_MULTIPLIER, what deploy undo restores).
OWNER_TABLE = {
    "MOMO_HOD_V2": (Decimal("1"), 3, None, timedelta(minutes=60)),
    "ORB_V2": (Decimal("0.25"), 1, 1, timedelta(minutes=30)),
    "VWAP_PB_V2": (Decimal("0.5"), 1, 2, timedelta(minutes=30)),
    "GAP_GO_V2": (Decimal("0.5"), 1, 1, timedelta(minutes=30)),
}
# The no-limit PAPER test L-T1 (owner, 2026-10-23): the Step 0 rule's sizes, x1 until
# Step 0 shows an identity with n >= 30 sent trades and mean R <= 0 (it keeps its v7c row).
T1_MULTIPLIERS = {name: Decimal("1") for name in OWNER_TABLE}
# Both tables run through the golden: v7c's keeps the quarter-size zero branch covered.
TABLES = {"v7c": V7C_SIZE_MULTIPLIER, "t1": NO_LIMIT_TEST_SIZE_MULTIPLIER}
# Recorded quantities that floor to zero at each multiplier (none: every recorded
# intent was thousands of shares). Pinned so a fixture change is visible.
SCALED_TO_ZERO_COUNTS = {"MOMO_HOD_V2": 0, "ORB_V2": 0, "VWAP_PB_V2": 0, "GAP_GO_V2": 0}


def _bridge() -> ModuleType:
    spec = importlib.util.spec_from_file_location("paper_bridge_v7b_golden", BRIDGE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rows() -> list[dict[str, object]]:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
    assert len(rows) == 6
    for row in rows:
        assert set(row) == {"index", "session_date", "strategy_name", "symbol", "quantity",
                            "entry_limit", "stop_price", "target_price"}
        assert isinstance(row["quantity"], int) and row["quantity"] >= 1
    return rows


def test_the_shipped_tables_are_the_owners_tables() -> None:
    # Rewritten for the no-limit test L-T1 (A and B merged; explicit, not weakened): the shipped
    # tables are the test's. Sizes follow the Step 0 rule (NO_LIMIT_TEST_SIZE_MULTIPLIER, x1 for
    # now); the bridge's caps rows have no position or entries/day cap and keep the owner's
    # last-entry offsets, and there is no account-wide cap; both switches are on. The owner's
    # v7b/v7c tables stay pinned row for row (V7C_SIZE_MULTIPLIER and test_paper_bridge.V7C_CAPS,
    # what deploy undo restores).
    from tests.test_paper_bridge import V7C_CAPS
    from trading.risk.no_limit_test import NO_LIMIT_TEST

    bridge = _bridge()
    assert tuple(SENDABLE_IDENTITIES) == ("MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2")
    assert set(SENDABLE_IDENTITIES) == set(OWNER_TABLE) == set(IDENTITY_SIZE_MULTIPLIER) == set(bridge.IDENTITY_CAPS)
    assert bridge.SENDABLE_STRATEGY_NAMES == frozenset(SENDABLE_IDENTITIES)
    assert (bridge.NO_LIMIT_TEST, NO_LIMIT_TEST, bridge.BRIDGE_VERSION) == (True, True, "v7c-t1")
    assert IDENTITY_SIZE_MULTIPLIER is NO_LIMIT_TEST_SIZE_MULTIPLIER
    assert dict(IDENTITY_SIZE_MULTIPLIER) == T1_MULTIPLIERS
    for name, (multiplier, positions, entries, offset) in OWNER_TABLE.items():
        assert V7C_SIZE_MULTIPLIER[name] == multiplier, name
        caps = bridge.IDENTITY_CAPS[name]
        assert (caps.max_positions, caps.max_entries_per_day, caps.last_entry_before_close) == (
            None, None, offset
        ), name
        v7c = V7C_CAPS[name]
        assert (v7c.max_positions, v7c.max_entries_per_day, v7c.last_entry_before_close) == (
            positions, entries, offset
        ), name
    assert bridge.global_max_open() is None


@pytest.mark.parametrize("table", sorted(TABLES))
@pytest.mark.parametrize("identity", sorted(OWNER_TABLE))
def test_every_recorded_quantity_scales_to_floor_of_quantity_times_the_multiplier(identity: str, table: str) -> None:
    multiplier = TABLES[table][identity]
    zero = 0
    for row in _rows():
        recorded = int(row["quantity"])  # type: ignore[arg-type]
        expected = math.floor(recorded * multiplier)
        scaled = scaled_quantity(recorded, multiplier)
        assert scaled == expected, (identity, row["symbol"])
        assert scaled <= recorded  # never up
        zero += scaled == 0
    assert zero == SCALED_TO_ZERO_COUNTS[identity]


NOW = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)


def _setup(row: dict[str, object]) -> RuleSetup:
    return RuleSetup(
        run_id="run-1", candidate_id="cand-1", strategy_id="strategy-1", strategy_version=1,
        strategy_kind="momo_hod", symbol=str(row["symbol"]), atr=Decimal("0.08"),
        entry_limit=Decimal(str(row["entry_limit"])),
        stop_price=Decimal(str(row["stop_price"])),
        target_price=Decimal(str(row["target_price"])),
        bar_fingerprints=("a" * 64,), quote_fingerprint="b" * 64, detected_at=NOW,
    )


def _review(setup: RuleSetup) -> AIReview:
    return AIReview(
        setup_fingerprint=setup.fingerprint(), decision="PROPOSE", rationale="v7b golden",
        model_id="fixture", prompt_version="fixture", response_hash="c" * 64, latency_ms=1,
        cost_usd=Decimal("0"),
    )


@pytest.mark.parametrize("table", sorted(TABLES))
@pytest.mark.parametrize("identity", sorted(OWNER_TABLE))
def test_the_engine_reproduces_each_recorded_quantity_then_scales_it(identity: str, table: str) -> None:
    """End to end: a portfolio sized so the base policy allows exactly the recorded quantity."""
    multiplier = TABLES[table][identity]
    policy = RiskPolicy()
    now = NOW
    # One synthetic high-priced, wide-stop setup where the base policy allows three shares:
    # floor(3 x 0.25) == 0, so the quarter-size identity exercises the scaled-to-zero branch.
    zero_row = {"symbol": "ZZZZ", "quantity": 3, "entry_limit": "900.00", "stop_price": "880.00",
                "target_price": "940.00"}
    zero_seen = False
    for row in (*_rows(), zero_row):
        setup = _setup(row)
        recorded = int(row["quantity"])  # type: ignore[arg-type]
        unit_risk = setup.entry_limit - setup.stop_price
        # The base policy's quantity is min(risk budget / unit risk, notional budget / entry).
        # Size the account so whichever leg binds allows exactly ``recorded`` shares.
        by_risk = Decimal(recorded) * unit_risk * 100 / policy.max_risk_per_trade_pct
        by_notional = Decimal(recorded) * setup.entry_limit * 100 / policy.max_position_notional_pct
        net_liq = max(by_risk, by_notional).quantize(Decimal("0.01"))
        portfolio = PortfolioSnapshot(observed_at=now, net_liquidation=net_liq)
        review = _review(setup)
        base = size_and_evaluate_trade(
            policy=policy, portfolio=portfolio, setup=setup, review=review,
            admitted_symbols=frozenset({setup.symbol}), now=now,
        )
        assert base.approved and base.intent is not None, (row["symbol"], base.codes)
        base_quantity = base.intent.quantity
        assert base_quantity == recorded, (row["symbol"], base_quantity, recorded)
        scaled = size_and_evaluate_trade(
            policy=policy, portfolio=portfolio, setup=setup, review=review,
            admitted_symbols=frozenset({setup.symbol}), now=now, size_multiplier=multiplier,
        )
        expected = math.floor(base_quantity * multiplier)
        if expected == 0:
            zero_seen = True
            assert not scaled.approved and scaled.codes == (SIZE_SCALED_TO_ZERO,)
        else:
            assert scaled.approved and scaled.intent is not None
            assert scaled.intent.quantity == expected == scaled_quantity(base_quantity, multiplier)
    # The zero branch runs for the quarter-size identity and for no other.
    assert zero_seen is (multiplier < Decimal("0.5")), identity
