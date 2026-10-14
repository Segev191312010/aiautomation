"""The real Anthropic SDK (anthropic 1.x) behind an httpx MockTransport: no network.

The day plan and the managed review go through the SDK's own request
building, so a schema it refuses, or a request it never sends, shows up here
exactly as it would live. A request that was never sent, or that the API
refused with an error status, is never charged.
"""
from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

from anthropic import AsyncAnthropic
import httpx2
import pytest

from tests.manager_support import SESSION, context_builder, et, migrated, seed_sweep, sweep_row
from trading.intelligence.managed_review import (
    MANAGED_OUTPUT_SCHEMA,
    ClaudeManagedReviewer,
    ManagedReviewContext,
)
from trading.intelligence.prompts import MOMO_MANAGED_REVIEW_PROMPT_ID, MOMO_MANAGED_REVIEW_V1
from trading.intelligence.review_provider import ReviewSessionLimits
from trading.manager.constants import (
    AI_API_ERROR,
    AI_REQUEST_NOT_SENT,
    PLAN_SPEND_PURPOSE,
    PLAN_VALID,
)
from trading.manager.plan import DayPlanOutput
from trading.manager.planner import AnthropicPlanTransport, plan_output_schema, write_plan
from trading.manager.store import list_plans, read_spend
from trading.scheduler.sessions import market_session

RUNNING = ("orb-v2", "momo", "gap-v2", "vwap-v2")


def _client(handler, *, retries: int = 1) -> AsyncAnthropic:
    return AsyncAnthropic(
        api_key="sk-ant-test-not-a-real-key",
        base_url="http://anthropic.invalid",
        max_retries=retries,
        timeout=90.0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def _message(text: str, *, model: str = "claude-opus-5-5", stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 7600,
            "output_tokens": 4100,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }


def _typed_everywhere(node: Any, path: str = "$") -> list[str]:
    """Every schema node the API checks: typed, and closed when it is an object."""
    problems: list[str] = []
    if isinstance(node, dict):
        if "properties" in node:
            if node.get("additionalProperties") is not False:
                problems.append(f"{path}: object not closed")
            for name, child in node["properties"].items():
                if not any(key in child for key in ("type", "$ref", "anyOf", "oneOf", "allOf", "enum")):
                    problems.append(f"{path}.{name}: no type")
                problems += _typed_everywhere(child, f"{path}.{name}")
        for key in ("items", "$defs", "definitions"):
            value = node.get(key)
            if isinstance(value, dict) and key != "items":
                for name, child in value.items():
                    problems += _typed_everywhere(child, f"{path}.{key}.{name}")
            elif isinstance(value, dict):
                problems += _typed_everywhere(value, f"{path}.items")
    return problems


def test_every_field_of_the_plan_schema_is_typed_as_the_sdk_sends_it():
    schema = plan_output_schema()

    assert _typed_everywhere(schema) == []
    step = schema["$defs"]["StrategyStepOutput"]["properties"]
    assert step["strategy"]["type"] == "string"
    assert step["step"] == {**step["step"], "type": "number", "enum": [0.0, 0.25, 0.5, 0.75, 1.0]}
    assert _typed_everywhere(MANAGED_OUTPUT_SCHEMA) == []


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = migrated(tmp_path, monkeypatch)
    seed_sweep(path, [sweep_row(4931, "BF B", hot_rank=1), sweep_row(76792991, "TSLA", hot_rank=2)],
               observed_at=et(9, 19))
    return path


async def _plan_once(handler, **client_kwargs):
    from tests.manager_support import FakeClock

    clock = FakeClock(et(9, 20))
    record = await write_plan(
        session=market_session(SESSION), slot_at=et(9, 20), attempt=1, clock=clock,
        transport=AnthropicPlanTransport(client=_client(handler, **client_kwargs)),
        running=RUNNING, context_builder=context_builder(),
    )
    spend = await read_spend(spend_date=SESSION, purpose=PLAN_SPEND_PURPOSE)
    return record, spend


@pytest.mark.anyio
async def test_a_plan_is_exactly_one_request_with_a_valid_schema_through_the_real_sdk(db):
    sent: list[httpx2.Request] = []
    answer = json.dumps({
        "regime": "trend_up",
        "strategies": [
            {"strategy": "orb-v2", "step": 1, "reason": "trend"},
            {"strategy": "momo", "step": 1, "reason": "trend"},
            {"strategy": "gap-v2", "step": 0.5, "reason": "few gaps"},
            {"strategy": "vwap-v2", "step": 1, "reason": "ok"},
        ],
        "focus": [{"con_id": 4931, "strategies": ["momo"], "note": "new highs on volume"}],
    })

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json=_message(answer))

    record, spend = await _plan_once(handler)

    assert record.outcome == PLAN_VALID
    [request] = sent
    body = json.loads(request.content)
    assert request.url.path == "/v1/messages"
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert (body["model"], body["max_tokens"], body["fallbacks"]) == ("claude-opus-5-5", 16000, "default")
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    schema = body["output_config"]["format"]["schema"]
    assert schema == plan_output_schema() and _typed_everywhere(schema) == []
    assert "thinking" not in body and "tool_choice" not in body
    [stored] = await list_plans(SESSION)
    assert stored.plan is not None and stored.plan.focus_for("momo") == {4931}
    # $4/M in, $20/M out: 7600 and 4100 tokens.
    assert stored.cost_usd == Decimal("0.1124") == spend.spent_usd and spend.reserved_usd == 0


