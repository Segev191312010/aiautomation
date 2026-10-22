"""Effective multipliers for decisions recorded before the sidecar, and size steps (T-8).

A decision recorded before T-8 carries no multiplier: it is derived from its
run's ``source_sha`` through the committed commit table, never from git at
runtime. A size step is an event in the evidence; it never changes the
evidence identity, and R does not depend on the quantity.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import inspect
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest

from trading.contracts import RiskPolicy
from trading.research import source_commits
from trading.research.catalogue import EVALUATOR_FACTORIES
from trading.research.costs import DEFAULT_COST_MODEL
from trading.research.live_ledger import _trade
from trading.research.paper_promotion import EvidenceIdentity, code_identity
from trading.research.size_history import (
    BECAME_SENDABLE,
    DERIVED,
    PRE_V7B,
    RECORDED,
    SIZE_STEP,
    TABLE_VERSIONS,
    UNKNOWN,
    V7B_FACTOR_DROPPED,
    V7B_FIRST_COMMIT,
    V7B_SIZED,
    V7B_SIZED_FIRST_COMMIT,
    DecisionRef,
    RunRef,
    effective_multiplier_for,
    identity_size_history,
    table_version_for,
)
from trading.storage.decision_sizing import DecisionSizing, SizingIndex

REPO = Path(__file__).resolve().parents[2]
# Full commits of this repository (public history).
LIVE_88B963D = "88b963d"  # the deployed head on 2026-10-15 (bridge v5)
V7A_HEAD = "2b81eee"
V7B = V7B_FIRST_COMMIT
LISTED = source_commits.PRE_V7B_COMMITS | source_commits.V7B_COMMITS | source_commits.V7B_SIZED_COMMITS

# What ``shadow_service.source_sha()`` stores for a run: ``git rev-parse HEAD`` of the
# checkout, the full 40 digits (``-dirty`` added with local changes). Every head
# that recorded decisions on the live system, and every head the 2026-10-18
# layered install can run (P1-B.3, owner decisions 2026-10-17).
RAN_LIVE = (
    "88b963d0c15568c43ffafee4b07726351965d990",  # deployed 2026-10-15 (bridge v5)
    "904af3854602615aadd74f933b2b3693243bdb3d",  # R-A, deployed 2026-10-16 01:44 Israel
    "664c93be7a48a4d1fb17f85667f0bed0f4f62e40",  # R-B v7a, deployed 2026-10-16 01:48 Israel (live now)
)
V7B_HEADS = (
    "7872c86357eee85ddd2230300cd0791b92985240",  # v7b.2, the v7b verdict's head
    # its docs-only children, up to the frozen bridge-v7b tip (layer 1)
    "5b4c45bfcb06a377728fbc16013ab78d2a2fc8b5",
    "f31543030909677352d4f0dfddc3bd7e7135f395",
    "3a17ed29672fa725c51508f34bf08ecfc0b73a45",
    "8dac16b34b16a2bdfc3024efc8f630e431d22dff",
    "6578feb64aab012892c6ac1fa07d8ce3886a9f11",
    "5d0eb1ca947a45bfa1a30ab836850df70a1bf12e",
)
P1_HEADS = (
    "95e7534eeb7003d5ebe121e1050ade79df209415",  # P1-A takes v7b.1/v7b.2
    "aad91574106788f9e98f131ec3f155d022c07451",  # P1-A.2
    "a07084867dcf85349ccf1a548dff036815e12f12",  # P1-A.3
    "3375b7dcaba452b9058ae286d95a6fe9cd88312a",  # N14 re-read
    "282408a3081e95201c08168d110853804d5f8325",  # ops-p1, owner decisions 2026-10-17
    "ded4264f61c9afc9a413c53b3440787731275667",  # T-8 (its own copy of the v7b.1 fix)
    "622af486377aed1fb35ecf03feaa0b84c21c1d2a",  # T-8 merged into P1-B
    "3a4254e90dcb3c5cd5f63d8e9cbe458053637efd",  # P1-B.2
    "74025d798ca31485f420996582e4ccc3571df980",  # N15d re-read
    "6ba575a013c71aadf908c9bf96aaafd181113b62",  # W6.4, the base of P1-B.3
    # The weekend heads (regenerated after the P1-A freeze, S6b).
    "4c5c6c61125e0f5fe1742fdc4dfe270ad1f4e977",  # P1-A.4
    "d6b168babae0293804e839c41722da59fc433321",  # bridge-v7b docs merged into P1-A
    "33acc6a5ee8bc83e92dbc1bd2b7a7d5edc402c56",  # ledger: P1-A Docker gates on d6b168b
    "0b8b2b92526ededd6050dff4c709af0b1174dd53",  # N14b re-read
    "a6c8d671139470914670eeab0ab6151f81156a37",  # P1-A.5
    "0a4893e47c8c5b16f97f303a61a014a45b15d704",  # LEARNED: real log format
    "2608230632365c555926528ffc7f93c9d607f246",  # N14c re-read, Docker: the frozen P1-A tip (layer 2)
    "b2a8f8bc868b57ffff16bfa724236b4acce9f336",  # P1-B.3
    "0e52f7813260d40c8d5ee3e53abd40fd54503d14",  # P1-A.4 merged into P1-B
    "201172c8e6c80a082fe2403c976f2798af1aa4f7",  # P1-A.5 merged into P1-B
    "89ce62d21c3838b2dda640e14add07e936136a8f",  # the frozen P1-A tip merged into P1-B
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True, timeout=30
    ).stdout


def _full(short: str) -> str:
    """The full sha of a listed commit, from the table's own 12-digit prefix."""
    matches = [prefix for prefix in LISTED if prefix.startswith(short)]
    assert len(matches) == 1, short
    return matches[0] + "0" * 28  # a 40-digit sha with the listed prefix


