"""ORB_V2, VWAP_PB_V2 and GAP_GO_V2: the V1 rules behind the all-session window.

Owner decision "Both" (2026-10-13): the shadow simulations look for setups the
whole session (signal bars ending 09:35-15:30 ET, close - 30 minutes on a half
day). The golden tests below replay the real IBKR PAPER bars of 2026-10-09 and
2026-10-12 (tests/fixtures/momo) through V1 and V2 at every poll inside the V1
window and find every result identical: the window is the only change.
"""
from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.test_paper_bridge import pb as bridge
from tests.test_research import BF_B, FIGS, PRIOR, SESSION, _capture_store, _stored_day
from trading.contracts import Candidate, CompletedBar, RiskPolicy, ShadowRuntimeConfig
from trading.intelligence.prompts import REVIEW_PROMPTS, review_prompt_for
from trading.operations import shadow_service
from trading.operations.shadow_service import (
    MIN_POLL_SEPARATION,
    STRATEGIES,
    STRATEGY_CLIENT_OFFSET,
    STRATEGY_POLL_OFFSETS,
    poll_offset_problems,
    policy_fingerprints,
    strategy_entry_window,
)
from trading.operations.supervisor import STRATEGY_STARTS, STRATEGY_UNIVERSES, plan_session
from trading.research.replay import (
    POLL_DELAY,
    replay_strategy,
    synthetic_quote,
)
from trading.rules import all_session
from trading import sendable_identities
from trading.rules.all_session import (
    ALL_SESSION_STRATEGY_NAMES,
    ALL_SESSION_WINDOW,
    GAP_REVIEW_V2,
    GAP_REVIEW_V2_PROMPT_ID,
    V1_WINDOWS,
    WINDOW_FIELDS,
    AllSessionStrategy,
    EntryWindow,
    gap_go_v2_strategy,
    orb_v2_strategy,
    rules_fingerprint,
    vwap_pb_v2_strategy,
    windowed_strategy,
)
from trading.rules.gap_go import (
    GAP_GO_STRATEGY_KIND,
    GapGoConfig,
    GapGoStrategy,
    gap_go_strategy_spec,
)
from trading.rules.momo import MOMO_STRATEGY_KIND, MomoConfig, MomoStrategy
from trading.rules.orb import QUOTE_REQUIRED, ORBConfig
from trading.rules.strategy import ORBStrategy, StrategyEvaluator, entry_window_utc
from trading.rules.vwap_pb import VWAP_PB_STRATEGY_KIND, VwapPbConfig, VwapPbStrategy
from trading.scheduler.sessions import market_session
from trading.sendable_identities import SENDABLE_IDENTITIES, carries_sendable_prefix
from trading.storage.manager_setups import formed_setup

NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
FULL_DAY = date(2026, 10, 13)
HALF_DAY = date(2026, 11, 27)  # the day after Thanksgiving closes at 13:00
V2_KEYS = ("orb-v2", "vwap-v2", "gap-v2")
V1_OF = {"orb-v2": "orb", "vwap-v2": "vwap", "gap-v2": "gap"}
DEFAULT_CONFIGS = {
    "orb": ORBConfig(),
    VWAP_PB_STRATEGY_KIND: VwapPbConfig(),
    GAP_GO_STRATEGY_KIND: GapGoConfig(),
    MOMO_STRATEGY_KIND: MomoConfig(),
}


def _clock(moment: datetime) -> str:
    return moment.astimezone(NEW_YORK).strftime("%H:%M")


def _config(strategy: StrategyEvaluator, session_date: date) -> Any:
    inner = strategy.inner if isinstance(strategy, AllSessionStrategy) else strategy
    config_for = getattr(inner, "config_for", None)
    return config_for(session_date) if config_for is not None else inner.config


# --- the window ----------------------------------------------------------------


def test_the_all_session_window_is_pinned():
    assert ALL_SESSION_WINDOW == EntryWindow(
        start=time(9, 35), cutoff=time(15, 30), close_buffer=timedelta(minutes=30)
    )
    assert ALL_SESSION_WINDOW.payload() == {
        "start": "09:35",
        "cutoff": "15:30",
        "close_buffer_minutes": 30,
    }
    assert ALL_SESSION_WINDOW.label() == "09:35-15:30"


