"""Every decision's effective size multiplier, kept beside the decision (T-8).

``size_multiplier_effective = identity factor x managed factor`` is recorded by
the pipeline for every new decision in the decision-sizing sidecar
(``trading.storage.decision_sizing``), never in the decision: the decision
payload is fingerprinted, and the golden digests below (imported, not copied,
from tests/test_setup_persistence.py) must not move. The repository runner
must hand the pipeline's identity factor and sidecar recorder to the session
it opens (it used to drop the factor: every identity sized x1).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal
import json
import logging
import os
from pathlib import Path

import pytest

from tests.test_identity_sizing import RECORD, _assess, _Overlay, _Provider, _record_nothing, _setups
from tests.test_momo_v2 import BF_B
from tests.test_setup_persistence import (
    GOLDEN_MOMO,
    GOLDEN_ORB,
    ORB_KEY,
    ORB_SETUP,
    _digest,
    _normalized,
    _orb_input,
    _orb_strategy,
    _OrbReviewer,
    _assert_golden_momo,
    _replay_momo_session,
)
from trading.contracts import Decision, PortfolioSnapshot, RiskPolicy
from trading.orchestration.pipeline import SIZING_RECORD_FAILED, ShadowPipeline
from trading.rules.momo import MomoStrategy
from trading.storage import decision_sizing as sidecar
from trading.storage.decision_sizing import (
    SIZING_SIDECAR_DISABLED,
    DecisionSizing,
    SizingFileRefused,
    append_decision_sizing,
    read_decision_sizing,
    record_decision_sizing,
    sizing_directory,
    sizing_file,
)

# BF B's policy quantity on the recorded day at 665,000 net liquidation (the golden).
BF_B_POLICY_QTY = 3726


class _Capture:
    def __init__(self) -> None:
        self.rows: list[DecisionSizing] = []

    async def __call__(self, row: DecisionSizing) -> None:
        self.rows.append(row)


def _pipeline(capture: _Capture, **options) -> ShadowPipeline:
    return ShadowPipeline(
        proposal_provider=_Provider(),
        strategy=MomoStrategy(),
        decision_recorder=_record_nothing,
        setup_recorder=_setups,
        sizing_recorder=capture,
        **options,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("identity", "managed", "effective", "outcome"),
    [
        (Decimal("1"), None, "1", "shadow_accepted"),  # unmanaged, neutral identity
        (Decimal("0.25"), None, "0.25", "shadow_accepted"),  # identity-scaled (ORB_V2's row)
        (Decimal("0.5"), None, "0.5", "shadow_accepted"),  # identity-scaled (VWAP/GAP rows)
        (Decimal("1"), Decimal("0.5"), "0.5", "shadow_accepted"),  # managed
        (Decimal("0.5"), Decimal("0.5"), "0.25", "shadow_accepted"),  # managed and scaled
        (Decimal("0.5"), Decimal("0"), "0", "risk_blocked"),  # the manager skips the setup
    ],
)
async def test_every_decision_records_identity_times_managed_beside_it(identity, managed, effective, outcome):
    capture = _Capture()
    options = {"identity_multiplier": identity}
    if managed is not None:
        options["sizing"] = _Overlay(managed)
    decision = (await _assess(_pipeline(capture, **options))).decision
    assert decision is not None and decision.outcome.value == outcome

    [row] = capture.rows
    payload = row.payload()
    assert (row.decision_id, row.run_id, row.candidate_id, row.outcome) == (
        decision.id, decision.run_id, decision.candidate_id, outcome,
    )
    assert (row.strategy_name, row.sendable) == ("MOMO_HOD_V2", True)
    assert payload["size_multiplier_effective"] == effective
    assert payload["identity_factor"] == format(identity.normalize(), "f")
    assert payload["managed_factor"] == ("1" if managed is None else format(managed.normalize(), "f"))
    assert row.size_multiplier_effective == identity * (managed if managed is not None else Decimal("1"))
    # The recorded factor is the one the engine applied: floor(policy quantity x effective).
    if outcome == "shadow_accepted":
        expected = int((Decimal(BF_B_POLICY_QTY) * Decimal(effective)).to_integral_value(ROUND_FLOOR))
        assert decision.risk is not None and decision.risk.intent is not None
        assert decision.risk.intent.quantity == expected
    # The decision itself carries no sizing key: its payload is fingerprinted content.
    assert "size_multiplier_effective" not in json.dumps(decision.model_dump(mode="json"))


@pytest.mark.anyio
async def test_a_sendable_identity_without_a_row_records_no_multiplier_at_all():
    capture = _Capture()
    decision = (await _assess(_pipeline(capture, identity_multiplier=None))).decision
    assert decision is not None and decision.outcome.value == "risk_blocked"
    [row] = capture.rows
    assert row.identity_factor is None and row.size_multiplier_effective is None
    assert row.payload()["size_multiplier_effective"] is None


@pytest.mark.anyio
async def test_decisions_without_a_setup_record_the_identity_factor_and_no_managed_step():
    capture = _Capture()
    pipeline = _pipeline(capture, identity_multiplier=Decimal("0.25"), sizing=_Overlay(Decimal("0.5")))
    blocked = await pipeline.record_data_blocked(run_id="run-replay", strategy=RECORD, candidate=BF_B.candidate())
    late = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)  # after the entry cutoff
    rejected = (
        await pipeline.assess_one(
            run_id="run-replay", strategy=RECORD,
            item=_bf_b_without_quote(), portfolio=PortfolioSnapshot(observed_at=late, net_liquidation="665000"),
            policy=RiskPolicy(), session_date=late, admitted_symbols=frozenset({"BF B"}), now=late,
        )
    ).decision
    assert rejected is not None and rejected.outcome.value in {"rule_rejected", "data_blocked"}
    assert [row.decision_id for row in capture.rows] == [blocked.id, rejected.id]
    for row in capture.rows:
        assert (row.identity_factor, row.managed_factor, row.size_multiplier_effective) == (
            Decimal("0.25"), Decimal("1"), Decimal("0.25"),
        )


def _bf_b_without_quote():
    from trading.orchestration.pipeline import CandidateInput

    now = BF_B.signal_end
    return CandidateInput(
        candidate=BF_B.candidate(), bars=BF_B.bars_until(now), quote=None,
        reference_price=BF_B.reference_price, average_dollar_volume=BF_B.average_dollar_volume,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("review", ["PROPOSE", "ABSTAIN", "fail"])
async def test_the_golden_decisions_do_not_move_while_the_sidecar_records(review):
    capture = _Capture()
    decisions: list[Decision] = []

    async def record(*, decision: Decision, owner_id: str) -> None:
        decisions.append(decision)

    pipeline = ShadowPipeline(
        proposal_provider=_OrbReviewer(review),
        decision_recorder=record,
        setup_recorder=_setups,
        sizing_recorder=capture,
    )
    now = datetime(2026, 1, 2, 16, 30, tzinfo=timezone.utc)
    [decision] = await pipeline.run(
        run_id="run-golden-orb", strategy=_orb_strategy(), candidates=[_orb_input()],
        portfolio=PortfolioSnapshot(observed_at=now, net_liquidation="100000"),
        policy=RiskPolicy(), session_date=now.date(), now=now,
    )
    outcome, digest = GOLDEN_ORB[review]
    assert (decision.outcome.value, decision.decision_key, decision.setup_fingerprint) == (outcome, ORB_KEY, ORB_SETUP)
    assert _digest(decision) == digest
    [row] = capture.rows
    assert (row.decision_id, row.sendable, row.size_multiplier_effective) == (decision.id, False, Decimal("1"))


@pytest.mark.anyio
async def test_a_failing_sidecar_write_is_logged_and_changes_nothing(caplog):
    async def broken(_row: DecisionSizing) -> None:
        raise OSError("disk full")

    recorded: list[Decision] = []

    async def record(*, decision: Decision, owner_id: str) -> None:
        recorded.append(decision)

    working, failing = (
        ShadowPipeline(
            proposal_provider=_OrbReviewer("PROPOSE"), decision_recorder=record, setup_recorder=_setups,
            sizing_recorder=recorder,
        )
        for recorder in (_Capture(), broken)
    )
    now = datetime(2026, 1, 2, 16, 30, tzinfo=timezone.utc)
    kwargs = dict(
        run_id="run-golden-orb", strategy=_orb_strategy(), candidates=[_orb_input()],
        portfolio=PortfolioSnapshot(observed_at=now, net_liquidation="100000"),
        policy=RiskPolicy(), session_date=now.date(), now=now,
    )
    [good] = await working.run(**kwargs)
    with caplog.at_level(logging.ERROR, logger="autotrader.shadow.pipeline"):
        [bad] = await failing.run(**kwargs)
    assert _digest(bad) == _digest(good) == GOLDEN_ORB["PROPOSE"][1]
    assert recorded == [good, bad]  # the decision was recorded before the sidecar failed
    [failure] = [r for r in caplog.records if r.getMessage().startswith(SIZING_RECORD_FAILED)]
    assert bad.id in failure.getMessage()


def test_the_sidecar_is_private_appended_one_line_per_decision_and_read_back(tmp_path):
    directory = tmp_path / "autotrader-decision-sizing"
    stamp = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)
    rows = [
        DecisionSizing(
            decision_id=f"decision-{index}", run_id="shadow-0f0f0f0f-2026-10-19", candidate_id=f"c-{index}",
            strategy_id="strategy-orb2", strategy_version=1, strategy_name="ORB_V2", sendable=True,
            outcome="shadow_accepted", identity_factor=Decimal("0.25"), managed_factor=Decimal("1"),
            decided_at=stamp + timedelta(minutes=index),
        )
        for index in range(2)
    ]
    for row in rows:
        path = append_decision_sizing(directory, row)
    assert path == directory / "shadow-0f0f0f0f-2026-10-19.jsonl"
    assert directory.stat().st_mode & 0o777 == 0o700 and path.stat().st_mode & 0o777 == 0o600
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["size_multiplier_effective"] == "0.25"
    index = read_decision_sizing(directory)
    assert dict(index.records) == {row.decision_id: row for row in rows} and index.unreadable == ()

    # A torn line, a line whose product disagrees with its factors, a conflicting
    # duplicate and a symlinked file are named and left out, never guessed.
    tampered = json.loads(lines[1]) | {"decision_id": "decision-x", "size_multiplier_effective": "1"}
    conflict = json.loads(lines[0]) | {"identity_factor": "0.5", "size_multiplier_effective": "0.5"}
    with path.open("a") as stream:
        stream.write('{"schema": "autotrader-v1/decision-si\n')
        stream.write(json.dumps(tampered) + "\n")
        stream.write(json.dumps(conflict) + "\n")
    os.symlink(path, directory / "elsewhere.jsonl")
    index = read_decision_sizing(directory)
    assert set(index.records) == {"decision-1"}
    assert index.unreadable == (
        "elsewhere.jsonl",
        "shadow-0f0f0f0f-2026-10-19.jsonl:3",
        "shadow-0f0f0f0f-2026-10-19.jsonl:4",
        "decision-0",
    )
    assert read_decision_sizing(tmp_path / "missing").records == {}


def test_a_run_id_that_is_not_a_plain_file_name_gets_a_digest_name(tmp_path):
    assert sizing_file(tmp_path, "shadow-0f0f0f0f-2026-10-19-relabel").name == "shadow-0f0f0f0f-2026-10-19-relabel.jsonl"
    for unsafe in ("../escape", "a/b", ".hidden", "x" * 200):
        name = sizing_file(tmp_path, unsafe).name
        assert name.startswith("run-") and name.endswith(".jsonl") and "/" not in name


@pytest.mark.anyio
async def test_the_default_recorder_writes_beside_the_configured_database(tmp_path, monkeypatch):
    from config import cfg

    row = DecisionSizing(
        decision_id="decision-1", run_id="run-1", candidate_id="c-1", strategy_id="s-1",
        strategy_version=1, strategy_name="GAP_GO_V2", sendable=True, outcome="rule_rejected",
        identity_factor=Decimal("0.5"), managed_factor=Decimal("1"),
        decided_at=datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "autotrader.db"))
    written = await record_decision_sizing(row)
    assert written == tmp_path / "autotrader-decision-sizing" / "run-1.jsonl"
    assert sizing_directory(tmp_path / "autotrader.db") == written.parent
    monkeypatch.setattr(cfg, "DB_PATH", ":memory:")
    assert await record_decision_sizing(row) is None


@pytest.mark.anyio
async def test_the_repository_runner_keeps_the_identity_factor_and_the_sidecar(tmp_path, monkeypatch):
    """Regression: open_session rebuilt the pipeline without its identity factor (x1 for all)."""
    from config import cfg
    from db.core import init_db

    database = tmp_path / "momo.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    capture = _Capture()
    decisions, _ = await _replay_momo_session(
        database, monkeypatch, identity_multiplier=Decimal("0.25"), sizing_recorder=capture
    )
    bf_b, figs = decisions["BF B"], decisions["FIGS"]
    assert bf_b.outcome.value == "shadow_accepted" and figs.outcome.value == GOLDEN_MOMO["FIGS"][0]
    assert bf_b.risk is not None and bf_b.risk.intent is not None
    assert bf_b.risk.intent.quantity == 931  # floor(3726 x 0.25), not 3726
    rows = {row.decision_id: row for row in capture.rows}
    assert set(rows) == {bf_b.id, figs.id}
    assert {row.size_multiplier_effective for row in rows.values()} == {Decimal("0.25")}


@pytest.mark.anyio
async def test_the_default_sidecar_lands_beside_the_session_database(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    database = tmp_path / "momo.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    decisions, _ = await _replay_momo_session(database, monkeypatch)
    index = read_decision_sizing(sizing_directory(database))
    assert set(index.records) == {decision.id for decision in decisions.values()}
    assert {row.size_multiplier_effective for row in index.records.values()} == {Decimal("1")}


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["every write", "the first write only", "a writer that is not async"])
async def test_a_failed_sidecar_write_never_changes_a_decision_on_the_production_path(
    failure, tmp_path, monkeypatch, caplog
):
    """P1-B (mutation-guarded): the recorded MOMO day through the repository runner
    with ORB_V2's identity factor, once with a working sidecar and once with a
    failing one. Every decision (payload, outcome, quantity), every stored row and
    the order the writer is asked in are the same; the failure is only logged.
    Then one pipeline decides BF B twice with the failing writer: a failure leaves
    no state behind, so the later decision is the working pipeline's too."""
    import sqlite3

    from config import cfg
    from db.core import init_db

    async def replay(name: str, recorder) -> tuple[dict[str, Decision], list[tuple]]:
        database = tmp_path / f"{name}.db"
        monkeypatch.setattr(cfg, "DB_PATH", str(database))
        await init_db()
        decisions, _ = await _replay_momo_session(
            database, monkeypatch, identity_multiplier=Decimal("0.25"), sizing_recorder=recorder
        )
        with sqlite3.connect(database) as db:
            rows = db.execute(
                "SELECT decision_key, outcome, setup_fingerprint, payload_json FROM autotrader_candidate_decisions"
            ).fetchall()
        # As stored, less the random id, the wall-clock times and the approval token (the golden's rule).
        return decisions, sorted((key, outcome, setup, _normalized(json.loads(payload))) for key, outcome, setup, payload in rows)

    def failing_writer() -> tuple[object, list[str]]:
        asked: list[str] = []
        if failure == "every write":

            async def failing(row: DecisionSizing) -> None:
                asked.append(row.decision_id)
                raise OSError("disk full")

        elif failure == "the first write only":

            async def failing(row: DecisionSizing) -> None:
                asked.append(row.decision_id)
                if len(asked) == 1:
                    raise RuntimeError("sidecar directory vanished")

        else:

            def failing(row: DecisionSizing) -> None:  # awaiting its None raises TypeError
                asked.append(row.decision_id)

        return failing, asked

    failing, asked = failing_writer()
    working = _Capture()
    good, good_rows = await replay("working", working)
    with caplog.at_level(logging.ERROR, logger="autotrader.shadow.pipeline"):
        bad, bad_rows = await replay("failing", failing)

    assert set(bad) == set(good) == {"BF B", "FIGS"}
    for symbol, decision in good.items():
        assert _digest(bad[symbol]) == _digest(decision), symbol
    assert bad["BF B"].outcome.value == "shadow_accepted"
    assert bad["BF B"].risk is not None and bad["BF B"].risk.intent is not None
    assert bad["BF B"].risk.intent.quantity == 931  # floor(3726 x 0.25), as with a working sidecar
    assert bad_rows == good_rows and len(bad_rows) == 2
    # Asked once per decision, in the working run's order: nothing repeated. An OS
    # error turns the sidecar off for the run (N15a MEDIUM-1): asked once, then never.
    disabled = failure == "every write"
    good_keys = {decision.id: decision.decision_key for decision in good.values()}
    bad_keys = {decision.id: decision.decision_key for decision in bad.values()}
    expected = [good_keys[row.decision_id] for row in working.rows]
    assert [bad_keys[decision_id] for decision_id in asked] == (expected[:1] if disabled else expected)
    assert len(asked) == (1 if disabled else 2)
    logged = [record for record in caplog.records if record.getMessage().startswith(SIZING_RECORD_FAILED)]
    assert len(logged) == (2 if failure == "a writer that is not async" else 1)
    off = [record for record in caplog.records if record.getMessage().startswith(SIZING_SIDECAR_DISABLED)]
    assert len(off) == (1 if disabled else 0)

    # The same pipeline after its failed write: BF B decided again, as a working one decides it.
    reference = (await _assess(_pipeline(_Capture(), identity_multiplier=Decimal("0.25")))).decision
    writer, again = failing_writer()
    pipeline = _pipeline(writer, identity_multiplier=Decimal("0.25"))  # type: ignore[arg-type]
    first, second = [(await _assess(pipeline)).decision for _ in range(2)]
    assert len(again) == (1 if disabled else 2)
    for decision in (first, second):
        assert decision is not None and reference is not None
        assert _digest(decision) == _digest(reference)
        assert decision.risk is not None and decision.risk.intent is not None
        assert decision.risk.intent.quantity == 931


