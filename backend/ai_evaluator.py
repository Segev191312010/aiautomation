"""AI Evaluator — persisted evaluation runs with sliced metrics."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from database import get_db
from evaluation_math import (
    empty_slice_metrics,
    compute_hit_rate,
    compute_net_pnl,
    compute_expectancy,
    compute_max_drawdown_pct_from_pnls,
    compute_coverage,
    compute_abstain_rate,
    compute_avg_confidence,
    compute_calibration_error,
)



log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    return str(uuid.uuid4())


# ── Evaluation Run Lifecycle ─────────────────────────────────────────────────

async def create_evaluation_run(
    *,
    candidate_type: str,
    candidate_key: str,
    baseline_key: str | None = None,
    evaluation_mode: str,
    window_start: str | None = None,
    window_end: str | None = None,
    request_json: dict,
    user_id: str = "demo",
) -> str:
    """Create a new evaluation run and return its ID."""
    eval_id = _make_id()
    now = _now_iso()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_evaluation_runs "
            "(id, candidate_type, candidate_key, baseline_key, evaluation_mode, "
            " window_start, window_end, request_json, status, created_at, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
            (eval_id, candidate_type, candidate_key, baseline_key, evaluation_mode,
             window_start, window_end, json.dumps(request_json), now, user_id),
        )
        await db.commit()
    return eval_id


async def complete_evaluation_run(
    evaluation_id: str,
    *,
    summary: dict,
    status: str = "completed",
    error: str | None = None,
) -> None:
    """Mark an evaluation run as completed with summary."""
    now = _now_iso()
    async with get_db() as db:
        await db.execute(
            "UPDATE ai_evaluation_runs SET status=?, summary_json=?, error=?, completed_at=? WHERE id=?",
            (status, json.dumps(summary), error, now, evaluation_id),
        )
        await db.commit()


# ── Slice Metrics ────────────────────────────────────────────────────────────

def compute_slice_metrics(items: list[dict]) -> dict:
    """Compute metrics for a set of decision items.

    Returns: count, scored_count, hit_rate, net_pnl, expectancy, max_drawdown,
             coverage, abstain_rate, avg_confidence, calibration_error
    """
    count = len(items)
    if count == 0:
        return empty_slice_metrics()

    scored = [i for i in items if i.get("score_status") != "unscored" and i.get("realized_pnl") is not None]
    scored_count = len(scored)
    abstain_count = sum(1 for i in items if i.get("item_type") == "abstain")
    pnls = [float(i["realized_pnl"]) for i in scored]

    hit_rate = compute_hit_rate(pnls)
    net_pnl = compute_net_pnl(pnls)
    expectancy = compute_expectancy(pnls, min_samples=3)
    max_dd = compute_max_drawdown_pct_from_pnls(pnls)
    coverage = compute_coverage(count, scored_count)
    abstain_rate = compute_abstain_rate(count, abstain_count)
    avg_confidence = compute_avg_confidence(items)
    calibration_error = compute_calibration_error(scored, hit_rate)

    return {
        "count": count,
        "scored_count": scored_count,
        "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        "net_pnl": round(net_pnl, 2) if net_pnl is not None else None,
        "expectancy": round(expectancy, 2) if expectancy is not None else None,
        "max_drawdown": round(max_dd, 2) if max_dd is not None else None,
        "coverage": round(coverage, 4) if coverage is not None else None,
        "abstain_rate": round(abstain_rate, 4) if abstain_rate is not None else None,
        "avg_confidence": round(avg_confidence, 4) if avg_confidence is not None else None,
        "calibration_error": round(calibration_error, 4) if calibration_error is not None else None,
    }


async def save_evaluation_slices(
    evaluation_run_id: str,
    slices: list[dict],
    *,
    user_id: str = "demo",
) -> None:
    """Persist evaluation slices for a run."""
    now = _now_iso()
    async with get_db() as db:
        for s in slices:
            await db.execute(
                "INSERT INTO ai_evaluation_slices "
                "(evaluation_run_id, slice_type, slice_key, metrics_json, created_at, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (evaluation_run_id, s["slice_type"], s["slice_key"],
                 json.dumps(s["metrics"]), now, user_id),
            )
        await db.commit()


def build_slices_from_items(items: list[dict]) -> list[dict]:
    """Build all slice types from a list of decision items."""
    slices: list[dict] = []

    # Overall
    slices.append({
        "slice_type": "overall",
        "slice_key": "all",
        "metrics": compute_slice_metrics(items),
    })

    # By item_type
    by_type: dict[str, list[dict]] = {}
    for i in items:
        by_type.setdefault(i.get("item_type", "unknown"), []).append(i)
    for key, group in by_type.items():
        slices.append({"slice_type": "action_type", "slice_key": key, "metrics": compute_slice_metrics(group)})

    # By symbol
    by_symbol: dict[str, list[dict]] = {}
    for i in items:
        sym = i.get("symbol")
        if sym:
            by_symbol.setdefault(sym, []).append(i)
    for key, group in by_symbol.items():
        slices.append({"slice_type": "symbol", "slice_key": key, "metrics": compute_slice_metrics(group)})

    # By regime
    by_regime: dict[str, list[dict]] = {}
    for i in items:
        regime = i.get("regime")
        if regime:
            by_regime.setdefault(regime, []).append(i)
    for key, group in by_regime.items():
        slices.append({"slice_type": "regime", "slice_key": key, "metrics": compute_slice_metrics(group)})

    # By confidence bucket
    from ai_replay import _make_confidence_buckets
    buckets = _make_confidence_buckets(items)
    for key, group in buckets.items():
        slices.append({"slice_type": "confidence_bucket", "slice_key": key, "metrics": compute_slice_metrics(group)})

    return slices


# ── Queries ──────────────────────────────────────────────────────────────────

async def get_evaluation_run(evaluation_id: str, user_id: str = "demo") -> dict | None:
    """Fetch a single evaluation run."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, candidate_type, candidate_key, baseline_key, evaluation_mode, "
            "window_start, window_end, summary_json, status, error, created_at, completed_at "
            "FROM ai_evaluation_runs WHERE id=? AND user_id=?",
            (evaluation_id, user_id),
        ) as cur:
            r = await cur.fetchone()
    if not r:
        return None

    summary = {}
    if r[7]:
        try:
            summary = json.loads(r[7])
        except Exception:
            pass

    return {
        "id": r[0], "candidate_type": r[1], "candidate_key": r[2],
        "baseline_key": r[3], "evaluation_mode": r[4],
        "window_start": r[5], "window_end": r[6],
        "summary": summary, "status": r[8], "error": r[9],
        "created_at": r[10], "completed_at": r[11],
    }