@pytest.mark.parametrize(
    ("key", "full", "half"),
    [
        # ORB's first signal bar ends after its 09:30-09:45 range; VWAP_PB's
        # after its four-bar pullback lookback and the opening bar.
        ("orb-v2", ("09:50", "15:30"), ("09:50", "12:30")),
        ("vwap-v2", ("09:55", "15:30"), ("09:55", "12:30")),
        ("gap-v2", ("09:35", "15:30"), ("09:35", "12:30")),
    ],
)
def test_v2_windows_run_all_session_and_end_30_minutes_before_any_close(key, full, half):
    for day, expected in ((FULL_DAY, full), (HALF_DAY, half)):
        start, cutoff = strategy_entry_window(key, day)
        assert (_clock(start), _clock(cutoff)) == expected, (key, day)
        session = market_session(day)
        assert session is not None
        assert cutoff == min(
            datetime.combine(day, time(15, 30), tzinfo=NEW_YORK), session.closes_at - timedelta(minutes=30)
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": time(9, 30), "cutoff": time(15, 30)},
        {"start": time(15, 30), "cutoff": time(15, 30)},
        {"start": time(9, 35), "cutoff": time(16, 5)},
        {"start": time(9, 37), "cutoff": time(15, 30)},
        {"start": time(9, 35), "cutoff": time(15, 30), "close_buffer": timedelta(minutes=-5)},
        {"start": time(9, 35), "cutoff": time(15, 30), "close_buffer": timedelta(minutes=7)},
    ],
)
def test_an_entry_window_off_the_bar_grid_or_outside_regular_hours_is_refused(kwargs):
    with pytest.raises(ValueError):
        EntryWindow(**kwargs)


def test_orb_cannot_start_after_its_first_signal_bar():
    with pytest.raises(ValueError, match="09:50"):
        windowed_strategy("orb", EntryWindow(start=time(10, 0), cutoff=time(15, 30)))


# --- the rules are V1's, only the window differs ---------------------------------


@pytest.mark.parametrize("key", V2_KEYS)
@pytest.mark.parametrize("day", [FULL_DAY, HALF_DAY], ids=["full-day", "half-day"])
def test_v2_configs_differ_from_v1_only_in_the_window_fields(key, day):
    v2 = STRATEGIES[key][0]()
    v1 = STRATEGIES[V1_OF[key]][0]()
    assert v2.strategy_kind == v1.strategy_kind
    kind = v2.strategy_kind
    v1_config, v2_config = _config(v1, day), _config(v2, day)
    assert type(v1_config) is type(v2_config)
    differing = {
        item.name
        for item in fields(v1_config)
        if getattr(v1_config, item.name) != getattr(v2_config, item.name)
    }
    assert differing and differing <= WINDOW_FIELDS[kind], differing
    assert v1_config == DEFAULT_CONFIGS[kind]


def test_window_fields_are_pinned():
    assert WINDOW_FIELDS == {
        "orb": frozenset({"entry_cutoff"}),
        "vwap_pb": frozenset({"window_start", "entry_cutoff", "close_buffer"}),
        "gap_go": frozenset({"window_start", "entry_cutoff"}),
        "momo_hod": frozenset({"window_start", "entry_cutoff", "close_buffer"}),
    }


@pytest.mark.parametrize("kind", sorted(V1_WINDOWS))
@pytest.mark.parametrize("day", [FULL_DAY, HALF_DAY], ids=["full-day", "half-day"])
def test_each_v1_window_rebuilds_exactly_the_v1_strategy(kind, day):
    v1 = {
        "orb": ORBStrategy(),
        VWAP_PB_STRATEGY_KIND: VwapPbStrategy(),
        GAP_GO_STRATEGY_KIND: GapGoStrategy(),
        MOMO_STRATEGY_KIND: MomoStrategy(),
    }[kind]
    rebuilt = windowed_strategy(kind, V1_WINDOWS[kind])
    assert _config(rebuilt, day) == _config(v1, day) == DEFAULT_CONFIGS[kind]
    assert entry_window_utc(rebuilt, day) == entry_window_utc(v1, day)


def test_rules_fingerprints_ignore_the_window_and_name_the_v1_rules():
    for kind in V1_WINDOWS:
        payload_fields = {
            item.name for item in fields(DEFAULT_CONFIGS[kind])
        } - WINDOW_FIELDS[kind]
        assert payload_fields  # every other field is a rule
        assert rules_fingerprint(kind) == rules_fingerprint(kind)
    assert len({rules_fingerprint(kind) for kind in V1_WINDOWS}) == 4
    for key in V2_KEYS:
        prints = STRATEGIES[key][0]().policy_fingerprints()
        assert prints["rules"] == rules_fingerprint(STRATEGIES[key][0]().strategy_kind)
        assert prints["entry_window"] == ALL_SESSION_WINDOW.fingerprint()
        assert prints["rules_version"] == STRATEGIES[key][1]().name
    assert ALL_SESSION_WINDOW.fingerprint() != V1_WINDOWS["orb"].fingerprint()


