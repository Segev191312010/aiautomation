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
from trading.risk.identity_sizing import IDENTITY_SIZE_MULTIPLIER
from trading.sendable_identities import SENDABLE_IDENTITIES

FIXTURE = Path(__file__).parent / "fixtures" / "v7b_recorded_accepted_decisions.json"
BRIDGE_PATH = Path(__file__).resolve().parents[1] / "scripts_local" / "paper_bridge.py"

# The owner's tables (plan rev 3): multiplier, max positions, max entries/day, last entry offset.
OWNER_TABLE = {
    "MOMO_HOD_V2": (Decimal("1"), 3, None, timedelta(minutes=60)),
    "ORB_V2": (Decimal("0.25"), 1, 1, timedelta(minutes=30)),
    "VWAP_PB_V2": (Decimal("0.5"), 1, 2, timedelta(minutes=30)),
    "GAP_GO_V2": (Decimal("0.5"), 1, 1, timedelta(minutes=30)),
}
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
    bridge = _bridge()
    assert tuple(SENDABLE_IDENTITIES) == ("MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2")
    assert set(SENDABLE_IDENTITIES) == set(OWNER_TABLE) == set(IDENTITY_SIZE_MULTIPLIER) == set(bridge.IDENTITY_CAPS)
    assert bridge.SENDABLE_STRATEGY_NAMES == frozenset(SENDABLE_IDENTITIES)
    for name, (multiplier, positions, entries, offset) in OWNER_TABLE.items():
        assert IDENTITY_SIZE_MULTIPLIER[name] == multiplier, name
        caps = bridge.IDENTITY_CAPS[name]
        assert (caps.max_positions, caps.max_entries_per_day, caps.last_entry_before_close) == (
            positions, entries, offset
        ), name
    assert bridge.global_max_open() == 6
    assert bridge.BRIDGE_VERSION == "v7b"


@pytest.mark.parametrize("identity", sorted(OWNER_TABLE))
def test_every_recorded_quantity_scales_to_floor_of_quantity_times_the_multiplier(identity: str) -> None:
    multiplier = IDENTITY_SIZE_MULTIPLIER[identity]
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


@pytest.mark.parametrize("identity", sorted(OWNER_TABLE))
def test_the_engine_reproduces_each_recorded_quantity_then_scales_it(identity: str) -> None:
    """End to end: a portfolio sized so the base policy allows exactly the recorded quantity."""
    multiplier = IDENTITY_SIZE_MULTIPLIER[identity]
    policy = RiskPolicy()
    now = NOW
    for row in _rows():
        setup = _setup(row)
        recorded = int(row["quantity"])  # type: ignore[arg-type]
        unit_risk = setup.entry_limit - setup.stop_price
        # The base policy's quantity is min(risk budget / unit risk, notional budget / entry);
        # size the account so the risk leg is binding at exactly ``recorded`` shares.
        net_liq = (Decimal(recorded) * unit_risk * 100 / policy.max_risk_per_trade_pct).quantize(Decimal("0.01"))
        portfolio = PortfolioSnapshot(observed_at=now, net_liquidation=net_liq)
        review = _review(setup)
        base = size_and_evaluate_trade(
            policy=policy, portfolio=portfolio, setup=setup, review=review,
            admitted_symbols=frozenset({setup.symbol}), now=now,
        )
        if not base.approved:
            # The notional leg binds for this row (a cheap, wide stop): the base quantity is
            # smaller than the recorded one, still floor(base x m) below.
            continue
        assert base.intent is not None
        base_quantity = base.intent.quantity
        scaled = size_and_evaluate_trade(
            policy=policy, portfolio=portfolio, setup=setup, review=review,
            admitted_symbols=frozenset({setup.symbol}), now=now, size_multiplier=multiplier,
        )
        expected = math.floor(base_quantity * multiplier)
        if expected == 0:
            assert not scaled.approved and scaled.codes == (SIZE_SCALED_TO_ZERO,)
        else:
            assert scaled.approved and scaled.intent is not None
            assert scaled.intent.quantity == expected == scaled_quantity(base_quantity, multiplier)
