"""AI Decision Ledger — single source of truth for every AI decision and its outcome."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from context_utils import compute_context_hash
from database import get_db

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    return str(uuid.uuid4())


# ── Run lifecycle ────────────────────────────────────────────────────────────

async def start_decision_run(
    *,
    source: str,
    mode: str,
    provider: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    context_json: str,
    reasoning: str | None = None,
    aggregate_confidence: float | None = None,
    abstained: bool = False,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
    user_id: str = "demo",
) -> str:
    """Create a new decision run and return its ID."""
    run_id = _make_id()
    now = _now_iso()
    ctx_hash = compute_context_hash(context_json)

    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_decision_runs "
            "(id, source, mode, provider, model, prompt_version, context_hash, context_json, "
            " reasoning, aggregate_confidence, abstained, input_tokens, output_tokens, latency_ms, "
            " status, created_at, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)",
            (
                run_id, source, mode, provider, model, prompt_version,
                ctx_hash, context_json, reasoning, aggregate_confidence,
                1 if abstained else 0, input_tokens, output_tokens, latency_ms,
                now, user_id,
            ),
        )
        await db.commit()

    log.debug("Decision run started: %s (source=%s, mode=%s)", run_id, source, mode)
    return run_id


async def finalize_decision_run(
    run_id: str,
    *,
    status: str = "completed",
    error: str | None = None,
) -> None:
    """Mark a decision run as completed, error, etc."""
    now = _now_iso()
    async with get_db() as db:
        await db.execute(
            "UPDATE ai_decision_runs SET status=?, error=?, completed_at=? WHERE id=?",
            (status, error, now, run_id),
        )
        await db.commit()


# ── Item lifecycle ───────────────────────────────────────────────────────────

async def record_decision_items(
    run_id: str,
    items: list[dict],
    *,
    regime: str | None = None,
    user_id: str = "demo",
) -> list[str]:
    """Bulk-insert decision items for a run. Returns list of item IDs."""
    now = _now_iso()
    item_ids: list[str] = []

    async with get_db() as db:
        for idx, item in enumerate(items):
            item_id = _make_id()
            item_ids.append(item_id)
            await db.execute(
                "INSERT INTO ai_decision_items "
                "(id, run_id, item_index, item_type, action_name, target_key, symbol, "
                " proposed_json, confidence, regime, origin_rule_id, "
                " gate_status, score_status, created_at, updated_at, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'unscored', ?, ?, ?)",
                (
                    item_id, run_id, idx,
                    item.get("item_type", "unknown"),
                    item.get("action_name"),
                    item.get("target_key"),
                    item.get("symbol"),
                    json.dumps(item.get("proposed", {})),
                    item.get("confidence"),
                    regime or item.get("regime"),
                    item.get("origin_rule_id"),
                    now, now, user_id,
                ),
            )
        await db.commit()

    return item_ids


async def mark_decision_item_applied(
    item_id: str,
    *,
    applied_json: dict | None = None,
    created_rule_id: str | None = None,
    created_trade_id: str | None = None,
) -> None:
    """Mark an item as successfully applied."""
    now = _now_iso()
    applied = json.dumps(applied_json) if applied_json else None
    async with get_db() as db:
        await db.execute(
            "UPDATE ai_decision_items "
            "SET gate_status='applied', applied_json=?, created_rule_id=?, created_trade_id=?, updated_at=? "
            "WHERE id=?",
            (applied, created_rule_id, created_trade_id, now, item_id),
        )
        await db.commit()


async def mark_decision_item_blocked(item_id: str, reason: str) -> None:
    """Mark an item as blocked by guardrails."""
    now = _now_iso()
    async with get_db() as db:
        await db.execute(
            "UPDATE ai_decision_items SET gate_status='blocked', gate_reason=?, updated_at=? WHERE id=?",
            (reason, now, item_id),
        )
        await db.commit()


async def mark_decision_item_shadow(item_id: str, notes: str | None = None) -> None:
    """Mark an item as shadow-only (not applied, logged for analysis)."""
    now = _now_iso()
    async with get_db() as db:
        await db.execute(
            "UPDATE ai_decision_items SET gate_status='shadow', notes=?, updated_at=? WHERE id=?",
            (notes, now, item_id),
        )
        await db.commit()


async def attach_realized_trade(
    item_id: str,
    trade_id: str,
    realized_pnl: float | None,
    realized_at: str | None,
) -> None:
    """Link a finalized trade outcome to its originating decision item."""
    now = _now_iso()
    async with get_db() as db:
        await db.execute(
            "UPDATE ai_decision_items "
            "SET realized_trade_id=?, realized_pnl=?, realized_at=?, "
            "    score_status='direct_realized', score_source='finalize_trade_outcome', updated_at=? "
            "WHERE id=?",
            (trade_id, realized_pnl, realized_at, now, item_id),
        )
        await db.commit()


# ── Queries ──────────────────────────────────────────────────────────────────

async def get_decision_runs(
    *,
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
    mode: str | None = None,
    status: str | None = None,
    user_id: str = "demo",
) -> list[dict]:
    """Fetch decision runs with optional filters."""
    clauses = ["user_id = ?"]
    params: list = [user_id]

    if source:
        clauses.append("source = ?")
        params.append(source)
    if mode:
        clauses.append("mode = ?")
        params.append(mode)
    if status:
        clauses.append("status = ?")
        params.append(status)

    where = " AND ".join(clauses)
    params.extend([limit, offset])

    async with get_db() as db:
        async with db.execute(
            f"SELECT id, source, mode, provider, model, prompt_version, "
            f"aggregate_confidence, abstained, input_tokens, output_tokens, "
            f"status, error, created_at, completed_at "
            f"FROM ai_decision_runs WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ) as cur:
            rows = await cur.fetchall()

    results = []
    for r in rows:
        run_id = r[0]
        # Get item counts per gate_status
        item_counts = await _get_item_counts(run_id)
        results.append({
            "id": r[0], "source": r[1], "mode": r[2],
            "provider": r[3], "model": r[4], "prompt_version": r[5],
            "aggregate_confidence": r[6], "abstained": bool(r[7]),
            "input_tokens": r[8], "output_tokens": r[9],
            "status": r[10], "error": r[11],
            "created_at": r[12], "completed_at": r[13],
            "item_counts": item_counts,
        })
    return results


async def _get_item_counts(run_id: str) -> dict[str, int]:
    """Count items by gate_status for a run."""
    async with get_db() as db:
        async with db.execute(
            "SELECT gate_status, COUNT(*) FROM ai_decision_items WHERE run_id=? GROUP BY gate_status",
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {row[0]: row[1] for row in rows}


async def get_decision_run(run_id: str, user_id: str = "demo") -> dict | None:
    """Fetch a single decision run by ID."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, source, mode, provider, model, prompt_version, context_hash, "
            "reasoning, aggregate_confidence, abstained, input_tokens, output_tokens, latency_ms, "
            "status, error, created_at, completed_at "
            "FROM ai_decision_runs WHERE id=? AND user_id=?",
            (run_id, user_id),
        ) as cur:
            r = await cur.fetchone()
    if not r:
        return None

    item_counts = await _get_item_counts(run_id)
    return {
        "id": r[0], "source": r[1], "mode": r[2],
        "provider": r[3], "model": r[4], "prompt_version": r[5],
        "context_hash": r[6], "reasoning": r[7],
        "aggregate_confidence": r[8], "abstained": bool(r[9]),
        "input_tokens": r[10], "output_tokens": r[11], "latency_ms": r[12],
        "status": r[13], "error": r[14],
        "created_at": r[15], "completed_at": r[16],
        "item_counts": item_counts,
    }