# --- golden: identical results at every poll inside the V1 window ------------------


def _candidate(day) -> Candidate:
    return Candidate(
        id=f"golden-{day.con_id}",
        scanner_run_id="golden",
        con_id=day.con_id,
        symbol=day.symbol,
        primary_exchange=day.primary_exchange or "SMART",
        rank=1,
        admission_status="admitted",
    )


def _poll(strategy: StrategyEvaluator, bars, *, day, now, reference_price=None):
    """One live-like poll: bars first; a synthetic quote once the rules ask for one."""
    inputs: dict[str, Any] = {
        "candidate": _candidate(day),
        "session_date": day.session_date,
        "run_id": "golden-run",
        "strategy_id": "golden-strategy",
        "strategy_version": 1,
        "reference_price": reference_price or day.prior_close,
        "average_dollar_volume": day.avg_dollar_volume,
        "candidate_input_fingerprint": None,
        "now": now,
    }
    visible = tuple(bar for bar in bars if bar.interval_end_utc <= now)
    result = strategy.evaluate(bars=visible, quote=None, **inputs)
    if result.setup is None and result.rejection_reason == QUOTE_REQUIRED:
        close = visible[-1].close
        quote = synthetic_quote(con_id=day.con_id, close=close, now=now)
        result = strategy.evaluate(bars=visible, quote=quote, **inputs)
    return result


def _golden(v1, v2, bars, *, day, reference_price=None) -> tuple[int, list[str]]:
    """Compare every poll of the V1 window; returns (polls, setup clocks)."""
    start, cutoff = entry_window_utc(v1, day.session_date)
    polls, setups = 0, []
    for bar in bars:
        if bar.interval_start_utc.astimezone(NEW_YORK).date() != day.session_date or not bar.rth:
            continue
        if bar.interval_end_utc < start:
            continue
        now = bar.interval_end_utc + POLL_DELAY
        if now >= cutoff:
            break
        first = _poll(v1, bars, day=day, now=now, reference_price=reference_price)
        second = _poll(v2, bars, day=day, now=now, reference_price=reference_price)
        assert first == second, (day.symbol, _clock(now))
        polls += 1
        if first.setup is not None:
            assert first.setup.fingerprint() == second.setup.fingerprint()
            setups.append(_clock(first.setup.detected_at))
    return polls, setups


def _bars(capture, session_date: date = SESSION) -> tuple[CompletedBar, ...]:
    prior = _stored_day(capture, PRIOR).completed_bars()
    return (*prior, *_stored_day(capture, session_date).completed_bars())


def test_golden_orb_v1_and_v2_agree_on_real_bars_at_every_v1_poll():
    seen = {}
    for capture in (BF_B, FIGS):
        day = _stored_day(capture, SESSION)
        seen[capture.symbol] = _golden(ORBStrategy(), orb_v2_strategy(), _bars(capture), day=day)
    # 09:50-12:00 bar ends: 26 polls each. Both price BF B's 10:00 and FIGS's
    # 10:15 breakout identically at every poll whose synthetic ask is still
    # within the 0.2% chase limit of the breakout close (22 and 3 polls).
    assert seen == {"BF B": (26, ["10:00"] * 22), "FIGS": (26, ["10:15"] * 3)}


def test_golden_vwap_pb_v1_and_v2_agree_on_real_bars_at_every_v1_poll():
    seen = {}
    for capture in (BF_B, FIGS):
        day = _stored_day(capture, SESSION)
        seen[capture.symbol] = _golden(VwapPbStrategy(), vwap_pb_v2_strategy(), _bars(capture), day=day)
    # 10:30-14:25 bar ends, 48 polls each, no reclaim (test_research says why).
    assert seen == {"BF B": (48, []), "FIGS": (48, [])}


