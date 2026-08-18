import pytest

import config
from database import init_db
from ai_walk_forward import create_walk_forward_run, complete_walk_forward_run, get_walk_forward_run


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
