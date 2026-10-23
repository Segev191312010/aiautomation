"""The no-limit PAPER test L-T1, upstream of the bridge (plan v2 item 2; plan rev 1 T1-1/T1-3).

The mode is one literal (``trading.risk.no_limit_test.NO_LIMIT_TEST``), never
an environment variable or a setting. Under it the four sendable identities
decide under the test risk policy (max_open_positions 50 = MAX_CANDIDATE_RANK,
so the "3 accepted decisions per run" limit can never bind), every shadow name
under the unchanged default; sizes follow the Step 0 rule; the AI review cap is
100 calls / $25 per run in the contract and the settings alike; the Sentinel
logs only; the manager has no slot count; the promoter's open-risk gate is
closed. Test runs record the test policy's fingerprint, so they are their own
evidence identity and never enter the size-up track record. Each value is
checked against the mode here and by an import tripwire in its module, so a
half-on build cannot start.
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import inspect
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from tests.no_limit_support import V7C_MANAGER_MAX_OPEN, V7C_SENTINEL_MAX_ENTRIES_PER_DAY
from trading.contracts import (
    AccountSnapshot,
    AIReview,
    CompletedBar,
    EngineMode,
    PortfolioSnapshot,
    QuoteSnapshot,
    RiskPolicy,
    Run,
    ShadowRuntimeConfig,
)
from trading.risk import no_limit_test
from trading.risk.no_limit_test import (
    NO_LIMIT_TEST,
    NO_LIMIT_TEST_LABEL,
    NO_LIMIT_TEST_POLICY,
    run_policy,
)
from trading.sendable_identities import SENDABLE_IDENTITIES

BACKEND = Path(__file__).resolve().parents[1]
# RiskPolicy()'s fingerprint as v7c (bridge-v7c 306c393, code 64a7d71) records it:
# sha256 of its canonical JSON. Every earlier run's evidence identity holds it.
DEFAULT_RISK_POLICY_FINGERPRINT = "d8f1fe70882ed4bd746507cce04593d6e023cba4dc83b4ea5775cd53d323166c"
DEFAULT_RISK_POLICY_PAYLOAD = {
    "id": "moderate-pilot",
    "version": 1,
    "max_open_positions": 3,
    "max_risk_per_trade_pct": "0.5",
    "max_daily_loss_pct": "2",
    "max_position_notional_pct": "15",
    "max_total_notional_pct": "45",
    "min_reward_to_risk": "2",
}
# The Step 0 result the shipped table follows (plan v2 Step 0, run Sat 2026-10-24).
# Until the owner reports it, every identity is x1 (n < 30 is no evidence). A Step 0
# result changes this oracle and the one row in identity_sizing together.
STEP0_SIZES = {
    "MOMO_HOD_V2": Decimal("1"),
    "ORB_V2": Decimal("1"),
    "VWAP_PB_V2": Decimal("1"),
    "GAP_GO_V2": Decimal("1"),
}


# --- the switch ------------------------------------------------------------------------


def test_the_mode_is_one_literal_in_code_never_an_environment_variable_or_setting():
    source = inspect.getsource(no_limit_test)
    tree = ast.parse(source)
    assigned = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "NO_LIMIT_TEST" for target in node.targets)
    ]
    assert len(assigned) == 1 and isinstance(assigned[0], ast.Constant) and assigned[0].value is True
    assert NO_LIMIT_TEST is True and NO_LIMIT_TEST_LABEL == "L-T1"
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(name=node.module or "")])
    }
    assert imported.isdisjoint({"os", "tomllib", "json", "configparser", "argparse", "dotenv"}), imported
    for text in ("os.environ", "getenv(", "open(", "read_text", "sys.argv"):
        assert text not in source, text
    # Nothing else in the code base assigns the mode or reads it from the environment.
    for path in (BACKEND / "trading").rglob("*.py"):
        if path.name == "no_limit_test.py":
            continue
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {target.id for target in targets if isinstance(target, ast.Name)}
                assert "NO_LIMIT_TEST" not in names, path
        for line in text.splitlines():
            assert not ("LIMIT_TEST" in line and ("environ" in line or "getenv" in line)), (path, line)


def test_an_environment_variable_cannot_switch_the_mode_off():
    """A fresh interpreter with every plausible variable set to "off" still runs the test mode."""
    environment = {
        **os.environ,
        "NO_LIMIT_TEST": "0",
        "AUTOTRADER_NO_LIMIT_TEST": "0",
        "AUTOTRADER_V1_NO_LIMIT_TEST": "false",
        "NO_LIMIT_TEST_LABEL": "off",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    check = (
        "from trading.risk import no_limit_test as m\n"
        "from trading.risk import identity_sizing as s\n"
        "print(m.NO_LIMIT_TEST, m.run_policy('ORB_V2').id, s.IDENTITY_SIZE_MULTIPLIER is s.NO_LIMIT_TEST_SIZE_MULTIPLIER)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", check], cwd=BACKEND, env=environment, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "no-limit-test-t1", "True"]


# --- the test risk policy ----------------------------------------------------------------


def test_the_default_policy_and_its_fingerprint_are_byte_identical_to_v7c():
    default = RiskPolicy()
    assert default.model_dump(mode="json") == DEFAULT_RISK_POLICY_PAYLOAD
    assert default.fingerprint() == DEFAULT_RISK_POLICY_FINGERPRINT
    assert RiskPolicy.model_fields["max_open_positions"].default == 3


def test_the_test_policy_differs_from_the_default_only_in_its_id_and_open_positions():
    default = RiskPolicy().model_dump(mode="json")
    test = NO_LIMIT_TEST_POLICY.model_dump(mode="json")
    assert {key for key in default if default[key] != test[key]} == {"id", "max_open_positions"}
    assert (test["id"], test["version"], test["max_open_positions"]) == ("no-limit-test-t1", 1, 50)
    assert NO_LIMIT_TEST_POLICY.fingerprint() != RiskPolicy().fingerprint()
    assert NO_LIMIT_TEST_POLICY == RiskPolicy.model_validate(test)  # a valid contract value


def test_the_test_policy_can_never_bind_a_run():
    """50 = MAX_CANDIDATE_RANK: a run holds at most 50 candidates, so at most 49 others
    are accepted when any one is assessed, and 49 < 50 never reaches the limit."""
    from trading.orchestration.persistence import MAX_CANDIDATE_RANK
    from trading.risk.engine import size_and_evaluate_trade
    from tests.test_identity_sizing import _review, _setup

    assert NO_LIMIT_TEST_POLICY.max_open_positions == MAX_CANDIDATE_RANK == 50
    bound = [meta.le for meta in RiskPolicy.model_fields["max_open_positions"].metadata if hasattr(meta, "le")]
    assert bound == [MAX_CANDIDATE_RANK]
    with pytest.raises(ValueError):
        RiskPolicy(max_open_positions=MAX_CANDIDATE_RANK + 1)
    now = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
    setup = _setup("20.00", "19.50", "21.00", symbol="ZZZ")
    review = _review(setup)
    others = tuple(f"S{index}" for index in range(MAX_CANDIDATE_RANK - 1))
    portfolio = PortfolioSnapshot(observed_at=now, net_liquidation=Decimal("100000"), pending_symbols=others)
    unbound = size_and_evaluate_trade(
        policy=NO_LIMIT_TEST_POLICY, portfolio=portfolio, setup=setup, review=review,
        admitted_symbols=frozenset({"ZZZ"}), now=now,
    )
    assert unbound.approved and "max_open_positions_reached" not in unbound.codes
    bound_by_default = size_and_evaluate_trade(
        policy=RiskPolicy(), portfolio=portfolio, setup=setup, review=review,
        admitted_symbols=frozenset({"ZZZ"}), now=now,
    )
    assert bound_by_default.codes == ("max_open_positions_reached",)


@pytest.mark.parametrize("name", SENDABLE_IDENTITIES)
def test_a_sendable_identity_runs_under_the_test_policy(name):
    assert run_policy(name) is NO_LIMIT_TEST_POLICY
    assert run_policy(f" {name} ") is NO_LIMIT_TEST_POLICY  # the bridge strips the stored name too


@pytest.mark.parametrize(
    "name",
    ["ORB_V1", "VWAP_PB_V1", "GAP_GO_V1", "AP_MOMO_HOD_V1", "SH_ORB_X", "orb_v2", "ORB_V2_X", "MOMO_HOD_V1", ""],
)
def test_every_shadow_name_keeps_the_default_policy(name):
    assert run_policy(name) == RiskPolicy()
    assert run_policy(name).fingerprint() == DEFAULT_RISK_POLICY_FINGERPRINT


# --- separate evidence identity ------------------------------------------------------------


@pytest.mark.parametrize("key", ["momo", "orb-v2", "vwap-v2", "gap-v2"])
def test_a_test_run_is_its_own_evidence_identity_never_the_code_identity(key):
    from trading.operations import shadow_service
    from trading.research.paper_promotion import code_identity, identity_from_run
    from tests.test_shadow_service import runtime_config

    evaluator_factory, spec_factory, prompt_id = shadow_service.STRATEGIES[key]
    name = spec_factory().name
    kind = evaluator_factory().strategy_kind
    config = runtime_config()
    test_prints = shadow_service.policy_fingerprints(
        config, run_policy(name), prompt_id=prompt_id, strategy_name=key
    )
    default_prints = shadow_service.policy_fingerprints(
        config, RiskPolicy(), prompt_id=prompt_id, strategy_name=key
    )
    assert test_prints["risk"] == NO_LIMIT_TEST_POLICY.fingerprint()
    test_identity = identity_from_run(name, kind, test_prints)
    assert test_identity is not None
    assert test_identity != code_identity(key)
    assert test_identity.fingerprint() != code_identity(key).fingerprint()
    # The size-up track record is always the default policy's identity.
    assert identity_from_run(name, kind, default_prints) == code_identity(key)
    assert code_identity(key).risk_policy == DEFAULT_RISK_POLICY_FINGERPRINT


@pytest.mark.anyio
async def test_test_sessions_stay_out_of_the_promotion_window(tmp_path, monkeypatch):
    """Runs stored through the real repository and read back by the promoter's live
    evidence reader: the test session is its own identity, so the window (and every
    trade in it) is the default identity's sessions only."""
    from config import cfg
    from db.core import init_db
    from tests.test_shadow_service import runtime_config
    from trading.data import RunRepository
    from trading.operations import shadow_service
    from trading.orchestration.session_runner import ensure_strategy
    from trading.research.live_ledger import LiveBracket, LiveLedger, LiveTrade
    from trading.research.paper_promotion import code_identity, identity_from_run, read_live_evidence
    from trading.research.promotion_proposals import evaluate_identity, identity_evidence
    from trading.research.size_history import IdentitySizeHistory
    from trading.rules.all_session import orb_v2_strategy_spec

    database = tmp_path / "evidence.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    strategy = await ensure_strategy(owner_id="owner-a", spec=orb_v2_strategy_spec())
    assert strategy.spec.name == "ORB_V2"
    config = runtime_config()
    v7c_day, test_day = date(2026, 10, 28), date(2026, 11, 2)
    prints = {
        v7c_day: shadow_service.policy_fingerprints(config, RiskPolicy(), prompt_id="orb-review-v1",
                                                    strategy_name="orb-v2"),
        test_day: shadow_service.policy_fingerprints(config, run_policy("ORB_V2"), prompt_id="orb-review-v1",
                                                     strategy_name="orb-v2"),
    }
    for day, fingerprints in prints.items():
        await RunRepository().save(Run(
            id=f"shadow-orb-v2-{day.isoformat()}", owner_id="owner-a", source_sha="abcdef1",
            strategy_id=strategy.strategy_id, strategy_version=strategy.version,
            policy_fingerprints=fingerprints, session_date=day, mode=EngineMode.SHADOW,
        ))

    live = read_live_evidence(database)
    default = code_identity("orb-v2").fingerprint()
    test = identity_from_run("ORB_V2", "orb", prints[test_day])
    assert test is not None and test.fingerprint() != default
    assert live.sessions[default] == frozenset({v7c_day})
    assert live.sessions[test.fingerprint()] == frozenset({test_day})

    def trade(day: date, decision_id: str) -> LiveTrade:
        return LiveTrade(
            session_date=day, decision_id=decision_id, strategy="ORB_V2", symbol="AAA", shares=100,
            risk_per_share=Decimal("0.50"), gross_pnl=Decimal("100"), commissions=Decimal("1.00"),
            gross_r=Decimal("2"), cost_r=Decimal("0.02"), exit_reason="target",
        )

    ledger = LiveLedger(
        trades=(trade(v7c_day, "d-v7c"), trade(test_day, "d-test")),
        sessions=(v7c_day, test_day),
        brackets=(LiveBracket(v7c_day, "d-v7c", "ORB_V2", "closed"),
                  LiveBracket(test_day, "d-test", "ORB_V2", "closed")),
    )
    evidence = identity_evidence(
        "ORB_V2", current=Decimal("1"), live=live, ledger=ledger, shadow={},
        size_history=IdentitySizeHistory(identity="ORB_V2"),
    )
    assert evidence.evidence_identity == default
    assert evidence.window == (v7c_day,)
    assert [item.decision_id for item in evidence.trades] == ["d-v7c"]
    assert evaluate_identity(evidence).trades == 1


