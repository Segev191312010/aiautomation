"""Claude review worker tests.

Cover the worker's outcome mapping without touching the real Anthropic API or
the (sibling-owned) mcp_server module:

* disabled flag -> the loop is a no-op (claims nothing)
* approved propose_order -> candidate marked ``applied``
* explicit decline -> ``declined_by_ai``
* API error -> ``failed``
* daily cost cap reached -> the loop idles (claims nothing)

AsyncAnthropic and mcp_server.dispatch_tool_call are patched where the worker
imports them (lazy imports inside the function), so tests do not depend on the
sibling agent's module-load order.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import claude_worker
from db.core import get_db, init_db
from db.direct_candidates import get_candidate_status


# ── Helpers / fakes ────────────────────────────────────────────────────────────

async def _queue_tv_candidate(cand_id: str, symbol: str = "AAPL") -> None:
    """Insert a TradingView candidate already in pending_review."""
    queued_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO direct_candidates "
            "(id, user_id, symbol, payload, queued_at, ttl_seconds, status, source) "
            "VALUES (?, 'demo', ?, ?, ?, 900, 'pending_review', 'tv_webhook')",
            (cand_id, symbol, '{"action": "buy"}', queued_at),
        )
        await db.commit()


def _text_block(text: str = "Declining: signal too weak.") -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text,
                           model_dump=lambda **_: {"type": "text", "text": text})


def _tool_use_block(name: str, tool_input: dict, block_id: str = "tu_1") -> SimpleNamespace:
    dump = {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input,
                           model_dump=lambda **_: dict(dump))


def _message(content: list, stop_reason: str, in_tok: int = 100, out_tok: int = 50) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


class _FakeMessages:
    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        if not self._responses:
            raise AssertionError("Anthropic create called more times than expected")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class _FakeAnthropic:
    """Stand-in for AsyncAnthropic that replays scripted responses."""

    _script: list = []

    def __init__(self, *_args, **_kwargs):
        self.messages = _FakeMessages(list(type(self)._script))


def _install_fake_mcp(dispatch: AsyncMock | None = None) -> None:
    """Inject a fake mcp_server module so the worker's lazy import resolves."""
    mod = types.ModuleType("mcp_server")
    mod.tools_spec = [
        {"name": "propose_order", "description": "propose a paper order",
         "input_schema": {"type": "object", "properties": {}}},
    ]
    mod.dispatch_tool_call = dispatch or AsyncMock(return_value={"ok": True})
    sys.modules["mcp_server"] = mod


@pytest.fixture(autouse=True)
async def _clean_state():
    """Each test starts with the mcp_server slot and candidate queue cleared.

    The lane uses one DB_PATH for the whole file, so leftover pending_review
    rows from a prior test would otherwise be claimed ahead of the row the
    current test just queued (claim_one_candidate picks the oldest).
    """
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM direct_candidates")
        await db.commit()
    saved = sys.modules.get("mcp_server")
    yield
    if saved is not None:
        sys.modules["mcp_server"] = saved
    else:
        sys.modules.pop("mcp_server", None)


# ── Tests ──────────────────────────────────────────────────────────────────────

async def test_disabled_is_noop():
    """When CLAUDE_WORKER_ENABLED is false, one loop iteration claims nothing."""
    await init_db()
    await _queue_tv_candidate("disabled-1")

    with patch.object(claude_worker.cfg, "CLAUDE_WORKER_ENABLED", False), \
         patch.object(claude_worker.cfg, "CLAUDE_WORKER_POLL_SECONDS", 0), \
         patch.object(claude_worker, "claim_one_candidate", AsyncMock()) as claim:
        await _one_iteration()
        claim.assert_not_called()

    # Untouched.
    assert await get_candidate_status("disabled-1") == "pending_review"


async def test_approve_path_marks_applied():
    await init_db()
    await _queue_tv_candidate("approve-1")
    _install_fake_mcp(AsyncMock(return_value={"order_id": "sim-1", "status": "filled"}))

    _FakeAnthropic._script = [
        _message([_tool_use_block("propose_order", {"symbol": "AAPL", "qty": 10})], "tool_use"),
        _message([_text_block("Order proposed.")], "end_turn"),
    ]

    with patch.object(claude_worker.cfg, "CLAUDE_WORKER_ENABLED", True), \
         patch("anthropic.AsyncAnthropic", _FakeAnthropic):
        candidate = await claude_worker.claim_one_candidate()
        status = await claude_worker.process_candidate(candidate)

    assert status == "applied"
    assert await get_candidate_status("approve-1") == "applied"


