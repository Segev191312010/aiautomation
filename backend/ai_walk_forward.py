"""Persistence primitives for reproducible AI walk-forward evidence.

The six-hour learning loop reports rolling outcomes; it does not create
train/test folds.  This module stores fold boundaries and results produced by
an explicitly invoked evaluator, so an audit can distinguish evidence from
operational telemetry.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _validate_fold(fold: dict[str, Any], expected_index: int) -> None:
    if fold.get("fold_index") != expected_index:
        raise ValueError("fold_index values must be contiguous starting at zero")
    train_start = fold.get("train_start")
    train_end = fold.get("train_end")
    test_start = fold.get("test_start")
    test_end = fold.get("test_end")
    if not all(isinstance(v, str) and v for v in (train_start, train_end, test_start, test_end)):
        raise ValueError("walk-forward folds require non-empty ISO boundary strings")
    if not train_start < train_end <= test_start < test_end:
        raise ValueError("fold boundaries must satisfy train_start < train_end <= test_start < test_end")
    for key in ("train_count", "test_count"):
        if int(fold.get(key, 0)) < 0:
            raise ValueError(f"{key} cannot be negative")
    if not isinstance(fold.get("metrics", {}), dict) or not isinstance(fold.get("evidence", {}), dict):
        raise ValueError("metrics and evidence must be objects")


async def create_walk_forward_run(
    *, candidate_type: str, candidate_key: str, folds: list[dict[str, Any]],
    request: dict[str, Any], baseline_key: str | None = None,
    user_id: str = "demo",
) -> str:
    """Persist a queued run and immutable, non-overlapping fold definitions."""
    if not folds:
        raise ValueError("at least one walk-forward fold is required")
    for index, fold in enumerate(folds):
        _validate_fold(fold, index)
    run_id = str(uuid.uuid4())
    now = _now()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_walk_forward_runs "
            "(id,candidate_type,candidate_key,baseline_key,request_json,status,created_at,user_id) "
            "VALUES (?,?,?,?,?,'running',?,?)",
            (run_id, candidate_type, candidate_key, baseline_key, _json(request), now, user_id),
        )
        for fold in folds:
            await db.execute(
                "INSERT INTO ai_walk_forward_folds "
                "(run_id,fold_index,train_start,train_end,test_start,test_end,train_count,test_count,metrics_json,evidence_json,created_at,user_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, fold["fold_index"], fold["train_start"], fold["train_end"],
                 fold["test_start"], fold["test_end"], int(fold.get("train_count", 0)),
                 int(fold.get("test_count", 0)), _json(fold.get("metrics", {})),
                 _json(fold.get("evidence", {})), now, user_id),
            )
        await db.commit()
    return run_id


async def complete_walk_forward_run(
    run_id: str, *, summary: dict[str, Any], status: str = "completed",
    error: str | None = None, user_id: str = "demo",
) -> None:
    if status not in {"completed", "failed"}:
        raise ValueError("status must be completed or failed")
    async with get_db() as db:
        await db.execute(
            "UPDATE ai_walk_forward_runs SET status=?,summary_json=?,error=?,completed_at=? "
            "WHERE id=? AND user_id=?",
            (status, _json(summary), error, _now(), run_id, user_id),
        )
        await db.commit()


async def get_walk_forward_run(run_id: str, user_id: str = "demo") -> dict[str, Any] | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT id,candidate_type,candidate_key,baseline_key,request_json,status,summary_json,error,created_at,completed_at "
            "FROM ai_walk_forward_runs WHERE id=? AND user_id=?", (run_id, user_id)
        ) as cur:
            row = await cur.fetchone()
        async with db.execute(
            "SELECT fold_index,train_start,train_end,test_start,test_end,train_count,test_count,metrics_json,evidence_json "
            "FROM ai_walk_forward_folds WHERE run_id=? AND user_id=? ORDER BY fold_index", (run_id, user_id)
        ) as cur:
            folds = await cur.fetchall()
    if not row:
        return None
    def loads(value: str | None) -> dict[str, Any]:
        return json.loads(value) if value else {}
    return {
        "id": row[0], "candidate_type": row[1], "candidate_key": row[2],
        "baseline_key": row[3], "request": loads(row[4]), "status": row[5],
        "summary": loads(row[6]), "error": row[7], "created_at": row[8],
        "completed_at": row[9],
        "folds": [
            {"fold_index": f[0], "train_start": f[1], "train_end": f[2],
             "test_start": f[3], "test_end": f[4], "train_count": f[5],
             "test_count": f[6], "metrics": loads(f[7]), "evidence": loads(f[8])}
            for f in folds
        ],
    }