async def get_evaluation_runs(
    limit: int = 50, offset: int = 0, user_id: str = "demo",
) -> list[dict]:
    """Fetch evaluation runs, newest first."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, candidate_type, candidate_key, baseline_key, evaluation_mode, "
            "window_start, window_end, summary_json, status, created_at, completed_at "
            "FROM ai_evaluation_runs WHERE user_id=? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()

    results = []
    for r in rows:
        summary = {}
        if r[7]:
            try:
                summary = json.loads(r[7])
            except Exception:
                pass
        results.append({
            "id": r[0], "candidate_type": r[1], "candidate_key": r[2],
            "baseline_key": r[3], "evaluation_mode": r[4],
            "window_start": r[5], "window_end": r[6],
            "summary": summary, "status": r[8],
            "created_at": r[9], "completed_at": r[10],
        })
    return results


async def get_evaluation_slices(
    evaluation_id: str, user_id: str = "demo",
) -> list[dict]:
    """Fetch all slices for an evaluation run."""
    async with get_db() as db:
        async with db.execute(
            "SELECT slice_type, slice_key, metrics_json "
            "FROM ai_evaluation_slices WHERE evaluation_run_id=? AND user_id=? "
            "ORDER BY slice_type, slice_key",
            (evaluation_id, user_id),
        ) as cur:
            rows = await cur.fetchall()

    results = []
    for r in rows:
        metrics = {}
        try:
            metrics = json.loads(r[2])
        except Exception:
            pass
        results.append({
            "slice_type": r[0], "slice_key": r[1], **metrics,
        })
    return results


async def compare_evaluations(
    baseline_id: str, candidate_id: str, user_id: str = "demo",
) -> dict:
    """Compare two evaluation runs side by side."""
    baseline = await get_evaluation_run(baseline_id, user_id)
    candidate = await get_evaluation_run(candidate_id, user_id)
    baseline_slices = await get_evaluation_slices(baseline_id, user_id) if baseline else []
    candidate_slices = await get_evaluation_slices(candidate_id, user_id) if candidate else []

    return {
        "baseline": baseline,
        "candidate": candidate,
        "baseline_slices": baseline_slices,
        "candidate_slices": candidate_slices,
    }
