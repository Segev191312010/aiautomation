"""MCP-style tool definitions + dispatchers for the Claude worker.

Exposes a small, READ-mostly surface to a Claude model running as the
autonomous worker:

  * Read tools  — signal / positions / account / recent-signals / market-data.
    These reuse the existing services (IBKR client, market_data, the persisted
    direct_candidates queue) and never mutate state.
  * Write tools — ``propose_order`` (routes through the shared
    ``place_proposed_order`` chain, so it cannot bypass any gate) and
    ``mark_declined`` (terminal review transition on a TV/AI candidate).

``tools_spec`` is plain JSON-schema tool definitions (Anthropic tool-use
format). ``dispatch_tool_call`` maps a tool-use block onto the async
dispatchers below.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from db.core import get_db
from db.direct_candidates import get_candidate_status, mark_candidate_status
from models import Rule, TradeAction
from order_proposal import place_proposed_order

log = logging.getLogger(__name__)


# ── Tool schema (Anthropic tool-use format) ──────────────────────────────────
tools_spec: list[dict[str, Any]] = [
    {
        "name": "get_signal",
        "description": (
            "Fetch a single queued/pending trading signal (a direct candidate) "
            "by its signal_id. Returns the symbol, payload, status and age. "
            "Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "signal_id": {
                    "type": "string",
                    "description": "The candidate/signal id to fetch.",
                },
            },
            "required": ["signal_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_positions",
        "description": (
            "List current open positions in the paper account (symbol, qty, "
            "average cost, market price, market value, unrealized P&L). Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_account",
        "description": (
            "Get the paper account summary: balance (net liquidation), cash, "
            "margin used, realized and unrealized P&L. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_recent_signals",
        "description": (
            "List recent trading signals (direct candidates), most recent first. "
            "Optionally filter by status. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max signals to return (1-50, default 10).",
                    "minimum": 1,
                    "maximum": 50,
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter, e.g. 'pending_review'.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_market_data",
        "description": (
            "Get the latest traded price for a symbol (IBKR snapshot with a "
            "Yahoo fallback). Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. 'AAPL'.",
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "propose_order",
        "description": (
            "Propose a LIMIT order. The platform runs the full pre-trade chain "
            "(risk, portfolio impact, runtime safety gate) before any broker "
            "submission; a rejected proposal must NOT be retried. Returns the "
            "proposal result with status approved | rejected | deferred."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol."},
                "action": {
                    "type": "string",
                    "enum": ["BUY", "SELL"],
                    "description": "Order side.",
                },
                "qty": {
                    "type": "integer",
                    "description": "Number of shares (> 0).",
                    "minimum": 1,
                },
                "limit_price": {
                    "type": "number",
                    "description": "LIMIT price (required — market orders are not allowed).",
                    "exclusiveMinimum": 0,
                },
                "signal_id": {
                    "type": "string",
                    "description": "The signal_id this order acts on.",
                },
            },
            "required": ["symbol", "action", "qty", "limit_price", "signal_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mark_declined",
        "description": (
            "Decline a signal — record that the AI chose not to act on it. "
            "Transitions the candidate to 'declined_by_ai' (terminal). Use this "
            "freely whenever the setup is not worth taking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "signal_id": {"type": "string", "description": "The signal_id to decline."},
                "reason": {
                    "type": "string",
                    "description": "Short reason for declining.",
                },
            },
            "required": ["signal_id", "reason"],
            "additionalProperties": False,
        },
    },
]


# ── Read dispatchers ─────────────────────────────────────────────────────────

async def mcp_get_signal(signal_id: str) -> dict[str, Any]:
    """Fetch one direct candidate by id (read-only)."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, user_id, symbol, payload, queued_at, ttl_seconds, status, source "
            "FROM direct_candidates WHERE id=?",
            (signal_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {"found": False, "signal_id": signal_id}
    return _row_to_signal(row)


async def mcp_get_recent_signals(limit: int = 10, status: str | None = None) -> dict[str, Any]:
    """List recent direct candidates, most recent first (read-only)."""
    limit = max(1, min(int(limit), 50))
    query = (
        "SELECT id, user_id, symbol, payload, queued_at, ttl_seconds, status, source "
        "FROM direct_candidates"
    )
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY queued_at DESC LIMIT ?"
    params.append(limit)
    async with get_db() as db:
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return {"signals": [_row_to_signal(r) for r in rows], "count": len(rows)}


async def mcp_get_positions() -> dict[str, Any]:
    """List current open positions (read-only)."""
    from ibkr_client import ibkr

    if not ibkr.is_connected():
        return {"connected": False, "positions": []}
    positions = await ibkr.get_positions()
    return {
        "connected": True,
        "positions": [
            {
                "symbol": p.symbol,
                "asset_type": p.asset_type,
                "qty": p.qty,
                "avg_cost": p.avg_cost,
                "market_price": p.market_price,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "realized_pnl": p.realized_pnl,
            }
            for p in positions
        ],
    }


async def mcp_get_account() -> dict[str, Any]:
    """Get the account summary (read-only)."""
    from ibkr_client import ibkr

    if not ibkr.is_connected():
        return {"connected": False}
    acct = await ibkr.get_account_summary()
    return {
        "connected": True,
        "balance": acct.balance,
        "cash": acct.cash,
        "margin_used": acct.margin_used,
        "unrealized_pnl": acct.unrealized_pnl,
        "realized_pnl": acct.realized_pnl,
        "currency": acct.currency,
    }


async def mcp_get_market_data(symbol: str) -> dict[str, Any]:
    """Get the latest price for a symbol (read-only)."""
    from market_data import get_latest_price

    sym = symbol.upper()
    price = await get_latest_price(sym)
    return {"symbol": sym, "price": price, "available": price is not None}


# ── Write dispatchers ─────────────────────────────────────────────────────────

async def mcp_propose_order(
    symbol: str,
    action: str,
    qty: int,
    limit_price: float,
    signal_id: str,
) -> dict[str, Any]:
    """Build a LIMIT-only rule and route it through the shared pre-trade chain.

    Returns the serialized ``ProposalResult`` dict. The proposal can never
    bypass a gate — it calls the same ``place_proposed_order`` the rule engine
    path uses.
    """
    from dataclasses import asdict

    rule = Rule(
        name=f"claude_worker:{signal_id}",
        symbol=symbol.upper(),
        enabled=False,
        conditions=[],
        action=TradeAction(
            type=action.upper(),  # type: ignore[arg-type]
            asset_type="STK",
            quantity=int(qty),
            order_type="LMT",
            limit_price=float(limit_price),
        ),
        status="paper",
        ai_generated=True,
        created_by="ai",
    )
    result = await place_proposed_order(rule, source="claude_worker", user_id="demo")
    payload = asdict(result)
    payload["signal_id"] = signal_id
    return payload


async def mcp_mark_declined(signal_id: str, reason: str) -> dict[str, Any]:
    """Decline a candidate — transition to 'declined_by_ai' (terminal)."""
    await mark_candidate_status(signal_id, "declined_by_ai")
    status = await get_candidate_status(signal_id)
    log.info("mcp_mark_declined: signal=%s reason=%s -> %s", signal_id, reason, status)
    return {"signal_id": signal_id, "status": status, "reason": reason}


# ── Tool-call dispatch ─────────────────────────────────────────────────────────

_DISPATCH = {
    "get_signal": lambda a: mcp_get_signal(a["signal_id"]),
    "get_positions": lambda a: mcp_get_positions(),
    "get_account": lambda a: mcp_get_account(),
    "get_recent_signals": lambda a: mcp_get_recent_signals(
        limit=a.get("limit", 10), status=a.get("status"),
    ),
    "get_market_data": lambda a: mcp_get_market_data(a["symbol"]),
    "propose_order": lambda a: mcp_propose_order(
        symbol=a["symbol"],
        action=a["action"],
        qty=a["qty"],
        limit_price=a["limit_price"],
        signal_id=a["signal_id"],
    ),
    "mark_declined": lambda a: mcp_mark_declined(a["signal_id"], a["reason"]),
}


async def dispatch_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one Anthropic tool-use block to its async dispatcher.

    ``tool_call`` is the tool_use content block: ``{"name": ..., "input": {...}}``.
    Unknown tools and dispatcher errors are returned as ``{"error": ...}`` so
    the caller can feed a ``tool_result`` back to the model rather than crash.
    """
    name = tool_call.get("name", "")
    args = tool_call.get("input") or tool_call.get("arguments") or {}
    handler = _DISPATCH.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return await handler(args)
    except Exception as exc:  # noqa: BLE001 — surface as tool_result, never crash the loop
        log.exception("dispatch_tool_call failed: tool=%s args=%s", name, args)
        return {"error": f"{type(exc).__name__}: {exc}"}


def _row_to_signal(row: Any) -> dict[str, Any]:
    """Map a direct_candidates row tuple to a JSON-serializable signal dict."""
    cand_id, user_id, symbol, payload_json, queued_at, ttl_seconds, status, source = row
    try:
        payload = json.loads(payload_json) if payload_json else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    age_seconds: float | None = None
    try:
        qa = datetime.fromisoformat(str(queued_at).replace("Z", "+00:00"))
        if qa.tzinfo is None:
            qa = qa.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - qa).total_seconds()
    except (TypeError, ValueError):
        age_seconds = None
    return {
        "found": True,
        "signal_id": cand_id,
        "user_id": user_id,
        "symbol": symbol,
        "payload": payload,
        "queued_at": queued_at,
        "ttl_seconds": ttl_seconds,
        "status": status,
        "source": source,
        "age_seconds": age_seconds,
    }
