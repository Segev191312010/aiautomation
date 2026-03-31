"""AI Replay Engine — stored-context replay and rule backtest replay for evaluation."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from database import get_db
from config import cfg

log = logging.getLogger(__name__)


# ── Context Selection ────────────────────────────────────────────────────────

async def _select_replay_contexts(
    *,
    window_start: str,
    window_end: str,
    source: str = "optimizer",
    limit: int = 500,
    user_id: str = "demo",
) -> list[dict]:
    """Select historical decision runs for replay."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, context_json, context_hash, model, prompt_version, "
            "aggregate_confidence, reasoning, created_at "
            "FROM ai_decision_runs "
            "WHERE source=? AND created_at >= ? AND created_at <= ? AND user_id=? "
            "ORDER BY created_at ASC LIMIT ?",
            (source, window_start, window_end, user_id, limit),
        ) as cur:
            rows = await cur.fetchall()

    return [
        {
            "run_id": r[0],
            "context_json": r[1],
            "context_hash": r[2],
            "model": r[3],
            "prompt_version": r[4],
            "aggregate_confidence": r[5],
            "reasoning": r[6],
            "created_at": r[7],
        }
        for r in rows
    ]


# ── Stored-Context Replay ────────────────────────────────────────────────────

async def generate_candidate_items_from_context(
    context_json: str,
    candidate_config: dict,
) -> dict | None:
    """Call the LLM with a stored context snapshot using candidate model/prompt config.

    Delegates to candidate_registry.generate_candidate_items().
    """
    from candidate_registry import generate_candidate_items
    return await generate_candidate_items(context_json, candidate_config)


async def run_stored_context_existing(
    *,
    window_days: int = 90,
    limit_runs: int = 500,
    min_confidence: float | None = None,
    symbols: list[str] | None = None,
    action_types: list[str] | None = None,
    user_id: str = "demo",
) -> dict:
    """Summarize already-persisted decision items. NO LLM call. Honors all filters."""
    from ai_decision_ledger import get_decision_runs, get_decision_items

    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=window_days)).isoformat()
    window_end = now.isoformat()

    runs = await get_decision_runs(limit=limit_runs, source="optimizer", user_id=user_id)
    # Filter by window
    runs = [r for r in runs if r["created_at"] >= window_start]

    all_items: list[dict] = []
    for run in runs:
        items = await get_decision_items(run["id"], user_id=user_id)
        for item in items:
            # Apply filters
            if min_confidence is not None and (item.get("confidence") or 0) < min_confidence:
                continue
            if symbols and item.get("symbol") and item["symbol"] not in symbols:
                continue
            if action_types and item.get("item_type") not in action_types:
                continue
            all_items.append(item)

    return {
        "mode": "stored_context_existing",
        "runs_evaluated": len(runs),
        "items_count": len(all_items),
        "items": all_items,
        "window_start": window_start,
        "window_end": window_end,
    }


async def run_stored_context_generate(
    *,
    candidate_key: str,
    baseline_key: str | None = None,
    candidate_type: str = "model_version",
    window_days: int = 90,
    limit_runs: int = 500,
    min_confidence: float | None = None,
    symbols: list[str] | None = None,
    action_types: list[str] | None = None,
    user_id: str = "demo",
) -> dict:
    """Select stored contexts, generate candidate items via LLM, score matchable items.

    Filters (min_confidence, symbols, action_types) are applied to generated items
    post-generation — matching the filter semantics of stored_context_existing mode.
    """
    from candidate_registry import resolve_candidate
    from replay_scoring import score_candidate_item_against_historical
    from ai_decision_ledger import get_decision_items
    from decision_item_factory import build_ledger_items

    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=window_days)).isoformat()
    window_end = now.isoformat()

    candidate_config = resolve_candidate(candidate_type, candidate_key)

    contexts = await _select_replay_contexts(
        window_start=window_start, window_end=window_end,
        limit=limit_runs, user_id=user_id,
    )

    if not contexts:
        return {"mode": "stored_context_generate", "runs_evaluated": 0, "candidate_items": [], "scored_items": [], "errors": ["No runs in window"]}

    candidate_items: list[dict] = []
    scored_items: list[dict] = []
    errors: list[str] = []

    for ctx in contexts:
        result = await generate_candidate_items_from_context(ctx["context_json"], candidate_config)
        if not result:
            errors.append(f"Failed for run {ctx['run_id']}")
            continue

        # Get baseline items for scoring
        baseline_items = await get_decision_items(ctx["run_id"], user_id=user_id)

        generated = build_ledger_items(result)

        for gen_item in generated:
            # Apply filters (parity with stored_context_existing)
            if min_confidence is not None and (gen_item.get("confidence") or 0) < min_confidence:
                continue
            if symbols and gen_item.get("symbol") and gen_item["symbol"] not in symbols:
                continue
            if action_types and gen_item.get("item_type") not in action_types:
                continue

            score = score_candidate_item_against_historical(gen_item, baseline_items)
            gen_item.update(score)
            gen_item["original_run_id"] = ctx["run_id"]
            scored_items.append(gen_item)

        candidate_items.append({
            "original_run_id": ctx["run_id"],
            "candidate_decisions": result,
            "scored_count": sum(1 for g in scored_items if g.get("original_run_id") == ctx["run_id"] and g.get("score_status") != "unscored"),
        })

    return {
        "mode": "stored_context_generate",
        "runs_evaluated": len(contexts),
        "candidate_items": candidate_items,
        "scored_items": scored_items,
        "errors": errors,
        "filters_applied": {
            "min_confidence": min_confidence,
            "symbols": symbols,
            "action_types": action_types,
        },
        "window_start": window_start,
        "window_end": window_end,
    }