def _ref(name: str, sha: str, **extra) -> DecisionRef:
    return DecisionRef(
        decision_id=extra.pop("decision_id", "decision-1"), run_id="run-1", session_date=date(2026, 10, 19),
        strategy_name=name, source_sha=sha, **extra,
    )


def test_the_commit_table_is_closed_disjoint_and_pinned():
    pre, v7b = source_commits.PRE_V7B_COMMITS, source_commits.V7B_COMMITS
    sized = source_commits.V7B_SIZED_COMMITS
    assert (len(pre), len(v7b), len(sized)) == (
        source_commits.PRE_V7B_COUNT, source_commits.V7B_COUNT, source_commits.V7B_SIZED_COUNT,
    )
    assert not pre & v7b and not pre & sized and not v7b & sized
    assert all(re.fullmatch(r"[0-9a-f]{12}", item) for item in pre | v7b | sized)
    assert V7B[:12] in v7b and V7B_SIZED_FIRST_COMMIT[:12] in sized
    for short in (LIVE_88B963D, V7A_HEAD, "3354c3c", "e5997af"):  # bridge v5, v7a
        assert any(item.startswith(short) for item in pre), short
    for short in ("784c2bd", "a8cba98", "227b8b3", "299d6e1"):  # v7b and its descendants
        assert any(item.startswith(short) for item in v7b), short
    assert [version.label for version in TABLE_VERSIONS] == ["pre-v7b", "v7b-factor-dropped", "v7b"]


@pytest.mark.parametrize("sha", RAN_LIVE + V7B_HEADS + P1_HEADS)
def test_every_head_that_ran_live_or_installs_on_sunday_is_listed_as_its_table(sha):
    """The decisions recorded on the live system (88b963d, 904af38, 664c93b) sized
    MOMO_HOD_V2 x1 alone; from v7b.1 on (7872c86 to 5d0eb1c, the P1 heads) the
    runner keeps the identity factor: the four rows. Full shas, as runs store
    them, clean and ``-dirty``."""
    v7a = sha in RAN_LIVE
    version, rows = (PRE_V7B, {"MOMO_HOD_V2": "1"}) if v7a else (
        V7B_SIZED, {"MOMO_HOD_V2": "1", "ORB_V2": "0.25", "VWAP_PB_V2": "0.5", "GAP_GO_V2": "0.5"}
    )
    for stored in (sha, sha + "-dirty"):
        lookup = table_version_for(stored)
        assert (lookup.version, lookup.commit, lookup.dirty) == (version, sha[:12], stored.endswith("-dirty"))
        for name in ("MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2", "ORB_V1", "AP_MOMO_HOD_V1"):
            result = effective_multiplier_for(_ref(name, stored))
            factor = Decimal(rows.get(name, "1"))
            assert (result.source, result.table_version) == (DERIVED, version.label), name
            assert (result.value, result.identity_factor, result.sendable) == (factor, factor, name in rows), name
    assert dict(version.applied) == {name: Decimal(value) for name, value in rows.items()}
    assert dict(version.table) == dict(version.applied)


