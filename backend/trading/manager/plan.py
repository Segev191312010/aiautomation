"""The DayPlan schema and the narrow-only gate (Principle 2).

Two models describe one plan:

* ``DayPlanOutput`` is the wire format handed to ``messages.parse`` as
  ``output_format``. Its JSON schema is strict (enums for the regime and the
  steps, ``additionalProperties: false``), so the API constrains generation;
  its Python validation is deliberately lenient (any number, any string,
  extra keys kept), so one bad entry never fails the whole parse.
* ``DayPlan`` is what deterministic code trusts. Only ``narrow_plan`` builds
  it, entry by entry: a focus entry outside the offered candidates C, naming
  a strategy that is not running, carrying an order field or a malformed note
  is dropped with ``ai_invalid_entry`` (a CRITICAL tripwire when it tried to
  widen); the others are kept. A day step outside the fixed set is dropped
  (``ai_invalid_step``) and that strategy keeps the neutral step 1, which is
  its unmanaged behaviour. Strategies a deterministic gate holds off (the
  kill switch, the day stop, risk_off) stay off whatever the model says, so
  a refresh never re-enables them.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading.manager.constants import (
    AI_INVALID_ENTRY,
    AI_INVALID_NOTE,
    AI_INVALID_STEP,
    AI_MALFORMED,
    AI_ORDER_FIELD_ECHO,
    AI_UNKNOWN_STRATEGY,
    CON_ID_NOT_OFFERED,
    CON_ID_UNKNOWN,
    DAY_STEPS,
    DUPLICATE_CON_ID,
    FOCUS_MAX,
    FOCUS_OVER_LIMIT,
    GATE_HELD_OFF,
    MALFORMED_ENTRY,
    MANAGER_WIDENING_ATTEMPT,
    NEUTRAL_STEP,
    NO_STRATEGIES,
    NOTE_MAX,
    ORDER_FIELD_KEYS,
    PLAN_VALID,
    REASON_MAX,
    REGIMES,
    STRATEGY_NOT_RUNNING,
)

logger = logging.getLogger("autotrader.manager.plan")

_STEP_ENUM = [float(step) for step in DAY_STEPS]
# The wire objects keep unknown keys (so an echoed order field is seen and
# named) while the schema sent to the API still forbids them.
_WIRE_CONFIG = ConfigDict(extra="allow", json_schema_extra={"additionalProperties": False})


# -- the wire format (output_format) --------------------------------------------------


class StrategyStepOutput(BaseModel):
    """One running strategy's day step, as the model writes it."""

    model_config = _WIRE_CONFIG

    strategy: Any = Field(description="A running strategy, exactly as listed in running_strategies.")
    step: Any = Field(
        description="Day size step: 0 switches the strategy off for the day; 1 keeps full size.",
        json_schema_extra={"type": "number", "enum": _STEP_ENUM},
    )
    reason: Any = Field(
        description=f"Why, in one line of at most {REASON_MAX} characters.",
        json_schema_extra={"type": "string"},
    )


class FocusEntryOutput(BaseModel):
    """One focus name, as the model writes it."""

    model_config = _WIRE_CONFIG

    con_id: Any = Field(
        description="The con_id of one row in candidates; never any other.",
        json_schema_extra={"type": "integer"},
    )
    strategies: Any = Field(
        description="The running strategies this name is a focus for.",
        json_schema_extra={"type": "array", "items": {"type": "string"}},
    )
    note: Any = Field(
        description=f"One line of at most {NOTE_MAX} characters: why it is in focus.",
        json_schema_extra={"type": "string"},
    )


class DayPlanOutput(BaseModel):
    """The model's answer: regime, a step per running strategy, at most 30 focus names."""

    model_config = _WIRE_CONFIG

    regime: Any = Field(json_schema_extra={"type": "string", "enum": list(REGIMES)})
    strategies: list[StrategyStepOutput] = Field(
        description="Exactly one entry per running strategy."
    )
    focus: list[FocusEntryOutput] = Field(
        description=f"At most {FOCUS_MAX} names from candidates, best first."
    )


# -- the trusted plan -----------------------------------------------------------------


def _one_line(text: str, limit: int) -> bool:
    return 1 <= len(text) <= limit and "\n" not in text and "\r" not in text


class StrategyStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: Decimal
    reason: str = Field(min_length=1, max_length=REASON_MAX)

    @field_validator("step")
    @classmethod
    def _fixed_step(cls, value: Decimal) -> Decimal:
        if value not in DAY_STEPS:
            raise ValueError("a day step is one of 0, 0.25, 0.5, 0.75, 1")
        return value

    @field_validator("reason")
    @classmethod
    def _reason_line(cls, value: str) -> str:
        if not _one_line(value, REASON_MAX):
            raise ValueError("a reason is one line")
        return value


class FocusEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    con_id: int = Field(gt=0)
    strategies: tuple[str, ...] = Field(min_length=1)
    note: str

    @field_validator("note")
    @classmethod
    def _note_line(cls, value: str) -> str:
        if not _one_line(value, NOTE_MAX):
            raise ValueError(f"a note is one line of at most {NOTE_MAX} characters")
        return value


class DayPlan(BaseModel):
    """A plan deterministic code may act on: built only by ``narrow_plan``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    regime: str
    strategies: dict[str, StrategyStep]
    focus: tuple[FocusEntry, ...] = Field(max_length=FOCUS_MAX)

    @field_validator("regime")
    @classmethod
    def _known_regime(cls, value: str) -> str:
        if value not in REGIMES:
            raise ValueError("unknown regime")
        return value

    @model_validator(mode="after")
    def _focus_is_unique(self) -> DayPlan:
        con_ids = [entry.con_id for entry in self.focus]
        if len(con_ids) != len(set(con_ids)):
            raise ValueError("a focus names each contract once")
        running = set(self.strategies)
        if any(not set(entry.strategies) <= running for entry in self.focus):
            raise ValueError("a focus entry names only running strategies")
        return self

    def step_for(self, strategy: str) -> Decimal:
        """The day step for ``strategy``; one the plan does not cover stays neutral."""
        planned = self.strategies.get(strategy)
        return NEUTRAL_STEP if planned is None else planned.step

    def focus_for(self, strategy: str) -> frozenset[int]:
        return frozenset(entry.con_id for entry in self.focus if strategy in entry.strategies)

    def note_for(self, con_id: int) -> str | None:
        return next((entry.note for entry in self.focus if entry.con_id == con_id), None)

    def strategies_payload(self) -> dict[str, dict[str, object]]:
        return {
            name: {"step": float(item.step), "reason": item.reason}
            for name, item in sorted(self.strategies.items())
        }

    def focus_payload(self) -> list[dict[str, object]]:
        return [
            {"con_id": entry.con_id, "strategies": list(entry.strategies), "note": entry.note}
            for entry in self.focus
        ]


@dataclass(frozen=True, slots=True)
class GateStates:
    """The deterministic gates at plan time; a plan never re-enables what they hold off."""

    kill_switch: bool = False
    day_stop: bool = False
    risk_off: bool = False
    # Families the risk_off regime pauses (MOMO's family today).
    risk_off_families: frozenset[str] = frozenset({"momo"})

    def held_off(self, strategy: str) -> str | None:
        if self.kill_switch:
            return "kill_switch"
        if self.day_stop:
            return "day_stop"
        if self.risk_off and strategy in self.risk_off_families:
            return "risk_off"
        return None

    def payload(self) -> dict[str, object]:
        return {
            "kill_switch": self.kill_switch,
            "day_stop": self.day_stop,
            "risk_off": self.risk_off,
            "risk_off_families": sorted(self.risk_off_families),
        }


@dataclass(frozen=True, slots=True)
class Dropped:
    """One part of the model's answer the gate refused, with its named token."""

    kind: str  # "focus", "strategy" or "plan"
    index: int | None
    token: str
    detail: str
    widening: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "index": self.index,
            "token": self.token,
            "detail": self.detail,
            "widening": self.widening,
        }


@dataclass(frozen=True, slots=True)
class NarrowResult:
    """``plan`` is set only for outcome ``valid``; ``dropped`` lists every refusal."""

    outcome: str
    plan: DayPlan | None
    dropped: tuple[Dropped, ...] = field(default=())

    @property
    def valid(self) -> bool:
        return self.outcome == PLAN_VALID and self.plan is not None

    def dropped_payload(self) -> list[dict[str, object]]:
        return [item.payload() for item in self.dropped]


