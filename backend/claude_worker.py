"""Claude review worker — drains pending_review TradingView candidates.

A background loop (modelled on ``ai_learning.ai_learning_loop``) that claims one
``pending_review`` TradingView candidate at a time, builds a context snapshot,
and asks Claude (via the Anthropic SDK + the MCP tool surface) to approve or
decline a *paper* order. The worker is PAPER-only: it proposes orders against
the virtual SimEngine account, never the live broker.

Design notes
------------
* ``anthropic`` and ``mcp_server`` are imported lazily *inside* functions so
  importing this module never fails when the package/flag/sibling module is
  absent. When ``cfg.CLAUDE_WORKER_ENABLED`` is false the loop just idles.
* A single candidate is claimed atomically via an ``UPDATE ... RETURNING``
  (SQLite >= 3.35) inside a transaction, flipping ``pending_review`` ->
  ``in_review`` so concurrent workers never double-claim.
* The tool-result loop is multi-turn: each ``tool_use`` block is dispatched and
  its result appended as a ``tool_result`` user turn, re-calling the API until
  ``stop_reason != 'tool_use'`` (capped at ``MAX_TOOL_ITERS``).
* Every candidate reaches a *defined* terminal status — ``applied`` (an
  approved ``propose_order``), ``declined_by_ai`` (explicit decline), or
  ``failed`` (empty/no-tool result, API error, or timeout). Nothing is left
  silently in ``in_review``.
* Token usage is persisted to the decision ledger so cost accrues in
  ``ai_decision_runs``; the daily spend is checked against
  ``cfg.CLAUDE_DAILY_COST_USD_CAP`` before each claim.
"""
from __future__ import annotations

import asyncio
import json
import logging

from config import cfg
from db.core import transaction
from db.direct_candidates import mark_candidate_status

log = logging.getLogger(__name__)

# Cap on assistant<->tool round-trips per candidate so a misbehaving model
# cannot loop forever.
MAX_TOOL_ITERS = 5

# Per-call request timeout (seconds) — a stuck API call must not wedge the loop.
API_TIMEOUT_SECONDS = 90

SYSTEM_PROMPT = (
    "You are a disciplined PAPER-trading risk reviewer for an automated equities "
    "platform. A TradingView webhook produced a trade candidate. Using the "
    "provided account, open positions, recent signals, and the signal payload, "
    "decide whether to approve a paper order.\n\n"
    "Rules:\n"
    "- You operate in PAPER mode only. Never assume real money is at risk, but "
    "size and risk-manage as if it were.\n"
    "- If, and only if, the candidate is sound, call the propose_order tool with "
    "a concrete, risk-appropriate order. Approving means calling propose_order.\n"
    "- If the candidate is weak, conflicting, or under-specified, do NOT call "
    "propose_order. Instead state clearly that you decline and why.\n"
    "- Use the read-only tools to gather any extra context you need before "
    "deciding. Keep tool use minimal and purposeful."
)


# ── Candidate claiming ────────────────────────────────────────────────────────

async def claim_one_candidate() -> dict | None:
    """Atomically claim the oldest pending TV candidate.

    Flips one ``pending_review`` row to ``in_review`` and returns
    ``{id, symbol, payload}`` (payload parsed to a dict), or ``None`` when the
    queue is empty.
    """
    async with transaction() as db:
        async with db.execute(
            "UPDATE direct_candidates SET status='in_review' "
            "WHERE id IN ("
            "  SELECT id FROM direct_candidates "
            "  WHERE status='pending_review' AND source='tv_webhook' "
            "  ORDER BY queued_at ASC LIMIT 1"
            ") "
            "RETURNING id, symbol, payload"
        ) as cur:
            row = await cur.fetchone()

    if not row:
        return None

    cand_id, symbol, payload_raw = row
    try:
        payload = json.loads(payload_raw) if payload_raw else {}
    except (json.JSONDecodeError, TypeError):
        log.warning("claude_worker: corrupt payload for candidate %s", cand_id)
        payload = {}
    return {"id": cand_id, "symbol": symbol, "payload": payload}


# ── Cost tracking ─────────────────────────────────────────────────────────────

async def _today_cost_usd() -> float:
    """Today's Claude spend in USD, read from the decision ledger."""
    try:
        from datetime import datetime, timezone

        from ai_learning import compute_cost_report

        report = await compute_cost_report(days=1)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for day in report.get("daily", []):
            if day.get("date") == today:
                return float(day.get("estimated_cost_usd", 0.0))
        # Fall back to the window total if the daily bucket is missing.
        return float(report.get("total_cost_usd", 0.0))
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("claude_worker: cost report unavailable: %s", exc)
        return 0.0


async def _daily_cap_reached() -> bool:
    cap = cfg.CLAUDE_DAILY_COST_USD_CAP
    if cap <= 0:
        return False
    spent = await _today_cost_usd()
    if spent >= cap:
        log.warning(
            "claude_worker: daily cost cap reached ($%.2f >= $%.2f) — idling",
            spent, cap,
        )
        return True
    return False


# ── Anthropic message helpers ─────────────────────────────────────────────────

def _content_to_params(content) -> list[dict]:
    """Convert SDK content blocks from an assistant turn into request params."""
    params: list[dict] = []
    for block in content:
        # SDK blocks expose .model_dump(); fall back to dict() for plain dicts.
        if hasattr(block, "model_dump"):
            params.append(block.model_dump(exclude_none=True))
        elif isinstance(block, dict):
            params.append(block)
    return params


def _extract_tool_uses(content) -> list:
    """Return the tool_use blocks from an assistant message's content."""
    uses = []
    for block in content:
        if getattr(block, "type", None) == "tool_use":
            uses.append(block)
    return uses