# --- every T1 value outside the bridge agrees with the mode ----------------------------------


def test_every_t1_value_outside_the_bridge_agrees_with_the_mode(tmp_path):
    from tests.test_shadow_settings import _VALID, _write
    from trading.intelligence.review_provider import REVIEW_POOL_SIZE
    from trading.manager import context as manager_context
    from trading.operations import sentinel
    from trading.operations.settings import load_shadow_runtime_config
    from trading.research import promotion_proposals
    from trading.risk import identity_sizing

    assert NO_LIMIT_TEST is True
    # The AI review cap: the contract's only value, and what the settings build.
    fields = ShadowRuntimeConfig.model_fields
    assert (fields["max_ai_calls"].default, fields["max_ai_cost_usd"].default) == (100, Decimal("25"))
    loaded = load_shadow_runtime_config(_write(tmp_path, _VALID))
    assert (loaded.max_ai_calls, loaded.max_ai_cost_usd, loaded.max_candidates) == (100, Decimal("25"), 20)
    assert (no_limit_test.NO_LIMIT_TEST_AI_MAX_CALLS, no_limit_test.NO_LIMIT_TEST_AI_MAX_COST_USD) == (
        loaded.max_ai_calls, loaded.max_ai_cost_usd,
    )
    # Sizes: the Step 0 table is the shipped one.
    assert identity_sizing.IDENTITY_SIZE_MULTIPLIER is identity_sizing.NO_LIMIT_TEST_SIZE_MULTIPLIER
    assert dict(identity_sizing.IDENTITY_SIZE_MULTIPLIER) == STEP0_SIZES
    # The Sentinel logs only, uncapped like the bridge's T1 table.
    assert sentinel.SENTINEL_FENCES_LIVE is False
    assert dict(sentinel.MAX_ENTRIES_PER_DAY) == {name: None for name in SENDABLE_IDENTITIES}
    # The manager has no slot count; the promoter's open-risk gate is closed.
    assert manager_context.BRIDGE_MAX_OPEN is None
    assert promotion_proposals.OPEN_RISK_TERM_DEPLOYED is False
    # The policy, the risk bound and the review pool.
    assert {name: run_policy(name) for name in SENDABLE_IDENTITIES} == dict.fromkeys(
        SENDABLE_IDENTITIES, NO_LIMIT_TEST_POLICY
    )
    assert REVIEW_POOL_SIZE == 4


