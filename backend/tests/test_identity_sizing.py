"""Per-identity size multipliers: fixed, Decimal, fail-closed, upstream of the bridge.

The oracle: ``quantity == floor(policy quantity x multiplier)`` for every row
of the sizing table (v7a ships MOMO at 1; the v7b values are pinned here so the
oracle exists before the rows do). An unmanaged pipeline with the neutral
identity factor calls the engine exactly as before (no keyword); the managed
path multiplies the two Decimal factors and records the managed one alone; a
sendable identity without a row blocks every setup with
``identity_sizing_missing`` (one CRITICAL), never a KeyError, never full size.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import math

import pytest

from tests.test_momo_v2 import BF_B, SESSION, _proposal
from trading.contracts import (
    AIReview,
    PortfolioSnapshot,
    RiskPolicy,
    RuleSetup,
    StrategyRecord,
    StrategyState,
)
from trading.manager.managed import narrowing_problems
from trading.orchestration.pipeline import NO_SCALING, CandidateInput, ShadowPipeline
from trading.orchestration.session_runner import momo_strategy_spec
from trading.risk.engine import (
    IDENTITY_SIZING_MISSING,
    SIZE_SCALED_TO_ZERO,
    policy_quantity,
    size_and_evaluate_trade,
)
from trading.risk.identity_sizing import IDENTITY_SIZE_MULTIPLIER, identity_multiplier
from trading.rules.momo import MomoStrategy
from trading.sendable_identities import SENDABLE_IDENTITIES

NOW = datetime(2026, 10, 15, 14, 30, tzinfo=timezone.utc)
# The owner's sizing table (test oracle): identity -> multiplier. v7a ships the
# MOMO row; v7b adds the other three with exactly these values.
SIZING_TABLE = {
    "MOMO_HOD_V2": Decimal("1"),
    "VWAP_PB_V2": Decimal("0.5"),
    "GAP_GO_V2": Decimal("0.5"),
    "ORB_V2": Decimal("0.25"),
}
BASE_RISK_PCT = Decimal("0.5")
BASE_NOTIONAL_PCT = Decimal("15")


def _setup(entry: str, stop: str, target: str, symbol: str = "AAPL") -> RuleSetup:
    return RuleSetup(
        run_id="run-1", candidate_id="cand-1", strategy_id="strategy-1", strategy_version=1,
        strategy_kind="momo_hod", symbol=symbol, atr=Decimal("0.08"),
        entry_limit=Decimal(entry), stop_price=Decimal(stop), target_price=Decimal(target),
        bar_fingerprints=("a" * 64,), quote_fingerprint="b" * 64, detected_at=NOW,
    )


def _review(setup: RuleSetup) -> AIReview:
    return AIReview(
        setup_fingerprint=setup.fingerprint(),
        decision="PROPOSE",
        rationale="fixture",
        model_id="fixture",
        prompt_version="fixture",
        response_hash="c" * 64,
        latency_ms=1,
        cost_usd=Decimal("0"),
    )


# Three fixtures: a cheap wide stop, an expensive tight stop, and a high-priced
# name whose quarter size floors to zero shares.
FIXTURES = (
    (Decimal("100000"), "20.00", "19.50", "21.00"),
    (Decimal("250000"), "180.00", "179.40", "181.20"),
    (Decimal("20000"), "900.00", "890.00", "920.00"),
)


def test_the_shipped_table_is_decimal_frozen_and_exactly_the_sendable_set():
    assert set(IDENTITY_SIZE_MULTIPLIER) == set(SENDABLE_IDENTITIES)
    assert IDENTITY_SIZE_MULTIPLIER["MOMO_HOD_V2"] == Decimal("1")
    for name, value in IDENTITY_SIZE_MULTIPLIER.items():
        assert type(value) is Decimal and Decimal("0") < value <= Decimal("1"), name
        assert SIZING_TABLE[name] == value  # the oracle and the shipped row agree
    with pytest.raises(TypeError):
        IDENTITY_SIZE_MULTIPLIER["MOMO_HOD_V2"] = Decimal("2")  # type: ignore[index]


@pytest.mark.parametrize(("identity", "multiplier"), sorted(SIZING_TABLE.items()))
@pytest.mark.parametrize(("net_liq", "entry", "stop", "target"), FIXTURES)
def test_quantity_is_floor_of_policy_quantity_times_multiplier(identity, multiplier, net_liq, entry, stop, target):
    setup = _setup(entry, stop, target)
    portfolio = PortfolioSnapshot(observed_at=NOW, net_liquidation=net_liq)
    policy = RiskPolicy()
    assert (policy.max_risk_per_trade_pct, policy.max_position_notional_pct) == (BASE_RISK_PCT, BASE_NOTIONAL_PCT)
    base = policy_quantity(policy=policy, portfolio=portfolio, setup=setup)
    expected = math.floor(base * multiplier)

    approval = size_and_evaluate_trade(
        policy=policy, portfolio=portfolio, setup=setup, review=_review(setup),
        admitted_symbols=frozenset({setup.symbol}), now=NOW, size_multiplier=multiplier,
    )
    if expected >= 1:
        assert approval.approved and approval.intent is not None
        assert approval.intent.quantity == expected
        unit_risk = setup.entry_limit - setup.stop_price
        # Realised risk never exceeds the row's share of the 0.5 % base.
        assert approval.intent.quantity * unit_risk <= net_liq * BASE_RISK_PCT * multiplier / 100
        assert approval.intent.quantity * setup.entry_limit <= net_liq * BASE_NOTIONAL_PCT * multiplier / 100
    else:
        # The zero-quantity branch: the engine's existing terminal code, no intent.
        assert not approval.approved and approval.intent is None
        assert approval.codes == (SIZE_SCALED_TO_ZERO,)


def test_identity_multiplier_contract():
    assert identity_multiplier("ORB_V9", sendable=False) == Decimal("1")
    assert identity_multiplier("anything", sendable=False) == Decimal("1")
    assert identity_multiplier("MOMO_HOD_V2", sendable=True) == Decimal("1")
    assert identity_multiplier(" MOMO_HOD_V2 ", sendable=True) == Decimal("1")
    assert identity_multiplier("ORB_V2", sendable=True) == Decimal("0.25")
    assert identity_multiplier("VWAP_PB_V2", sendable=True) == Decimal("0.5")
    assert identity_multiplier("GAP_GO_V2", sendable=True) == Decimal("0.5")
    # Fail closed: a sendable name without a row is None, never 1.
    assert identity_multiplier("ORB_V9", sendable=True) is None
    assert type(identity_multiplier("ORB_V9", sendable=False)) is Decimal


@pytest.mark.parametrize("bad", [0.5, Decimal("0"), Decimal("1.5"), Decimal("NaN"), "0.5"])
def test_the_pipeline_refuses_a_non_decimal_or_out_of_range_identity_factor(bad):
    with pytest.raises((TypeError, ValueError)):
        ShadowPipeline(proposal_provider=object(), strategy=MomoStrategy(), identity_multiplier=bad)  # type: ignore[arg-type]


class _Overlay:
    """A managed sizing hook that answers a fixed step and records what it was told."""

    def __init__(self, answer: Decimal) -> None:
        self.answer = answer
        self.recorded: list[Decimal] = []

    async def size_multiplier(self, *, setup, review):
        return self.answer

    async def after_decision(self, *, size_multiplier, **_kwargs):
        self.recorded.append(size_multiplier)


def _strategy_record() -> StrategyRecord:
    spec = momo_strategy_spec()
    return StrategyRecord.model_validate({
        "strategy_id": "strategy-replay", "owner_id": "owner", "version": 1, "revision": 0,
        "content_hash": spec.fingerprint(), "spec": spec.model_dump(mode="json"),
        "state": StrategyState.SHADOW.value,
    })


RECORD = _strategy_record()  # one record: its timestamps must not differ between calls


class _Provider:
    async def propose_trade(self, *, setup):
        return _proposal(setup)


async def _record_nothing(**_kwargs):
    return None


async def _setups(_row):
    return None


async def _assess(pipeline: ShadowPipeline):
    now = BF_B.signal_end + timedelta(seconds=15)
    return await pipeline.assess_one(
        run_id="run-replay", strategy=RECORD,
        item=CandidateInput(candidate=BF_B.candidate(), bars=BF_B.bars_until(now), quote=BF_B.quote(now),
                            reference_price=BF_B.reference_price,
                            average_dollar_volume=BF_B.average_dollar_volume),
        portfolio=PortfolioSnapshot(observed_at=now, net_liquidation=Decimal("665000")),
        policy=RiskPolicy(), session_date=SESSION, admitted_symbols=frozenset({"BF B"}), now=now,
    )


def _spy(monkeypatch) -> list[dict]:
    from trading.engine import AutotraderEngine

    seen: list[dict] = []

    async def assess(self, **kwargs):
        seen.append(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(AutotraderEngine, "assess", assess)
    return seen


@pytest.mark.anyio
async def test_an_unmanaged_pipeline_with_the_neutral_factor_calls_the_engine_exactly_as_before(monkeypatch):
    seen = _spy(monkeypatch)
    for pipeline in (
        ShadowPipeline(proposal_provider=_Provider(), strategy=MomoStrategy(),
                       decision_recorder=_record_nothing, setup_recorder=_setups),
        ShadowPipeline(proposal_provider=_Provider(), strategy=MomoStrategy(),
                       decision_recorder=_record_nothing, setup_recorder=_setups,
                       identity_multiplier=Decimal("1")),
        ShadowPipeline(proposal_provider=_Provider(), strategy=MomoStrategy(),
                       decision_recorder=_record_nothing, setup_recorder=_setups,
                       identity_multiplier=Decimal("0.25")),
    ):
        await _assess(pipeline)
    default, neutral, quarter = seen
    assert "size_multiplier" not in default and "size_multiplier" not in neutral
    assert default == neutral
    assert quarter["size_multiplier"] == Decimal("0.25") and type(quarter["size_multiplier"]) is Decimal
    assert {key: value for key, value in quarter.items() if key != "size_multiplier"} == default


@pytest.mark.anyio
async def test_the_managed_path_multiplies_decimals_and_records_the_managed_factor_alone(monkeypatch):
    seen = _spy(monkeypatch)
    overlay = _Overlay(Decimal("0.75"))
    pipeline = ShadowPipeline(proposal_provider=_Provider(), strategy=MomoStrategy(),
                              decision_recorder=_record_nothing, setup_recorder=_setups,
                              sizing=overlay, identity_multiplier=Decimal("1"))
    await _assess(pipeline)
    assert seen[-1]["size_multiplier"] == Decimal("0.75")
    assert type(seen[-1]["size_multiplier"]) is Decimal
    # The managed record sees the manager's own factor, so narrowing_problems holds.
    assert overlay.recorded == [Decimal("0.75")]

    scaled = _Overlay(Decimal("0.75"))
    pipeline = ShadowPipeline(proposal_provider=_Provider(), strategy=MomoStrategy(),
                              decision_recorder=_record_nothing, setup_recorder=_setups,
                              sizing=scaled, identity_multiplier=Decimal("0.5"))
    await _assess(pipeline)
    assert seen[-1]["size_multiplier"] == Decimal("0.375")  # 0.5 x 0.75, a Decimal product
    assert type(seen[-1]["size_multiplier"]) is Decimal
    assert scaled.recorded == [Decimal("0.75")]  # never the product


def test_narrowing_problems_hold_for_the_recorded_managed_factor():
    from trading.manager.constants import NEUTRAL_STEP
    from trading.manager.managed import MANAGED
    from trading.manager.store import ManagedDecisionRecord

    record = ManagedDecisionRecord(
        decision_id="d1", run_id="r1", candidate_id="c1", con_id=1, setup_fingerprint="a" * 64,
        session_date=SESSION, outcome="shadow_accepted", mode=MANAGED, neutral_reason=None, mark=None,
        plan_id="p1", eligible=(1,), focus=(1,), gates={}, day_step=Decimal("0.75"), setup_step=NEUTRAL_STEP,
        policy_qty=100, final_qty=75, created_at=NOW,
    )
    assert narrowing_problems(record, size_multiplier=Decimal("0.75")) == []
    # Mixing the identity factor into the managed record would be flagged.
    assert "the applied multiplier differs from the recorded steps" in narrowing_problems(
        record, size_multiplier=Decimal("0.375")
    )


@pytest.mark.anyio
async def test_a_sendable_identity_without_a_row_blocks_every_setup_once_critical(monkeypatch, caplog):
    seen = _spy(monkeypatch)
    recorded: list = []

    async def record(*, decision, owner_id):
        recorded.append(decision)

    pipeline = ShadowPipeline(proposal_provider=_Provider(), strategy=MomoStrategy(),
                              decision_recorder=record, setup_recorder=_setups,
                              identity_multiplier=None)
    with caplog.at_level(logging.CRITICAL, logger="autotrader.shadow.pipeline"):
        first = await _assess(pipeline)
        second = await _assess(pipeline)
    assert seen == []  # the engine is never asked; nothing is sized at 1
    for assessment in (first, second):
        decision = assessment.decision
        assert decision is not None and decision.outcome.value == "risk_blocked"
        assert decision.risk is not None and decision.risk.codes == (IDENTITY_SIZING_MISSING,)
        assert decision.risk.intent is None
    assert len(recorded) == 2
    critical = [r for r in caplog.records if r.getMessage().startswith(IDENTITY_SIZING_MISSING)]
    assert len(critical) == 1 and "MOMO_HOD_V2" in critical[0].getMessage()
    assert pipeline.identity_multiplier is None
    assert NO_SCALING == Decimal("1")


# --- the repository runner keeps the identity factor (found 2026-10-16, before v7b) ---


@pytest.mark.anyio
async def test_the_repository_runner_keeps_the_identity_factor(tmp_path, monkeypatch):
    """Regression: open_session rebuilt the pipeline without its identity factor, so every
    production session sized every identity at x1 (ORB_V2 would have sent 4x its row)."""
    from config import cfg
    from db.core import init_db
    from tests.test_setup_persistence import _replay_momo_session

    database = tmp_path / "momo.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    neutral, _ = await _replay_momo_session(database, monkeypatch)
    base = neutral["BF B"].risk.intent.quantity

    scaled_db = tmp_path / "momo-scaled.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(scaled_db))
    await init_db()
    scaled, _ = await _replay_momo_session(scaled_db, monkeypatch, identity_multiplier=Decimal("0.25"))
    bf_b = scaled["BF B"]
    assert bf_b.outcome.value == "shadow_accepted"
    assert bf_b.risk is not None and bf_b.risk.intent is not None
    assert base > 4
    assert bf_b.risk.intent.quantity == math.floor(base * Decimal("0.25"))  # 3726 -> 931 on the golden day


def test_with_decision_recorder_copies_every_constructor_argument():
    """Every constructor argument is set away from its default, so a copy that drops one
    falls back to the default and fails here. A new argument fails here too, until both
    this fixture and ``with_decision_recorder`` carry it (v7b.2, review MEDIUM-1)."""
    import inspect

    from trading.contracts import EngineMode

    async def other(**_kwargs) -> None:
        return None

    parameters = {
        name: param
        for name, param in inspect.signature(ShadowPipeline.__init__).parameters.items()
        if name != "self"
    }
    arguments = {
        "proposal_provider": _Provider(),
        "decision_recorder": _record_nothing,
        "strategy": MomoStrategy(),
        "mode": EngineMode.BACKTEST,  # any valid mode but the default SHADOW
        "setup_recorder": _setups,
        "sizing": _Overlay(Decimal("0.5")),
        "identity_multiplier": Decimal("0.25"),
    }
    assert set(arguments) == set(parameters), "a constructor argument the fixture does not set"
    original = ShadowPipeline(**arguments)
    copy = original.with_decision_recorder(other)
    assert copy is not original
    for name, param in parameters.items():
        held = getattr(original, f"_{name}")
        # Off its default: a dropped argument cannot match the original by accident.
        assert held is not param.default, name
        if name == "decision_recorder":
            assert getattr(copy, f"_{name}") is other
        else:
            assert getattr(copy, f"_{name}") is held, name

    # A sendable identity with no row (None) stays None in the copy, never x1.
    missing = ShadowPipeline(**{**arguments, "identity_multiplier": None})
    assert missing.with_decision_recorder(other).identity_multiplier is None


@pytest.mark.anyio
async def test_the_repository_runner_blocks_a_sendable_identity_without_a_row(tmp_path, monkeypatch):
    """The fail-closed branch through open_session. Before v7b.1 the copy dropped the
    factor, so None became x1 and BF B was accepted at 3726 shares (review LOW-1)."""
    import sqlite3

    from config import cfg
    from db.core import init_db
    from tests.test_setup_persistence import _replay_momo_session

    database = tmp_path / "momo-missing.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    decisions, _ = await _replay_momo_session(database, monkeypatch, identity_multiplier=None)
    bf_b = decisions["BF B"]
    assert bf_b.outcome.value == "risk_blocked"
    assert bf_b.risk is not None and bf_b.risk.codes == (IDENTITY_SIZING_MISSING,)
    assert bf_b.risk.intent is None
    assert all(decision.outcome.value != "shadow_accepted" for decision in decisions.values())
    # The bridge reads shadow_accepted rows only: there is nothing for it to send.
    with sqlite3.connect(database) as db:
        stored = dict(db.execute("SELECT decision_key, outcome FROM autotrader_candidate_decisions"))
    assert stored[bf_b.decision_key] == "risk_blocked"
    assert "shadow_accepted" not in stored.values()


@pytest.mark.anyio
async def test_the_repository_runner_records_through_the_lease_checked_recorder(tmp_path, monkeypatch):
    """Every decision the runner stores goes through the recorder it hands the pipeline,
    and that recorder checks the lease before it writes (v7b.2, review LOW-2)."""
    import sqlite3

    from config import cfg
    from db.core import init_db
    from tests.test_setup_persistence import _replay_momo_session
    from trading.orchestration import persistence
    from trading.scheduler.lease import LeaseHeartbeat, LeaseLostError

    handed: list = []
    passed: list = []
    copy = ShadowPipeline.with_decision_recorder

    def spy(self, decision_recorder):
        handed.append(decision_recorder)

        async def counting(*, decision, owner_id):
            passed.append(decision)
            await decision_recorder(decision=decision, owner_id=owner_id)

        return copy(self, counting)

    monkeypatch.setattr(ShadowPipeline, "with_decision_recorder", spy)
    database = tmp_path / "momo-lease.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    await _replay_momo_session(database, monkeypatch)

    with sqlite3.connect(database) as db:
        stored = sorted(row[0] for row in db.execute("SELECT decision_key FROM autotrader_candidate_decisions"))
    assert len(handed) == 1
    assert len(stored) == 2 and sorted(decision.decision_key for decision in passed) == stored
    recorder = handed[0]
    assert recorder is not persistence._record_decision_idempotent  # never the bare writer

    # Once the lease is gone, the recorder refuses before anything is written.
    async def lease_lost(self, *, now=None):
        raise LeaseLostError("fixture: the shadow lease was lost")

    writes: list = []

    async def write(**kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(LeaseHeartbeat, "require", lease_lost)
    monkeypatch.setattr(persistence, "_record_decision_idempotent", write)
    with pytest.raises(LeaseLostError):
        await recorder(decision=passed[0], owner_id="owner-a")
    assert writes == []
