"""The DayPlan schema and the narrow-only gate (Principle 2), entry by entry.

The model's answer is untrusted. Each focus entry is checked on its own:
inside C, naming running strategies only, no order fields, one short line.
A bad entry is dropped with ``ai_invalid_entry`` (a CRITICAL tripwire when it
tried to widen) and the rest are kept; a day step outside the fixed set is
dropped and that strategy stays neutral; what a deterministic gate holds off
stays off.
"""
from __future__ import annotations

from decimal import Decimal
import json
import logging
import random

from pydantic import TypeAdapter
import pytest

from trading.manager import constants
from trading.manager.constants import (
    AI_INVALID_ENTRY,
    AI_INVALID_NOTE,
    AI_INVALID_STEP,
    AI_MALFORMED,
    AI_ORDER_FIELD_ECHO,
    AI_UNKNOWN_STRATEGY,
    CON_ID_NOT_OFFERED,
    CON_ID_UNKNOWN,
    DUPLICATE_CON_ID,
    FOCUS_OVER_LIMIT,
    GATE_HELD_OFF,
    MANAGER_WIDENING_ATTEMPT,
    NO_STRATEGIES,
    PLAN_VALID,
    STRATEGY_NOT_RUNNING,
)
from trading.manager.plan import DayPlan, DayPlanOutput, GateStates, narrow_plan

RUNNING = ("momo", "orb", "vwap", "gap")
OFFERED = frozenset({4931, 265598, 76792991, 272093})
KNOWN = OFFERED | {9999, 8888}
OPEN = GateStates()


def _answer(**overrides: object) -> dict[str, object]:
    answer: dict[str, object] = {
        "regime": "trend_up",
        "strategies": [
            {"strategy": "momo", "step": 1, "reason": "trend day, broad breadth"},
            {"strategy": "orb", "step": 0.5, "reason": "choppy open"},
            {"strategy": "vwap", "step": 0, "reason": "no pullbacks in a trend day"},
            {"strategy": "gap", "step": 0.75, "reason": "few clean gappers"},
        ],
        "focus": [
            {"con_id": 4931, "strategies": ["momo"], "note": "new highs on 3x volume"},
            {"con_id": 265598, "strategies": ["momo", "orb"], "note": "index leader"},
        ],
    }
    answer.update(overrides)
    return answer


def _narrow(answer: object, gates: GateStates = OPEN):
    return narrow_plan(answer, offered=OFFERED, known=KNOWN, running=RUNNING, gates=gates)


def test_the_managers_limits_are_pinned():
    assert constants.FOCUS_MAX == 30
    assert constants.PLAN_TTL.total_seconds() == 75 * 60
    assert constants.MANAGER_DAILY_BUDGET_USD == Decimal("5")
    assert constants.PLAN_TIMEOUT_SECONDS == 90.0
    assert constants.MAX_CONTEXT_ROWS == 80
    assert constants.NOTE_MAX == 140
    assert constants.MANAGER_MODEL == "claude-opus-5-5"
    assert constants.MANAGER_EFFORT == "medium"
    assert constants.DAY_STEPS == tuple(Decimal(text) for text in ("0", "0.25", "0.5", "0.75", "1"))
    assert constants.REGIMES == ("trend_up", "trend_down", "range", "high_vol", "unclear")
    assert [f"{moment:%H:%M}" for moment in constants.PLAN_TIMES] == [
        "09:20", "09:46", "10:30", "11:30", "12:30", "13:30", "14:30",
    ]
    assert constants.MANAGER_VERSION == "manager-v1"


def test_a_clean_answer_becomes_the_plan_it_states():
    result = _narrow(_answer())

    assert result.outcome == PLAN_VALID and result.dropped == ()
    plan = result.plan
    assert plan is not None and plan.regime == "trend_up"
    assert {name: plan.step_for(name) for name in RUNNING} == {
        "momo": Decimal("1"), "orb": Decimal("0.5"), "vwap": Decimal("0"), "gap": Decimal("0.75"),
    }
    assert plan.focus_for("momo") == {4931, 265598}
    assert plan.focus_for("orb") == {265598}
    assert plan.note_for(4931) == "new highs on 3x volume"