def _premarket(day, *, high: str, lots: str = "400") -> tuple[CompletedBar, ...]:
    """Synthetic 08:00-09:30 ET pre-market bars (rth=False) under ``high``."""
    start = datetime.combine(day.session_date, time(8, 0), tzinfo=NEW_YORK).astimezone(UTC)
    peak = Decimal(high)
    bars = []
    for index in range(18):
        open_ = peak - Decimal("0.30")
        bars.append(
            CompletedBar(
                con_id=day.con_id,
                interval_start_utc=start + timedelta(minutes=5 * index),
                interval_end_utc=start + timedelta(minutes=5 * (index + 1)),
                open=open_,
                high=peak if index == 9 else open_ + Decimal("0.05"),
                low=open_ - Decimal("0.05"),
                close=open_,
                volume=Decimal(lots),
                rth=False,
                source="ibkr",
                data_mode="historical",
            )
        )
    return tuple(bars)


def _gap_day_bars(high: str) -> tuple[Any, tuple[CompletedBar, ...]]:
    # FIGS's real regular-hours bars after a synthetic pre-market; with a 15.30
    # reference close its 15.89 open is a 3.86% gap.
    day = _stored_day(FIGS, SESSION)
    prior = _stored_day(FIGS, PRIOR).completed_bars()
    return day, (*prior, *_premarket(day, high=high), *day.completed_bars())


def test_golden_gap_go_v1_and_v2_agree_inside_the_v1_window():
    day, bars = _gap_day_bars("16.40")
    polls, setups = _golden(GapGoStrategy(), gap_go_v2_strategy(), bars, day=day, reference_price="15.30")
    # The trigger is the first bar's 16.48 high; the 09:35-09:40 bar closed
    # 16.59 above it, the first breakout, priced at 09:40 by both.
    assert polls == 11 and setups == ["09:40"]


def test_gap_go_v2_prices_a_first_breakout_v1_never_could():
    # A 17.20 pre-market high: FIGS's first close above it comes after 10:30.
    day, bars = _gap_day_bars("17.20")
    assert _golden(GapGoStrategy(), gap_go_v2_strategy(), bars, day=day, reference_price="15.30")[1] == []
    v2 = gap_go_v2_strategy()
    start, cutoff = entry_window_utc(v2, SESSION)
    found = None
    for bar in day.completed_bars():
        now = bar.interval_end_utc + POLL_DELAY
        if bar.interval_end_utc < start or now >= cutoff:
            continue
        result = _poll(v2, bars, day=day, now=now, reference_price="15.30")
        if result.setup is not None:
            found = result.setup
            break
        if not result.retryable:
            break
    assert found is not None
    assert time(10, 30) < found.detected_at.astimezone(NEW_YORK).time() <= time(15, 30)
    assert found.levels["trigger"] == Decimal("17.20")


def test_golden_orb_replay_trades_are_v1s_inside_the_v1_window(tmp_path):
    store = _capture_store(tmp_path)
    v1 = replay_strategy("orb", store)
    v2 = replay_strategy("orb-v2", store)
    assert [trade.to_payload() for trade in v2.trades] == [trade.to_payload() for trade in v1.trades]
    assert v2.metadata["rules_version"] == "ORB_V2"


# --- never sendable -------------------------------------------------------------


def test_no_v2_simulation_can_reach_the_paper_bridge():
    assert SENDABLE_IDENTITIES == ("MOMO_HOD_V2",)
    names = {key: STRATEGIES[key][1]().name for key in V2_KEYS}
    assert names == {"orb-v2": "ORB_V2", "vwap-v2": "VWAP_PB_V2", "gap-v2": "GAP_GO_V2"}
    assert tuple(names.values()) == ALL_SESSION_STRATEGY_NAMES
    for name in names.values():
        assert not name.upper().startswith("MOMO_HOD")
        assert not carries_sendable_prefix(name) and name not in SENDABLE_IDENTITIES
        stored = bridge.strategy_name_from_spec(json.dumps({"name": name}))
        assert not bridge.may_send_orders(stored)
    # Every registry name except the one sendable identity is shadow only.
    registry_names = {factory().name for _evaluator, factory, _prompt in STRATEGIES.values()}
    assert {name for name in registry_names if carries_sendable_prefix(name)} == {"MOMO_HOD_V2"}