@pytest.mark.skipif(shutil.which("git") is None or not (REPO / ".git").exists(), reason="needs the git history")
def test_the_commit_table_agrees_with_the_git_history():
    """Tests may read git; the lookup itself never does."""
    present = set(_git("rev-list", "--branches", "--tags", "--remotes").split())
    descendants = set(
        _git("rev-list", "--branches", "--tags", "--remotes", "--ancestry-path", f"^{V7B}").split()
    ) | {V7B}
    by_prefix = {sha[:12]: sha for sha in present}
    for prefix in source_commits.V7B_COMMITS | source_commits.V7B_SIZED_COMMITS:
        if prefix in by_prefix:  # a rebased branch's commit may be gone
            assert by_prefix[prefix] in descendants, prefix
    for prefix in source_commits.PRE_V7B_COMMITS:
        if prefix in by_prefix:
            assert by_prefix[prefix] not in descendants, prefix
    # Among v7b's descendants the code decides the version: the runner keeps the
    # identity factor (v7b.1's ``with_decision_recorder``, or T-8's own copy of it)
    # and the four rows are the table, or it drops the factor.
    for sized, prefixes in ((True, source_commits.V7B_SIZED_COMMITS), (False, source_commits.V7B_COMMITS)):
        for prefix in prefixes:
            if prefix not in by_prefix:
                continue
            persistence = _git("show", f"{by_prefix[prefix]}:backend/trading/orchestration/persistence.py")
            assert ("self._pipeline.with_decision_recorder(" in persistence) is sized, prefix
            source = _git("show", f"{by_prefix[prefix]}:backend/trading/risk/identity_sizing.py")
            rows = dict(re.findall(r'"([A-Z0-9_]+)": Decimal\("([0-9.]+)"\)', source))
            assert {name: Decimal(value) for name, value in rows.items()} == dict(V7B_SIZED.table), prefix
    # Every head named above is in the history, so the parametrized checks are real commits.
    for sha in RAN_LIVE + V7B_HEADS + P1_HEADS:
        assert sha in present, sha
    # The tables are the constants at those commits.
    v7b_source = _git("show", f"{V7B}:backend/trading/risk/identity_sizing.py")
    rows = dict(re.findall(r'"([A-Z0-9_]+)": Decimal\("([0-9.]+)"\)', v7b_source))
    assert {name: Decimal(value) for name, value in rows.items()} == dict(V7B_FACTOR_DROPPED.table)
    v7a_source = _git("show", "3354c3c:backend/trading/risk/identity_sizing.py")
    rows = dict(re.findall(r'"([A-Z0-9_]+)": Decimal\("([0-9.]+)"\)', v7a_source))
    assert {name: Decimal(value) for name, value in rows.items()} == dict(PRE_V7B.table)
    # The runner that dropped the factor: v7b's persistence rebuilt the pipeline without it.
    persistence = _git("show", f"{V7B}:backend/trading/orchestration/persistence.py")
    assert "sizing=self._pipeline.sizing," in persistence and "identity_multiplier" not in persistence