# --------------------------------------------------------------------------
# A stalled sidecar never holds up a decision (N15a MEDIUM-1, LOW-1)
# --------------------------------------------------------------------------

# A stall fails these tests at this bound, never hangs the test process.
TEST_BOUND_S = 10.0


def _sizing_row(run_id: str = "run-1") -> DecisionSizing:
    return DecisionSizing(
        decision_id="decision-1", run_id=run_id, candidate_id="c-1", strategy_id="s-1",
        strategy_version=1, strategy_name="ORB_V2", sendable=True, outcome="shadow_accepted",
        identity_factor=Decimal("0.25"), managed_factor=Decimal("1"),
        decided_at=datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc),
    )


@pytest.mark.anyio
async def test_a_sidecar_that_never_returns_is_cut_off_at_the_deadline_and_the_day_goes_on(
    tmp_path, monkeypatch, caplog
):
    """A recorder that never returns, on the recorded MOMO day through the
    repository runner: every decision is the working sidecar's, the later
    candidate is still assessed, and the sidecar is off for the rest of the run
    (one failure line, one CRITICAL "disabled" line, never asked again)."""
    import asyncio

    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(sidecar, "SIZING_RECORD_DEADLINE_S", 0.05)
    asked: list[str] = []

    async def never_returns(row: DecisionSizing) -> None:
        asked.append(row.decision_id)
        await asyncio.Event().wait()

    async def replay(name: str, recorder) -> dict[str, Decision]:
        database = tmp_path / f"{name}.db"
        monkeypatch.setattr(cfg, "DB_PATH", str(database))
        await init_db()
        async with asyncio.timeout(TEST_BOUND_S):
            decisions, _ = await _replay_momo_session(
                database, monkeypatch, identity_multiplier=Decimal("0.25"), sizing_recorder=recorder
            )
        return decisions

    good = await replay("working", _Capture())
    with caplog.at_level(logging.ERROR, logger="autotrader.shadow.pipeline"):
        bad = await replay("hung", never_returns)

    assert set(bad) == set(good) == {"BF B", "FIGS"}
    for symbol, decision in good.items():
        assert _digest(bad[symbol]) == _digest(decision), symbol
    assert bad["BF B"].risk is not None and bad["BF B"].risk.intent is not None
    assert bad["BF B"].risk.intent.quantity == 931
    assert len(asked) == 1  # the later decision's line is never asked for
    failed = [r for r in caplog.records if r.getMessage().startswith(SIZING_RECORD_FAILED)]
    off = [r for r in caplog.records if r.getMessage().startswith(SIZING_SIDECAR_DISABLED)]
    assert len(failed) == 1 and failed[0].exc_info is not None and failed[0].exc_info[0] is TimeoutError
    assert len(off) == 1 and off[0].levelno == logging.CRITICAL
    assert "sidecar disabled for this run run-golden-momo after TimeoutError" in off[0].getMessage()