async def get_decision_items(run_id: str, user_id: str = "demo") -> list[dict]:
    """Fetch all items for a decision run."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, run_id, item_index, item_type, action_name, target_key, symbol, "
            "proposed_json, applied_json, gate_status, gate_reason, confidence, regime, "
            "origin_rule_id, created_rule_id, created_trade_id, realized_trade_id, "
            "realized_pnl, realized_at, score_status, score_source, notes, created_at, updated_at "
            "FROM ai_decision_items WHERE run_id=? AND user_id=? ORDER BY item_index",
            (run_id, user_id),
        ) as cur:
            rows = await cur.fetchall()

    return [
        {
            "id": r[0], "run_id": r[1], "item_index": r[2],
            "item_type": r[3], "action_name": r[4], "target_key": r[5],
            "symbol": r[6],
            "proposed_json": _safe_json_parse(r[7]),
            "applied_json": _safe_json_parse(r[8]),
            "gate_status": r[9], "gate_reason": r[10],
            "confidence": r[11], "regime": r[12],
            "origin_rule_id": r[13], "created_rule_id": r[14],
            "created_trade_id": r[15], "realized_trade_id": r[16],
            "realized_pnl": r[17], "realized_at": r[18],
            "score_status": r[19], "score_source": r[20],
            "notes": r[21], "created_at": r[22], "updated_at": r[23],
        }
        for r in rows
    ]


def _safe_json_parse(s: str | None) -> dict | None:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None