def test_a_momo_name_is_refused_for_a_v2_simulation_but_a_granted_v2_name_is_not(monkeypatch):
    with pytest.raises(ValueError, match="MOMO's name"):
        AllSessionStrategy("MOMO_HOD_V3", "orb")
    with pytest.raises(RuntimeError, match="MOMO's name"):
        all_session._never_momo_named(("ORB_V2", " momo_hod_x"))
    # Bridge v7: the owner may grant a V2 name PAPER authority; that is decided by
    # SENDABLE_IDENTITIES and checked at launch, never refused at import.
    monkeypatch.setattr(sendable_identities, "SENDABLE_IDENTITIES", ("MOMO_HOD_V2", "ORB_V2"))
    all_session._never_momo_named(("ORB_V2", "VWAP_PB_V2", "GAP_GO_V2"))
    AllSessionStrategy("ORB_V2", "orb")  # constructs: the grant is not an import-time refusal


# --- registry, offsets, supervisor ---------------------------------------------------


def test_v2_simulations_poll_in_slots_of_their_own():
    assert {key: STRATEGY_POLL_OFFSETS[key] for key in V2_KEYS} == {
        "orb-v2": timedelta(seconds=75),
        "gap-v2": timedelta(seconds=90),
        "vwap-v2": timedelta(seconds=105),
    }
    for key in V2_KEYS:
        for other, offset in STRATEGY_POLL_OFFSETS.items():
            if other != key:
                assert abs(offset - STRATEGY_POLL_OFFSETS[key]) >= MIN_POLL_SEPARATION
    assert STRATEGY_POLL_OFFSETS["momo"] == timedelta(seconds=15) == min(STRATEGY_POLL_OFFSETS.values())
    assert poll_offset_problems(STRATEGY_POLL_OFFSETS) == []
    # A V2 slot colliding with any other trips the offset check.
    broken = {**STRATEGY_POLL_OFFSETS, "gap-v2": timedelta(seconds=80)}
    assert "orb-v2 and gap-v2 poll 0:00:05 apart" in poll_offset_problems(broken)


def test_v2_simulations_replace_v1_in_the_supervisor_with_the_v1_client_ids():
    assert STRATEGY_STARTS == {
        "orb-v2": time(9, 28),
        "momo": time(9, 55),
        "gap-v2": time(9, 28),
        "vwap-v2": time(9, 28),
    }
    assert STRATEGY_UNIVERSES == {
        "orb-v2": "scanner",
        "momo": "watchlist",
        "gap-v2": "all-gappers",
        "vwap-v2": "watchlist",
    }
    for key in V2_KEYS:
        assert STRATEGY_CLIENT_OFFSET[key] == STRATEGY_CLIENT_OFFSET[V1_OF[key]]
    scheduled_ids = [STRATEGY_CLIENT_OFFSET[name] for name in STRATEGY_STARTS]
    assert len(scheduled_ids) == len(set(scheduled_ids))


@pytest.mark.parametrize(
    ("day", "end"), [(FULL_DAY, ("15:31", "15:30")), (HALF_DAY, ("12:31", "12:30"))]
)
def test_supervised_v2_children_run_0928_to_one_minute_after_their_cutoff(day, end):
    session = market_session(day)
    assert session is not None
    plans = {plan.name: plan for plan in plan_session(session)}
    assert not {"orb", "gap", "vwap"} & set(plans)
    for key in V2_KEYS:
        plan = plans[key]
        assert _clock(plan.starts_at) == "09:28"
        assert (_clock(plan.ends_at), _clock(plan.finishes_at)) == end
        assert plan.argv[3:] == (
            "run", "--strategy", key, "--universe", STRATEGY_UNIVERSES[key], "--date", day.isoformat(),
        )


def test_v2_reviews_use_prompts_written_for_their_rules_and_the_per_run_budget():
    for key in V2_KEYS:
        evaluator_factory, _spec, prompt_id = STRATEGIES[key]
        assert prompt_id is not None
        review_prompt_for(prompt_id, strategy_kind=evaluator_factory().strategy_kind)
    assert STRATEGIES["gap-v2"][2] == GAP_REVIEW_V2_PROMPT_ID == "gap-review-v2"
    assert REVIEW_PROMPTS["gap-review-v2"] == GAP_REVIEW_V2
    assert "between 09:35 and 15:30 ET (12:30 on a half day)" in GAP_REVIEW_V2
    # gap-review-v1 states the V1 window, which would contradict a V2 setup.
    assert "between 09:35 and 10:30 ET" in REVIEW_PROMPTS["gap-review-v1"]
    # The review keeps its per-run limits, 20 calls and $5, pinned by the
    # runtime config every strategy run reads (no per-strategy override).
    assert ShadowRuntimeConfig.model_fields["max_ai_calls"].default == 20
    assert ShadowRuntimeConfig.model_fields["max_ai_cost_usd"].default == Decimal("5")