def test_the_wire_schema_is_strict_while_parsing_stays_lenient():
    schema = TypeAdapter(DayPlanOutput).json_schema()
    text = json.dumps(schema)
    # The API constrains generation: enums, no extra keys anywhere.
    assert schema["additionalProperties"] is False
    assert all(item["additionalProperties"] is False for item in schema["$defs"].values())
    assert schema["properties"]["regime"]["enum"] == list(constants.REGIMES)
    assert '"enum": [0.0, 0.25, 0.5, 0.75, 1.0]' in text
    # Python-side parsing keeps a bad entry (and its extra keys) for the gate to name.
    parsed = DayPlanOutput.model_validate(
        _answer(focus=[{"con_id": 4931, "strategies": ["momo"], "note": "x", "quantity": 500}],
                strategies=[{"strategy": "momo", "step": 2.0, "reason": "all in"}])
    )
    assert parsed.model_dump()["focus"][0]["quantity"] == 500
    assert parsed.model_dump()["strategies"][0]["step"] == 2.0


@pytest.mark.parametrize(
    ("entry", "detail", "widening"),
    [
        ({"con_id": 9999, "strategies": ["momo"], "note": "known, not offered"}, CON_ID_NOT_OFFERED, True),
        ({"con_id": 123456789, "strategies": ["momo"], "note": "made up"}, CON_ID_UNKNOWN, True),
        ({"con_id": -4, "strategies": ["momo"], "note": "negative"}, CON_ID_UNKNOWN, True),
        ({"con_id": "4931", "strategies": ["momo"], "note": "text id"}, CON_ID_UNKNOWN, True),
        ({"con_id": True, "strategies": ["momo"], "note": "bool id"}, CON_ID_UNKNOWN, True),
        ({"con_id": 76792991, "strategies": ["breakout2"], "note": "x"}, STRATEGY_NOT_RUNNING, True),
        ({"con_id": 76792991, "strategies": [], "note": "x"}, NO_STRATEGIES, False),
        ({"con_id": 76792991, "strategies": ["momo"], "note": "two\nlines"}, AI_INVALID_NOTE, False),
        ({"con_id": 76792991, "strategies": ["momo"], "note": "x" * 141}, AI_INVALID_NOTE, False),
        ({"con_id": 76792991, "strategies": ["momo"], "note": "x", "quantity": 900},
         f"{AI_ORDER_FIELD_ECHO}:quantity", True),
        ({"con_id": 76792991, "strategies": ["momo"], "note": "x", "stop_price": 1},
         f"{AI_ORDER_FIELD_ECHO}:stop_price", True),
        ({"con_id": 76792991, "strategies": ["momo"]}, "malformed_entry", False),
        ("TSLA", "malformed_entry", False),
    ],
)
def test_one_bad_focus_entry_is_dropped_and_named_and_the_others_kept(entry, detail, widening, caplog):
    answer = _answer()
    answer["focus"] = [*answer["focus"], entry]  # type: ignore[misc]
    with caplog.at_level(logging.WARNING, logger="autotrader.manager.plan"):
        result = _narrow(answer)

    assert result.outcome == PLAN_VALID
    assert result.plan is not None and [item.con_id for item in result.plan.focus] == [4931, 265598]
    [dropped] = result.dropped
    assert (dropped.kind, dropped.index, dropped.token, dropped.detail) == ("focus", 2, AI_INVALID_ENTRY, detail)
    assert dropped.widening is widening
    critical = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert bool(critical) is widening
    if widening:
        assert critical[0].getMessage().startswith(MANAGER_WIDENING_ATTEMPT)