@pytest.mark.anyio
async def test_a_fifo_at_the_runs_sidecar_path_is_refused_at_once_and_the_day_goes_on(
    tmp_path, monkeypatch, caplog
):
    """The reviewer's probe on the production recorder: a FIFO at the run's
    sidecar path blocked the open in the kernel until a reader came, and every
    later candidate with it. Now it is refused before any open: the golden
    decisions, the later candidate assessed, nothing written into the FIFO."""
    import asyncio

    from config import cfg
    from db.core import init_db

    database = tmp_path / "momo.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    directory = sizing_directory(database)
    directory.mkdir(mode=0o700)
    fifo = directory / "run-golden-momo.jsonl"
    os.mkfifo(fifo, 0o600)
    try:
        with caplog.at_level(logging.ERROR, logger="autotrader.shadow.pipeline"):
            async with asyncio.timeout(TEST_BOUND_S):
                decisions, _ = await _replay_momo_session(database, monkeypatch)
    finally:
        # A writer parked in open() (the defect) is released by any reader, so a
        # failing run never leaves a thread blocked behind the test.
        os.close(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK))

    _assert_golden_momo(decisions, database)
    assert stat_is_fifo(fifo)
    failed = [r for r in caplog.records if r.getMessage().startswith(SIZING_RECORD_FAILED)]
    off = [r for r in caplog.records if r.getMessage().startswith(SIZING_SIDECAR_DISABLED)]
    assert len(failed) == 1 and failed[0].exc_info is not None
    assert failed[0].exc_info[0] is SizingFileRefused
    assert len(off) == 1 and "after SizingFileRefused" in off[0].getMessage()