async def test_decline_path_marks_declined_by_ai():
    """An explicit decline tool call maps to declined_by_ai (no propose_order)."""
    await init_db()
    await _queue_tv_candidate("decline-1")
    _install_fake_mcp(AsyncMock(return_value={"ok": True}))

    _FakeAnthropic._script = [
        _message([_tool_use_block("mark_declined", {"signal_id": "decline-1", "reason": "risk/reward is poor"})], "tool_use"),
        _message([_text_block("Declined.")], "end_turn"),
    ]

    with patch.object(claude_worker.cfg, "CLAUDE_WORKER_ENABLED", True), \
         patch("anthropic.AsyncAnthropic", _FakeAnthropic):
        candidate = await claude_worker.claim_one_candidate()
        status = await claude_worker.process_candidate(candidate)

    assert status == "declined_by_ai"
    assert await get_candidate_status("decline-1") == "declined_by_ai"


async def test_explicit_decline_tool_marks_declined_by_ai():
    """A decline tool call maps to declined_by_ai via the real classifier."""
    await init_db()
    await _queue_tv_candidate("decline-2")
    _install_fake_mcp(AsyncMock(return_value={"ok": True}))

    _FakeAnthropic._script = [
        _message([_tool_use_block("mark_declined", {"signal_id": "decline-2", "reason": "weak"})], "tool_use"),
        _message([_text_block("Declined.")], "end_turn"),
    ]

    with patch.object(claude_worker.cfg, "CLAUDE_WORKER_ENABLED", True), \
         patch("anthropic.AsyncAnthropic", _FakeAnthropic):
        candidate = await claude_worker.claim_one_candidate()
        status = await claude_worker.process_candidate(candidate)

    assert status == "declined_by_ai"
    assert await get_candidate_status("decline-2") == "declined_by_ai"


async def test_api_error_marks_failed():
    await init_db()
    await _queue_tv_candidate("error-1")
    _install_fake_mcp()

    _FakeAnthropic._script = [RuntimeError("API exploded")]

    with patch.object(claude_worker.cfg, "CLAUDE_WORKER_ENABLED", True), \
         patch("anthropic.AsyncAnthropic", _FakeAnthropic):
        candidate = await claude_worker.claim_one_candidate()
        status = await claude_worker.process_candidate(candidate)

    assert status == "failed"
    assert await get_candidate_status("error-1") == "failed"


async def test_empty_no_tool_marks_failed():
    """A response with no tool calls and no decline mapping -> failed (never silent)."""
    await init_db()
    await _queue_tv_candidate("empty-1")
    _install_fake_mcp()

    _FakeAnthropic._script = [_message([_text_block("Hmm.")], "end_turn")]

    with patch.object(claude_worker.cfg, "CLAUDE_WORKER_ENABLED", True), \
         patch("anthropic.AsyncAnthropic", _FakeAnthropic):
        candidate = await claude_worker.claim_one_candidate()
        status = await claude_worker.process_candidate(candidate)

    assert status == "failed"
    assert await get_candidate_status("empty-1") == "failed"


async def test_daily_cost_cap_idles():
    """When today's spend >= cap, the loop iteration claims nothing."""
    await init_db()
    await _queue_tv_candidate("cap-1")

    with patch.object(claude_worker.cfg, "CLAUDE_WORKER_ENABLED", True), \
         patch.object(claude_worker.cfg, "CLAUDE_WORKER_POLL_SECONDS", 0), \
         patch.object(claude_worker.cfg, "CLAUDE_DAILY_COST_USD_CAP", 5.0), \
         patch.object(claude_worker, "_today_cost_usd", AsyncMock(return_value=9.99)), \
         patch.object(claude_worker, "claim_one_candidate", AsyncMock()) as claim:
        await _one_iteration()
        claim.assert_not_called()

    assert await get_candidate_status("cap-1") == "pending_review"


async def test_claim_one_candidate_flips_status_and_is_exclusive():
    await init_db()
    await _queue_tv_candidate("claim-a")

    first = await claude_worker.claim_one_candidate()
    assert first is not None and first["id"] == "claim-a"
    assert first["payload"] == {"action": "buy"}
    assert await get_candidate_status("claim-a") == "in_review"

    # Already claimed -> nothing left to claim.
    second = await claude_worker.claim_one_candidate()
    assert second is None


# ── Loop driver ────────────────────────────────────────────────────────────────

async def _one_iteration() -> None:
    """Run exactly one body of claude_worker_loop by breaking out of the while."""
    import asyncio

    # Patch sleep to raise after the first call so the loop exits after one pass.
    class _Stop(Exception):
        pass

    async def _sleep_then_stop(_secs):
        raise _Stop()

    with patch.object(asyncio, "sleep", _sleep_then_stop):
        try:
            await claude_worker.claude_worker_loop()
        except _Stop:
            pass