def test_v2_runs_record_their_rules_and_window_apart_and_v1_runs_are_unchanged():
    config = ShadowRuntimeConfig(
        paper_account_id="DU1234567",
        data_client_id=17,
        order_client_id=18,
        model_id="claude-opus-5-5",
        prompt_id="orb-review-v1",
        calendar_id="XNYS",
        calendar_version="2026.1",
        commission_policy_id="ibkr-fixed",
        commission_policy_version=1,
    )
    v1 = policy_fingerprints(config, RiskPolicy(), prompt_id="vwap-review-v1", strategy_name="vwap")
    v2 = policy_fingerprints(config, RiskPolicy(), prompt_id="vwap-review-v1", strategy_name="vwap-v2")
    assert {"rules", "entry_window", "rules_version"}.isdisjoint(v1)
    assert v1["vwap_pb"] == VwapPbConfig().fingerprint()
    assert v2["rules"] == rules_fingerprint("vwap_pb")
    assert v2["entry_window"] == ALL_SESSION_WINDOW.fingerprint()
    assert v2["rules_version"] == "VWAP_PB_V2"
    assert {key: value for key, value in v2.items() if key in v1} == {
        key: value for key, value in v1.items() if key != "vwap_pb"
    }


def test_a_v2_setup_carries_the_hour_bucket_of_its_signal_bar():
    day, bars = _gap_day_bars("17.20")
    v2 = gap_go_v2_strategy()
    start, cutoff = entry_window_utc(v2, SESSION)
    setup = None
    for bar in day.completed_bars():
        now = bar.interval_end_utc + POLL_DELAY
        if bar.interval_end_utc < start or now >= cutoff:
            continue
        result = _poll(v2, bars, day=day, now=now, reference_price="15.30")
        if result.setup is not None:
            setup = result.setup
            break
    assert setup is not None
    record = shadow_service.STRATEGIES["gap-v2"][1]()
    strategy = type("Record", (), {"spec": record, "content_hash": "f" * 64})()
    row = formed_setup(
        setup=setup,
        strategy=strategy,  # type: ignore[arg-type]
        candidate=_candidate(day),
        bars=bars,
        session_date=SESSION,
        now=setup.detected_at + timedelta(seconds=30),
    )
    signal_start = setup.detected_at - timedelta(minutes=5)
    assert row.hour_bucket == signal_start.astimezone(NEW_YORK).hour > 10
    assert row.strategy_name == "GAP_GO_V2"


def test_gap_go_v1_still_refuses_a_window_past_the_earliest_close():
    # GapGoStrategy has no calendar: only a resolved per-session config may run later.
    with pytest.raises(ValueError, match="13:00"):
        GapGoStrategy(replace(GapGoConfig(), entry_cutoff=time(13, 5)))
    assert gap_go_strategy_spec().name == "GAP_GO_V1"