# ── Rule Backtest Replay ─────────────────────────────────────────────────────

async def run_rule_backtest_replay(
    rule_id: str,
    *,
    window_days: int = 365,
    user_id: str = "demo",
) -> dict:
    """Backtest a rule snapshot deterministically.

    W1-05: Uses rule_replay_adapter for fail-closed replayability guard.
    Only rules with explicit replay_config can be backtested.
    """
    from database import get_rule
    from rule_replay_adapter import is_rule_replayable, build_backtest_request_from_rule

    rule = await get_rule(rule_id, user_id=user_id)
    if not rule:
        return {"error": f"Rule {rule_id} not found"}

    # W1-05: fail-closed replayability guard
    ok, reason = is_rule_replayable(rule)
    if not ok:
        return {"not_replayable": True, "reason": reason, "rule_id": rule_id, "rule_name": rule.name}

    bt_request = build_backtest_request_from_rule(rule)

    # Determine symbols to test
    if rule.symbol:
        symbols = [rule.symbol]
    elif rule.universe:
        try:
            from screener import load_universe
            all_syms = load_universe(rule.universe)
            symbols = all_syms[:5]
        except Exception:
            symbols = ["SPY"]
    else:
        symbols = ["SPY"]

    from backtester import run_backtest

    results_by_symbol: dict[str, dict] = {}
    aggregate_pnl = 0.0
    aggregate_trades = 0

    for sym in symbols:
        try:
            bt_result = await run_backtest(
                entry_conditions=bt_request["entry_conditions"],
                exit_conditions=bt_request["exit_conditions"],
                symbol=sym,
                period=bt_request.get("period", "2y"),
                interval=bt_request.get("interval", "1d"),
                initial_capital=bt_request.get("initial_capital", 100_000.0),
                position_size_pct=bt_request.get("position_size_pct", 10.0),
                stop_loss_pct=bt_request.get("stop_loss_pct", 0.0),
                take_profit_pct=bt_request.get("take_profit_pct", 0.0),
                condition_logic=bt_request.get("condition_logic", "AND"),
            )
            metrics = bt_result.get("metrics", {})
            if hasattr(metrics, "model_dump"):
                metrics = metrics.model_dump()
            results_by_symbol[sym] = {
                "total_return_pct": metrics.get("total_return_pct", 0),
                "num_trades": metrics.get("num_trades", 0),
                "win_rate": metrics.get("win_rate", 0),
                "profit_factor": metrics.get("profit_factor", 0),
                "max_drawdown_pct": metrics.get("max_drawdown_pct", 0),
                "sharpe_ratio": metrics.get("sharpe_ratio", 0),
            }
            aggregate_pnl += metrics.get("total_return_pct", 0)
            aggregate_trades += metrics.get("num_trades", 0)
        except Exception as exc:
            log.warning("Backtest replay failed for %s/%s: %s", rule_id, sym, exc)
            results_by_symbol[sym] = {"error": str(exc)}

    return {
        "rule_id": rule_id,
        "rule_name": rule.name,
        "symbols_tested": symbols,
        "replay_config": bt_request,
        "by_symbol": results_by_symbol,
        "aggregate_return_pct": round(aggregate_pnl / max(len(symbols), 1), 2),
        "aggregate_trades": aggregate_trades,
    }


# ── Confidence Buckets (delegated to evaluation_math) ───────────────────────

def _make_confidence_buckets(items: list[dict]) -> dict[str, list[dict]]:
    """Backward-compat wrapper. Use evaluation_math.make_confidence_buckets directly."""
    from evaluation_math import make_confidence_buckets
    return make_confidence_buckets(items)