@pytest.mark.parametrize(
    ("entry", "token", "detail"),
    [
        ({"strategy": "momo", "step": 2.0, "reason": "size up"}, AI_INVALID_STEP, "momo:2.0"),
        ({"strategy": "momo", "step": 0.3, "reason": "odd"}, AI_INVALID_STEP, "momo:0.3"),
        ({"strategy": "momo", "step": -1, "reason": "neg"}, AI_INVALID_STEP, "momo:-1"),
        ({"strategy": "momo", "step": "NaN", "reason": "nan"}, AI_INVALID_STEP, "momo:NaN"),
        ({"strategy": "momo", "step": True, "reason": "bool"}, AI_INVALID_STEP, "momo:True"),
        ({"strategy": "mystery", "step": 1, "reason": "switch on"}, AI_UNKNOWN_STRATEGY, "mystery"),
        ({"strategy": "momo", "step": 1, "reason": "x", "quantity": 100}, AI_ORDER_FIELD_ECHO, "quantity"),
        ({"strategy": "momo", "step": 1, "reason": "a\nb"}, AI_INVALID_NOTE, "momo:reason"),
    ],
)
def test_a_bad_day_step_is_dropped_and_its_strategy_stays_neutral(entry, token, detail):
    answer = _answer()
    answer["strategies"] = [entry, *answer["strategies"][1:]]  # type: ignore[index]

    result = _narrow(answer)

    assert result.outcome == PLAN_VALID and result.plan is not None
    # Never above 1: a dropped step leaves the strategy at its unmanaged size.
    assert result.plan.step_for("momo") == Decimal("1")
    assert result.plan.step_for("orb") == Decimal("0.5")
    assert [(item.kind, item.token, item.detail) for item in result.dropped] == [("strategy", token, detail)]


def test_a_strategy_the_answer_leaves_out_stays_neutral_and_duplicates_are_dropped():
    answer = _answer(strategies=[
        {"strategy": "orb", "step": 0, "reason": "off"},
        {"strategy": "orb", "step": 1, "reason": "on again"},
    ])
    result = _narrow(answer)

    assert result.plan is not None
    assert result.plan.step_for("orb") == Decimal("0")
    assert result.plan.step_for("momo") == result.plan.step_for("gap") == Decimal("1")
    assert [(item.token, item.detail) for item in result.dropped] == [(AI_INVALID_STEP, "orb:duplicate")]


def test_the_focus_keeps_thirty_names_and_each_once():
    offered = frozenset(range(1, 41))
    focus = [{"con_id": index, "strategies": ["momo"], "note": f"name {index}"} for index in range(1, 36)]
    focus.insert(3, {"con_id": 2, "strategies": ["momo"], "note": "again"})

    result = narrow_plan(_answer(focus=focus), offered=offered, known=offered, running=RUNNING, gates=OPEN)

    assert result.plan is not None and len(result.plan.focus) == 30
    assert [item.con_id for item in result.plan.focus] == list(range(1, 31))
    details = [item.detail for item in result.dropped]
    assert details == [DUPLICATE_CON_ID] + [FOCUS_OVER_LIMIT] * 5


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        ("not a plan", AI_MALFORMED),
        ([], AI_MALFORMED),
        ({"regime": "trend_up", "strategies": []}, AI_MALFORMED),  # focus missing
        (_answer(regime="moonshot"), AI_MALFORMED),
        (_answer(focus="all of them"), AI_MALFORMED),
        (_answer(extra_field=1), AI_MALFORMED),
        (_answer(quantity=1000), AI_ORDER_FIELD_ECHO),
        (_answer(side="BUY"), AI_ORDER_FIELD_ECHO),
    ],
)
def test_an_answer_that_is_not_a_plan_gets_no_plan_and_its_named_outcome(answer, outcome):
    result = _narrow(answer)

    assert (result.outcome, result.plan, result.valid) == (outcome, None, False)


