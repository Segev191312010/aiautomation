"""Canonical repositories for the persisted V1 data plane.

These adapters only persist validated domain contracts.  They do not fetch
market data or invoke strategy, broker, or model providers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Mapping

import aiosqlite

from db.core import get_db, transaction
from trading.contracts import (
    Candidate,
    CompletedBar,
    RawScannerResult,
    Run,
    ScannerRun,
    fingerprint_payload,
)


class DuplicateRecordError(ValueError):
    """Raised when a natural key already contains a different record."""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _timestamp(value: datetime | None = None) -> str:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return observed.astimezone(timezone.utc).isoformat()


def _bar_payload(bar: CompletedBar) -> dict[str, object]:
    return {
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
    }


@dataclass(frozen=True)
class FeatureSnapshot:
    candidate_id: str
    input_fingerprint: str
    features: Mapping[str, object]
    fingerprint: str
    created_at: datetime


class RunRepository:
    async def save(self, run: Run) -> Run:
        async with transaction() as db:
            try:
                await db.execute(
                    """
                    INSERT INTO autotrader_runs
                    (id, owner_id, source_sha, strategy_id, strategy_version, session_date,
                     mode, status, policy_fingerprints_json, started_at, closed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.owner_id,
                        run.source_sha,
                        run.strategy_id,
                        run.strategy_version,
                        run.session_date.isoformat(),
                        run.mode.value,
                        run.status,
                        _json(run.policy_fingerprints),
                        _timestamp(run.started_at) if run.started_at else None,
                        _timestamp(run.closed_at) if run.closed_at else None,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise DuplicateRecordError(f"run already exists: {run.id}") from exc
        return run

    async def get(self, *, run_id: str, owner_id: str) -> Run | None:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, owner_id, source_sha, strategy_id, strategy_version,
                       session_date, mode, status, policy_fingerprints_json,
                       started_at, closed_at
                FROM autotrader_runs WHERE id=? AND owner_id=?
                """,
                (run_id, owner_id),
            ) as cursor:
                row = await cursor.fetchone()
        return self._from_row(row) if row else None

    async def list(self, *, owner_id: str, limit: int = 100) -> list[Run]:
        safe_limit = min(max(limit, 1), 500)
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, owner_id, source_sha, strategy_id, strategy_version,
                       session_date, mode, status, policy_fingerprints_json,
                       started_at, closed_at
                FROM autotrader_runs WHERE owner_id=?
                ORDER BY COALESCE(started_at, id) DESC LIMIT ?
                """,
                (owner_id, safe_limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> Run:
        (
            run_id,
            owner_id,
            source_sha,
            strategy_id,
            strategy_version,
            session_date,
            mode,
            status,
            policy_json,
            started_at,
            closed_at,
        ) = row
        return Run(
            id=str(run_id),
            owner_id=str(owner_id),
            source_sha=str(source_sha),
            strategy_id=str(strategy_id),
            strategy_version=int(strategy_version),
            session_date=datetime.fromisoformat(str(session_date)).date(),
            mode=str(mode),
            status=str(status),
            policy_fingerprints=json.loads(str(policy_json)),
            started_at=datetime.fromisoformat(str(started_at)) if started_at else None,
            closed_at=datetime.fromisoformat(str(closed_at)) if closed_at else None,
        )


class ScannerRunRepository:
    async def save(self, scanner_run: ScannerRun) -> ScannerRun:
        canonical_parameters = json.loads(_json(scanner_run.parameters))
        if scanner_run.parameters_fingerprint != fingerprint_payload(canonical_parameters):
            raise ValueError("scanner parameters fingerprint does not match parameters")
        async with transaction() as db:
            try:
                await db.execute(
                    """
                    INSERT INTO autotrader_scanner_runs
                    (id, run_id, parameters_json, parameters_fingerprint, status, started_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scanner_run.id,
                        scanner_run.run_id,
                        _json(scanner_run.parameters),
                        scanner_run.parameters_fingerprint,
                        scanner_run.status,
                        _timestamp(scanner_run.started_at),
                        _timestamp(scanner_run.completed_at) if scanner_run.completed_at else None,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise DuplicateRecordError(f"scanner run already exists: {scanner_run.id}") from exc
        return scanner_run

    async def get(self, scanner_run_id: str) -> ScannerRun | None:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, run_id, parameters_json, parameters_fingerprint, status,
                       started_at, completed_at
                FROM autotrader_scanner_runs WHERE id=?
                """,
                (scanner_run_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return ScannerRun(
            id=str(row[0]),
            run_id=str(row[1]),
            parameters=json.loads(str(row[2])),
            parameters_fingerprint=str(row[3]),
            status=str(row[4]),
            started_at=datetime.fromisoformat(str(row[5])),
            completed_at=datetime.fromisoformat(str(row[6])) if row[6] else None,
        )


class RawScannerResultRepository:
    """Data-plane facade for immutable zero-based scanner callback rows."""

    async def save(self, result: RawScannerResult) -> RawScannerResult:
        from trading.storage.shadow_ledger import SQLiteShadowLedger

        return await SQLiteShadowLedger().save_raw_scanner_result(result)

    async def save_many(
        self, results: tuple[RawScannerResult, ...]
    ) -> tuple[RawScannerResult, ...]:
        from trading.storage.shadow_ledger import SQLiteShadowLedger

        return await SQLiteShadowLedger().save_raw_scanner_results(results)

    async def list_for_scanner(
        self, scanner_run_id: str
    ) -> tuple[RawScannerResult, ...]:
        from trading.storage.shadow_ledger import SQLiteShadowLedger

        return await SQLiteShadowLedger().list_raw_scanner_results(scanner_run_id)


class CandidateRepository:
    async def save(self, candidate: Candidate) -> Candidate:
        contract = candidate.model_dump(mode="json")
        async with transaction() as db:
            try:
                await db.execute(
                    """
                    INSERT INTO autotrader_candidates
                    (id, scanner_run_id, con_id, symbol, rank, contract_json,
                     admission_status, rejection_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.id,
                        candidate.scanner_run_id,
                        candidate.con_id,
                        candidate.symbol,
                        candidate.rank,
                        _json(contract),
                        candidate.admission_status,
                        candidate.rejection_reason,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise DuplicateRecordError(f"candidate natural key already exists: {candidate.id}") from exc
        return candidate

    async def list_for_scanner(self, scanner_run_id: str) -> list[Candidate]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, scanner_run_id, con_id, symbol, contract_json,
                       rank, admission_status, rejection_reason
                FROM autotrader_candidates WHERE scanner_run_id=? ORDER BY rank
                """,
                (scanner_run_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        result: list[Candidate] = []
        for row in rows:
            contract = json.loads(str(row[4]))
            contract.update(
                {
                    "id": str(row[0]),
                    "scanner_run_id": str(row[1]),
                    "con_id": int(row[2]),
                    "symbol": str(row[3]),
                    "rank": int(row[5]),
                    "admission_status": str(row[6]),
                    "rejection_reason": row[7],
                }
            )
            result.append(Candidate.model_validate(contract))
        return result

    async def list_for_run(self, *, run_id: str, owner_id: str) -> list[Candidate]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT c.id, c.scanner_run_id, c.con_id, c.symbol, c.contract_json,
                       c.rank, c.admission_status, c.rejection_reason
                FROM autotrader_candidates c
                JOIN autotrader_scanner_runs sr ON sr.id=c.scanner_run_id
                JOIN autotrader_runs r ON r.id=sr.run_id
                WHERE sr.run_id=? AND r.owner_id=? ORDER BY c.rank
                """,
                (run_id, owner_id),
            ) as cursor:
                rows = await cursor.fetchall()
        result: list[Candidate] = []
        for row in rows:
            contract = json.loads(str(row[4]))
            contract.update(
                {
                    "id": str(row[0]),
                    "scanner_run_id": str(row[1]),
                    "con_id": int(row[2]),
                    "symbol": str(row[3]),
                    "rank": int(row[5]),
                    "admission_status": str(row[6]),
                    "rejection_reason": row[7],
                }
            )
            result.append(Candidate.model_validate(contract))
        return result


class RawBarRepository:
    async def _save_on_db(self, db: aiosqlite.Connection, bar: CompletedBar) -> str:
        bar_id = bar.fingerprint()
        try:
            await db.execute(
                """
                INSERT INTO autotrader_market_bars
                (id, con_id, interval_start_utc, interval_end_utc, timeframe, source,
                 data_mode, rth, is_complete, ohlcv_json, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    bar_id,
                    bar.con_id,
                    _timestamp(bar.interval_start_utc),
                    _timestamp(bar.interval_end_utc),
                    bar.timeframe,
                    bar.source,
                    bar.data_mode,
                    int(bar.rth),
                    _json(_bar_payload(bar)),
                    bar_id,
                ),
            )
        except aiosqlite.IntegrityError:
            async with db.execute(
                """
                SELECT id, fingerprint FROM autotrader_market_bars
                WHERE con_id=? AND interval_start_utc=? AND timeframe=?
                  AND source=? AND data_mode=?
                """,
                (
                    bar.con_id,
                    _timestamp(bar.interval_start_utc),
                    bar.timeframe,
                    bar.source,
                    bar.data_mode,
                ),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is None or str(existing[1]) != bar_id:
                raise DuplicateRecordError(
                    "bar natural key already exists with a different fingerprint"
                )
        return bar_id

    async def save(self, bar: CompletedBar) -> str:
        async with transaction() as db:
            return await self._save_on_db(db, bar)

    async def attach_to_candidate(self, *, run_id: str, candidate_id: str, bar: CompletedBar) -> str:
        async with transaction() as db:
            async with db.execute(
                """
                SELECT 1
                FROM autotrader_candidates c
                JOIN autotrader_scanner_runs sr ON sr.id=c.scanner_run_id
                WHERE c.id=? AND sr.run_id=?
                """,
                (candidate_id, run_id),
            ) as cursor:
                if await cursor.fetchone() is None:
                    raise ValueError("candidate does not belong to run")
            bar_id = await self._save_on_db(db, bar)
            try:
                await db.execute(
                    "INSERT INTO autotrader_run_bars (run_id, candidate_id, bar_id) VALUES (?, ?, ?)",
                    (run_id, candidate_id, bar_id),
                )
            except aiosqlite.IntegrityError as exc:
                raise DuplicateRecordError("bar is already attached to candidate") from exc
        return bar_id

    async def get(self, bar_id: str) -> CompletedBar | None:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT con_id, interval_start_utc, interval_end_utc, timeframe, source,
                       data_mode, rth, is_complete, ohlcv_json, fingerprint
                FROM autotrader_market_bars WHERE id=?
                """,
                (bar_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        if int(row[7]) != 1:
            raise ValueError("persisted market bar is not complete")
        payload = json.loads(str(row[8]))
        bar = CompletedBar(
            con_id=int(row[0]),
            interval_start_utc=datetime.fromisoformat(str(row[1])),
            interval_end_utc=datetime.fromisoformat(str(row[2])),
            timeframe=str(row[3]),
            open=Decimal(str(payload["open"])),
            high=Decimal(str(payload["high"])),
            low=Decimal(str(payload["low"])),
            close=Decimal(str(payload["close"])),
            volume=Decimal(str(payload["volume"])),
            rth=bool(row[6]),
            source=str(row[4]),
            data_mode=str(row[5]),
        )
        if bar.fingerprint() != str(row[9]):
            raise ValueError("persisted market bar fingerprint mismatch")
        return bar


class FeatureRepository:
    async def save(
        self,
        *,
        candidate_id: str,
        input_fingerprint: str,
        features: Mapping[str, object],
        created_at: datetime | None = None,
    ) -> FeatureSnapshot:
        if not input_fingerprint:
            raise ValueError("input_fingerprint is required")
        normalized = json.loads(_json(dict(features)))
        fingerprint = fingerprint_payload(
            {
                "candidate_id": candidate_id,
                "input_fingerprint": input_fingerprint,
                "features": normalized,
            }
        )
        created = created_at or datetime.now(timezone.utc)
        snapshot = FeatureSnapshot(
            candidate_id=candidate_id,
            input_fingerprint=input_fingerprint,
            features=normalized,
            fingerprint=fingerprint,
            created_at=created,
        )
        async with transaction() as db:
            async with db.execute(
                """
                SELECT fingerprint FROM autotrader_feature_snapshots
                WHERE candidate_id=? AND input_fingerprint=?
                """,
                (candidate_id, input_fingerprint),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                if str(existing[0]) == fingerprint:
                    raise DuplicateRecordError("feature snapshot already exists")
                raise DuplicateRecordError(
                    "feature input already has a different fingerprint"
                )
            try:
                await db.execute(
                    """
                    INSERT INTO autotrader_feature_snapshots
                    (id, candidate_id, input_fingerprint, fingerprint, features_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        candidate_id,
                        input_fingerprint,
                        fingerprint,
                        _json(normalized),
                        _timestamp(created),
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise DuplicateRecordError("feature snapshot already exists") from exc
        return snapshot


RunScopedBarRepository = RawBarRepository

__all__ = [
    "CandidateRepository",
    "DuplicateRecordError",
    "FeatureRepository",
    "FeatureSnapshot",
    "RawBarRepository",
    "RawScannerResultRepository",
    "RawScannerRepository",
    "RunRepository",
    "RunScopedBarRepository",
    "ScannerRunRepository",
]


RawScannerRepository = RawScannerResultRepository