def _exec_module(monkeypatch, path: Path, name: str, replacements: dict[str, str]) -> ModuleType:
    """Run a module's source, with exact text replacements, as a fresh module."""
    source = path.read_text(encoding="utf-8")
    for old, new in replacements.items():
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    module = ModuleType(name)
    module.__file__ = str(path)
    monkeypatch.setitem(sys.modules, name, module)
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102 - the module's own source
    return module


MODE_IMPORT = "from trading.risk.no_limit_test import NO_LIMIT_TEST, NO_LIMIT_TEST_LABEL\n"
MODE_OFF = 'NO_LIMIT_TEST, NO_LIMIT_TEST_LABEL = False, "L-T1"\n'


@pytest.mark.parametrize(
    ("relative", "v7c_values"),
    [
        (
            "trading/risk/identity_sizing.py",
            {"IDENTITY_SIZE_MULTIPLIER: Mapping[str, Decimal] = NO_LIMIT_TEST_SIZE_MULTIPLIER":
             "IDENTITY_SIZE_MULTIPLIER: Mapping[str, Decimal] = V7C_SIZE_MULTIPLIER"},
        ),
        (
            "trading/operations/sentinel.py",
            {'{"MOMO_HOD_V2": None, "ORB_V2": None, "VWAP_PB_V2": None, "GAP_GO_V2": None}':
             '{"MOMO_HOD_V2": None, "ORB_V2": 1, "VWAP_PB_V2": 2, "GAP_GO_V2": 1}',
             "SENTINEL_FENCES_LIVE = False\n": "SENTINEL_FENCES_LIVE = True\n"},
        ),
        (
            "trading/manager/context.py",
            {"BRIDGE_MAX_OPEN: int | None = None\n": "BRIDGE_MAX_OPEN: int | None = 3\n"},
        ),
        (
            "trading/research/promotion_proposals.py",
            {"OPEN_RISK_TERM_DEPLOYED = False\n": "OPEN_RISK_TERM_DEPLOYED = True\n"},
        ),
    ],
)
def test_a_half_on_build_cannot_import_and_the_switch_off_restores_v7c(monkeypatch, relative, v7c_values):
    path = BACKEND / relative
    name = "t1_tripwire_" + path.stem
    if relative != "trading/research/promotion_proposals.py":
        # Mode off with the test's values: refused at import.
        with pytest.raises(RuntimeError, match="no-limit test L-T1 is off"):
            _exec_module(monkeypatch, path, name, {MODE_IMPORT: MODE_OFF})
    # Mode on with v7c's values: refused at import.
    with pytest.raises(RuntimeError, match="no-limit test L-T1 is on"):
        _exec_module(monkeypatch, path, name, v7c_values)
    # Mode off with v7c's values: the v7c build imports, with exactly v7c's values.
    module = _exec_module(monkeypatch, path, name, {MODE_IMPORT: MODE_OFF, **v7c_values})
    if relative == "trading/risk/identity_sizing.py":
        assert dict(module.IDENTITY_SIZE_MULTIPLIER) == {
            "MOMO_HOD_V2": Decimal("1"), "ORB_V2": Decimal("0.25"), "VWAP_PB_V2": Decimal("0.5"),
            "GAP_GO_V2": Decimal("0.5"),
        }
    elif relative == "trading/operations/sentinel.py":
        assert module.SENTINEL_FENCES_LIVE is True
        assert dict(module.MAX_ENTRIES_PER_DAY) == dict(V7C_SENTINEL_MAX_ENTRIES_PER_DAY)
    elif relative == "trading/manager/context.py":
        assert module.BRIDGE_MAX_OPEN == V7C_MANAGER_MAX_OPEN
    else:
        assert module.OPEN_RISK_TERM_DEPLOYED is True


