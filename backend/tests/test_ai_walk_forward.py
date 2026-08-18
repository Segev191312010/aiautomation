import pytest

import config
from database import init_db
from ai_walk_forward import (
    build_walk_forward_folds,
    create_walk_forward_run,
    complete_walk_forward_run,
    get_walk_forward_run,
    run_walk_forward_evaluation,
)


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    path = str(tmp_path / "walk-forward.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", path)


def _fold(index=0):
    return {
        "fold_index": index,
        "train_start": "2026-01-01T00:00:00+00:00",
        "train_end": "2026-02-01T00:00:00+00:00",
        "test_start": "2026-02-01T00:00:00+00:00",
        "test_end": "2026-03-01T00:00:00+00:00",
        "train_count": 100,
        "test_count": 20,
        "metrics": {"hit_rate": 0.6},
        "evidence": {"dataset_hash": "abc"},
    }


def _items():
    return [
        {"id": "a", "created_at": "2026-01-01T00:00:00+00:00", "score_status": "direct_realized", "realized_pnl": 2.0},
        {"id": "b", "created_at": "2026-01-02T00:00:00+00:00", "score_status": "direct_realized", "realized_pnl": -1.0},
        {"id": "c", "created_at": "2026-01-03T00:00:00+00:00", "score_status": "direct_realized", "realized_pnl": 3.0},
        {"id": "d", "created_at": "2026-01-04T00:00:00+00:00", "score_status": "direct_realized", "realized_pnl": 4.0},
        {"id": "e", "created_at": "2026-01-05T00:00:00+00:00", "score_status": "direct_realized", "realized_pnl": -2.0},
    ]


def test_build_folds_is_chronological_and_fingerprinted():
    folds, fingerprint = build_walk_forward_folds(_items(), train_size=2, test_size=1)
    assert len(folds) == 3
    assert len(fingerprint) == 64
    assert folds[0]["train_end"] == folds[0]["test_start"]
    assert folds[0]["metrics"]["test"]["net_pnl"] == 3.0
    assert folds[0]["evidence"]["lookahead_check"] == "max_train_timestamp < min_test_timestamp"


def test_build_folds_rejects_naive_or_insufficient_inputs():
    with pytest.raises(ValueError, match="timezone"):
        build_walk_forward_folds([{**_items()[0], "created_at": "2026-01-01T00:00:00"}], train_size=1, test_size=1)
    with pytest.raises(ValueError, match="not enough"):
        build_walk_forward_folds(_items()[:2], train_size=2, test_size=1)


@pytest.mark.anyio
async def test_run_adapter_persists_completed_evidence(_isolated_db, anyio_backend):
    await init_db()
    run_id = await run_walk_forward_evaluation(
        candidate_type="prompt_version", candidate_key="deterministic-v1",
        items=_items(), train_size=2, test_size=1,
        request={"fixture": "unit-test"},
    )
    result = await get_walk_forward_run(run_id)
    assert result["status"] == "completed"
    assert result["summary"]["performance_claim"] is False
    assert len(result["folds"]) == 3
    assert result["folds"][0]["evidence"]["dataset_sha256"] == result["summary"]["dataset_sha256"]


@pytest.mark.anyio
async def test_walk_forward_persists_boundaries_and_results(_isolated_db, anyio_backend):
    await init_db()
    run_id = await create_walk_forward_run(
        candidate_type="prompt_version", candidate_key="v2",
        folds=[_fold(0), {**_fold(1), "train_start": "2026-02-01T00:00:00+00:00", "train_end": "2026-03-01T00:00:00+00:00", "test_start": "2026-03-01T00:00:00+00:00", "test_end": "2026-04-01T00:00:00+00:00"}],
        request={"window": "rolling", "purge": "0d"},
    )
    await complete_walk_forward_run(run_id, summary={"folds": 2, "test_hit_rate": 0.55})
    result = await get_walk_forward_run(run_id)
    assert result["status"] == "completed"
    assert len(result["folds"]) == 2
    assert result["folds"][0]["evidence"]["dataset_hash"] == "abc"


@pytest.mark.anyio
async def test_walk_forward_rejects_overlap_and_empty_folds(_isolated_db, anyio_backend):
    await init_db()
    with pytest.raises(ValueError, match="at least one"):
        await create_walk_forward_run(candidate_type="model", candidate_key="x", folds=[], request={})
    bad = _fold()
    bad["test_start"] = "2026-01-15T00:00:00+00:00"
    with pytest.raises(ValueError, match="boundaries"):
        await create_walk_forward_run(candidate_type="model", candidate_key="x", folds=[bad], request={})