def stat_is_fifo(path: Path) -> bool:
    import stat

    return stat.S_ISFIFO(os.lstat(path).st_mode)


def test_a_sizing_path_that_is_not_a_regular_file_is_never_opened(tmp_path, monkeypatch):
    """lstat before open: a FIFO with a reader waiting (an open would succeed and
    the line would land in it), a directory and a symlink are refused before any
    open; a regular file is appended to as before."""
    directory = tmp_path / "autotrader-decision-sizing"
    directory.mkdir(mode=0o700)
    row = _sizing_row()
    target = sizing_file(directory, row.run_id)
    opened: list[str] = []
    real_open = os.open

    def spy(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    os.mkfifo(target, 0o600)
    reader = real_open(target, os.O_RDONLY | os.O_NONBLOCK)
    try:
        monkeypatch.setattr(os, "open", spy)
        with pytest.raises(SizingFileRefused):
            append_decision_sizing(directory, row)
        assert str(target) not in opened
        try:
            data = os.read(reader, 4096)
        except BlockingIOError:
            data = b""
        assert data == b""
    finally:
        os.close(reader)
    target.unlink()

    for make in (lambda: target.mkdir(), lambda: target.symlink_to(tmp_path / "elsewhere.jsonl")):
        make()
        with pytest.raises(SizingFileRefused):
            append_decision_sizing(directory, row)
        assert str(target) not in opened and not (tmp_path / "elsewhere.jsonl").exists()
        target.rmdir() if target.is_dir() and not target.is_symlink() else target.unlink()

    assert append_decision_sizing(directory, row) == target
    assert opened == [str(target)] and len(target.read_text().splitlines()) == 1


@pytest.mark.anyio
async def test_the_default_recorder_writes_on_a_daemon_thread_of_its_own(tmp_path, monkeypatch):
    """A write that never returns parks only its own daemon thread: the pipeline's
    deadline cuts the wait off, and the process can still exit (the default
    executor is joined by asyncio.run at exit)."""
    import asyncio
    import threading

    from config import cfg

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "autotrader.db"))
    release = threading.Event()
    threads: list[threading.Thread] = []

    def stuck(directory: Path, row: DecisionSizing) -> Path:
        threads.append(threading.current_thread())
        release.wait(TEST_BOUND_S)
        return directory

    monkeypatch.setattr(sidecar, "append_decision_sizing", stuck)
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await record_decision_sizing(_sizing_row())
        [thread] = threads
        assert thread.daemon and thread.name == "decision-sizing"
        assert thread is not threading.main_thread()
    finally:
        release.set()
    thread.join(TEST_BOUND_S)
    await asyncio.sleep(0)  # its late result is dropped, never raised