def test_the_size_table_refuses_a_row_the_step0_rule_cannot_produce(monkeypatch):
    path = BACKEND / "trading/risk/identity_sizing.py"
    source = path.read_text(encoding="utf-8")
    test_table = source[source.index("NO_LIMIT_TEST_SIZE_MULTIPLIER: Mapping"):]
    orb_row = '        "ORB_V2": Decimal("1"),\n'
    assert orb_row in test_table
    # 0.5 is neither x1 nor ORB_V2's own v7c row (0.25): refused.
    with pytest.raises(RuntimeError, match="Step 0 rule cannot produce"):
        _exec_module(monkeypatch, path, "t1_sizes_bad_row", {
            '"ORB_V2": Decimal("1"),\n        "VWAP_PB_V2": Decimal("1"),':
            '"ORB_V2": Decimal("0.5"),\n        "VWAP_PB_V2": Decimal("1"),',
        })
    # Keeping the v7c row (the rule's other outcome) is the one-line edit, and imports.
    kept = _exec_module(monkeypatch, path, "t1_sizes_kept_row", {
        '"ORB_V2": Decimal("1"),\n        "VWAP_PB_V2": Decimal("1"),':
        '"ORB_V2": Decimal("0.25"),\n        "VWAP_PB_V2": Decimal("1"),',
    })
    assert kept.IDENTITY_SIZE_MULTIPLIER["ORB_V2"] == Decimal("0.25")


