from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.contracts import Candidate, CompletedBar, EngineMode, FixedUniverse, RuleCondition, Run, ScannerRun, StrategySpec, fingerprint_payload
from trading.data import CandidateRepository, DuplicateRecordError, FeatureRepository, RawBarRepository, RunRepository, ScannerRunRepository
from trading.replay import PersistedReplayRepository


def _spec() -> StrategySpec:
    return StrategySpec(
        name="Replay strategy",
        universe=FixedUniverse(symbols=("AAPL",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value=Decimal("1.5")),),
        rationale="Deterministic persistence fixture.",
    )


def _bar(*, close: str = "100") -> CompletedBar:
    start = datetime(2026, 8, 27, 13, 30, tzinfo=timezone.utc)
    return CompletedBar(
        con_id=123,
        interval_start_utc=start,
        interval_end_utc=start + timedelta(minutes=5),
        open="99",
        high=max(Decimal("101"), Decimal(close)),
        low="98",
        close=close,
        volume="10000",
        rth=True,
        source="ibkr",
        data_mode="historical",
    )


@pytest.mark.anyio
async def test_canonical_data_repositories_are_deterministic_and_duplicate_safe(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db
    from trading.storage.repository import create_strategy

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "data.db"))
    await init_db()
    strategy = await create_strategy(spec=_spec(), owner_id="owner-a", actor="owner-a")
    run = Run(
        owner_id="owner-a",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=1,
        policy_fingerprints={"rules": "a" * 64},
        session_date=date(2026, 8, 27),
        mode=EngineMode.SHADOW,
    )
    await RunRepository().save(run)
    parameters = {"scan_code": "HOT_BY_VOLUME", "limit": 50}
    scanner_run = ScannerRun(
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=datetime(2026, 8, 27, 13, 45, tzinfo=timezone.utc),
    )
    scanner_repository = ScannerRunRepository()
    await scanner_repository.save(scanner_run)
    candidate = Candidate(
        scanner_run_id=scanner_run.id,
        con_id=123,
        symbol="AAPL",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )
    candidate_repository = CandidateRepository()
    await candidate_repository.save(candidate)
    assert (await candidate_repository.list_for_scanner(scanner_run.id))[0] == candidate

    bar_repository = RawBarRepository()
    bar_id = await bar_repository.attach_to_candidate(
        run_id=run.id, candidate_id=candidate.id, bar=_bar()
    )
    assert await bar_repository.save(_bar()) == bar_id
    assert await bar_repository.get(bar_id) == _bar()
    with pytest.raises(DuplicateRecordError):
        await bar_repository.attach_to_candidate(
            run_id=run.id, candidate_id=candidate.id, bar=_bar()
        )

    feature_repository = FeatureRepository()
    feature = await feature_repository.save(
        candidate_id=candidate.id,
        input_fingerprint=bar_id,
        features={"rvol": "2.0", "close": "100"},
    )
    assert feature.fingerprint == feature.fingerprint
    with pytest.raises(DuplicateRecordError):
        await feature_repository.save(
            candidate_id=candidate.id,
            input_fingerprint=bar_id,
            features={"rvol": "2.0", "close": "100"},
        )

    replay_repository = PersistedReplayRepository()
    first = await replay_repository.load(run_id=run.id, candidate_id=candidate.id)
    second = await replay_repository.load(run_id=run.id, candidate_id=candidate.id)
    assert first.dataset_fingerprint == second.dataset_fingerprint
    assert first.source_sha == run.source_sha
    assert [item.fingerprint for item in first.bars] == [bar_id]
    assert first.features == ({"close": "100", "rvol": "2.0"},)


@pytest.mark.anyio
async def test_repositories_reject_bad_fingerprints_and_cross_run_attachments(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db
    from trading.storage.repository import create_strategy

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "validation.db"))
    await init_db()
    strategy = await create_strategy(spec=_spec(), owner_id="owner-a", actor="owner-a")
    run = Run(
        owner_id="owner-a",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=1,
        policy_fingerprints={"rules": "a" * 64},
        session_date=date(2026, 8, 27),
        mode=EngineMode.SHADOW,
    )
    await RunRepository().save(run)
    scanner_run = ScannerRun(
        run_id=run.id,
        parameters={"scan_code": "HOT_BY_VOLUME"},
        parameters_fingerprint="b" * 64,
        status="completed",
        started_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="fingerprint"):
        await ScannerRunRepository().save(scanner_run)
    scanner_run = scanner_run.model_copy(
        update={"parameters_fingerprint": fingerprint_payload(scanner_run.parameters)}
    )
    await ScannerRunRepository().save(scanner_run)
    candidate = Candidate(
        scanner_run_id=scanner_run.id,
        con_id=123,
        symbol="AAPL",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )
    await CandidateRepository().save(candidate)
    with pytest.raises(ValueError, match="does not belong"):
        await RawBarRepository().attach_to_candidate(
            run_id="different-run", candidate_id=candidate.id, bar=_bar()
        )