def test_the_lookup_reads_no_git_at_runtime():
    import ast

    from trading.research import size_history

    tree = ast.parse(Path(size_history.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not imported & {"subprocess", "os", "shutil"}
    assert "git" not in {
        node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


@pytest.mark.parametrize(
    ("name", "short", "factor", "sendable", "version"),
    [
        ("MOMO_HOD_V2", LIVE_88B963D, "1", True, "pre-v7b"),
        ("ORB_V2", LIVE_88B963D, "1", False, "pre-v7b"),  # shadow-only, unscaled
        ("VWAP_PB_V2", V7A_HEAD, "1", False, "pre-v7b"),
        ("MOMO_HOD_V2", "784c2bd", "1", True, "v7b-factor-dropped"),
        # v7b's table said 0.25 / 0.5, but the runner sized every decision x1.
        ("ORB_V2", "784c2bd", "1", True, "v7b-factor-dropped"),
        ("GAP_GO_V2", "299d6e1", "1", True, "v7b-factor-dropped"),
        ("ORB_V1", "299d6e1", "1", False, "v7b-factor-dropped"),
    ],
)
def test_a_decision_before_the_sidecar_derives_its_multiplier_from_its_commit(name, short, factor, sendable, version):
    result = effective_multiplier_for(_ref(name, _full(short)))
    assert (result.source, result.table_version) == (DERIVED, version)
    assert (result.value, result.identity_factor, result.managed_factor, result.sendable) == (
        Decimal(factor), Decimal(factor), Decimal("1"), sendable,
    )


def test_a_managed_decision_before_the_sidecar_takes_its_steps_only_when_it_proposed():
    sha = _full("299d6e1")
    proposed = effective_multiplier_for(_ref("AP_MOMO_HOD_V1", sha, review="PROPOSE", managed_steps=Decimal("0.5")))
    abstained = effective_multiplier_for(_ref("AP_MOMO_HOD_V1", sha, review="ABSTAIN", managed_steps=Decimal("0.5")))
    assert (proposed.value, proposed.managed_factor, proposed.sendable) == (Decimal("0.5"), Decimal("0.5"), False)
    assert (abstained.value, abstained.managed_factor) == (Decimal("1"), Decimal("1"))


@pytest.mark.parametrize("sha", ["f" * 40, "abcdef1", "unknown-source", "", "ZZZ" * 14])
def test_a_commit_the_table_does_not_list_is_unknown_never_guessed(sha):
    result = effective_multiplier_for(_ref("ORB_V2", sha))
    assert (result.source, result.value, result.identity_factor, result.sendable) == (UNKNOWN, None, None, None)
    assert table_version_for(sha).version is None


def test_a_dirty_checkout_reads_as_its_commit_and_is_marked():
    lookup = table_version_for(_full(LIVE_88B963D) + "-dirty")
    assert lookup.version is PRE_V7B and lookup.dirty
    assert effective_multiplier_for(_ref("MOMO_HOD_V2", _full(LIVE_88B963D) + "-dirty")).note == "dirty checkout"


def _sizing(decision_id: str, run_id: str, factor: str | None, *, name: str = "ORB_V2", sendable: bool = True,
            managed: str = "1") -> DecisionSizing:
    return DecisionSizing(
        decision_id=decision_id, run_id=run_id, candidate_id=f"c-{decision_id}", strategy_id="s-orb2",
        strategy_version=1, strategy_name=name, sendable=sendable, outcome="shadow_accepted",
        identity_factor=None if factor is None else Decimal(factor), managed_factor=Decimal(managed),
        decided_at=datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc),
    )


def test_a_recorded_multiplier_wins_over_the_commit():
    row = _sizing("decision-1", "run-1", "0.25", managed="0.5")
    result = effective_multiplier_for(_ref("ORB_V2", _full("299d6e1")), recorded={"decision-1": row})
    assert (result.source, result.value, result.identity_factor, result.managed_factor) == (
        RECORDED, Decimal("0.125"), Decimal("0.25"), Decimal("0.5"),
    )
    missing = effective_multiplier_for(
        _ref("ORB_V2", "f" * 40), recorded={"decision-1": _sizing("decision-1", "run-1", None)}
    )
    assert (missing.source, missing.value, missing.note) == (RECORDED, None, "identity_sizing_missing")


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_size_steps_are_events_counted_from_the_last_step():
    pre, v7b, t8 = _full(LIVE_88B963D), _full("299d6e1"), "e" * 40  # t8: a commit after the table
    days = _sessions(date(2026, 10, 12), 17)
    runs = [RunRef(f"run-{day}", day, "ORB_V2", pre) for day in days[:2]]
    runs += [RunRef(f"run-{day}", day, "ORB_V2", v7b) for day in days[2:5]]
    runs += [RunRef(f"run-{day}", day, "ORB_V2", t8) for day in days[5:]]
    runs.append(RunRef("run-momo", days[0], "MOMO_HOD_V2", pre))  # another identity: ignored
    index = SizingIndex(records={
        f"d-{day}": _sizing(f"d-{day}", f"run-{day}", "0.25") for day in days[5:]
    })
    history = identity_size_history("ORB_V2", runs, index)

    assert [(event.session_date, event.kind, event.before, event.after) for event in history.events] == [
        (days[2], BECAME_SENDABLE, None, Decimal("1")),
        (days[5], SIZE_STEP, Decimal("1"), Decimal("0.25")),
    ]
    assert history.events[0].source == f"{DERIVED}:v7b-factor-dropped"
    assert history.events[1].source == RECORDED
    assert history.latest_factor() == Decimal("0.25")
    assert history.sessions_since_last_step(Decimal("0.25")) == 12
    # A size the code says but no session has run with yet: zero sessions.
    assert history.sessions_since_last_step(Decimal("0.5")) == 0
    payload = history.payload(Decimal("0.25"))
    assert payload["events"][1] == {
        "session_date": days[5].isoformat(), "event": "size_step", "from": "1", "to": "0.25", "source": "recorded",
    }
    assert payload["sessions_since_last_step"] == 12 and payload["unknown_sessions"] == []

    # A session of unknown size could hide a step: the count restarts after it.
    gap = days[10]
    runs = [run for run in runs if run.session_date != gap] + [RunRef("run-gap", gap, "ORB_V2", "f" * 40)]
    index = SizingIndex(records={key: row for key, row in index.records.items() if row.run_id != f"run-{gap}"})
    history = identity_size_history("ORB_V2", runs, index)
    assert history.unknown_sessions == (gap,)
    assert history.sessions_since_last_step(Decimal("0.25")) == 6
    assert history.unknown_sources == ("ffffffffffff",)


@pytest.mark.parametrize(
    "table",
    [
        {"MOMO_HOD_V2": Decimal("0.5"), "ORB_V2": Decimal("0.5"), "VWAP_PB_V2": Decimal("1"), "GAP_GO_V2": Decimal("1")},
        {"MOMO_HOD_V2": Decimal("1"), "ORB_V2": Decimal("1"), "VWAP_PB_V2": Decimal("0.25"), "GAP_GO_V2": Decimal("0.25")},
    ],
)
def test_the_evidence_identity_is_the_same_whatever_the_multiplier_table(table, monkeypatch):
    """A size step never restarts an identity's evidence (code_identity excludes the table)."""
    from types import MappingProxyType

    import trading.risk.identity_sizing as identity_sizing

    before = {key: code_identity(key).fingerprint() for key in EVALUATOR_FACTORIES}
    shipped = identity_sizing.IDENTITY_SIZE_MULTIPLIER
    patched = MappingProxyType(dict(table))
    # Every binding of the shipped table, under whatever name a module imported it.
    for module in list(sys.modules.values()):
        if not getattr(module, "__name__", "").startswith("trading."):
            continue
        for attribute, value in list(vars(module).items()):
            if value is shipped:
                monkeypatch.setattr(module, attribute, patched)
    assert identity_sizing.IDENTITY_SIZE_MULTIPLIER is patched
    after = {key: code_identity(key).fingerprint() for key in EVALUATOR_FACTORIES}
    assert after == before
    assert code_identity("orb-v2", risk_policy=RiskPolicy()).fingerprint() == before["orb-v2"]
    # Nothing the identity is made of names a size.
    assert set(EvidenceIdentity.__dataclass_fields__) == {
        "strategy", "rules", "entry_window", "risk_policy", "manager_version", "prompt_id",
    }


def test_r_is_quantity_free_except_where_the_commission_minimum_binds():
    def bracket(shares: int) -> dict[str, object]:
        return {
            "filled": shares, "exit_filled": shares, "entry": "50.00", "stop": "49.00",
            "avg_entry": "50.0200", "avg_exit": "51.9700", "strategy_name": "ORB_V2", "exit_reason": "target",
        }

    day = date(2026, 10, 19)
    quarter, full = (_trade(day, f"d-{shares}", bracket(shares), cost_model=DEFAULT_COST_MODEL) for shares in (400, 1600))
    # Gross R: the quantity cancels exactly.
    assert quarter.gross_r == full.gross_r == Decimal("1.9500")
    # Above IBKR's $1 per-order minimum the commission is per share, so net R is equal too.
    assert quarter.cost_r == full.cost_r and quarter.net_r == full.net_r
    # Only a tiny order (the $1 minimum binds) pays relatively more: cost, not size, moves.
    tiny = _trade(day, "d-20", bracket(20), cost_model=DEFAULT_COST_MODEL)
    assert tiny.gross_r == full.gross_r and tiny.cost_r > full.cost_r
    # A simulated setup has no quantity at all: its cost in R takes none.
    assert "shares" not in inspect.signature(DEFAULT_COST_MODEL.cost_r).parameters


# --- P3-F: runs at or after the size store's install read the store -------------------------------

ET = timezone(timedelta(hours=-4))  # America/New_York in October 2026 (EDT)
# Migration 18 applied on Friday 2026-10-16 at 18:30 ET, after that session.
STORE_INSTALLED = datetime(2026, 10, 16, 22, 30, tzinfo=timezone.utc)
BEFORE = (date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16))
AFTER = (date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22))
V7B_SIZED_HEAD = "5d0eb1ca947a45bfa1a30ab836850df70a1bf12e"  # listed: the v7b table applied
STORE_ERA_SHA = "e" * 40  # a commit after the commit table: only the store can size its runs