def test_the_contract_tripwire_refuses_an_ai_cap_that_disagrees_with_the_mode(monkeypatch):
    path = BACKEND / "trading/risk/no_limit_test.py"
    with pytest.raises(RuntimeError, match="AI cap is 100 calls"):
        _exec_module(monkeypatch, path, "t1_mode_off_cap_on", {"NO_LIMIT_TEST = True\n": "NO_LIMIT_TEST = False\n"})


# --- the production path: shadow_service hands the run its policy -----------------------------


async def _session_capture(monkeypatch, strategy_name: str) -> dict[str, object]:
    """Run ``run_shadow_session`` on fakes; what reached the runner, its run and the provider."""
    from tests.test_shadow_service import TUESDAY, _production_universe, et, runtime_config
    from trading.operations import shadow_service
    from trading.orchestration.session_runner import SessionReport
    from trading.scheduler.sessions import market_session

    captured: dict[str, object] = {}

    class FakeActor:
        connected = True

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def connect(self) -> None:
            pass

        async def disconnect(self) -> None:
            pass

        async def preflight(self, *, required_data_lines: int) -> SimpleNamespace:
            return SimpleNamespace(ready=True, reasons=())

        async def account_snapshot(self) -> None:
            raise AssertionError("the fake runner never reads the account")

    class FakeMarket:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def load_contract(self, con_id: int) -> None:
            pass

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured["runner"] = kwargs

        async def run(self, **kwargs: object) -> SessionReport:
            captured["run"] = kwargs
            return SessionReport(run_id=str(kwargs["run_id"]), session_date=TUESDAY)

    class FakeController:
        admits = None

        def entry_gate(self, gate):
            return gate

        async def record_unfocused(self, **_kwargs: object) -> None:
            return None

    async def managed_session(**kwargs: object):
        return FakeController(), kwargs["neutral"]

    async def strategy(**kwargs: object) -> SimpleNamespace:
        captured["spec_name"] = kwargs["spec"].name
        return SimpleNamespace(strategy_id="f" * 32)

    def provider(**kwargs: object) -> SimpleNamespace:
        captured["provider"] = kwargs
        return SimpleNamespace(usage=SimpleNamespace(calls=0, cost_usd=Decimal("0")))

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(shadow_service, "ensure_strategy", strategy)
    monkeypatch.setattr(shadow_service, "_managed_session", managed_session)
    monkeypatch.setattr(shadow_service, "read_anthropic_api_key", lambda: "sk-test")
    monkeypatch.setattr(shadow_service, "build_anthropic_client", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "AnthropicMessagesTransport", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "StructuredAIReviewProvider", provider)
    monkeypatch.setattr(shadow_service, "TWSPaperActor", FakeActor)
    monkeypatch.setattr(shadow_service, "PaperIBKRMarketData", FakeMarket)
    monkeypatch.setattr(shadow_service, "ShadowPipeline", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "RepositoryBackedShadowRunner", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "ShadowSessionRunner", FakeRunner)
    monkeypatch.setattr(shadow_service, "source_sha", lambda: "abc1234")

    await shadow_service.run_shadow_session(
        config=runtime_config(),
        owner_id="demo",
        session=market_session(TUESDAY),
        clock=lambda: et(TUESDAY, 9, 0),
        sleep=no_sleep,
        strategy_name=strategy_name,
        universe=_production_universe(strategy_name),
    )
    return captured