@pytest.mark.anyio
async def test_every_decision_is_recorded_before_its_sidecar_line_on_every_path():
    """LOW-1 (mutation M1): on the main path, the terminal path and
    record_data_blocked, the decision is written first, then its sidecar line."""
    events: list[tuple[str, str]] = []

    async def record(*, decision: Decision, owner_id: str) -> None:
        events.append(("decision", decision.id))

    async def sizing(row: DecisionSizing) -> None:
        events.append(("sizing", row.decision_id))

    pipeline = ShadowPipeline(
        proposal_provider=_Provider(), strategy=MomoStrategy(), decision_recorder=record,
        setup_recorder=_setups, sizing_recorder=sizing, identity_multiplier=Decimal("0.25"),
    )
    accepted = (await _assess(pipeline)).decision  # the main path (risk engine)
    late = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)  # after the entry cutoff
    rejected = (
        await pipeline.assess_one(
            run_id="run-replay", strategy=RECORD,
            item=_bf_b_without_quote(), portfolio=PortfolioSnapshot(observed_at=late, net_liquidation="665000"),
            policy=RiskPolicy(), session_date=late, admitted_symbols=frozenset({"BF B"}), now=late,
        )
    ).decision  # the terminal path
    blocked = await pipeline.record_data_blocked(run_id="run-replay", strategy=RECORD, candidate=BF_B.candidate())

    assert accepted is not None and accepted.outcome.value == "shadow_accepted"
    assert rejected is not None and rejected.outcome.value in {"rule_rejected", "data_blocked"}
    ids = [accepted.id, rejected.id, blocked.id]
    assert len(set(ids)) == 3
    assert events == [event for decision_id in ids for event in (("decision", decision_id), ("sizing", decision_id))]


def test_the_sidecar_module_reads_no_decision_back_and_touches_no_authority():
    import ast

    source = Path(sidecar.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden = ("trading.authority", "trading.execution", "trading.storage.repository", "db.core", "aiosqlite")
    assert not [name for name in imported if name.startswith(forbidden)], imported
    for statement in ("INSERT", "UPDATE ", "DELETE", "CREATE "):
        assert statement not in source
