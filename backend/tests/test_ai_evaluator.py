"""Stage 10 evaluator tests — slice metrics, build_slices, persistence, compare."""
import json
import pytest

import config
import database
from database import init_db
from ai_evaluator import (
    compute_slice_metrics,
    build_slices_from_items,
    create_evaluation_run,
    complete_evaluation_run,
    save_evaluation_slices,
    get_evaluation_run,
    get_evaluation_runs,
    get_evaluation_slices,
    compare_evaluations,
)


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "eval.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)


def _scored_items(pnls: list[float], confidence: float = 0.7) -> list[dict]:
    return [
        {
            "item_type": "direct_trade",
            "score_status": "direct_realized",
            "realized_pnl": p,
            "confidence": confidence,
            "symbol": "AAPL",
            "regime": "bull" if p > 0 else "bear",
        }
        for p in pnls
    ]


# ── Slice Metrics ────────────────────────────────────────────────────────────

def test_compute_slice_metrics_empty():
    m = compute_slice_metrics([])
    assert m["count"] == 0
    assert m["hit_rate"] is None


def test_compute_slice_metrics_all_wins():
    items = _scored_items([10.0, 20.0, 30.0, 5.0, 15.0])
    m = compute_slice_metrics(items)
    assert m["count"] == 5
    assert m["scored_count"] == 5
    assert m["hit_rate"] == 1.0
    assert m["net_pnl"] == 80.0
    assert m["coverage"] == 1.0


def test_compute_slice_metrics_mixed():
    items = _scored_items([50.0, -20.0, 30.0, -10.0, 15.0])
    m = compute_slice_metrics(items)
    assert m["scored_count"] == 5
    assert m["hit_rate"] == 0.6
    assert m["net_pnl"] == 65.0
    assert m["expectancy"] is not None


def test_compute_slice_metrics_calibration_error():
    items = _scored_items([10.0, -5.0, 20.0, -3.0, 8.0], confidence=0.8)
    m = compute_slice_metrics(items)
    # hit_rate = 3/5 = 0.6, avg_confidence = 0.8, calibration = |0.8 - 0.6| = 0.2
    assert m["calibration_error"] == 0.2


def test_compute_slice_metrics_with_abstain():
    items = _scored_items([10.0, -5.0])
    items.append({"item_type": "abstain", "score_status": "unscored", "realized_pnl": None, "confidence": 0.2})
    m = compute_slice_metrics(items)
    assert m["count"] == 3
    assert m["scored_count"] == 2
    assert m["abstain_rate"] is not None
    assert abs(m["abstain_rate"] - 0.3333) < 0.01


# ── Build Slices ─────────────────────────────────────────────────────────────

def test_build_slices_from_items_produces_all_types():
    items = _scored_items([10.0, -5.0, 20.0])
    slices = build_slices_from_items(items)
    slice_types = {s["slice_type"] for s in slices}
    assert "overall" in slice_types
    assert "action_type" in slice_types
    assert "symbol" in slice_types
    assert "regime" in slice_types
    assert "confidence_bucket" in slice_types


def test_build_slices_overall_matches_direct():
    items = _scored_items([10.0, -5.0, 20.0, -3.0, 15.0])
    slices = build_slices_from_items(items)
    overall = next(s for s in slices if s["slice_type"] == "overall")
    direct = compute_slice_metrics(items)
    assert overall["metrics"]["count"] == direct["count"]
    assert overall["metrics"]["hit_rate"] == direct["hit_rate"]


# ── Persistence ──────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_evaluation_run_lifecycle(_isolated_db, anyio_backend):
    await init_db()

    eval_id = await create_evaluation_run(
        candidate_type="prompt_version",
        candidate_key="v2",
        baseline_key="v1",
        evaluation_mode="stored_context",
        window_start="2026-03-01",
        window_end="2026-03-20",
        request_json={"test": True},
    )
    assert eval_id

    run = await get_evaluation_run(eval_id)
    assert run is not None
    assert run["status"] == "running"
    assert run["candidate_key"] == "v2"

    await complete_evaluation_run(eval_id, summary={"hit_rate": 0.65, "net_pnl": 150.0})
    run = await get_evaluation_run(eval_id)
    assert run["status"] == "completed"
    assert run["summary"]["hit_rate"] == 0.65


@pytest.mark.anyio
async def test_evaluation_slices_persist(_isolated_db, anyio_backend):
    await init_db()

    eval_id = await create_evaluation_run(
        candidate_type="rule_snapshot", candidate_key="rule-1",
        evaluation_mode="rule_backtest", request_json={},
    )

    slices = [
        {"slice_type": "overall", "slice_key": "all", "metrics": {"count": 10, "hit_rate": 0.7}},
        {"slice_type": "symbol", "slice_key": "AAPL", "metrics": {"count": 5, "hit_rate": 0.8}},
    ]
    await save_evaluation_slices(eval_id, slices)

    loaded = await get_evaluation_slices(eval_id)
    assert len(loaded) == 2
    assert loaded[0]["slice_type"] == "overall"
    assert loaded[0]["count"] == 10


@pytest.mark.anyio
async def test_compare_evaluations(_isolated_db, anyio_backend):
    await init_db()

    base_id = await create_evaluation_run(
        candidate_type="prompt_version", candidate_key="v1",
        evaluation_mode="stored_context", request_json={},
    )
    await save_evaluation_slices(base_id, [
        {"slice_type": "overall", "slice_key": "all", "metrics": {"count": 10, "hit_rate": 0.5}},
    ])
    await complete_evaluation_run(base_id, summary={"hit_rate": 0.5})

    cand_id = await create_evaluation_run(
        candidate_type="prompt_version", candidate_key="v2",
        evaluation_mode="stored_context", request_json={},
    )
    await save_evaluation_slices(cand_id, [
        {"slice_type": "overall", "slice_key": "all", "metrics": {"count": 10, "hit_rate": 0.7}},
    ])
    await complete_evaluation_run(cand_id, summary={"hit_rate": 0.7})

    comparison = await compare_evaluations(base_id, cand_id)
    assert comparison["baseline"] is not None
    assert comparison["candidate"] is not None
    assert len(comparison["baseline_slices"]) == 1
    assert len(comparison["candidate_slices"]) == 1


@pytest.mark.anyio
async def test_get_evaluation_runs_list(_isolated_db, anyio_backend):
    await init_db()

    await create_evaluation_run(
        candidate_type="prompt_version", candidate_key="v1",
        evaluation_mode="stored_context", request_json={},
    )
    await create_evaluation_run(
        candidate_type="rule_snapshot", candidate_key="rule-1",
        evaluation_mode="rule_backtest", request_json={},
    )

    runs = await get_evaluation_runs(limit=10)
    assert len(runs) == 2