def _strategy_names() -> list[str]:
    from trading.operations import shadow_service

    return sorted(shadow_service.STRATEGIES)


@pytest.mark.parametrize("strategy_name", _strategy_names())
async def test_every_session_hands_its_runner_the_run_policy_and_records_it(monkeypatch, strategy_name):
    """The first hop of the production path: run_shadow_session -> runner.run(policy=...)."""
    from trading.intelligence.review_provider import REVIEW_POOL_SIZE
    from trading.operations import shadow_service
    from trading.orchestration.session_runner import ShadowSessionRunner

    captured = await _session_capture(monkeypatch, strategy_name)

    spec_name = str(captured["spec_name"])
    run = captured["run"]
    assert isinstance(run, dict)
    expected = NO_LIMIT_TEST_POLICY if strategy_name in shadow_service.SENDABLE_KEYS else RiskPolicy()
    assert run["policy"] == expected == run_policy(spec_name)
    assert run["policy_fingerprints"]["risk"] == expected.fingerprint()  # type: ignore[index]
    # The run's AI cap: 100 calls / $25, from the runtime config.
    limits = captured["provider"]["limits"]  # type: ignore[index]
    assert (limits.max_calls, limits.max_cost_usd) == (100, Decimal("25"))
    # The review pool: the runner's default, never overridden by the service.
    runner = captured["runner"]
    assert isinstance(runner, dict) and "review_pool_size" not in runner
    assert inspect.signature(ShadowSessionRunner).parameters["review_pool_size"].default == REVIEW_POOL_SIZE == 4


