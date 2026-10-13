"""Strategy session selection, the cutoff refusal, the MOMO prompt and the resume fallback."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from decimal import Decimal
import logging
import os
import signal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from trading.contracts import RiskPolicy, ShadowRuntimeConfig
from trading.intelligence import prompts
from trading.operations import shadow_service
from trading.orchestration import ShadowPersistenceConflictError
from trading.orchestration.session_runner import SessionAborted, SessionReport
from trading.orchestration.watchlist_source import hotlist_refresh
from trading.scheduler.sessions import market_session, next_market_session

ET = ZoneInfo("America/New_York")
TUESDAY = date(2026, 10, 13)


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ET).astimezone(timezone.utc)


def runtime_config(prompt_id: str = "orb-review-v1") -> ShadowRuntimeConfig:
    return ShadowRuntimeConfig(
        paper_account_id="DU1",
        data_client_id=17,
        order_client_id=18,
        model_id="claude-opus-5-5",
        prompt_id=prompt_id,
        calendar_id="XNYS",
        calendar_version="2026.1",
        commission_policy_id="ibkr-fixed",
        commission_policy_version=1,
    )


def forbidden(*_args, **_kwargs):
    raise AssertionError("side effect before the session start checks")


# --- session selection --------------------------------------------------------


def test_requested_date_is_judged_by_the_strategys_own_cutoff():
    half_past_noon = et(TUESDAY, 12, 30)
    select = shadow_service.session_for_strategy

    assert select("momo", now=half_past_noon, requested=TUESDAY).session_date == TUESDAY
    with pytest.raises(SessionAborted, match="orb entry window for 2026-10-13 closed at 12:00 ET"):
        select("orb", now=half_past_noon, requested=TUESDAY)
    with pytest.raises(SessionAborted, match="momo entry window for 2026-10-13 closed at 15:00 ET"):
        select("momo", now=et(TUESDAY, 15, 0), requested=TUESDAY)
    # A later session is used as given; the run waits for its window.
    assert select("orb", now=half_past_noon, requested=date(2026, 10, 14)).session_date == date(
        2026, 10, 14
    )
    with pytest.raises(SessionAborted, match="not an XNYS trading session"):
        select("momo", now=half_past_noon, requested=date(2026, 10, 17))


def test_default_session_follows_the_strategys_own_window():
    select = shadow_service.session_for_strategy
    half_past_noon = et(TUESDAY, 12, 30)

    assert select("momo", now=half_past_noon).session_date == TUESDAY
    assert select("orb", now=half_past_noon).session_date == date(2026, 10, 14)
    assert select("orb", now=et(TUESDAY, 9, 0)).session_date == TUESDAY
    saturday = datetime(2026, 10, 17, 11, 0, tzinfo=ET)
    assert select("momo", now=saturday).session_date == date(2026, 10, 19)


def test_half_day_moves_the_momo_cutoff_in():
    half_day = date(2026, 11, 27)
    with pytest.raises(SessionAborted, match="momo entry window for 2026-11-27 closed at 12:00 ET"):
        shadow_service.session_for_strategy("momo", now=et(half_day, 12, 30), requested=half_day)


def test_unknown_strategy_and_universe_are_rejected():
    with pytest.raises(ValueError, match="unknown strategy"):
        shadow_service.strategy_entry_window("nonexistent", TUESDAY)
    with pytest.raises(ValueError, match="unknown universe"):
        shadow_service._require_strategy("momo", "everything")


def test_fresh_run_label_is_eastern_hour_and_minute():
    assert shadow_service.fresh_run_label(et(TUESDAY, 10, 32)) == "r1032"


# --- session start checks -------------------------------------------------------


async def test_run_refuses_a_closed_window_before_any_side_effect(monkeypatch):
    for name in ("ensure_strategy", "read_anthropic_api_key", "TWSPaperActor"):
        monkeypatch.setattr(shadow_service, name, forbidden)

    with pytest.raises(SessionAborted, match="orb entry window for 2026-10-13 closed at 12:00 ET"):
        await shadow_service.run_shadow_session(
            config=runtime_config(),
            owner_id="demo",
            session=market_session(TUESDAY),
            clock=lambda: et(TUESDAY, 12, 30),
            strategy_name="orb",
        )


async def test_momo_reviews_use_v2_and_fail_closed_without_it(monkeypatch):
    assert shadow_service.STRATEGIES["momo"][2] == "momo-review-v2"
    assert prompts.REVIEW_PROMPT_STRATEGY_KINDS["momo-review-v2"] == "momo_hod"
    monkeypatch.delitem(prompts.REVIEW_PROMPTS, "momo-review-v2")
    for name in ("ensure_strategy", "read_anthropic_api_key", "TWSPaperActor"):
        monkeypatch.setattr(shadow_service, name, forbidden)

    with pytest.raises(ValueError, match="momo-review-v2"):
        await shadow_service.run_shadow_session(
            config=runtime_config(),
            owner_id="demo",
            session=market_session(TUESDAY),
            clock=lambda: et(TUESDAY, 9, 0),
            strategy_name="momo",
            universe="watchlist",
        )


def test_policy_fingerprints_bind_prompt_model_and_config():
    config = runtime_config()
    prints = shadow_service.policy_fingerprints(config, RiskPolicy())
    assert set(prints) == {
        "risk",
        "opening_range_breakout",
        "execution",
        "review_prompt",
        "review_model",
        "runtime_config",
    }
    assert all(len(value) == 64 for value in prints.values())

    momo = shadow_service.policy_fingerprints(
        config, RiskPolicy(), prompt_id="momo-review-v2", strategy_name="momo"
    )
    assert len(momo["momo_hod"]) == 64
    assert momo["review_prompt"] != prints["review_prompt"]
    # The risk-off pause is part of MOMO's run identity; ORB's prints are unchanged.
    assert momo["market_regime_rule"] == "momo pauses in risk_off"
    assert set(momo) == set(prints) | {"momo_hod", "market_regime_rule"}


# --- dynamic hot list -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("strategy_name", "universe", "dynamic"),
    [
        ("momo", "watchlist", True),
        ("vwap", "watchlist", True),
        ("gap", "gappers", False),
        ("orb", "scanner", False),
    ],
)
async def test_only_watchlist_sessions_add_symbols_that_heat_up_later(
    monkeypatch, strategy_name, universe, dynamic
):
    captured: dict[str, dict[str, object]] = {}
    loaded: list[int] = []

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
            loaded.append(con_id)

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured["runner"] = kwargs

        async def run(self, **kwargs: object) -> SessionReport:
            captured["run"] = kwargs
            return SessionReport(run_id=str(kwargs["run_id"]), session_date=TUESDAY)

    async def strategy(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(strategy_id="f" * 32)

    async def no_sleep(_seconds: float) -> None:
        return None

    usage = SimpleNamespace(usage=SimpleNamespace(calls=0, cost_usd=Decimal("0")))
    monkeypatch.setattr(shadow_service, "ensure_strategy", strategy)
    monkeypatch.setattr(shadow_service, "read_anthropic_api_key", lambda: "sk-test")
    monkeypatch.setattr(shadow_service, "build_anthropic_client", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "AnthropicMessagesTransport", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "StructuredAIReviewProvider", lambda **_kwargs: usage)
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
        universe=universe,
    )

    refresh = captured["run"]["candidate_refresh"]
    loader = captured["runner"]["contract_loader"]
    assert (universe in shadow_service.DYNAMIC_UNIVERSES) is dynamic
    if not dynamic:
        assert refresh is None and loader is None
        return
    assert refresh is hotlist_refresh
    assert captured["run"]["candidate_source"] is not None
    # Added and resumed candidates load their contract through the session's market.
    assert callable(loader)
    await loader(4242)
    assert loaded == [4242]


# --- resume fallback -----------------------------------------------------------------


class RecordingSession:
    def __init__(self, *failures: BaseException) -> None:
        self.failures = list(failures)
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if self.failures:
            raise self.failures.pop(0)
        return "report"


async def _day(monkeypatch, fake: RecordingSession, *, universe: str, run_label: str | None = None):
    monkeypatch.setattr(shadow_service, "run_shadow_session", fake)
    return await shadow_service.run_shadow_day(
        config=runtime_config(),
        owner_id="demo",
        session=market_session(TUESDAY),
        strategy_name="momo",
        universe=universe,
        run_label=run_label,
        clock=lambda: et(TUESDAY, 10, 32),
    )


async def test_watchlist_run_that_cannot_resume_starts_one_fresh_labelled_run(monkeypatch):
    # A dynamic watchlist run resumes a newer hot list; changed code cannot resume.
    fake = RecordingSession(ShadowPersistenceConflictError("stored run differs from immutable request"))

    assert await _day(monkeypatch, fake, universe="watchlist") == "report"

    assert [call["run_label"] for call in fake.calls] == [None, "r1032"]
    assert {(call["strategy_name"], call["universe"]) for call in fake.calls} == {("momo", "watchlist")}


async def test_resume_fallback_runs_at_most_once(monkeypatch):
    fake = RecordingSession(
        ShadowPersistenceConflictError("first"), ShadowPersistenceConflictError("second")
    )

    with pytest.raises(ShadowPersistenceConflictError, match="second"):
        await _day(monkeypatch, fake, universe="watchlist")
    assert len(fake.calls) == 2


async def test_fallback_never_reuses_the_label_that_conflicted(monkeypatch):
    fake = RecordingSession(ShadowPersistenceConflictError("conflict"))

    with pytest.raises(ShadowPersistenceConflictError):
        await _day(monkeypatch, fake, universe="watchlist", run_label="r1032")
    assert len(fake.calls) == 1


@pytest.mark.parametrize("universe", ["scanner", "watchlist"])
async def test_other_failures_are_not_retried(monkeypatch, universe):
    fake = RecordingSession(SessionAborted("TWS preflight failed"))

    with pytest.raises(SessionAborted):
        await _day(monkeypatch, fake, universe=universe)
    assert len(fake.calls) == 1


async def test_scanner_resume_conflict_starts_one_fresh_run(monkeypatch):
    """A restarted ORB child re-scans, which can never match the stored run."""
    fake = RecordingSession(ShadowPersistenceConflictError("stored scanner run differs"))

    await _day(monkeypatch, fake, universe="scanner")

    assert len(fake.calls) == 2
    assert fake.calls[1]["run_label"] not in (None, fake.calls[0]["run_label"])


# --- CLI ------------------------------------------------------------------------------


def test_cli_shadow_run_flags_and_removed_daemon():
    from trading import cli

    parser = cli._parser()
    defaults = parser.parse_args(["shadow", "run"])
    assert (defaults.strategy, defaults.universe, defaults.date, defaults.new_run) == (
        "orb",
        "scanner",
        None,
        None,
    )
    full = parser.parse_args(
        [
            "shadow", "run", "--strategy", "momo", "--universe", "watchlist",
            "--date", "2026-10-13", "--new-run", "retry",
        ]
    )
    assert (full.strategy, full.universe, full.date, full.new_run) == (
        "momo",
        "watchlist",
        TUESDAY,
        "retry",
    )
    assert cli._STRATEGY_CHOICES == tuple(shadow_service.STRATEGIES)
    assert cli._UNIVERSE_CHOICES == shadow_service.UNIVERSES
    assert parser.parse_args(["shadow", "agent", "status"]).agent_action == "status"
    for removed in (["shadow", "daemon"], ["shadow", "run", "--strategy", "bogus"]):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(removed)
        assert error.value.code == 2


@pytest.fixture
def quiet_shadow_cli(monkeypatch):
    """Keep CLI runs off the real database, config and log directory."""
    from trading import cli
    from trading.operations import logging as shadow_logging, settings

    initialized: list[bool] = []

    def fake_schema_check(**_kwargs: object) -> None:
        # Children only check the schema (read-only); they never migrate.
        initialized.append(True)

    monkeypatch.setattr(cli, "require_current_schema", fake_schema_check)
    monkeypatch.setattr(settings, "load_shadow_runtime_config", runtime_config)
    monkeypatch.setattr(
        shadow_logging,
        "configure_shadow_logging",
        lambda: logging.getLogger(f"test.shadow.{uuid4().hex}"),
    )
    return initialized


def _next_session_date() -> date:
    return next_market_session(datetime.now(timezone.utc)).session_date


def test_cli_shadow_run_passes_strategy_universe_date_and_label(monkeypatch, capsys, quiet_shadow_cli):
    from trading import cli

    calls: list[dict[str, object]] = []

    class Report:
        def summary(self) -> dict[str, object]:
            return {"run_id": "shadow-x"}

    async def fake_day(**kwargs: object) -> Report:
        calls.append(kwargs)
        return Report()

    monkeypatch.setattr(shadow_service, "run_shadow_day", fake_day)
    session_date = _next_session_date()

    code = cli.main(
        [
            "shadow", "run", "--strategy", "momo", "--universe", "watchlist",
            "--date", session_date.isoformat(), "--new-run", "retry",
        ]
    )

    assert code == 0
    assert '"run_id": "shadow-x"' in capsys.readouterr().out
    (call,) = calls
    assert (call["strategy_name"], call["universe"], call["run_label"]) == ("momo", "watchlist", "retry")
    assert call["session"].session_date == session_date
    assert quiet_shadow_cli == [True]


def test_cli_shadow_run_refuses_a_closed_window_before_the_database(monkeypatch, capsys, quiet_shadow_cli):
    from trading import cli

    monkeypatch.setattr(shadow_service, "run_shadow_day", forbidden)

    assert cli.main(["shadow", "run", "--strategy", "orb", "--date", "2026-10-09"]) == 1

    assert "orb entry window for 2026-10-09 closed at 12:00 ET" in capsys.readouterr().err
    assert quiet_shadow_cli == []


def test_cli_shadow_run_sigterm_cancels_the_session_and_cleans_up(monkeypatch, quiet_shadow_cli):
    from trading import cli

    cleaned: list[str] = []

    async def fake_day(**_kwargs: object) -> None:
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.sleep(30)
        finally:
            cleaned.append("tws disconnected")

    monkeypatch.setattr(shadow_service, "run_shadow_day", fake_day)

    code = cli.main(
        ["shadow", "run", "--strategy", "momo", "--universe", "watchlist", "--date", _next_session_date().isoformat()]
    )

    assert code == cli.EXIT_STOPPED == 0
    assert cleaned == ["tws disconnected"]
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