async def _seed_runs(database: Path, monkeypatch, runs, *, store: bool = True) -> dict[tuple[str, date], str]:
    """One shadow run and one rule-rejected decision per (name, day, sha), on a database whose
    size store was installed at STORE_INSTALLED (or that has none, at migration 17)."""
    from hashlib import sha256

    from config import cfg
    from db.core import init_db
    from tests.fakes.legacy_strategies import insert_legacy_shadow_strategy
    from trading.contracts import (
        Candidate, Decision, DecisionOutcome, EngineMode, FixedUniverse, RuleCondition, Run, ScannerRun,
        StrategySpec, fingerprint_payload,
    )
    from trading.data import CandidateRepository, RunRepository, ScannerRunRepository
    from trading.storage import migrations as schema_migrations
    from trading.storage.repository import record_decision

    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    with monkeypatch.context() as patch:
        patch.setattr(schema_migrations, "_now", lambda: STORE_INSTALLED.isoformat())
        if not store:
            patch.setattr(schema_migrations, "MIGRATIONS",
                          tuple(item for item in schema_migrations.MIGRATIONS if item.version < 18))
        await init_db()
    strategies: dict[str, str] = {}
    decisions: dict[tuple[str, date], str] = {}
    for number, (name, day, sha) in enumerate(runs):
        if name not in strategies:
            spec = StrategySpec(name=name, universe=FixedUniverse(symbols=("AAPL",)),
                                conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
                                rationale=f"{name} size-history fixture.")
            strategies[name] = (await insert_legacy_shadow_strategy(spec=spec, owner_id="demo")).strategy_id
        run_id = f"run-{name}-{day}"
        await RunRepository().save(Run(
            id=run_id, owner_id="demo", source_sha=sha, strategy_id=strategies[name], strategy_version=1,
            policy_fingerprints={"risk": RiskPolicy().fingerprint()}, session_date=day, mode=EngineMode.SHADOW,
        ))
        parameters = {"scan_code": "HOT_BY_VOLUME", "limit": 50}
        started = datetime.combine(day, datetime.min.time(), tzinfo=ET) + timedelta(hours=9)
        await ScannerRunRepository().save(ScannerRun(
            id=f"{run_id}-scan", run_id=run_id, parameters=parameters,
            parameters_fingerprint=fingerprint_payload(parameters), status="completed", started_at=started,
        ))
        candidate = Candidate(id=f"{run_id}-AAA", scanner_run_id=f"{run_id}-scan", con_id=1000 + number,
                              symbol="AAA", primary_exchange="NASDAQ", rank=1, admission_status="admitted")
        await CandidateRepository().save(candidate)
        decision_id = f"decision-{name}-{day}"
        await record_decision(decision=Decision(
            id=decision_id, run_id=run_id, candidate_id=candidate.id, strategy_id=strategies[name],
            strategy_version=1, decision_key=sha256(decision_id.encode()).hexdigest(),
            outcome=DecisionOutcome.RULE_REJECTED, created_at=started + timedelta(hours=1),
        ), owner_id="demo")
        decisions[(name, day)] = decision_id
    return decisions