@pytest.mark.anyio
async def test_an_error_status_is_named_and_never_charged(db):
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "x"}})

    record, spend = await _plan_once(handler)

    assert (record.outcome, record.cost_usd) == (AI_API_ERROR, 0)
    assert len(sent) == 1
    assert (spend.calls, spend.spent_usd, spend.reserved_usd) == (1, 0, 0)


@pytest.mark.anyio
async def test_a_request_that_never_connects_is_named_and_never_charged(db):
    attempts: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request)
        raise httpx2.ConnectError("connection refused", request=request)

    record, spend = await _plan_once(handler, retries=0)

    assert (record.outcome, record.cost_usd) == (AI_REQUEST_NOT_SENT, 0)
    assert (spend.spent_usd, spend.reserved_usd) == (0, 0)


@pytest.mark.anyio
async def test_a_schema_the_sdk_would_refuse_is_caught_before_any_request(db, monkeypatch, caplog):
    """The pre-fix schema (an untyped field) made the SDK raise before sending:
    it was recorded ai_malformed and charged a phantom reservation."""
    from pydantic import Field
    from typing import Any as AnyType

    from trading.manager import plan as plan_module

    class Untyped(plan_module.StrategyStepOutput):
        strategy: AnyType = Field(description="no JSON type")

    class Broken(DayPlanOutput):
        strategies: list[Untyped]  # type: ignore[assignment]

    monkeypatch.setattr("trading.manager.planner.DayPlanOutput", Broken)
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json=_message("{}"))

    record, spend = await _plan_once(handler)

    assert sent == []
    assert (record.outcome, record.cost_usd) == (AI_REQUEST_NOT_SENT, 0)
    assert (spend.calls, spend.spent_usd, spend.reserved_usd) == (1, 0, 0)


@pytest.mark.anyio
async def test_the_managed_review_is_one_request_with_its_schema_through_the_real_sdk():
    from datetime import datetime, timezone

    from trading.contracts import RuleSetup

    sent: list[httpx2.Request] = []
    answer = json.dumps({"decision": "PROPOSE", "confidence": 0.7, "rationale": "Clean break.",
                         "step": 0.5, "note": "late in the move"})

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json=_message(answer))

    reviewer = ClaudeManagedReviewer(
        client=_client(handler), model_id="claude-opus-5-5", prompt_id=MOMO_MANAGED_REVIEW_PROMPT_ID,
        system_prompt=MOMO_MANAGED_REVIEW_V1, limits=ReviewSessionLimits(timeout_seconds=20),
        session_id="run-1", context_provider=None,
    )
    setup = RuleSetup(
        run_id="run-1", candidate_id="cand-1", strategy_id="s", strategy_version=1,
        strategy_kind="momo_hod", symbol="BF B", atr="0.0792", entry_limit="26.77",
        stop_price="26.51", target_price="27.29", bar_fingerprints=("a" * 64,),
        quote_fingerprint="b" * 64, detected_at=datetime(2026, 10, 12, 15, 50, 5, tzinfo=timezone.utc),
    )
    context = ManagedReviewContext(plan_id="20261013-0946-1", regime="trend_up", strategy="momo",
                                   day_step=Decimal("1"), focus_note="leader")

    review, verdict = await reviewer.review(setup=setup, context=context)

    [request] = sent
    body = json.loads(request.content)
    assert body["output_config"] == {
        "effort": "low", "format": {"type": "json_schema", "schema": MANAGED_OUTPUT_SCHEMA},
    }
    assert json.loads(body["messages"][0]["content"])["manager_context"]["plan_id"] == "20261013-0946-1"
    assert (review.decision, review.failure_reason) == ("PROPOSE", None)
    assert (verdict.step, verdict.note, verdict.token) == (Decimal("0.5"), "late in the move", None)