@pytest.mark.parametrize(
    ("gates", "held"),
    [
        (GateStates(kill_switch=True), {"momo", "orb", "vwap", "gap"}),
        (GateStates(day_stop=True), {"momo", "orb", "vwap", "gap"}),
        # risk_off is not clamped into a plan: the runtime gate pauses managed
        # MOMO exactly while V2 pauses, never for a plan's whole 75 minutes.
        (GateStates(risk_off=True), set()),
    ],
)
def test_a_refresh_never_re_enables_what_a_deterministic_gate_switched_off(gates, held):
    """The model asks for every strategy at full size; the gates keep theirs off."""
    answer = _answer(strategies=[{"strategy": name, "step": 1, "reason": "go"} for name in RUNNING])

    result = _narrow(answer, gates)

    assert result.plan is not None
    for name in RUNNING:
        assert result.plan.step_for(name) == (Decimal("0") if name in held else Decimal("1")), name
    assert sorted(item.detail.split(":")[0] for item in result.dropped if item.token == GATE_HELD_OFF) == sorted(held)
    # Asking for "off" where a gate holds off is not a refusal.
    quiet = _narrow(_answer(strategies=[{"strategy": name, "step": 0, "reason": "off"} for name in RUNNING]), gates)
    assert not [item for item in quiet.dropped if item.token == GATE_HELD_OFF]


def _random_answer(rng: random.Random) -> object:
    """Adversarial noise: valid, invalid and widening entries mixed."""
    con_ids = [*OFFERED, 9999, 123, -1, "4931", None]
    names = [*RUNNING, "mystery", 7]
    steps = [0, 0.25, 0.5, 0.75, 1, 1.5, 2.0, -0.25, "1", None]
    focus = []
    for _ in range(rng.randint(0, 40)):
        entry: dict[str, object] = {
            "con_id": rng.choice(con_ids),
            "strategies": rng.sample(names, rng.randint(0, 3)),
            "note": rng.choice(["ok", "x" * 150, "a\nb", "", 5]),
        }
        if rng.random() < 0.1:
            entry[rng.choice(["quantity", "price", "shares"])] = 100
        focus.append(entry if rng.random() > 0.05 else "junk")
    strategies = [
        {"strategy": rng.choice(names), "step": rng.choice(steps), "reason": rng.choice(["r", "a\nb"])}
        for _ in range(rng.randint(0, 6))
    ]
    return {"regime": rng.choice([*constants.REGIMES, "bogus"]), "strategies": strategies, "focus": focus}


def test_property_every_plan_the_gate_builds_only_narrows():
    """Over thousands of adversarial answers: nothing outside C, no unknown strategy,
    no step outside the fixed set, at most 30 names, each once, and gates respected."""
    rng = random.Random(20261013)
    valid = 0
    for _ in range(3000):
        gates = GateStates(
            kill_switch=rng.random() < 0.1, day_stop=rng.random() < 0.1, risk_off=rng.random() < 0.2
        )
        result = _narrow(_random_answer(rng), gates)
        if result.plan is None:
            assert result.outcome != PLAN_VALID
            continue
        valid += 1
        plan = result.plan
        assert isinstance(plan, DayPlan)
        assert set(plan.strategies) == set(RUNNING)
        assert all(item.step in constants.DAY_STEPS for item in plan.strategies.values())
        assert len(plan.focus) <= constants.FOCUS_MAX
        assert {entry.con_id for entry in plan.focus} <= OFFERED
        assert len({entry.con_id for entry in plan.focus}) == len(plan.focus)
        assert all(set(entry.strategies) <= set(RUNNING) and entry.strategies for entry in plan.focus)
        assert all(1 <= len(entry.note) <= 140 and "\n" not in entry.note for entry in plan.focus)
        for name in RUNNING:
            if gates.held_off(name):
                assert plan.step_for(name) == 0
    assert valid > 1000