def test_the_policy_check_covers_every_sendable_key_and_a_shadow_key():
    from trading.operations import shadow_service

    keys = set(shadow_service.STRATEGIES)
    names = {shadow_service.STRATEGIES[key][1]().name for key in shadow_service.SENDABLE_KEYS}
    assert names == set(SENDABLE_IDENTITIES)
    assert keys - shadow_service.SENDABLE_KEYS  # at least one shadow key keeps the default


# --- the production path: a 4th and 5th accepted decision in one run ---------------------------

SESSION = date(2026, 8, 27)
OPEN = datetime(2026, 8, 27, 13, 30, tzinfo=timezone.utc)
HASH = "c" * 64


def _bar(con_id: int, start: datetime, *, high: str, close: str) -> CompletedBar:
    return CompletedBar(
        con_id=con_id, interval_start_utc=start, interval_end_utc=start + timedelta(minutes=5),
        open="100", high=high, low="99", close=close, volume="10000", rth=True, source="ibkr",
        data_mode="live",
    )


class BreakoutMarket:
    """Scanner symbols that each break out of their opening range at their own bar."""

    def __init__(self, clock, breakouts: dict[str, datetime]) -> None:
        self.clock = clock
        self.breakouts = breakouts
        self.symbols = {201 + index: symbol for index, symbol in enumerate(breakouts)}

    async def scanner_snapshot(self, *, scanner_run_id):
        from trading.market.ibkr_boundary import IBKRScannerResult, qualify_scanner_results

        results = tuple(
            IBKRScannerResult(
                con_id=con_id, symbol=symbol, currency="USD", security_type="STK", stock_type="COMMON",
                primary_exchange="NASDAQ", rank=rank, raw_rank=rank - 1,
            )
            for rank, (con_id, symbol) in enumerate(self.symbols.items(), 1)
        )
        return qualify_scanner_results(results, scanner_run_id=scanner_run_id)

    async def completed_bars(self, *, con_id, now):
        breakout_at = self.breakouts[self.symbols[con_id]]
        prior_open = OPEN - timedelta(days=1)
        bars = [
            _bar(con_id, prior_open + timedelta(minutes=5 * index), high="101", close="100")
            for index in range(20)
        ]
        start = OPEN
        while start + timedelta(minutes=5) <= now:
            breakout = start >= breakout_at
            bars.append(_bar(
                con_id, start,
                high="103.2" if breakout else ("101" if start < OPEN + timedelta(minutes=15) else "100.8"),
                close="103" if breakout else "100.5",
            ))
            start += timedelta(minutes=5)
        return tuple(bars)

    async def quote(self, *, con_id):
        return QuoteSnapshot(con_id=con_id, bid="102.99", ask="103", observed_at=self.clock(), min_tick="0.01")

    async def liquidity(self, *, con_id, session_date):
        from trading.market.ibkr_boundary import LiquidityProfile

        return LiquidityProfile(
            con_id=con_id, reference_price=Decimal("103"), average_dollar_volume=Decimal("25000000"),
            sessions_used=20, last_session_date=session_date - timedelta(days=1),
        )


class ProposeAll:
    def __init__(self) -> None:
        self.calls = 0

    async def propose_trade(self, *, setup):
        self.calls += 1
        return AIReview(
            setup_fingerprint=setup.fingerprint(), decision="PROPOSE", confidence="0.7",
            rationale="T1 fixture review.", model_id="fixture", prompt_version="fixture",
            response_hash=HASH, latency_ms=1, cost_usd="0.02",
        )


async def _account() -> AccountSnapshot:
    return AccountSnapshot(
        paper_account_id="DU1234567", client_id=17, observed_at=datetime.now(timezone.utc),
        net_liquidation=Decimal("100000"), available_funds=Decimal("100000"),
        realized_day_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
    )


