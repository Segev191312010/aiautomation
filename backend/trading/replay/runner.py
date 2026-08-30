"""Replay persisted market data without external providers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json

from db.core import get_db
from trading.contracts import CompletedBar, fingerprint_payload


@dataclass(frozen=True)
class ReplayBar:
    bar: CompletedBar
    fingerprint: str


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    candidate_id: str
    source_sha: str
    bars: tuple[ReplayBar, ...]
    features: tuple[dict[str, object], ...]
    dataset_fingerprint: str


def _bar_from_row(row: tuple[object, ...]) -> CompletedBar:
    payload = json.loads(str(row[8]))
    bar = CompletedBar(
        con_id=int(row[0]),
        interval_start_utc=datetime.fromisoformat(str(row[1])),
        interval_end_utc=datetime.fromisoformat(str(row[2])),
        timeframe=str(row[3]),
        open=payload["open"],
        high=payload["high"],
        low=payload["low"],
        close=payload["close"],
        volume=payload["volume"],
        rth=bool(row[6]),
        source=str(row[4]),
        data_mode=str(row[5]),
    )
    if int(row[7]) != 1:
        raise ValueError("replay refuses incomplete persisted bars")
    if bar.fingerprint() != str(row[9]):
        raise ValueError("replay refuses a bar fingerprint mismatch")
    return bar


class PersistedReplayRepository:
    """Loads a replay dataset strictly from the canonical SQLite tables."""

    async def load(self, *, run_id: str, candidate_id: str) -> ReplayResult:
        async with get_db() as db:
            async with db.execute(
                "SELECT source_sha FROM autotrader_runs WHERE id=?", (run_id,)
            ) as cursor:
                run = await cursor.fetchone()
            if run is None:
                raise LookupError("replay run does not exist")
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
                    raise ValueError("replay candidate does not belong to run")
            async with db.execute(
                """
                SELECT b.con_id, b.interval_start_utc, b.interval_end_utc, b.timeframe,
                       b.source, b.data_mode, b.rth, b.is_complete, b.ohlcv_json, b.fingerprint
                FROM autotrader_run_bars rb
                JOIN autotrader_market_bars b ON b.id=rb.bar_id
                WHERE rb.run_id=? AND rb.candidate_id=?
                ORDER BY b.interval_start_utc, b.fingerprint
                """,
                (run_id, candidate_id),
            ) as cursor:
                bar_rows = await cursor.fetchall()
            async with db.execute(
                """
                SELECT fs.features_json, fs.input_fingerprint, fs.fingerprint
                FROM autotrader_feature_snapshots fs
                JOIN autotrader_candidates c ON c.id=fs.candidate_id
                JOIN autotrader_scanner_runs sr ON sr.id=c.scanner_run_id
                WHERE fs.candidate_id=? AND sr.run_id=?
                ORDER BY fs.fingerprint
                """,
                (candidate_id, run_id),
            ) as cursor:
                feature_rows = await cursor.fetchall()

        bars = tuple(
            ReplayBar(bar=bar, fingerprint=bar.fingerprint())
            for bar in (_bar_from_row(row) for row in bar_rows)
        )
        bar_fingerprints = [item.fingerprint for item in bars]
        if len(bar_fingerprints) != len(set(bar_fingerprints)):
            raise ValueError("replay dataset contains duplicate bars")
        features: list[dict[str, object]] = []
        for feature_json, input_fingerprint, fingerprint in feature_rows:
            payload = json.loads(str(feature_json))
            expected = fingerprint_payload(
                {
                    "candidate_id": candidate_id,
                    "input_fingerprint": str(input_fingerprint),
                    "features": payload,
                }
            )
            if expected != str(fingerprint):
                raise ValueError("replay refuses a feature fingerprint mismatch")
            features.append(payload)
        dataset_fingerprint = fingerprint_payload(
            {
                "run_id": run_id,
                "candidate_id": candidate_id,
                "source_sha": str(run[0]),
                "bars": bar_fingerprints,
                "features": [
                    {"fingerprint": str(row[2]), "payload": payload}
                    for row, payload in zip(feature_rows, features, strict=True)
                ],
            }
        )
        return ReplayResult(
            run_id=run_id,
            candidate_id=candidate_id,
            source_sha=str(run[0]),
            bars=bars,
            features=tuple(features),
            dataset_fingerprint=dataset_fingerprint,
        )


__all__ = ["PersistedReplayRepository", "ReplayBar", "ReplayResult"]
