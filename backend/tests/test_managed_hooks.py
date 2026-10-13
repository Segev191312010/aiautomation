"""The decision path's two manager hooks fail toward the unmanaged run.

* the session runner's per-candidate filter: a deterministic skip; a filter
  that raises or answers a non-bool admits every candidate (CRITICAL once);
* the pipeline's managed sizing: a multiplier in [0, 1]; any failure or an
  out-of-range answer sizes the setup as unmanaged (1), never larger, and
  0 skips it with ``size_scaled_to_zero``. An unmanaged pipeline calls the
  engine exactly as before (no size_multiplier argument at all).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
from types import SimpleNamespace

import pytest

from trading.contracts import AccountSnapshot, Candidate
from trading.orchestration.pipeline import MANAGED_SIZE_FAILED, NO_SCALING, ShadowPipeline
from trading.orchestration.session_runner import (
    CANDIDATE_FILTER_FAILED,
    PollSchedule,
    ShadowSessionRunner,
)
from trading.rules.momo import MomoStrategy

NOW = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)


def _candidate(con_id: int) -> Candidate:
    return Candidate(
        id=f"scan-1-{con_id}", scanner_run_id="scan-1", con_id=con_id, symbol=f"S{con_id}",
        primary_exchange="NYSE", rank=1, admission_status="admitted",
    )


def _runner(candidate_filter) -> ShadowSessionRunner:
    async def account() -> AccountSnapshot:
        raise AssertionError("not used")

    return ShadowSessionRunner(
        market=object(),  # type: ignore[arg-type]
        read_account=account,
        runner=object(),  # type: ignore[arg-type]
        clock=lambda: NOW,
        poll_schedule=PollSchedule(offset=timedelta(seconds=120), bar_max_age=timedelta(seconds=240)),
        candidate_filter=candidate_filter,
    )


def test_no_filter_admits_everything_and_a_filter_skips_deterministically():
    eligible = (_candidate(1), _candidate(2))
    assert _runner(None)._admitted(eligible[0], eligible)
    focus = _runner(lambda candidate, _eligible: candidate.con_id == 2)
    assert [focus._admitted(item, eligible) for item in eligible] == [False, True]


@pytest.mark.parametrize("answer", [RuntimeError("plan unreadable"), "yes", None])
def test_a_broken_filter_admits_every_candidate_and_says_so_once(answer, caplog):
    def broken(_candidate, _eligible):
        if isinstance(answer, Exception):
            raise answer
        return answer

    runner = _runner(broken)
    eligible = (_candidate(1), _candidate(2))
    with caplog.at_level(logging.INFO, logger="autotrader.shadow.session"):
        assert all(runner._admitted(item, eligible) for item in eligible * 3)
    critical = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(critical) == 1 and critical[0].getMessage().startswith(CANDIDATE_FILTER_FAILED)


class _Overlay:
    def __init__(self, answer) -> None:
        self.answer = answer

    async def size_multiplier(self, *, setup, review):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    async def after_decision(self, **_kwargs):
        raise RuntimeError("audit write failed")  # never changes the decision


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (Decimal("0.5"), Decimal("0.5")),
        (Decimal("0"), Decimal("0")),
        (Decimal("1"), Decimal("1")),
        (Decimal("2"), NO_SCALING),
        (Decimal("-0.25"), NO_SCALING),
        (Decimal("NaN"), NO_SCALING),
        (0.5, NO_SCALING),
        (RuntimeError("no plan"), NO_SCALING),
    ],
)
@pytest.mark.anyio
async def test_the_managed_multiplier_is_in_zero_to_one_or_neutral(answer, expected, caplog):
    pipeline = ShadowPipeline(proposal_provider=object(), strategy=MomoStrategy(), sizing=_Overlay(answer))  # type: ignore[arg-type]
    review = SimpleNamespace(decision="PROPOSE")
    setup = SimpleNamespace(fingerprint=lambda: "a" * 64)

    with caplog.at_level(logging.CRITICAL, logger="autotrader.shadow.pipeline"):
        multiplier = await pipeline._managed_multiplier(setup=setup, review=review)  # type: ignore[arg-type]

    assert multiplier == expected
    failed = [record for record in caplog.records if record.getMessage().startswith(MANAGED_SIZE_FAILED)]
    assert bool(failed) == (expected == NO_SCALING and answer != Decimal("1"))
    # An ABSTAIN is never sized: the overlay is not even asked.
    abstain = await pipeline._managed_multiplier(setup=setup, review=SimpleNamespace(decision="ABSTAIN"))  # type: ignore[arg-type]
    assert abstain == NO_SCALING


@pytest.mark.anyio
async def test_an_unmanaged_pipeline_calls_the_engine_without_a_multiplier(monkeypatch):
    """Golden for V2: the engine call is the pre-manager call, argument for argument."""
    from trading.engine import AutotraderEngine

    seen: list[dict] = []

    async def assess(self, **kwargs):
        seen.append(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(AutotraderEngine, "assess", assess)
    from tests.test_momo_v2 import BF_B, SESSION, _et, _proposal
    from trading.contracts import PortfolioSnapshot, RiskPolicy, StrategyRecord, StrategyState
    from trading.orchestration.pipeline import CandidateInput
    from trading.orchestration.session_runner import momo_strategy_spec

    spec = momo_strategy_spec()
    record = StrategyRecord.model_validate({
        "strategy_id": "strategy-replay", "owner_id": "owner", "version": 1, "revision": 0,
        "content_hash": spec.fingerprint(), "spec": spec.model_dump(mode="json"),
        "state": StrategyState.SHADOW.value,
    })

    class Provider:
        async def propose_trade(self, *, setup):
            return _proposal(setup)

    async def record_nothing(**_kwargs):
        return None

    async def setups(_row):
        return None

    for sizing in (None, _Overlay(Decimal("1"))):
        pipeline = ShadowPipeline(proposal_provider=Provider(), strategy=MomoStrategy(),
                                  decision_recorder=record_nothing, setup_recorder=setups, sizing=sizing)
        now = BF_B.signal_end + timedelta(seconds=15)
        await pipeline.assess_one(
            run_id="run-replay", strategy=record,
            item=CandidateInput(candidate=BF_B.candidate(), bars=BF_B.bars_until(now), quote=BF_B.quote(now),
                                reference_price=BF_B.reference_price,
                                average_dollar_volume=BF_B.average_dollar_volume),
            portfolio=PortfolioSnapshot(observed_at=now, net_liquidation=Decimal("665000")),
            policy=RiskPolicy(), session_date=SESSION, admitted_symbols=frozenset({"BF B"}), now=now,
        )
    unmanaged, managed = seen
    assert "size_multiplier" not in unmanaged
    assert managed["size_multiplier"] == Decimal("1")
    assert {key: value for key, value in managed.items() if key != "size_multiplier"} == unmanaged
    assert _et(SESSION, 11, 50) == BF_B.signal_end
