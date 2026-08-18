"""Persistence primitives for reproducible AI walk-forward evidence.

The six-hour learning loop reports rolling outcomes; it does not create
train/test folds.  This module stores fold boundaries and results produced by
an explicitly invoked evaluator, so an audit can distinguish evidence from
operational telemetry.
"""
from __future__ import annotations

import json
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _parse_timestamp(value: Any, *, field: str = "created_at") -> datetime:
    """Parse an aware ISO timestamp; naive timestamps are unsafe for folds."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _validate_fold(fold: dict[str, Any], expected_index: int) -> None:
    if fold.get("fold_index") != expected_index:
        raise ValueError("fold_index values must be contiguous starting at zero")
    train_start = fold.get("train_start")
    train_end = fold.get("train_end")
    test_start = fold.get("test_start")
    test_end = fold.get("test_end")
    if not all(isinstance(v, str) and v for v in (train_start, train_end, test_start, test_end)):
        raise ValueError("walk-forward folds require non-empty ISO boundary strings")
    parsed = [_parse_timestamp(v, field="fold boundary") for v in (train_start, train_end, test_start, test_end)]
    if not parsed[0] < parsed[1] <= parsed[2] < parsed[3]:
        raise ValueError("fold boundaries must satisfy train_start < train_end <= test_start < test_end")
    for key in ("train_count", "test_count"):
        if int(fold.get(key, 0)) < 0:
            raise ValueError(f"{key} cannot be negative")
    if not isinstance(fold.get("metrics", {}), dict) or not isinstance(fold.get("evidence", {}), dict):
        raise ValueError("metrics and evidence must be objects")


def _dataset_fingerprint(items: list[dict[str, Any]]) -> str:
    payload = _json(items).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_walk_forward_folds(
    items: list[dict[str, Any]], *, train_size: int, test_size: int,
    step_size: int | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Build deterministic, chronological folds from persisted decision items.

    Windows are expressed in distinct timestamp groups, never individual rows,
    so decisions sharing a timestamp cannot straddle train and test.  The
    returned metrics describe only already-realized persisted outcomes; this
    function does not generate or claim model performance.
    """
    if train_size < 1 or test_size < 1:
        raise ValueError("train_size and test_size must be positive")
    step = test_size if step_size is None else step_size
    if step < 1:
        raise ValueError("step_size must be positive")
    normalized: list[tuple[datetime, dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("items must contain objects")
        timestamp = _parse_timestamp(item.get("created_at"))
        normalized.append((timestamp, item))
    normalized.sort(key=lambda pair: (pair[0], _json(pair[1])))
    if not normalized:
        raise ValueError("at least one item is required")

    groups: list[tuple[datetime, list[dict[str, Any]]]] = []
    for timestamp, item in normalized:
        if groups and timestamp == groups[-1][0]:
            groups[-1][1].append(item)
        else:
            groups.append((timestamp, [item]))
    if len(groups) < train_size + test_size:
        raise ValueError("not enough distinct timestamps for one train/test fold")

    fingerprint = _dataset_fingerprint([item for _, item in normalized])
    folds: list[dict[str, Any]] = []
    start = 0
    while start + train_size + test_size <= len(groups):
        train_groups = groups[start:start + train_size]
        test_groups = groups[start + train_size:start + train_size + test_size]
        train_items = [item for _, group in train_groups for item in group]
        test_items = [item for _, group in test_groups for item in group]
        train_start = train_groups[0][0]
        test_start = test_groups[0][0]
        test_end = test_groups[-1][0] + timedelta(microseconds=1)
        # Explicitly assert the temporal invariant before persisting anything.
        if not max(_parse_timestamp(i["created_at"]) for i in train_items) < min(
            _parse_timestamp(i["created_at"]) for i in test_items
        ):
            raise ValueError("look-ahead detected: train data reaches test data")
        folds.append({
            "fold_index": len(folds),
            "train_start": train_start.isoformat(),
            "train_end": test_start.isoformat(),
            "test_start": test_start.isoformat(),
            "test_end": test_end.isoformat(),
            "train_count": len(train_items),
            "test_count": len(test_items),
            "metrics": {
                "train": _metrics(train_items),
                "test": _metrics(test_items),
            },
            "evidence": {
                "source": "persisted_decision_items",
                "dataset_sha256": fingerprint,
                "train_item_count": len(train_items),
                "test_item_count": len(test_items),
                "lookahead_check": "max_train_timestamp < min_test_timestamp",
            },
        })
        start += step
    if not folds:
        raise ValueError("not enough distinct timestamps for one train/test fold")
    return folds, fingerprint


def _metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    # Local import avoids a module cycle and keeps this adapter independently testable.
    from ai_evaluator import compute_slice_metrics
    return compute_slice_metrics(items)


async def run_walk_forward_evaluation(
    *, candidate_type: str, candidate_key: str, items: list[dict[str, Any]],
    train_size: int, test_size: int, step_size: int | None = None,
    request: dict[str, Any] | None = None, baseline_key: str | None = None,
    user_id: str = "demo",
) -> str:
    """Persist a bounded evaluation over deterministic, already-stored items."""
    folds, fingerprint = build_walk_forward_folds(
        items, train_size=train_size, test_size=test_size, step_size=step_size,
    )
    run_id = await create_walk_forward_run(
        candidate_type=candidate_type, candidate_key=candidate_key,
        baseline_key=baseline_key, folds=folds,
        request={
            **(request or {}),
            "evaluator": "build_walk_forward_folds",
            "train_size": train_size, "test_size": test_size,
            "step_size": test_size if step_size is None else step_size,
            "dataset_sha256": fingerprint,
        }, user_id=user_id,
    )
    summary = {
        "fold_count": len(folds),
        "dataset_sha256": fingerprint,
        "train_count": sum(f["train_count"] for f in folds),
        "test_count": sum(f["test_count"] for f in folds),
        "metrics_scope": "persisted_realized_outcomes_only",
        "performance_claim": False,
    }
    await complete_walk_forward_run(run_id, summary=summary, user_id=user_id)
    return run_id


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