_TOP_LEVEL_KEYS = frozenset({"regime", "strategies", "focus"})
_STRATEGY_KEYS = frozenset({"strategy", "step", "reason"})
_FOCUS_KEYS = frozenset({"con_id", "strategies", "note"})


def _order_fields(keys: Iterable[object]) -> list[str]:
    return sorted(str(key) for key in keys if str(key).strip().lower() in ORDER_FIELD_KEYS)


def _step(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        step = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not step.is_finite():
        return None
    for allowed in DAY_STEPS:
        if step == allowed:
            return allowed
    return None


def _tripwire(dropped: Dropped) -> Dropped:
    if dropped.widening:
        logger.critical(
            "%s: %s %s[%s] dropped (%s: %s)",
            MANAGER_WIDENING_ATTEMPT,
            dropped.token,
            dropped.kind,
            dropped.index,
            dropped.token,
            dropped.detail,
        )
    else:
        logger.warning(
            "%s %s[%s] dropped: %s", dropped.token, dropped.kind, dropped.index, dropped.detail
        )
    return dropped


def narrow_plan(
    answer: object,
    *,
    offered: frozenset[int],
    known: frozenset[int],
    running: Sequence[str],
    gates: GateStates,
) -> NarrowResult:
    """Validate the model's answer entry by entry into a ``DayPlan``.

    ``offered`` is C, the con_ids of the context pack's candidate rows;
    ``known`` adds every contract the system knows (so a refused con_id is
    named "not offered" or "unknown"); ``running`` is the strategy set the
    plan may manage. Pure: no I/O, no clock.
    """
    if isinstance(answer, BaseModel):
        answer = answer.model_dump()
    if not isinstance(answer, Mapping):
        return NarrowResult(outcome=AI_MALFORMED, plan=None)
    echoed = _order_fields(answer)
    if echoed:
        refused = _tripwire(Dropped("plan", None, AI_ORDER_FIELD_ECHO, ",".join(echoed), True))
        return NarrowResult(outcome=AI_ORDER_FIELD_ECHO, plan=None, dropped=(refused,))
    if set(answer) - _TOP_LEVEL_KEYS or not _TOP_LEVEL_KEYS <= set(answer):
        return NarrowResult(outcome=AI_MALFORMED, plan=None)
    regime = answer.get("regime")
    strategies_raw = answer.get("strategies")
    focus_raw = answer.get("focus")
    if regime not in REGIMES or not isinstance(strategies_raw, list) or not isinstance(focus_raw, list):
        return NarrowResult(outcome=AI_MALFORMED, plan=None)

    running_set = tuple(dict.fromkeys(running))
    dropped: list[Dropped] = []
    steps: dict[str, StrategyStep] = {}
    for index, item in enumerate(strategies_raw):
        refusal = _strategy_refusal(item, running_set, steps)
        if refusal is not None:
            dropped.append(_tripwire(Dropped("strategy", index, *refusal)))
            continue
        assert isinstance(item, Mapping)
        step = _step(item["step"])
        assert step is not None  # _strategy_refusal refused every other step
        steps[str(item["strategy"])] = StrategyStep(step=step, reason=str(item["reason"]))
    for name in running_set:
        planned = steps.get(name)
        if planned is None:
            steps[name] = StrategyStep(step=NEUTRAL_STEP, reason="no valid step from the manager")
        held = gates.held_off(name)
        if held is not None:
            planned = steps[name]
            if planned.step != Decimal("0"):
                dropped.append(
                    _tripwire(Dropped("strategy", None, GATE_HELD_OFF, f"{name}:{held}"))
                )
            steps[name] = StrategyStep(step=Decimal("0"), reason=f"held off by {held}")

    focus: list[FocusEntry] = []
    seen: set[int] = set()
    for index, item in enumerate(focus_raw):
        if len(focus) >= FOCUS_MAX:
            dropped.append(
                _tripwire(Dropped("focus", index, AI_INVALID_ENTRY, FOCUS_OVER_LIMIT))
            )
            continue
        refusal = _focus_refusal(item, offered=offered, known=known, running=running_set, seen=seen)
        if refusal is not None:
            dropped.append(_tripwire(Dropped("focus", index, AI_INVALID_ENTRY, *refusal)))
            continue
        assert isinstance(item, Mapping)
        entry = FocusEntry(
            con_id=int(item["con_id"]),
            strategies=tuple(dict.fromkeys(str(name) for name in item["strategies"])),
            note=str(item["note"]),
        )
        seen.add(entry.con_id)
        focus.append(entry)

    plan = DayPlan(
        regime=str(regime),
        strategies={name: steps[name] for name in running_set},
        focus=tuple(focus),
    )
    return NarrowResult(outcome=PLAN_VALID, plan=plan, dropped=tuple(dropped))


def _strategy_refusal(
    item: object, running: tuple[str, ...], steps: Mapping[str, StrategyStep]
) -> tuple[str, str, bool] | None:
    """``(token, detail, widening)`` for a day-step entry the gate refuses."""
    if not isinstance(item, Mapping):
        return AI_INVALID_STEP, MALFORMED_ENTRY, False
    echoed = _order_fields(item)
    if echoed:
        return AI_ORDER_FIELD_ECHO, ",".join(echoed), True
    if set(item) != _STRATEGY_KEYS:
        return AI_INVALID_STEP, MALFORMED_ENTRY, False
    name = item.get("strategy")
    if not isinstance(name, str) or name not in running:
        # Switching on a strategy that is not running would widen.
        return AI_UNKNOWN_STRATEGY, str(name)[:64], True
    if name in steps:
        return AI_INVALID_STEP, f"{name}:duplicate", False
    if _step(item.get("step")) is None:
        # A step outside the fixed set (e.g. 2.0) could only mean sizing up.
        return AI_INVALID_STEP, f"{name}:{str(item.get('step'))[:32]}", True
    reason = item.get("reason")
    if not isinstance(reason, str) or not _one_line(reason, REASON_MAX):
        return AI_INVALID_NOTE, f"{name}:reason", False
    return None


def _focus_refusal(
    item: object,
    *,
    offered: frozenset[int],
    known: frozenset[int],
    running: tuple[str, ...],
    seen: set[int],
) -> tuple[str, bool] | None:
    """``(detail, widening)`` for a focus entry the gate refuses."""
    if not isinstance(item, Mapping):
        return MALFORMED_ENTRY, False
    echoed = _order_fields(item)
    if echoed:
        return f"{AI_ORDER_FIELD_ECHO}:{','.join(echoed)}", True
    if set(item) != _FOCUS_KEYS:
        return MALFORMED_ENTRY, False
    con_id = item.get("con_id")
    if isinstance(con_id, bool) or not isinstance(con_id, int) or con_id <= 0:
        return CON_ID_UNKNOWN, True
    if con_id not in offered:
        return (CON_ID_NOT_OFFERED if con_id in known else CON_ID_UNKNOWN), True
    if con_id in seen:
        return DUPLICATE_CON_ID, False
    names = item.get("strategies")
    if not isinstance(names, list) or not names:
        return NO_STRATEGIES, False
    if any(not isinstance(name, str) or name not in running for name in names):
        return STRATEGY_NOT_RUNNING, True
    note = item.get("note")
    if not isinstance(note, str) or not _one_line(note, NOTE_MAX):
        return AI_INVALID_NOTE, False
    return None


def plan_from_stored(
    *, regime: str, strategies: Mapping[str, Any], focus: Sequence[Mapping[str, Any]]
) -> DayPlan:
    """Rebuild a stored valid plan; any inconsistency raises (the caller goes neutral)."""
    return DayPlan(
        regime=regime,
        strategies={
            str(name): StrategyStep(step=Decimal(str(item["step"])), reason=str(item["reason"]))
            for name, item in strategies.items()
        },
        focus=tuple(
            FocusEntry(
                con_id=int(item["con_id"]),
                strategies=tuple(str(name) for name in item["strategies"]),
                note=str(item["note"]),
            )
            for item in focus
        ),
    )


__all__ = [
    "DayPlan",
    "DayPlanOutput",
    "Dropped",
    "FocusEntry",
    "FocusEntryOutput",
    "GateStates",
    "NarrowResult",
    "StrategyStep",
    "StrategyStepOutput",
    "narrow_plan",
    "plan_from_stored",
]