def _classify_outcome(tool_calls: list[dict]) -> str:
    """Map the tools Claude invoked (and their RESULTS) to a terminal status.

    * a ``propose_order`` the platform ACCEPTED -> ``applied``
    * an explicit ``mark_declined`` tool -> ``declined_by_ai``
    * otherwise (no decisive tool, OR a proposal the platform rejected/deferred)
      -> ``failed`` — never silently 'applied' on a rejected/deferred proposal.
    """
    names = {c.get("name") for c in tool_calls}
    for c in tool_calls:
        if c.get("name") == "propose_order":
            result = c.get("result")
            status = result.get("status") if isinstance(result, dict) else None
            if status in ("approved", "applied", "filled", "submitted"):
                return "applied"
    if "mark_declined" in names:
        return "declined_by_ai"
    return "failed"


# ── Candidate processing ──────────────────────────────────────────────────────

async def process_candidate(candidate: dict) -> str:
    """Run the multi-turn review for one candidate; return its terminal status.

    The status is also persisted via ``mark_candidate_status``. Any failure path
    (empty/no-tool result, API error, timeout) resolves to ``failed`` — never
    leaving the candidate stuck in ``in_review``.
    """
    cand_id = candidate["id"]
    status = "failed"
    input_tokens = 0
    output_tokens = 0
    run_id: str | None = None

    try:
        from anthropic import AsyncAnthropic

        from claude_context import build_context

        # Lazy MCP imports — sibling agent owns mcp_server; tolerate absence.
        from mcp_server import dispatch_tool_call, tools_spec

        context = await build_context(candidate)

        # Open a ledger run so token cost accrues even if the call later errors.
        try:
            from ai_decision_ledger import start_decision_run

            run_id = await start_decision_run(
                source="tv_webhook",
                mode="paper",
                provider="anthropic",
                model=cfg.CLAUDE_WORKER_MODEL,
                context_json=json.dumps(context, default=str),
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("claude_worker: ledger run not started: %s", exc)

        client = AsyncAnthropic()
        messages: list[dict] = [
            {
                "role": "user",
                "content": (
                    "Review this paper-trade candidate and decide:\n"
                    + json.dumps(context, default=str)
                ),
            }
        ]

        tool_calls: list[dict] = []
        decided = False

        for _ in range(MAX_TOOL_ITERS):
            response = await asyncio.wait_for(
                client.messages.create(
                    model=cfg.CLAUDE_WORKER_MODEL,
                    max_tokens=2048,
                    system=[{
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    tools=tools_spec,
                    messages=messages,
                ),
                timeout=API_TIMEOUT_SECONDS,
            )

            usage = getattr(response, "usage", None)
            if usage is not None:
                input_tokens += getattr(usage, "input_tokens", 0) or 0
                output_tokens += getattr(usage, "output_tokens", 0) or 0

            tool_uses = _extract_tool_uses(response.content)
            if response.stop_reason != "tool_use" or not tool_uses:
                # Model stopped without (further) tools this turn.
                break

            # Append the assistant turn, then dispatch each tool and append
            # the tool_result user turn before re-calling.
            messages.append({"role": "assistant", "content": _content_to_params(response.content)})
            tool_results: list[dict] = []
            for tu in tool_uses:
                try:
                    result = await dispatch_tool_call({"name": tu.name, "input": tu.input})
                    is_error = False
                except Exception as exc:
                    result = {"error": f"tool error: {exc}"}
                    is_error = True
                tool_calls.append({"name": tu.name, "input": tu.input, "result": result})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})
            decided = True

        status = _classify_outcome(tool_calls) if (tool_calls or decided) else "failed"

    except asyncio.TimeoutError:
        log.warning("claude_worker: API timeout reviewing candidate %s", cand_id)
        status = "failed"
    except Exception as exc:
        log.exception("claude_worker: error reviewing candidate %s: %s", cand_id, exc)
        status = "failed"

    # Persist token usage so daily cost accrues.
    if run_id is not None:
        try:
            from ai_decision_ledger import finalize_decision_run
            from db.core import get_db

            async with get_db() as db:
                await db.execute(
                    "UPDATE ai_decision_runs SET input_tokens=?, output_tokens=? WHERE id=?",
                    (input_tokens, output_tokens, run_id),
                )
                await db.commit()
            await finalize_decision_run(
                run_id,
                status="completed" if status != "failed" else "error",
                error=None if status != "failed" else "review_failed",
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("claude_worker: ledger finalize failed: %s", exc)

    try:
        await mark_candidate_status(cand_id, status)
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("claude_worker: failed to mark candidate %s as %s: %s", cand_id, status, exc)

    log.info("claude_worker: candidate %s -> %s", cand_id, status)
    return status


# ── Background loop ───────────────────────────────────────────────────────────

async def claude_worker_loop() -> None:
    """Background task: review pending_review TV candidates one at a time."""
    log.info("Claude review worker loop started (poll=%ss)", cfg.CLAUDE_WORKER_POLL_SECONDS)
    while True:
        try:
            if not cfg.CLAUDE_WORKER_ENABLED:
                await asyncio.sleep(cfg.CLAUDE_WORKER_POLL_SECONDS)
                continue

            if await _daily_cap_reached():
                await asyncio.sleep(cfg.CLAUDE_WORKER_POLL_SECONDS)
                continue

            candidate = await claim_one_candidate()
            if candidate is None:
                await asyncio.sleep(cfg.CLAUDE_WORKER_POLL_SECONDS)
                continue

            await process_candidate(candidate)
        except Exception as e:
            log.exception("Claude worker loop error: %s", e)
        await asyncio.sleep(cfg.CLAUDE_WORKER_POLL_SECONDS)