async def test_size_history_uses_events_after_install(tmp_path, monkeypatch):
    """Through the production readers (read_size_steps, read_decision_refs) on a real database: runs
    before the size store's install keep the commit table; runs at or after it take the size the
    store had in force at their session's start (a step-down effective 10-21; a step-up written
    during the 10-22 session is not in force that day); an unreadable store names no size for
    them (never the baseline), and a database without the store sizes every run by its commit."""
    from tests.test_size_store import SizeLedger
    from trading.research.size_history import SIZE_STORE, read_decision_refs, read_size_steps

    runs = [("VWAP_PB_V2", day, V7B_SIZED_HEAD) for day in BEFORE]
    runs += [("VWAP_PB_V2", day, STORE_ERA_SHA) for day in AFTER] + [("ORB_V1", AFTER[0], STORE_ERA_SHA)]
    database = tmp_path / "autotrader.db"
    ids = await _seed_runs(database, monkeypatch, runs)
    ledger = SizeLedger(database)
    ledger.step("VWAP_PB_V2", "0.5", "0.25", effective=AFTER[2], at=datetime(2026, 10, 20, 20, 0, tzinfo=ET))
    ledger.step("VWAP_PB_V2", "0.25", "0.5", effective=AFTER[3], at=datetime(2026, 10, 22, 9, 0, tzinfo=ET))

    steps = read_size_steps(database, SizingIndex(), current={"VWAP_PB_V2": Decimal("0.25")})
    history = steps.histories["VWAP_PB_V2"]
    table = f"{DERIVED}:{V7B_SIZED.label}"
    assert [(item.session_date, item.factor, item.sendable, item.source) for item in history.sessions] == [
        *((day, Decimal("0.5"), True, table) for day in BEFORE),
        (AFTER[0], Decimal("0.5"), True, SIZE_STORE),  # the baseline: no event in force yet
        (AFTER[1], Decimal("0.5"), True, SIZE_STORE),
        (AFTER[2], Decimal("0.25"), True, SIZE_STORE),  # the step-down, effective 10-21
        (AFTER[3], Decimal("0.25"), True, SIZE_STORE),  # the step-up was written after 10-22 began
    ]
    assert [(event.session_date, event.kind, event.before, event.after, event.source) for event in history.events] == [
        (BEFORE[0], BECAME_SENDABLE, None, Decimal("0.5"), table),
        (AFTER[2], SIZE_STEP, Decimal("0.5"), Decimal("0.25"), SIZE_STORE),
    ]
    assert history.sessions_since_last_step(Decimal("0.25")) == 2 and history.unknown_sources == ()

    refs = read_decision_refs(database, decision_ids=ids.values())
    multipliers = {key: effective_multiplier_for(refs[decision_id]) for key, decision_id in ids.items()}
    before = multipliers[("VWAP_PB_V2", BEFORE[1])]
    assert (before.source, before.table_version, before.value) == (DERIVED, V7B_SIZED.label, Decimal("0.5"))
    stepped = multipliers[("VWAP_PB_V2", AFTER[2])]
    assert (stepped.source, stepped.value, stepped.identity_factor, stepped.sendable) == (
        SIZE_STORE, Decimal("0.25"), Decimal("0.25"), True,
    )
    assert stepped.note == "size event 1, step_down effective 2026-10-21"
    assert multipliers[("VWAP_PB_V2", AFTER[3])].value == Decimal("0.25")
    assert multipliers[("VWAP_PB_V2", AFTER[0])].note == "baseline: no size event in force"
    shadow = multipliers[("ORB_V1", AFTER[0])]
    assert (shadow.source, shadow.value, shadow.sendable) == (SIZE_STORE, Decimal("1"), False)

    # An unreadable store: the store-era runs name no size (never the 0.5 baseline); earlier runs keep the table.
    with sqlite3.connect(database) as db:
        db.execute("UPDATE autotrader_schema_migrations SET checksum = ? WHERE version = 18", ("0" * 64,))
    history = read_size_steps(database, SizingIndex(), current={"VWAP_PB_V2": Decimal("0.25")}).histories["VWAP_PB_V2"]
    assert history.unknown_sessions == AFTER
    assert [item.factor for item in history.sessions[: len(BEFORE)]] == [Decimal("0.5")] * len(BEFORE)
    [reason] = history.unknown_sources
    assert reason.startswith("identity_size_store_unreadable: schema_checksum_mismatch")
    refs = read_decision_refs(database, decision_ids=ids.values())
    unread = effective_multiplier_for(refs[ids[("VWAP_PB_V2", AFTER[0])]])
    assert (unread.source, unread.value, unread.identity_factor) == (UNKNOWN, None, None)
    assert effective_multiplier_for(refs[ids[("VWAP_PB_V2", BEFORE[0])]]).value == Decimal("0.5")

    # No size store in the database (migration 17): every run is sized by its commit, as before P3-C.
    plain = tmp_path / "v17.db"
    await _seed_runs(plain, monkeypatch, runs, store=False)
    history = read_size_steps(plain, SizingIndex(), current={"VWAP_PB_V2": Decimal("0.25")}).histories["VWAP_PB_V2"]
    assert history.unknown_sessions == AFTER and history.unknown_sources == (STORE_ERA_SHA[:12],)
