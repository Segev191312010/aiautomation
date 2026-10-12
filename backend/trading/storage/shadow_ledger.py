"""Append-only SQLite repositories for real-data shadow evidence."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import json

import aiosqlite

from db.core import get_db, transaction
from trading.contracts import (
    AccountSnapshot,
    ModelledFill,
    QuoteSnapshot,
    RawScannerResult,
    ShadowReconciliationReport,
    ShadowSessionReceipt,
)


class ShadowLedgerConflict(ValueError):
    """Raised when an immutable shadow identity has conflicting contents."""


def _dump(value: object) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.isoformat()


async def _assert_candidate_run(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    candidate_id: str,
) -> None:
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
            raise ValueError("candidate does not belong to the shadow run")


class SQLiteShadowLedger:
    """Persists immutable scanner, quote, account, fill, and receipt evidence."""

    async def save_raw_scanner_result(
        self, result: RawScannerResult
    ) -> RawScannerResult:
        await self.save_raw_scanner_results((result,))
        return result

    async def save_raw_scanner_results(
        self, results: Sequence[RawScannerResult]
    ) -> tuple[RawScannerResult, ...]:
        records = tuple(results)
        if len(records) > 50:
            raise ValueError("a scanner run may persist at most 50 raw results")
        scanner_ids = {record.scanner_run_id for record in records}
        if len(scanner_ids) > 1:
            raise ValueError("raw scanner results must belong to one scanner run")
        if len({record.raw_rank for record in records}) != len(records):
            raise ValueError("raw scanner ranks must be unique")
        if len({record.con_id for record in records}) != len(records):
            raise ValueError("raw scanner conIds must be unique")

        async with transaction() as db:
            for record in records:
                payload = _dump(record)
                fingerprint = record.fingerprint()
                try:
                    await db.execute(
                        """
                        INSERT INTO autotrader_raw_scanner_results
                        (id, scanner_run_id, request_id, raw_rank, normalized_rank,
                         con_id, payload_json, fingerprint, received_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fingerprint,
                            record.scanner_run_id,
                            record.request_id,
                            record.raw_rank,
                            record.normalized_rank,
                            record.con_id,
                            payload,
                            fingerprint,
                            _timestamp(record.received_at),
                        ),
                    )
                except aiosqlite.IntegrityError as exc:
                    async with db.execute(
                        """
                        SELECT payload_json, fingerprint
                        FROM autotrader_raw_scanner_results
                        WHERE scanner_run_id=? AND (raw_rank=? OR con_id=?)
                        """,
                        (record.scanner_run_id, record.raw_rank, record.con_id),
                    ) as cursor:
                        existing = await cursor.fetchone()
                    if existing is None or (
                        str(existing[0]) != payload
                        or str(existing[1]) != fingerprint
                    ):
                        raise ShadowLedgerConflict(
                            "raw scanner natural key has conflicting contents"
                        ) from exc
        return records

    async def list_raw_scanner_results(
        self, scanner_run_id: str
    ) -> tuple[RawScannerResult, ...]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT payload_json, fingerprint
                FROM autotrader_raw_scanner_results
                WHERE scanner_run_id=? ORDER BY raw_rank
                """,
                (scanner_run_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        records: list[RawScannerResult] = []
        for payload, fingerprint in rows:
            record = RawScannerResult.model_validate(json.loads(str(payload)))
            if record.fingerprint() != str(fingerprint):
                raise ValueError("persisted raw scanner fingerprint mismatch")
            records.append(record)
        return tuple(records)

    async def save_quote(
        self,
        *,
        run_id: str,
        candidate_id: str,
        quote: QuoteSnapshot,
    ) -> QuoteSnapshot:
        payload = _dump(quote)
        fingerprint = quote.fingerprint()
        async with transaction() as db:
            await _assert_candidate_run(db, run_id=run_id, candidate_id=candidate_id)
            try:
                await db.execute(
                    """
                    INSERT INTO autotrader_quote_snapshots
                    (id, run_id, candidate_id, con_id, observed_at, payload_json,
                     fingerprint)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        run_id,
                        candidate_id,
                        quote.con_id,
                        _timestamp(quote.observed_at),
                        payload,
                        fingerprint,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                async with db.execute(
                    """
                    SELECT payload_json, fingerprint
                    FROM autotrader_quote_snapshots
                    WHERE run_id=? AND candidate_id=?
                    """,
                    (run_id, candidate_id),
                ) as cursor:
                    existing = await cursor.fetchone()
                if existing is None or (
                    str(existing[0]) != payload
                    or str(existing[1]) != fingerprint
                ):
                    raise ShadowLedgerConflict(
                        "candidate quote already has conflicting contents"
                    ) from exc
        return quote

    async def list_quotes(self, run_id: str) -> tuple[QuoteSnapshot, ...]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT payload_json, fingerprint
                FROM autotrader_quote_snapshots
                WHERE run_id=? ORDER BY observed_at, candidate_id
                """,
                (run_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        quotes: list[QuoteSnapshot] = []
        for payload, fingerprint in rows:
            quote = QuoteSnapshot.model_validate(json.loads(str(payload)))
            if quote.fingerprint() != str(fingerprint):
                raise ValueError("persisted quote fingerprint mismatch")
            quotes.append(quote)
        return tuple(quotes)

    async def save_account_snapshot(
        self,
        snapshot: AccountSnapshot,
        *,
        run_id: str | None = None,
    ) -> AccountSnapshot:
        payload = _dump(snapshot)
        try:
            async with transaction() as db:
                await db.execute(
                    """
                    INSERT INTO autotrader_account_snapshots
                    (id, run_id, paper_account_id, client_id, observed_at,
                     payload_json, fingerprint)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.fingerprint,
                        run_id,
                        snapshot.paper_account_id,
                        snapshot.client_id,
                        _timestamp(snapshot.observed_at),
                        payload,
                        snapshot.fingerprint,
                    ),
                )
        except aiosqlite.IntegrityError as exc:
            async with get_db() as db:
                async with db.execute(
                    """
                    SELECT run_id, payload_json FROM autotrader_account_snapshots
                    WHERE fingerprint=?
                    """,
                    (snapshot.fingerprint,),
                ) as cursor:
                    existing = await cursor.fetchone()
            if existing is None or (
                existing[0] != run_id or str(existing[1]) != payload
            ):
                raise ShadowLedgerConflict(
                    "account snapshot fingerprint has conflicting contents"
                ) from exc
        return snapshot

    async def list_account_snapshots(
        self, *, run_id: str | None = None
    ) -> tuple[AccountSnapshot, ...]:
        condition = "WHERE run_id=?" if run_id is not None else ""
        parameters = (run_id,) if run_id is not None else ()
        async with get_db() as db:
            async with db.execute(
                f"""
                SELECT payload_json, fingerprint
                FROM autotrader_account_snapshots {condition}
                ORDER BY observed_at, id
                """,
                parameters,
            ) as cursor:
                rows = await cursor.fetchall()
        snapshots: list[AccountSnapshot] = []
        for payload, fingerprint in rows:
            snapshot = AccountSnapshot.model_validate(json.loads(str(payload)))
            if snapshot.fingerprint != str(fingerprint):
                raise ValueError("persisted account snapshot fingerprint mismatch")
            snapshots.append(snapshot)
        return tuple(snapshots)

    async def save_reconciliation_report(
        self,
        report: ShadowReconciliationReport,
        *,
        run_id: str | None = None,
    ) -> ShadowReconciliationReport:
        payload = _dump(report)
        try:
            async with transaction() as db:
                await db.execute(
                    """
                    INSERT INTO autotrader_shadow_reconciliation_reports
                    (id, run_id, account_fingerprint, status, payload_json,
                     observed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report.id,
                        run_id,
                        report.account_fingerprint,
                        report.status,
                        payload,
                        _timestamp(report.observed_at),
                    ),
                )
        except aiosqlite.IntegrityError as exc:
            async with get_db() as db:
                async with db.execute(
                    """
                    SELECT run_id, payload_json
                    FROM autotrader_shadow_reconciliation_reports WHERE id=?
                    """,
                    (report.id,),
                ) as cursor:
                    existing = await cursor.fetchone()
            if existing is None or (
                existing[0] != run_id or str(existing[1]) != payload
            ):
                raise ShadowLedgerConflict(
                    "account reconciliation already has conflicting contents"
                ) from exc
        return report

    async def list_reconciliation_reports(
        self, *, run_id: str | None = None
    ) -> tuple[ShadowReconciliationReport, ...]:
        condition = "WHERE run_id=?" if run_id is not None else ""
        parameters = (run_id,) if run_id is not None else ()
        async with get_db() as db:
            async with db.execute(
                f"""
                SELECT id, account_fingerprint, status, payload_json, observed_at
                FROM autotrader_shadow_reconciliation_reports {condition}
                ORDER BY observed_at, id
                """,
                parameters,
            ) as cursor:
                rows = await cursor.fetchall()
        reports: list[ShadowReconciliationReport] = []
        for row in rows:
            report = ShadowReconciliationReport.model_validate(
                json.loads(str(row[3]))
            )
            expected = (
                report.id,
                report.account_fingerprint,
                report.status,
                _timestamp(report.observed_at),
            )
            actual = (str(row[0]), str(row[1]), str(row[2]), str(row[4]))
            if actual != expected:
                raise ValueError(
                    "persisted shadow reconciliation columns do not match its payload"
                )
            reports.append(report)
        return tuple(reports)

    async def save_modelled_fill(self, fill: ModelledFill) -> ModelledFill:
        payload = _dump(fill)
        fingerprint = fill.fingerprint()
        async with transaction() as db:
            await _assert_candidate_run(
                db,
                run_id=fill.run_id,
                candidate_id=fill.candidate_id,
            )
            try:
                await db.execute(
                    """
                    INSERT INTO autotrader_modelled_fills
                    (id, run_id, candidate_id, fill_fingerprint, payload_json,
                     filled_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.id,
                        fill.run_id,
                        fill.candidate_id,
                        fingerprint,
                        payload,
                        _timestamp(fill.filled_at),
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                async with db.execute(
                    """
                    SELECT payload_json, fill_fingerprint
                    FROM autotrader_modelled_fills WHERE id=?
                    """,
                    (fill.id,),
                ) as cursor:
                    existing = await cursor.fetchone()
                if existing is None or (
                    str(existing[0]) != payload
                    or str(existing[1]) != fingerprint
                ):
                    raise ShadowLedgerConflict(
                        "modelled fill identity has conflicting contents"
                    ) from exc
        return fill

    async def list_modelled_fills(
        self, run_id: str
    ) -> tuple[ModelledFill, ...]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT payload_json, fill_fingerprint
                FROM autotrader_modelled_fills
                WHERE run_id=? ORDER BY filled_at, id
                """,
                (run_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        fills: list[ModelledFill] = []
        for payload, fingerprint in rows:
            fill = ModelledFill.model_validate(json.loads(str(payload)))
            if fill.fingerprint() != str(fingerprint):
                raise ValueError("persisted modelled fill fingerprint mismatch")
            fills.append(fill)
        return tuple(fills)

    async def save_session_receipt(
        self, receipt: ShadowSessionReceipt
    ) -> ShadowSessionReceipt:
        payload = _dump(receipt)
        fingerprint = receipt.fingerprint()
        try:
            async with transaction() as db:
                await db.execute(
                    """
                    INSERT INTO autotrader_shadow_session_receipts
                    (id, run_id, source_sha, session_date, state,
                     evidence_eligible, receipt_fingerprint, payload_json,
                     created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.id,
                        receipt.run_id,
                        receipt.source_sha,
                        receipt.session_date.isoformat(),
                        receipt.state.value,
                        int(receipt.evidence_eligible),
                        fingerprint,
                        payload,
                        _timestamp(receipt.created_at),
                    ),
                )
        except aiosqlite.IntegrityError as exc:
            async with get_db() as db:
                async with db.execute(
                    """
                    SELECT payload_json, receipt_fingerprint
                    FROM autotrader_shadow_session_receipts WHERE id=?
                    """,
                    (receipt.id,),
                ) as cursor:
                    existing = await cursor.fetchone()
            if existing is None or (
                str(existing[0]) != payload
                or str(existing[1]) != fingerprint
            ):
                raise ShadowLedgerConflict(
                    "shadow receipt identity has conflicting contents"
                ) from exc
        return receipt

    async def list_session_receipts(
        self,
        source_sha: str,
        evidence_eligible_only: bool = False,
    ) -> tuple[ShadowSessionReceipt, ...]:
        eligibility = "AND evidence_eligible=1" if evidence_eligible_only else ""
        async with get_db() as db:
            async with db.execute(
                f"""
                SELECT payload_json, receipt_fingerprint
                FROM autotrader_shadow_session_receipts
                WHERE source_sha=? {eligibility}
                ORDER BY session_date, created_at, id
                """,
                (source_sha,),
            ) as cursor:
                rows = await cursor.fetchall()
        receipts: list[ShadowSessionReceipt] = []
        for payload, fingerprint in rows:
            receipt = ShadowSessionReceipt.model_validate(json.loads(str(payload)))
            if receipt.fingerprint() != str(fingerprint):
                raise ValueError("persisted shadow receipt fingerprint mismatch")
            receipts.append(receipt)
        return tuple(receipts)


ShadowLedgerRepository = SQLiteShadowLedger

__all__ = [
    "SQLiteShadowLedger",
    "ShadowLedgerConflict",
    "ShadowLedgerRepository",
]