async def run_breakout_day(tmp_path, monkeypatch, *, policy: RiskPolicy, breakouts: dict[str, datetime],
                           provider=None, review_pool_size: int | None = None, database_name: str = "day.db"):
    """One ORB day through the real session runner, leased session and database."""
    from config import cfg
    from db.core import init_db
    from trading.operations.shadow_service import poll_schedule
    from trading.orchestration import RepositoryBackedShadowRunner, ShadowPipeline
    from trading.orchestration.session_runner import ShadowSessionRunner, ensure_orb_strategy
    from trading.scheduler.sessions import market_session

    database = tmp_path / database_name
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    clock = {"now": OPEN - timedelta(minutes=20)}

    async def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    strategy = await ensure_orb_strategy(owner_id="demo")
    provider = provider or ProposeAll()
    options = {} if review_pool_size is None else {"review_pool_size": review_pool_size}
    runner = ShadowSessionRunner(
        market=BreakoutMarket(lambda: clock["now"], breakouts),
        read_account=_account,
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=provider),
            clock=lambda: clock["now"],
            lease_ttl=timedelta(hours=6),
            lease_renew_interval=timedelta(hours=1),
        ),
        clock=lambda: clock["now"],
        poll_schedule=poll_schedule("orb", "scanner"),
        sleep=sleep,
        **options,
    )
    report = await runner.run(
        session=market_session(SESSION), strategy=strategy, policy=policy, source_sha="abcdef1",
        policy_fingerprints={"risk": policy.fingerprint()},
    )
    return report, database, provider


def _decisions_in_write_order(database: Path, run_id: str) -> list[tuple[str, str, dict]]:
    import json
    import sqlite3

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """SELECT c.symbol, d.outcome, d.payload_json FROM autotrader_candidate_decisions d
               JOIN autotrader_candidates c ON c.id = d.candidate_id
               WHERE d.run_id = ? ORDER BY d.rowid""",
            (run_id,),
        ).fetchall()
    return [(str(symbol), str(outcome), json.loads(payload)) for symbol, outcome, payload in rows]


STAGGERED = {
    symbol: OPEN + timedelta(minutes=40 + 10 * index)
    for index, symbol in enumerate(("AAA", "BBB", "CCC", "DDD", "EEE"))
}


@pytest.mark.anyio
async def test_a_fourth_and_fifth_accepted_decision_in_one_run_under_the_test_policy(tmp_path, monkeypatch):
    """Five breakouts in five polls: each poll's portfolio carries the run's accepted
    entries (pending exposure), so the default policy stops at three; the test policy
    accepts all five, each at the full policy size."""
    report, database, _provider = await run_breakout_day(
        tmp_path, monkeypatch, policy=NO_LIMIT_TEST_POLICY, breakouts=STAGGERED,
    )
    decisions = _decisions_in_write_order(database, report.run_id)
    assert [(symbol, outcome) for symbol, outcome, _ in decisions] == [
        (symbol, "shadow_accepted") for symbol in STAGGERED
    ]
    for _symbol, _outcome, payload in decisions:
        assert payload["risk"]["policy_fingerprint"] == NO_LIMIT_TEST_POLICY.fingerprint()
        assert payload["risk"]["approved"] is True
    quantities = {payload["risk"]["intent"]["quantity"] for _s, _o, payload in decisions}
    assert len(quantities) == 1  # the same setup sizes the same: no cap narrows the 4th or 5th


@pytest.mark.anyio
async def test_the_same_day_under_the_default_policy_stops_at_three(tmp_path, monkeypatch):
    report, database, _provider = await run_breakout_day(
        tmp_path, monkeypatch, policy=RiskPolicy(), breakouts=STAGGERED,
    )
    decisions = _decisions_in_write_order(database, report.run_id)
    assert [(symbol, outcome) for symbol, outcome, _ in decisions] == [
        ("AAA", "shadow_accepted"), ("BBB", "shadow_accepted"), ("CCC", "shadow_accepted"),
        ("DDD", "risk_blocked"), ("EEE", "risk_blocked"),
    ]
    for _symbol, outcome, payload in decisions[3:]:
        assert payload["risk"]["codes"] == ["max_open_positions_reached"]