# --- the session wiring -----------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "source", "gap_bars", "dynamic"),
    [
        ("orb-v2", None, False, False),
        ("vwap-v2", "hotlist_candidates", False, True),
        ("gap-v2", "gap_union_candidates", True, False),
        ("gap", "gap_candidates", True, False),
    ],
)
async def test_each_v2_session_gets_its_universe_and_bars_like_its_v1(monkeypatch, key, source, gap_bars, dynamic):
    from types import SimpleNamespace

    from trading.orchestration.session_runner import SessionReport

    captured: dict[str, Any] = {}
    selected: list[str] = []

    class FakeActor:
        connected = True

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            captured["client_id"] = _args[0].client_id

        async def connect(self) -> None:
            pass

        async def disconnect(self) -> None:
            pass

        async def preflight(self, *, required_data_lines: int) -> SimpleNamespace:
            return SimpleNamespace(ready=True, reasons=())

        async def account_snapshot(self) -> None:
            raise AssertionError("never read")

    class FakeMarket:
        def __init__(self, **kwargs: object) -> None:
            captured["bar_max_age"] = kwargs["bar_max_age"]

        async def load_contract(self, con_id: int) -> None:
            pass

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured["runner"] = kwargs

        async def run(self, **kwargs: object) -> SessionReport:
            captured["run"] = kwargs
            return SessionReport(run_id=str(kwargs["run_id"]), session_date=FULL_DAY)

    def fake_source(name: str):
        async def select(run, *, now):
            selected.append(name)
            return "scanner-run", (), {}

        return select

    async def strategy(**kwargs: object) -> SimpleNamespace:
        captured["spec"] = kwargs["spec"]
        return SimpleNamespace(strategy_id="f" * 32)

    async def no_sleep(_seconds: float) -> None:
        return None

    usage = SimpleNamespace(usage=SimpleNamespace(calls=0, cost_usd=Decimal("0")))

    def transport(**kwargs: Any) -> object:
        captured["prompt"] = kwargs["system_prompt"]
        return object()

    def provider(**kwargs: Any) -> object:
        captured["limits"] = kwargs["limits"]
        return usage

    for name in ("hotlist_candidates", "gap_candidates", "gap_union_candidates"):
        monkeypatch.setattr(shadow_service, name, fake_source(name))
    monkeypatch.setattr(shadow_service, "ensure_strategy", strategy)
    monkeypatch.setattr(shadow_service, "read_anthropic_api_key", lambda: "sk-test")
    monkeypatch.setattr(shadow_service, "build_anthropic_client", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "AnthropicMessagesTransport", transport)
    monkeypatch.setattr(shadow_service, "StructuredAIReviewProvider", provider)
    monkeypatch.setattr(shadow_service, "TWSPaperActor", FakeActor)
    monkeypatch.setattr(shadow_service, "PaperIBKRMarketData", FakeMarket)
    monkeypatch.setattr(shadow_service, "ShadowPipeline", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "RepositoryBackedShadowRunner", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "ShadowSessionRunner", FakeRunner)
    monkeypatch.setattr(shadow_service, "source_sha", lambda: "abc1234")
    config = ShadowRuntimeConfig(
        paper_account_id="DU1234567", data_client_id=17, order_client_id=18,
        model_id="claude-opus-5-5", prompt_id="orb-review-v1", calendar_id="XNYS",
        calendar_version="2026.1", commission_policy_id="ibkr-fixed", commission_policy_version=1,
    )

    await shadow_service.run_shadow_session(
        config=config,
        owner_id="demo",
        session=market_session(FULL_DAY),
        clock=lambda: datetime.combine(FULL_DAY, time(9, 0), tzinfo=NEW_YORK).astimezone(UTC),
        sleep=no_sleep,
        strategy_name=key,
        universe=STRATEGY_UNIVERSES.get(key, "gappers"),
    )

    assert captured["spec"] == STRATEGIES[key][1]()
    assert captured["client_id"] == 17 + STRATEGY_CLIENT_OFFSET[key]
    assert (captured["runner"]["bars_fetcher"] is not None) is gap_bars
    assert (captured["run"]["candidate_refresh"] is not None) is dynamic
    assert captured["prompt"] == REVIEW_PROMPTS[STRATEGIES[key][2]]
    assert (captured["limits"].max_calls, captured["limits"].max_cost_usd) == (20, Decimal("5"))
    candidate_source = captured["run"]["candidate_source"]
    if source is None:
        assert candidate_source is None  # ORB keeps its 09:45 IBKR scan
    else:
        await candidate_source("run")
        assert selected == [source]


# --- GAP_GO_V2's universe: watchlist gappers plus the day universe's gappers -------

GAP_NOW = datetime(2026, 10, 12, 13, 30, tzinfo=UTC)


