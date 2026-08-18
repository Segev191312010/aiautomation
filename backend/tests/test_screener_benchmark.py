import importlib.util
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).parents[1] / "scripts" / "screener_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("screener_benchmark", _SCRIPT)
assert _SPEC and _SPEC.loader
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


@pytest.mark.anyio
async def test_offline_benchmark_is_network_free_and_integrity_checked():
    result = await benchmark.run_offline_benchmark(
        symbol_count=5, bars=230, concurrency=2, repeats=2, seed=7
    )

    assert result["network_calls"] == 0
    assert result["symbol_count"] == 5
    assert result["concurrency"] == 2
    assert result["matched_count_consistent"] is True
    assert result["result_integrity"] == {
        "unique_symbols": True,
        "finite_scores": True,
        "sorted_by_score": True,
        "counts_consistent": True,
    }
    assert len(result["durations_ms"]) == 2
    assert result["min_ms"] <= result["median_ms"] <= result["max_ms"]


@pytest.mark.anyio
async def test_offline_benchmark_rejects_short_fixtures():
    with pytest.raises(ValueError, match="bars must be >= 220"):
        await benchmark.run_offline_benchmark(symbol_count=1, bars=10)
