"""The plan writer: schedule, the 90 s wrapper, the spend ledger, and an
adversarial fake Claude (no test reaches the network).

Every attempt appends one plan row with the manager version, the prompt id,
its cost and a named outcome; over budget no call is made; the context pack
comes from the database alone and offers at most 80 candidate rows.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
import json
import sqlite3
from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from tests.manager_support import (
    SESSION,
    FakeClock,
    FakePlanTransport,
    answered,
    context_builder,
    et,
    migrated,
    plan_answer,
    seed_sweep,
    sweep_row,
)
from trading.intelligence.manager_prompts import DAY_PLAN_PROMPT_ID, DAY_PLAN_V1
from trading.manager.constants import (
    AI_INVALID_ENTRY,
    AI_INVALID_STEP,
    AI_MALFORMED,
    AI_ORDER_FIELD_ECHO,
    AI_REFUSAL,
    AI_TIMEOUT,
    AI_TRUNCATED,
    BUDGET_EXHAUSTED,
    CON_ID_NOT_OFFERED,
    CON_ID_UNKNOWN,
    MANAGER_VERSION,
    PLAN_SPEND_PURPOSE,
    PLAN_TTL,
    PLAN_VALID,
)
from trading.manager.context import BRIDGE_MAX_OPEN, bridge_summary, rank_candidates, replay_stats
from trading.manager.plan import DayPlanOutput
from trading.manager.planner import (
    AnthropicPlanTransport,
    PlanParseError,
    estimate_cost,
    run_manager_day,
    write_plan,
)
from trading.manager.schedule import plan_times, slot_label
from trading.manager.store import list_plans, read_spend
from trading.scheduler.sessions import market_session

RUNNING = ("momo", "orb", "vwap", "gap")
HALF_DAY = date(2026, 11, 27)
HOT = [(4931, "BF B"), (76792991, "TSLA"), (265598, "AAPL")]


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = migrated(tmp_path, monkeypatch)
    rows = [sweep_row(con_id, symbol, hot_rank=rank) for rank, (con_id, symbol) in enumerate(HOT, 1)]
    rows.append(sweep_row(272093, "MSFT"))  # in the sweep, not hot
    seed_sweep(path, rows, observed_at=et(9, 19))
    with sqlite3.connect(path) as connection:  # known to the universe, never offered
        connection.execute(
            "INSERT INTO autotrader_universe VALUES ('watchlist', 'IBM', 'NYSE', 'IBM', 8314, 'NYSE', 'resolved', NULL, 't')"
        )
    return path


async def _write(clock: FakeClock, transport: FakePlanTransport, *, attempt: int = 1, slot=None, **kwargs):
    return await write_plan(
        session=market_session(SESSION),
        slot_at=slot or et(9, 20),
        attempt=attempt,
        clock=clock,
        transport=transport,
        running=RUNNING,
        context_builder=context_builder(),
        **kwargs,
    )


# --- one plan ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_valid_plan_is_stored_with_its_version_prompt_cost_and_ttl(db):
    clock = FakeClock(et(9, 20))
    transport = FakePlanTransport(plan_answer([(4931, ["momo"], "highs on volume")], vwap=0))

    record = await _write(clock, transport)

    assert record.outcome == PLAN_VALID
    [stored] = await list_plans(SESSION)
    assert stored.plan_id == "20261013-0920-1" and stored.slot == "09:20"
    assert (stored.manager_version, stored.prompt_id, stored.model) == (MANAGER_VERSION, DAY_PLAN_PROMPT_ID, "claude-opus-5-5")
    assert stored.valid_until == stored.created_at + PLAN_TTL
    assert stored.plan is not None and stored.plan.focus_for("momo") == {4931}
    assert stored.plan.step_for("vwap") == 0
    assert stored.cost_usd == Decimal("0.1234")
    assert (stored.input_tokens, stored.output_tokens) == (6500, 4200)
    # C is stored: the four sweep rows, hot ones first; never the universe-only IBM.
    assert stored.candidates == (4931, 76792991, 265598, 272093)
    assert stored.gates == {"kill_switch": False, "day_stop": False, "risk_off": False, "risk_off_families": ["momo"]}
    [(system, user)] = transport.requests
    assert system == DAY_PLAN_V1
    payload = json.loads(user)
    assert [row["con_id"] for row in payload["candidates"]] == [4931, 76792991, 265598, 272093]
    assert payload["constraints"]["running_strategies"] == list(RUNNING)


@pytest.mark.parametrize(
    ("answer", "outcome", "dropped"),
    [
        # Outside C: a contract the system knows (IBM) but did not offer.
        (plan_answer([(4931, ["momo"], "ok"), (8314, ["momo"], "not offered")]), PLAN_VALID,
         [(AI_INVALID_ENTRY, CON_ID_NOT_OFFERED)]),
        # An unknown con_id: in no table at all.
        (plan_answer([(4931, ["momo"], "ok"), (123456789, ["momo"], "made up")]), PLAN_VALID,
         [(AI_INVALID_ENTRY, CON_ID_UNKNOWN)]),
        # A day step of 2.0: dropped, momo stays at 1.
        (plan_answer([(4931, ["momo"], "ok")], momo=2.0), PLAN_VALID, [(AI_INVALID_STEP, "momo:2.0")]),
        # An echoed quantity inside a focus entry: only that entry goes.
        ({**plan_answer([(4931, ["momo"], "ok")]),
          "focus": [{"con_id": 4931, "strategies": ["momo"], "note": "ok"},
                    {"con_id": 76792991, "strategies": ["momo"], "note": "x", "quantity": 5000}]},
         PLAN_VALID, [(AI_INVALID_ENTRY, "ai_order_field_echo:quantity")]),
        # An echoed quantity at the top: no plan at all.
        ({**plan_answer([(4931, ["momo"], "ok")]), "quantity": 5000}, AI_ORDER_FIELD_ECHO,
         [(AI_ORDER_FIELD_ECHO, "quantity")]),
        # Malformed shapes.
        ({"regime": "trend_up"}, AI_MALFORMED, []),
        ({**plan_answer([]), "regime": "to the moon"}, AI_MALFORMED, []),
    ],
    ids=["outside-C", "unknown-con-id", "step-2.0", "echoed-quantity-entry", "echoed-quantity-plan",
         "malformed-shape", "malformed-regime"],
)
@pytest.mark.anyio
async def test_adversarial_fake_claude_gets_its_named_tokens(db, answer, outcome, dropped):
    record = await _write(FakeClock(et(9, 20)), FakePlanTransport(answer))

    assert record.outcome == outcome
    [stored] = await list_plans(SESSION)
    assert stored.outcome == outcome
    assert [(item["token"], item["detail"]) for item in stored.dropped] == dropped
    if outcome == PLAN_VALID:
        assert stored.plan is not None
        assert {entry.con_id for entry in stored.plan.focus} <= set(stored.candidates)
        assert stored.plan.step_for("momo") == 1
    else:
        assert stored.plan is None and stored.valid_until is None


@pytest.mark.parametrize(
    ("response", "outcome", "charged"),
    [
        (PlanParseError("ValidationError"), AI_MALFORMED, "estimate"),
        (answered(None), AI_MALFORMED, "0.1234"),
        (answered(None, stop_reason="refusal", cost="0.02"), AI_REFUSAL, "0.02"),
        (answered(plan_answer([]), stop_reason="max_tokens", cost="0.35"), AI_TRUNCATED, "0.35"),
        (ConnectionError("network"), "ai_unavailable", "estimate"),
        (5.0, AI_TIMEOUT, "estimate"),  # sleeps past the wrapper
    ],
    ids=["parse-error", "no-parsed-output", "refusal", "truncated", "unavailable", "timeout"],
)
@pytest.mark.anyio
async def test_a_failed_call_records_its_outcome_and_never_undercounts_spend(db, response, outcome, charged):
    clock = FakeClock(et(9, 20))
    record = await _write(clock, FakePlanTransport(response), timeout_seconds=0.05)

    assert record.outcome == outcome and record.plan is None
    spend = await read_spend(spend_date=SESSION, purpose=PLAN_SPEND_PURPOSE)
    assert spend.calls == 1 and spend.reserved_usd == 0
    expected = record.cost_usd if charged == "estimate" else Decimal(charged)
    assert spend.spent_usd == expected
    if charged == "estimate":
        # The reservation itself is charged: a timed-out call may still be billed.
        assert record.cost_usd > Decimal("0.3")


@pytest.mark.anyio
async def test_the_wrapper_bounds_a_hanging_call_at_its_timeout(db):
    clock = FakeClock(et(9, 20))
    started = asyncio.get_running_loop().time()
    record = await _write(clock, FakePlanTransport(30.0), timeout_seconds=0.05)

    assert record.outcome == AI_TIMEOUT
    assert asyncio.get_running_loop().time() - started < 5


# --- the spend ledger -------------------------------------------------------------------


@pytest.mark.anyio
async def test_over_budget_no_call_is_made_and_no_new_plan_is_written(db):
    clock = FakeClock(et(9, 20))
    transport = FakePlanTransport(plan_answer([(4931, ["momo"], "ok")]))

    # Each reservation is about $0.33 (the whole output budget); $0.12 is spent.
    first = await _write(clock, transport, budget_usd=Decimal("0.4"))
    second = await _write(clock, transport, attempt=1, slot=et(9, 46), budget_usd=Decimal("0.4"))

    assert (first.outcome, second.outcome) == (PLAN_VALID, BUDGET_EXHAUSTED)
    assert len(transport.requests) == 1
    assert second.plan is None and second.cost_usd == 0
    spend = await read_spend(spend_date=SESSION, purpose=PLAN_SPEND_PURPOSE)
    assert spend.calls == 1 and spend.spent_usd == Decimal("0.1234")


@pytest.mark.anyio
async def test_spend_is_durable_across_a_restart(db):
    """A new manager process reads the same ledger: the budget is per date, not per run."""
    clock = FakeClock(et(9, 20))
    for slot in plan_times(market_session(SESSION))[:3]:
        clock.now = slot
        await _write(clock, FakePlanTransport(answered(plan_answer([]), cost="1.60")), slot=slot)
    # "Restart": nothing in memory survives; the ledger says $4.80 is spent.
    spend = await read_spend(spend_date=SESSION, purpose=PLAN_SPEND_PURPOSE)
    assert spend.spent_usd == Decimal("4.8") and spend.calls == 3
    transport = FakePlanTransport(plan_answer([]))
    record = await _write(clock, transport, slot=et(13, 30))
    assert record.outcome == BUDGET_EXHAUSTED and transport.requests == []
    # Another date starts fresh.
    assert (await read_spend(spend_date=date(2026, 10, 14), purpose=PLAN_SPEND_PURPOSE)).calls == 0


def test_the_reservation_covers_the_whole_output_budget_at_opus_prices():
    estimate = estimate_cost(DAY_PLAN_V1, "x" * 30000)
    # 16000 output tokens at $20/M plus ~12k input tokens at $4/M.
    assert Decimal("0.35") < estimate < Decimal("0.40")
    # $5 a day leaves room for every slot and one retry each on a normal day.
    assert estimate * 13 <= Decimal("5")


# --- the schedule -----------------------------------------------------------------------


def test_plan_times_are_half_day_aware():
    full = [slot_label(slot) for slot in plan_times(market_session(SESSION))]
    half = [slot_label(slot) for slot in plan_times(market_session(HALF_DAY))]

    assert full == ["09:20", "09:46", "10:30", "11:30", "12:30", "13:30", "14:30"]
    assert half == ["09:20", "09:46", "10:30", "11:30"]


@pytest.mark.anyio
async def test_the_manager_plans_every_slot_once_and_then_exits(db):
    clock = FakeClock(et(9, 0))
    transport = FakePlanTransport(*[plan_answer([(4931, ["momo"], "ok")])] * 7)

    report = await run_manager_day(
        session=market_session(SESSION), clock=clock, sleep=clock.sleep, transport=transport,
        running=RUNNING, context_builder=context_builder(),
    )

    assert report.summary()["valid"] == 7 and report.attempts == 7
    stored = await list_plans(SESSION)
    assert [plan.slot for plan in stored] == ["09:20", "09:46", "10:30", "11:30", "12:30", "13:30", "14:30"]
    # Each plan is in force until the next arrives (75 minutes cover each hour).
    for earlier, later in zip(stored, stored[1:]):
        assert earlier.valid_until is not None and earlier.valid_until > later.created_at


@pytest.mark.anyio
async def test_a_failed_slot_is_retried_once_and_a_restart_catches_up(db):
    clock = FakeClock(et(10, 0))  # a late start: 09:20's time is over, 09:46's is not
    transport = FakePlanTransport({"regime": "x"}, plan_answer([]))
    session = market_session(SESSION)

    async def stop_after_first(seconds: float) -> None:
        await clock.sleep(seconds)
        if clock.now >= et(10, 29):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_manager_day(session=session, clock=clock, sleep=stop_after_first, transport=transport,
                              running=RUNNING, context_builder=context_builder())

    stored = await list_plans(SESSION)
    assert [(plan.plan_id, plan.outcome) for plan in stored] == [
        ("20261013-0946-1", AI_MALFORMED),
        ("20261013-0946-2", PLAN_VALID),  # the retry, three minutes later
    ]
    assert stored[1].created_at == et(10, 3)

    # A restart within the slot finds its valid plan and makes no call for it.
    clock.now = et(10, 10)
    again = FakePlanTransport()

    async def stop(seconds: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_manager_day(session=session, clock=clock, sleep=stop, transport=again,
                              running=RUNNING, context_builder=context_builder())
    assert again.requests == []


# --- the context pack -------------------------------------------------------------------


def test_the_candidates_are_ordered_capped_at_80_and_merged_by_contract():
    sweep = [sweep_row(1000 + index, f"S{index}", score=str(index)) for index in range(90)]
    sweep[5] = sweep_row(1005, "S5", hot_rank=2)
    sweep[7] = sweep_row(1007, "S7", hot_rank=1)
    rows = rank_candidates(
        sweep=sweep,
        orb_scan=[(1007, "S7", 1), (5000, "ORBONLY", 2)],
        gappers=[(6000, "GAP", 1)],
        day_universe=[(7000, "DAYU", 1), (1005, "S5", 2)],
    )

    assert len(rows) == 80
    assert [row.con_id for row in rows[:5]] == [1007, 1005, 5000, 6000, 7000]
    assert rows[0].sources == ["hot", "watchlist", "orb_scan"] and rows[0].hot_rank == 1
    assert rows[1].sources == ["hot", "watchlist", "day_universe"]
    assert rows[2].metrics == {} and rows[2].payload()["last"] is None
    # The rest of the sweep follows by score, best first.
    assert [row.con_id for row in rows[5:8]] == [1089, 1088, 1087]
    payload = rows[0].payload()
    assert set(payload) == {
        "con_id", "symbol", "sources", "hot_rank", "last", "change_pct", "gap_pct", "rvol",
        "pct_from_high", "dollar_volume", "daily_atr", "industry", "category",
    }


@pytest.mark.anyio
async def test_the_context_pack_reads_only_the_database_and_carries_no_headlines(db, tmp_path):
    from trading.manager.context import build_context

    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at) VALUES ('paper', 1, 'owner', 't')"
        )

    async def market(_day, *, now):
        return {"regime": "risk_off", "summary": "SPY -1.2%", "headline": "ignored? no: it is a number feed"}

    trades = [{"status": "closed", "realized_pnl": "-325.09", "symbol": "BF B"},
              {"status": "open", "realized_pnl": "0", "symbol": "TSLA"},
              {"status": "skipped", "skip_reason": "daily loss limit reached", "symbol": "X"}]
    pack = await build_context(
        market_session(SESSION), now=et(9, 46), slot="09:46", running=RUNNING,
        bridge_reader=lambda _day: (trades, None), replay_reader=lambda: replay_stats(tmp_path / "none"),
        market_reader=market,
    )

    assert pack.offered == {4931, 76792991, 265598, 272093}
    assert 8314 in pack.known and 8314 not in pack.offered
    assert pack.gates.kill_switch and pack.gates.day_stop and pack.gates.risk_off
    assert pack.bridge == {
        "available": True, "realized_pnl_usd": "-325.09", "active": ["TSLA"],
        "slots_used": 1, "slots_free": BRIDGE_MAX_OPEN - 1, "day_stop": True,
    }
    payload = pack.payload()
    assert set(payload) == {
        "manager_version", "session", "constraints", "deterministic_gates", "market_context",
        "replay_stats", "todays_decisions", "bridge", "candidates",
    }
    assert "news" not in pack.encoded() and "headlines" not in pack.encoded()
    assert pack.digest() == pack.digest() and len(pack.digest()) == 64


def _day_universe_quote(con_id: int, symbol: str, rank: int, score: str) -> dict:
    return {
        "rank": rank, "symbol": symbol, "con_id": con_id, "primary_exchange": "NASDAQ",
        "last": "41.20", "bid": None, "ask": None, "prior_close": "38.00", "open": "39.10",
        "day_high": "41.50", "day_low": "38.90", "change_pct": "8.42", "gap_pct": "2.89",
        "volume_shares": "3000000", "rvol": "4.1", "dollar_volume": "123600000",
        "pct_from_high": "0.72", "score": score,
    }


@pytest.mark.anyio
async def test_the_day_universe_joins_c_with_its_quotes_atr_and_industry(db):
    """Phase 1's members and the newest sweep's day_universe section (never its rows)."""
    from trading.manager.context import build_context

    observed = et(9, 45)
    with sqlite3.connect(db) as connection:
        for con_id, symbol, rank in ((555001, "SCANA", 1), (555002, "SCANB", 2)):
            connection.execute(
                """INSERT INTO autotrader_manager_day_universe
                   (session_date, con_id, symbol, primary_exchange, first_seen, last_seen,
                    sources_json, best_rank, qualified, reason)
                   VALUES (?, ?, ?, 'NASDAQ', ?, ?, '["TOP_PERC_GAIN"]', ?, 1, NULL)""",
                (SESSION.isoformat(), con_id, symbol, observed.isoformat(), observed.isoformat(), rank),
            )
            connection.execute(
                """INSERT INTO autotrader_universe_daily
                   (session_date, con_id, symbol, prior_close, avg_daily_volume_shares,
                    avg_dollar_volume, daily_atr, qualified, reason, observed_at)
                   VALUES (?, ?, ?, '38.00', '900000', '34200000', '1.87', 1, NULL, ?)""",
                (SESSION.isoformat(), con_id, symbol, observed.isoformat()),
            )
        connection.execute(
            """INSERT INTO autotrader_manager_contract_info
               (con_id, symbol, primary_exchange, stock_type, industry, category, source, updated_at)
               VALUES (555001, 'SCANA', 'NASDAQ', 'COMMON', 'Technology', 'Semiconductors',
                       'day_universe', ?)""",
            (observed.isoformat(),),
        )
        section = {"size": 2, "quoted": 1, "errors": 1, "complete": True, "seconds": 3.5,
                   "rows": [_day_universe_quote(555001, "SCANA", 1, "88.5")]}
        hot = [sweep_row(con_id, symbol, hot_rank=rank) for rank, (con_id, symbol) in enumerate(HOT, 1)]
        payload = {"schema": "screener.v1", "session_date": SESSION.isoformat(),
                   "observed_at": observed.isoformat(), "rows": hot, "day_universe": section}
        connection.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES ('sweep-du', ?, ?, ?)",
            (SESSION.isoformat(), observed.isoformat(), json.dumps(payload)),
        )

    pack = await build_context(
        market_session(SESSION), now=et(9, 46), slot="09:46", running=RUNNING,
        bridge_reader=lambda _day: ((), None), replay_reader=lambda: {"available": False},
        market_reader=lambda _day, *, now: _none(),
    )

    rows = {row.con_id: row.payload() for row in pack.candidates}
    assert {555001, 555002} <= pack.offered
    assert rows[555001]["sources"] == ["day_universe"]
    assert (rows[555001]["change_pct"], rows[555001]["rvol"]) == ("8.42", "4.1")
    assert (rows[555001]["daily_atr"], rows[555001]["industry"], rows[555001]["category"]) == (
        "1.87", "Technology", "Semiconductors",
    )
    # Unquoted this sweep: offered, without numbers.
    assert rows[555002]["last"] is None and rows[555002]["daily_atr"] == "1.87"
    # The hot list ranks first; the day universe never joins it.
    assert [row.con_id for row in pack.candidates] == [4931, 76792991, 265598, 555001, 555002]
    assert all(row.hot_rank is None for row in pack.candidates if row.con_id in {555001, 555002})


async def _none():
    return None


def test_the_bridge_slot_count_is_the_pinned_bridges():
    from pathlib import Path
    import re

    source = (Path(__file__).parents[1] / "scripts_local" / "paper_bridge.py").read_text(encoding="utf-8")
    assert int(re.search(r"^MAX_OPEN\s*=\s*(\d+)", source, re.MULTILINE).group(1)) == BRIDGE_MAX_OPEN
    assert bridge_summary((), "bridge state unreadable (OSError)") == {
        "available": False, "problem": "bridge state unreadable (OSError)",
    }


def test_replay_statistics_come_from_the_newest_report(tmp_path):
    old = tmp_path / "2026-10-09"
    new = tmp_path / "2026-10-12"
    old.mkdir()
    new.mkdir()
    (old / "replay-momo.json").write_text(json.dumps({"stats": {"trades": 1}}))
    (new / "replay-momo.json").write_text(json.dumps({"stats": {"trades": 42, "expectancy_r": "0.12", "secret": 1}}))

    stats = replay_stats(tmp_path)

    assert stats["as_of"] == "2026-10-12"
    assert stats["strategies"]["momo"]["trades"] == 42
    assert "secret" not in stats["strategies"]["momo"]
    assert replay_stats(tmp_path / "missing") == {"available": False}


# --- the Anthropic transport (a fake client; no network) ---------------------------------


class _FakeMessages:
    def __init__(self, response=None, error: BaseException | None = None) -> None:
        self.calls: list[dict] = []
        self.response = response
        self.error = error

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _parsed_response(answer: dict, *, model: str = "claude-opus-5-5", stop_reason: str = "end_turn"):
    return SimpleNamespace(
        parsed_output=DayPlanOutput.model_validate(answer),
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=6000, output_tokens=4000, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0),
    )


@pytest.mark.anyio
async def test_the_transport_uses_messages_parse_with_the_dayplan_schema_and_medium_effort():
    answer = {**plan_answer([(4931, ["momo"], "ok")]), "quantity": 3}
    messages = _FakeMessages(_parsed_response(answer))
    transport = AnthropicPlanTransport(client=SimpleNamespace(messages=messages))

    response = await transport.plan(system="sys", user="{}")

    [call] = messages.calls
    assert call["model"] == "claude-opus-5-5" and call["output_format"] is DayPlanOutput
    assert call["output_config"] == {"effort": "medium"} and call["max_tokens"] == 16000
    assert call["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}
    assert call["extra_body"] == {"fallbacks": "default"}
    assert "thinking" not in call and "tool_choice" not in call
    # $4/M in, $20/M out; the echoed field survives for the gate to name.
    assert response.cost_usd == Decimal("0.104") and response.answer["quantity"] == 3
    # A fallback model is charged at the ceiling price.
    fallback = AnthropicPlanTransport(
        client=SimpleNamespace(messages=_FakeMessages(_parsed_response(answer, model="other")))
    )
    assert (await fallback.plan(system="s", user="u")).cost_usd == Decimal("0.26")


@pytest.mark.anyio
async def test_a_parse_failure_becomes_a_plan_parse_error():
    try:
        DayPlanOutput.model_validate({"regime": "x"})
    except ValidationError as exc:
        error = exc
    transport = AnthropicPlanTransport(client=SimpleNamespace(messages=_FakeMessages(error=error)))

    with pytest.raises(PlanParseError):
        await transport.plan(system="s", user="u")
    refused = AnthropicPlanTransport(client=SimpleNamespace(messages=_FakeMessages(
        _parsed_response(plan_answer([]), stop_reason="refusal")
    )))
    response = await refused.plan(system="s", user="u")
    assert response.answer is None and response.stop_reason == "refusal"


def test_estimated_cost_per_plan_from_a_full_context_pack():
    """80 rows of realistic size: the input stays near 10k tokens."""
    rows = rank_candidates(
        sweep=[sweep_row(1000 + index, f"SYM{index}", hot_rank=index + 1 if index < 35 else None,
                         score=str(100 - index)) for index in range(80)],
        orb_scan=[], gappers=[], day_universe=[],
    )
    text = json.dumps([row.payload() for row in rows], separators=(",", ":"))
    assert 15000 < len(text) < 30000
    assert estimate_cost(DAY_PLAN_V1, text) < Decimal("0.40")
    assert timedelta(minutes=75) == PLAN_TTL