async def _seed_gap_snapshot(*, day_universe: list[tuple[int, str, list[str], int]] | None) -> None:
    import sqlite3

    from config import cfg
    from db.core import get_db
    from trading.storage.manager_schema import MANAGER_PHASE1_SCHEMA

    rows = [
        {"symbol": "AAA", "con_id": 1, "source": "watchlist", "gap_pct": None, "change_pct": "4.5"},
        {"symbol": "BBB", "con_id": 2, "source": "watchlist", "gap_pct": "6.0", "change_pct": "1.0"},
        {"symbol": "CCC", "con_id": 3, "source": "watchlist", "gap_pct": "2.0", "change_pct": "2.5"},
        {"symbol": "DDD", "con_id": 4, "source": "ibkr_scan", "gap_pct": "9.0", "change_pct": "9.0"},
        {"symbol": "GGG", "con_id": 7, "source": "ibkr_scan", "gap_pct": "8.0", "change_pct": "8.0"},
    ]
    async with get_db() as db:
        await db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            ("snap-u", "2026-10-12", (GAP_NOW - timedelta(minutes=1)).isoformat(), json.dumps({"rows": rows})),
        )
        for con_id in (1, 2, 3, 4, 5, 6, 7):
            await db.execute(
                "INSERT INTO autotrader_universe_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("2026-10-12", con_id, f"S{con_id}", "50.00", "1000000", "90000000", None, 1, None, "t"),
            )
        await db.commit()
    if day_universe is None:
        return
    with sqlite3.connect(cfg.DB_PATH) as connection:
        for statement in MANAGER_PHASE1_SCHEMA:  # migration 15: already in place
            connection.execute(statement)
        for con_id, symbol, sources, qualified in day_universe:
            connection.execute(
                """INSERT INTO autotrader_manager_day_universe
                   (session_date, con_id, symbol, first_seen, last_seen, sources_json, best_rank,
                    qualified, reason)
                   VALUES ('2026-10-12', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    con_id,
                    symbol,
                    "2026-10-12T12:00:00+00:00",
                    "2026-10-12T13:25:00+00:00",
                    json.dumps(sources),
                    con_id,
                    qualified,
                    None if qualified else "price_out_of_range",
                ),
            )


def _gap_run():
    from trading.contracts import EngineMode, Run

    return Run(
        id="shadow-g-2026-10-12",
        owner_id="demo",
        source_sha="abcdef1",
        strategy_id="s",
        strategy_version=1,
        policy_fingerprints={},
        session_date=date(2026, 10, 12),
        mode=EngineMode.SHADOW,
    )


async def test_without_the_day_universe_table_gap_go_v2_trades_the_watchlist_gappers(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db
    from trading.orchestration.day_universe import qualified_day_universe
    from trading.orchestration.watchlist_source import gap_candidates, gap_union_candidates

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "u.db"))
    await init_db()
    await _seed_gap_snapshot(day_universe=None)

    assert await qualified_day_universe(date(2026, 10, 12)) == ()
    scanner_run, candidates, liquidity = await gap_union_candidates(_gap_run(), now=GAP_NOW)
    _v1_run, v1_candidates, _ = await gap_candidates(_gap_run(), now=GAP_NOW)

    assert [c.symbol for c in candidates] == [c.symbol for c in v1_candidates] == ["BBB", "AAA"]
    assert scanner_run.parameters["day_universe_names"] == 0
    assert scanner_run.parameters["scanner_gappers"] == 0
    assert set(liquidity) == {1, 2}


async def test_gap_go_v2_adds_the_day_universes_qualified_gappers(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db
    from trading.orchestration.day_universe import qualified_day_universe
    from trading.orchestration.watchlist_source import gap_union_candidates

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "u2.db"))
    await init_db()
    await _seed_gap_snapshot(
        day_universe=[
            (4, "DDD", ["ibkr_scan:HIGH_OPEN_GAP"], 1),  # swept: its 9% gap counts
            (5, "EEE", ["HIGH_OPEN_GAP"], 1),  # not swept yet: a gap scan found it
            (6, "FFF", ["TOP_PERC_GAIN"], 1),  # not swept, not from a gap scan
            (8, "HHH", ["HIGH_OPEN_GAP"], 0),  # not qualified
        ]
    )

    names = await qualified_day_universe(date(2026, 10, 12))
    assert [item.symbol for item in names] == ["DDD", "EEE", "FFF"]
    scanner_run, candidates, liquidity = await gap_union_candidates(_gap_run(), now=GAP_NOW)

    # GGG gaps 8% but is a sweep row outside the day universe; CCC gaps 2%.
    assert [c.symbol for c in candidates] == ["DDD", "BBB", "AAA", "EEE"]
    assert [c.rank for c in candidates] == [1, 2, 3, 4]
    assert scanner_run.parameters["source"] == "screener_gappers_with_day_universe"
    assert (scanner_run.parameters["watchlist_gappers"], scanner_run.parameters["scanner_gappers"]) == (2, 2)
    assert set(liquidity) == {1, 2, 4, 5}


async def test_an_unreadable_day_universe_reads_as_empty(tmp_path, monkeypatch):
    import sqlite3

    from config import cfg
    from trading.orchestration import day_universe

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "broken.db"))
    with sqlite3.connect(cfg.DB_PATH) as connection:
        connection.execute("CREATE TABLE autotrader_manager_day_universe (session_date TEXT)")

    assert await day_universe.qualified_day_universe(date(2026, 10, 12)) == ()
