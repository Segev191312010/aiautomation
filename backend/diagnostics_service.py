"""
Diagnostics service orchestrating refresh runs, scoring, and query helpers.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import statistics
import time
from typing import Any, Callable

import pandas as pd

from config import cfg
from database import get_db
from diagnostics_catalog import catalog_rows
from diagnostics_scoring import (
    freshness_from_business_lag,
    freshness_from_intraday_age,
    safe_mean_std,
    score_from_z,
    state_from_score,
)
from diagnostics_sources import (
    fred_series,
    yahoo_history,
    yahoo_market_map_rows,
    yahoo_news_rss,
    yahoo_recommendation_mean,
)

log = logging.getLogger(__name__)

RecordSuccess = Callable[[str], None]
RecordFailure = Callable[[str, str], None]


@dataclass
class RefreshConflict:
    run_id: int
    locked_by: str
    lock_expires_at: int


class DiagnosticsService:
    def __init__(
        self,
        record_success: RecordSuccess | None = None,
        record_failure: RecordFailure | None = None,
    ) -> None:
        self._record_success = record_success or (lambda _source: None)
        self._record_failure = record_failure or (lambda _source, _error: None)
        self._refresh_tasks: dict[int, asyncio.Task] = {}
        self._map_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(cfg.ENABLE_MARKET_DIAGNOSTICS)

    async def ensure_catalog_seeded(self) -> None:
        rows = catalog_rows()
        if not rows:
            return
        async with get_db() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS diag_indicator_catalog (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    invert_sign INTEGER NOT NULL DEFAULT 0,
                    lookback_days INTEGER NOT NULL DEFAULT 365,
                    expected_lag_business_days INTEGER NOT NULL DEFAULT 0,
                    stale_warn_s REAL NULL,
                    stale_critical_s REAL NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    stage TEXT NOT NULL DEFAULT '3A',
                    sector_weight_json TEXT NOT NULL DEFAULT '{}',
                    heuristic_version TEXT NOT NULL DEFAULT '1.0.0',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            for row in rows:
                await db.execute(
                    """
                    INSERT INTO diag_indicator_catalog (
                        code, name, source, frequency, weight, invert_sign, lookback_days,
                        expected_lag_business_days, stale_warn_s, stale_critical_s,
                        active, stage, sector_weight_json, heuristic_version,
                        metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(code) DO UPDATE SET
                        name=excluded.name,
                        source=excluded.source,
                        frequency=excluded.frequency,
                        weight=excluded.weight,
                        invert_sign=excluded.invert_sign,
                        lookback_days=excluded.lookback_days,
                        expected_lag_business_days=excluded.expected_lag_business_days,
                        stale_warn_s=excluded.stale_warn_s,
                        stale_critical_s=excluded.stale_critical_s,
                        active=excluded.active,
                        stage=excluded.stage,
                        sector_weight_json=excluded.sector_weight_json,
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        row["code"],
                        row["name"],
                        row["source"],
                        row["frequency"],
                        row["weight"],
                        row["invert_sign"],
                        row["lookback_days"],
                        row["expected_lag_business_days"],
                        row["stale_warn_s"],
                        row["stale_critical_s"],
                        row["active"],
                        row["stage"],
                        row["sector_weight_json"],
                        row["heuristic_version"],
                        row["metadata_json"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            await db.commit()

    async def trigger_refresh(self, lock_holder: str = "manual", wait: bool = False) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        run_id_or_conflict = await self._acquire_refresh_run(lock_holder=lock_holder)
        if isinstance(run_id_or_conflict, RefreshConflict):
            return {
                "status": "conflict",
                "run_id": run_id_or_conflict.run_id,
                "locked_by": run_id_or_conflict.locked_by,
                "lock_expires_at": run_id_or_conflict.lock_expires_at,
            }
        run_id = run_id_or_conflict
        task = asyncio.create_task(self._execute_refresh(run_id))
        self._refresh_tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._refresh_tasks.pop(rid, None))
        if wait:
            await task
        return {"status": "accepted", "run_id": run_id}

    async def backfill_if_needed(self, days: int = 365) -> None:
        if not self.enabled:
            return
        async with get_db() as db:
            async with db.execute("SELECT COUNT(*) FROM diag_indicator_values") as cur:
                row = await cur.fetchone()
        if int((row or [0])[0]) > 0:
            return
        await self.trigger_refresh(lock_holder="startup", wait=True)
        await self.trigger_refresh(lock_holder="startup", wait=True)

    async def get_refresh_run(self, run_id: int) -> dict[str, Any] | None:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, status, lock_holder, locked_at, lock_expires_at,
                       started_at, completed_at, error
                FROM diag_refresh_runs
                WHERE id=?
                """,
                (run_id,),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return {
            "run_id": int(row[0]),
            "status": row[1],
            "locked_by": row[2],
            "locked_at": row[3],
            "lock_expires_at": row[4],
            "started_at": row[5],
            "completed_at": row[6],
            "error": row[7],
        }

    async def get_overview(self, lookback_days: int = 90) -> dict[str, Any]:
        now_ts = int(time.time())
        cutoff = now_ts - (max(1, int(lookback_days)) * 86400)
        async with get_db() as db:
            async with db.execute(
                """
                SELECT as_of_ts, composite_score, state, indicator_count, stale_count, warn_count, summary_json
                FROM diag_system_snapshots
                ORDER BY as_of_ts DESC
                LIMIT 1
                """
            ) as cur:
                latest = await cur.fetchone()
            async with db.execute(
                """
                SELECT as_of_ts, composite_score
                FROM diag_system_snapshots
                WHERE as_of_ts>=?
                ORDER BY as_of_ts ASC
                """,
                (cutoff,),
            ) as cur:
                trend_rows = await cur.fetchall()

        if not latest:
            return {
                "as_of_ts": None,
                "composite_score": None,
                "state": "unknown",
                "indicator_count": 0,
                "stale_count": 0,
                "warn_count": 0,
                "trend": [],
                "widgets": {},
            }

        trend = [{"time": int(ts), "value": float(val)} for ts, val in trend_rows if val is not None]
        summary = self._json_loads(latest[6], {})
        return {
            "as_of_ts": int(latest[0]),
            "composite_score": float(latest[1]) if latest[1] is not None else None,
            "state": latest[2] or "unknown",
            "indicator_count": int(latest[3] or 0),
            "stale_count": int(latest[4] or 0),
            "warn_count": int(latest[5] or 0),
            "trend": trend,
            "widgets": summary.get("widgets", {}),
            "last_run_ts": summary.get("last_run_ts"),
        }

    async def list_indicators(self) -> list[dict[str, Any]]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT c.code, c.name, c.source, c.frequency, c.weight,
                       c.expected_lag_business_days, c.stale_warn_s, c.stale_critical_s,
                       v.as_of_ts, v.value, v.score, v.state, v.reason_code,
                       v.freshness_status, v.age_s, v.meta_json
                FROM diag_indicator_catalog c
                LEFT JOIN (
                    SELECT v1.*
                    FROM diag_indicator_values v1
                    JOIN (
                        SELECT code, MAX(as_of_ts) AS max_ts
                        FROM diag_indicator_values
                        GROUP BY code
                    ) latest
                    ON latest.code=v1.code AND latest.max_ts=v1.as_of_ts
                ) v ON v.code=c.code
                WHERE c.active=1
                ORDER BY c.code ASC
                """
            ) as cur:
                rows = await cur.fetchall()
        return [self._indicator_row_to_dict(row) for row in rows]

    async def get_indicator(self, code: str) -> dict[str, Any] | None:
        code = code.strip().upper()
        rows = await self.list_indicators()
        for row in rows:
            if row["code"] == code:
                return row
        return None

    async def get_indicator_history(self, code: str, days: int = 365) -> list[dict[str, Any]]:
        code = code.strip().upper()
        cutoff = int(time.time()) - (max(1, int(days)) * 86400)
        async with get_db() as db:
            async with db.execute(
                """
                SELECT as_of_ts, value, score, state, reason_code, freshness_status, age_s
                FROM diag_indicator_values
                WHERE code=? AND as_of_ts>=?
                ORDER BY as_of_ts ASC
                """,
                (code, cutoff),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {
                "time": int(r[0]),
                "value": float(r[1]) if r[1] is not None else None,
                "score": float(r[2]) if r[2] is not None else None,
                "state": r[3],
                "reason_code": r[4],
                "freshness": r[5],
                "age_s": float(r[6]) if r[6] is not None else None,
            }
            for r in rows
        ]

    async def get_market_map(self, days: int = 5) -> list[dict[str, Any]]:
        days = max(1, int(days))
        now = time.time()
        cached = self._map_cache.get(days)
        if cached and (now - cached[0]) <= cfg.DIAG_INTRADAY_INTERVAL_SECONDS:
            return cached[1]
        try:
            rows = await asyncio.wait_for(yahoo_market_map_rows(days=days), timeout=30.0)
            self._map_cache[days] = (now, rows)
            self._record_success("diag_market_map")
            return rows
        except Exception as exc:
            self._record_failure("diag_market_map", str(exc))
            if cached:
                return cached[1]
            raise

    async def get_sector_projections_latest(self, lookback_days: int = 90) -> dict[str, Any] | None:
        lookback_days = int(lookback_days)
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, run_ts, lookback_days, heuristic_version, status
                FROM diag_sector_projection_runs
                WHERE lookback_days=?
                ORDER BY run_ts DESC
                LIMIT 1
                """,
                (lookback_days,),
            ) as cur:
                run_row = await cur.fetchone()
            if not run_row:
                return None
            async with db.execute(
                """
                SELECT sector, score, direction
                FROM diag_sector_projection_values
                WHERE run_id=?
                ORDER BY sector ASC
                """,
                (run_row[0],),
            ) as cur:
                values = await cur.fetchall()

        return {
            "run_id": int(run_row[0]),
            "run_ts": int(run_row[1]),
            "lookback_days": int(run_row[2]),
            "heuristic_version": run_row[3],
            "status": run_row[4],
            "values": [
                {
                    "sector": row[0],
                    "score": float(row[1]),
                    "direction": row[2],
                }
                for row in values
            ],
        }

    async def get_sector_projections_history(self, days: int = 365) -> list[dict[str, Any]]:
        cutoff = int(time.time()) - (max(1, int(days)) * 86400)
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, run_ts, lookback_days, heuristic_version, status
                FROM diag_sector_projection_runs
                WHERE run_ts>=?
                ORDER BY run_ts DESC
                """,
                (cutoff,),
            ) as cur:
                runs = await cur.fetchall()

            results: list[dict[str, Any]] = []
            for run in runs:
                async with db.execute(
                    """
                    SELECT sector, score, direction
                    FROM diag_sector_projection_values
                    WHERE run_id=?
                    ORDER BY sector ASC
                    """,
                    (run[0],),
                ) as cur:
                    values = await cur.fetchall()
                results.append(
                    {
                        "run_id": int(run[0]),
                        "run_ts": int(run[1]),
                        "lookback_days": int(run[2]),
                        "heuristic_version": run[3],
                        "status": run[4],
                        "values": [
                            {"sector": row[0], "score": float(row[1]), "direction": row[2]}
                            for row in values
                        ],
                    }
                )
        return results

    async def get_news(self, hours: int = 24, limit: int = 200) -> list[dict[str, Any]]:
        hours = max(1, int(hours))
        limit = max(1, min(200, int(limit)))
        cutoff = int(time.time()) - (hours * 3600)
        async with get_db() as db:
            async with db.execute(
                """
                SELECT source, headline, url, published_at, fetched_at
                FROM diag_news_cache
                WHERE published_at>=?
                ORDER BY published_at DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ) as cur:
                rows = await cur.fetchall()

        if rows:
            return [
                {
                    "source": row[0],
                    "headline": row[1],
                    "url": row[2],
                    "published_at": int(row[3]),
                    "fetched_at": int(row[4]),
                }
                for row in rows
            ]

        await self._refresh_news_cache()
        async with get_db() as db:
            async with db.execute(
                """
                SELECT source, headline, url, published_at, fetched_at
                FROM diag_news_cache
                WHERE published_at>=?
                ORDER BY published_at DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ) as cur:
                rows = await cur.fetchall()

        return [
            {
                "source": row[0],
                "headline": row[1],
                "url": row[2],
                "published_at": int(row[3]),
                "fetched_at": int(row[4]),
            }
            for row in rows
        ]

    async def _acquire_refresh_run(self, lock_holder: str) -> int | RefreshConflict:
        now_ts = int(time.time())
        lock_expires_at = now_ts + int(cfg.DIAG_LOCK_TTL_SECONDS)
        async with self._lock:
            async with get_db() as db:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    async with db.execute(
                        """
                        SELECT id, lock_holder, lock_expires_at
                        FROM diag_refresh_runs
                        WHERE status='running'
                          AND lock_expires_at IS NOT NULL
                          AND lock_expires_at>?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (now_ts,),
                    ) as cur:
                        row = await cur.fetchone()
                    if row:
                        await db.commit()
                        return RefreshConflict(
                            run_id=int(row[0]),
                            locked_by=str(row[1] or "unknown"),
                            lock_expires_at=int(row[2] or now_ts),
                        )
                    cur = await db.execute(
                        """
                        INSERT INTO diag_refresh_runs (
                            status, lock_holder, locked_at, lock_expires_at,
                            started_at, completed_at, error
                        ) VALUES ('running', ?, ?, ?, ?, NULL, NULL)
                        """,
                        (lock_holder, now_ts, lock_expires_at, now_ts),
                    )
                    run_id = int(cur.lastrowid)
                    await db.commit()
                    return run_id
                except Exception:
                    await db.rollback()
                    raise

    async def _execute_refresh(self, run_id: int) -> None:
        started = time.perf_counter()
        now_ts = int(time.time())
        try:
            await self.ensure_catalog_seeded()
            catalog_specs = await self._fetch_active_catalog()
            for spec in catalog_specs:
                await self._compute_and_store_indicator(spec)

            await self._write_system_snapshot(now_ts)
            await self._write_sector_projections(now_ts, lookback_days=90)
            await self._refresh_news_cache()
            await self.get_market_map(days=int(cfg.DIAG_MARKET_MAP_DAYS_DEFAULT))

            async with get_db() as db:
                await db.execute(
                    """
                    UPDATE diag_refresh_runs
                    SET status='completed', completed_at=?, error=NULL,
                        locked_at=NULL, lock_expires_at=NULL
                    WHERE id=?
                    """,
                    (int(time.time()), run_id),
                )
                await db.commit()
            self._record_success("diag_refresh_jobs")
        except Exception as exc:
            log.exception("Diagnostics refresh failed (run_id=%s)", run_id)
            async with get_db() as db:
                await db.execute(
                    """
                    UPDATE diag_refresh_runs
                    SET status='failed', completed_at=?, error=?,
                        locked_at=NULL, lock_expires_at=NULL
                    WHERE id=?
                    """,
                    (int(time.time()), str(exc), run_id),
                )
                await db.commit()
            self._record_failure("diag_refresh_jobs", str(exc))
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            log.info("Diagnostics refresh run %s finished in %.1fms", run_id, elapsed_ms)

    async def _fetch_active_catalog(self) -> list[dict[str, Any]]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT code, source, frequency, weight, invert_sign, lookback_days,
                       expected_lag_business_days, stale_warn_s, stale_critical_s,
                       sector_weight_json, metadata_json
                FROM diag_indicator_catalog
                WHERE active=1
                ORDER BY code ASC
                """
            ) as cur:
                rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "code": row[0],
                    "source": row[1],
                    "frequency": row[2],
                    "weight": float(row[3] or 0.0),
                    "invert_sign": bool(row[4]),
                    "lookback_days": int(row[5] or 365),
                    "expected_lag_business_days": int(row[6] or 0),
                    "stale_warn_s": float(row[7]) if row[7] is not None else None,
                    "stale_critical_s": float(row[8]) if row[8] is not None else None,
                    "sector_weight_json": row[9] or "{}",
                    "metadata": self._json_loads(row[10], {}),
                }
            )
        return out

    async def _compute_and_store_indicator(self, spec: dict[str, Any]) -> None:
        code = spec["code"]
        now_ts = int(time.time())
        series: list[tuple[int, float]] = []
        try:
            series = await asyncio.wait_for(self._indicator_series(code, spec), timeout=30.0)
        except asyncio.TimeoutError:
            self._record_failure("diag_indicators", f"{code}: timeout")
            await self._insert_indicator_value(
                code=code,
                as_of_ts=now_ts,
                value=None,
                score=None,
                state=None,
                reason_code="missing_data",
                freshness_status="stale",
                age_s=None,
                source=spec["source"],
                meta={"error": "timeout"},
            )
            return
        except Exception as exc:
            self._record_failure("diag_indicators", f"{code}: {exc}")
            await self._insert_indicator_value(
                code=code,
                as_of_ts=now_ts,
                value=None,
                score=None,
                state=None,
                reason_code="missing_data",
                freshness_status="stale",
                age_s=None,
                source=spec["source"],
                meta={"error": str(exc)},
            )
            return

        if not series:
            reason = "awaiting_source_publish" if code in {"UNRATE", "CONSUMER_HEALTH"} else "missing_data"
            await self._insert_indicator_value(
                code=code,
                as_of_ts=now_ts,
                value=None,
                score=None,
                state=None,
                reason_code=reason,
                freshness_status="warn" if reason == "awaiting_source_publish" else "stale",
                age_s=None,
                source=spec["source"],
                meta={"empty": True},
            )
            self._record_failure("diag_indicators", f"{code}: empty series")
            return

        has_history = await self._indicator_has_history(code)
        points = series if not has_history else [series[-1]]

        baseline_values = [value for _ts, value in series[-spec["lookback_days"] :]]
        if spec["invert_sign"]:
            baseline_values = [-1.0 * value for value in baseline_values]
        mean, stddev = safe_mean_std(baseline_values)

        for as_of_ts, raw_value in points:
            adjusted = (-1.0 * raw_value) if spec["invert_sign"] else raw_value
            score = score_from_z(adjusted, mean, stddev)
            reason_code: str | None = None
            if len(baseline_values) < 20 or stddev <= 1e-9:
                score = 50.0
                reason_code = "warmup_insufficient_history"

            if spec["frequency"] in {"real_time", "intraday"}:
                age_s = max(0.0, float(now_ts - as_of_ts))
                freshness_status, freshness_reason = freshness_from_intraday_age(
                    age_s,
                    spec.get("stale_warn_s"),
                    spec.get("stale_critical_s"),
                )
                if freshness_reason and reason_code is None:
                    reason_code = freshness_reason
            else:
                dt = self._utc_from_unix(as_of_ts)
                freshness_status, freshness_reason, _lag = freshness_from_business_lag(
                    dt,
                    int(spec.get("expected_lag_business_days", 0)),
                    tz_name=str(cfg.DIAG_SCHEDULER_TIMEZONE),
                )
                age_s = float(now_ts - as_of_ts)
                if freshness_reason and reason_code is None:
                    reason_code = freshness_reason

            await self._insert_indicator_value(
                code=code,
                as_of_ts=int(as_of_ts),
                value=float(raw_value),
                score=float(score),
                state=state_from_score(score),
                reason_code=reason_code,
                freshness_status=freshness_status,
                age_s=age_s,
                source=spec["source"],
                meta={"mean": mean, "stddev": stddev},
            )

        self._record_success("diag_indicators")

    async def _indicator_series(self, code: str, spec: dict[str, Any]) -> list[tuple[int, float]]:
        if code == "VIX":
            return await self._series_close("^VIX")
        if code == "SPY_TREND":
            df = await yahoo_history("SPY", period="2y", interval="1d", prepost=False)
            if df.empty:
                return []
            closes = df["close"].astype(float)
            sma200 = closes.rolling(200).mean()
            out: list[tuple[int, float]] = []
            for idx in range(len(df)):
                ma = sma200.iloc[idx]
                if not (ma and ma > 0):
                    continue
                value = (float(closes.iloc[idx]) / float(ma)) - 1.0
                out.append((int(df["time"].iloc[idx].timestamp()), float(value)))
            return out
        if code == "FED_FUNDS_MOMENTUM":
            rows = await fred_series("DFF")
            out: list[tuple[int, float]] = []
            for idx in range(90, len(rows)):
                out.append((rows[idx][0], float(rows[idx][1] - rows[idx - 90][1])))
            return out
        if code == "UST_10Y_2Y":
            return await fred_series("T10Y2Y")
        if code == "BOND_MARKET_STABILITY":
            return await fred_series("BAA10Y")
        if code == "LIQUIDITY_PROXY":
            num_df = await yahoo_history("TLT", period="2y", interval="1d", prepost=False)
            den_df = await yahoo_history("HYG", period="2y", interval="1d", prepost=False)
            if num_df.empty or den_df.empty:
                return []
            merged = num_df[["time", "close"]].rename(columns={"close": "num"}).merge(
                den_df[["time", "close"]].rename(columns={"close": "den"}),
                on="time",
                how="inner",
            )
            out: list[tuple[int, float]] = []
            for _, row in merged.iterrows():
                den = float(row["den"])
                if den <= 0:
                    continue
                out.append((int(pd.Timestamp(row["time"]).timestamp()), float(row["num"] / den)))
            return out
        if code == "ANALYST_CONFIDENCE":
            values = await yahoo_recommendation_mean(["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"])
            if not values:
                return []
            ts = int(time.time())
            return [(ts, float(statistics.median(values)))]
        if code == "SENTIMENT_COMPOSITE":
            spy = await yahoo_history("SPY", period="2y", interval="1d", prepost=False)
            qqq = await yahoo_history("QQQ", period="2y", interval="1d", prepost=False)
            iwm = await yahoo_history("IWM", period="2y", interval="1d", prepost=False)
            if spy.empty or qqq.empty or iwm.empty:
                return []
            merged = (
                spy[["time", "close"]]
                .rename(columns={"close": "spy"})
                .merge(qqq[["time", "close"]].rename(columns={"close": "qqq"}), on="time", how="inner")
                .merge(iwm[["time", "close"]].rename(columns={"close": "iwm"}), on="time", how="inner")
            )
            out: list[tuple[int, float]] = []
            for idx in range(20, len(merged)):
                prev = merged.iloc[idx - 20]
                cur = merged.iloc[idx]
                if float(prev["spy"]) <= 0 or float(prev["qqq"]) <= 0 or float(prev["iwm"]) <= 0:
                    continue
                spy_ret = (float(cur["spy"]) / float(prev["spy"])) - 1.0
                qqq_ret = (float(cur["qqq"]) / float(prev["qqq"])) - 1.0
                iwm_ret = (float(cur["iwm"]) / float(prev["iwm"])) - 1.0
                composite = (0.5 * spy_ret) + (0.3 * qqq_ret) + (0.2 * iwm_ret)
                out.append((int(pd.Timestamp(cur["time"]).timestamp()), float(composite)))
            return out
        if code == "UNRATE":
            return await fred_series("UNRATE")
        if code == "CONSUMER_HEALTH":
            return await self._consumer_health_series()
        return []

    async def _series_close(self, symbol: str) -> list[tuple[int, float]]:
        df = await yahoo_history(symbol, period="2y", interval="1d", prepost=False)
        if df.empty:
            return []
        out: list[tuple[int, float]] = []
        for _, row in df.iterrows():
            out.append((int(pd.Timestamp(row["time"]).timestamp()), float(row["close"])))
        return out

    async def _consumer_health_series(self) -> list[tuple[int, float]]:
        umcsent = await fred_series("UMCSENT")
        dspic = await fred_series("DSPIC96")
        pcec = await fred_series("PCEC96")
        if not umcsent or not dspic or not pcec:
            return []

        umap = {ts: value for ts, value in umcsent}
        dmap = {ts: value for ts, value in dspic}
        pmap = {ts: value for ts, value in pcec}

        ordered = sorted(set(umap.keys()) & set(dmap.keys()) & set(pmap.keys()))
        out: list[tuple[int, float]] = []
        for idx in range(12, len(ordered)):
            ts = ordered[idx]
            ts_prev = ordered[idx - 12]
            d_prev = dmap.get(ts_prev)
            p_prev = pmap.get(ts_prev)
            if d_prev is None or p_prev is None or d_prev <= 0 or p_prev <= 0:
                continue
            dspic_yoy = ((dmap[ts] / d_prev) - 1.0) * 100.0
            pcec_yoy = ((pmap[ts] / p_prev) - 1.0) * 100.0
            composite = (0.4 * umap[ts]) + (0.3 * dspic_yoy) + (0.3 * pcec_yoy)
            out.append((ts, float(composite)))
        return out

    async def _indicator_has_history(self, code: str) -> bool:
        async with get_db() as db:
            async with db.execute("SELECT 1 FROM diag_indicator_values WHERE code=? LIMIT 1", (code,)) as cur:
                row = await cur.fetchone()
        return row is not None

    async def _insert_indicator_value(
        self,
        *,
        code: str,
        as_of_ts: int,
        value: float | None,
        score: float | None,
        state: str | None,
        reason_code: str | None,
        freshness_status: str,
        age_s: float | None,
        source: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO diag_indicator_values (
                    code, as_of_ts, value, score, state, reason_code,
                    freshness_status, age_s, source, meta_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, as_of_ts) DO UPDATE SET
                    value=excluded.value,
                    score=excluded.score,
                    state=excluded.state,
                    reason_code=excluded.reason_code,
                    freshness_status=excluded.freshness_status,
                    age_s=excluded.age_s,
                    source=excluded.source,
                    meta_json=excluded.meta_json
                """,
                (
                    code,
                    int(as_of_ts),
                    value,
                    score,
                    state,
                    reason_code,
                    freshness_status,
                    age_s,
                    source,
                    json.dumps(meta or {}, separators=(",", ":")),
                    int(time.time()),
                ),
            )
            await db.commit()

    async def _write_system_snapshot(self, as_of_ts: int) -> None:
        indicators = await self.list_indicators()
        weighted_scores: list[tuple[float, float]] = []
        stale_count = 0
        warn_count = 0
        for indicator in indicators:
            score = indicator.get("score")
            weight = float(indicator.get("weight") or 0.0)
            freshness = indicator.get("freshness_status")
            if freshness == "stale":
                stale_count += 1
            elif freshness == "warn":
                warn_count += 1
            if score is not None and weight > 0:
                weighted_scores.append((float(score), weight))

        composite = None
        state = "unknown"
        if weighted_scores:
            denom = sum(weight for _score, weight in weighted_scores)
            if denom > 0:
                composite = sum(score * weight for score, weight in weighted_scores) / denom
                state = state_from_score(composite) or "unknown"

        widgets = await self._build_widget_summary(indicators, composite)

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO diag_system_snapshots (
                    as_of_ts, composite_score, state, indicator_count,
                    stale_count, warn_count, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(as_of_ts) DO UPDATE SET
                    composite_score=excluded.composite_score,
                    state=excluded.state,
                    indicator_count=excluded.indicator_count,
                    stale_count=excluded.stale_count,
                    warn_count=excluded.warn_count,
                    summary_json=excluded.summary_json
                """,
                (
                    as_of_ts,
                    composite,
                    state,
                    len(indicators),
                    stale_count,
                    warn_count,
                    json.dumps({"widgets": widgets, "last_run_ts": as_of_ts}, separators=(",", ":")),
                    int(time.time()),
                ),
            )
            await db.commit()

    async def _build_widget_summary(self, indicators: list[dict[str, Any]], composite: float | None) -> dict[str, Any]:
        by_code = {item["code"]: item for item in indicators}
        dow = by_code.get("SPY_TREND", {}).get("score")
        sent = by_code.get("SENTIMENT_COMPOSITE", {}).get("score")
        div = by_code.get("VIX", {}).get("score")
        aas = by_code.get("LIQUIDITY_PROXY", {}).get("score")
        return {
            "system_overview": {
                "score": composite,
                "state": state_from_score(composite) if composite is not None else "unknown",
            },
            "dow_theory": {
                "score": dow,
                "state": state_from_score(float(dow)) if dow is not None else "unknown",
            },
            "sector_divergence": {
                "score": div,
                "state": state_from_score(float(div)) if div is not None else "unknown",
            },
            "aas": {
                "score": aas,
                "state": state_from_score(float(aas)) if aas is not None else "unknown",
            },
            "sentiment": {
                "score": sent,
                "state": state_from_score(float(sent)) if sent is not None else "unknown",
            },
        }

    async def _write_sector_projections(self, run_ts: int, lookback_days: int = 90) -> None:
        indicators = await self.list_indicators()
        score_by_code = {row["code"]: row.get("score") for row in indicators}
        async with get_db() as db:
            async with db.execute(
                """
                SELECT code, sector_weight_json, weight
                FROM diag_indicator_catalog
                WHERE active=1
                """
            ) as cur:
                weight_rows = await cur.fetchall()

        sector_totals: dict[str, float] = {}
        sector_weights: dict[str, float] = {}
        for code, sector_json, indicator_weight in weight_rows:
            score = score_by_code.get(code)
            if score is None:
                continue
            weights = self._json_loads(sector_json, {})
            if not isinstance(weights, dict):
                continue
            for sector, raw_weight in weights.items():
                try:
                    sw = float(raw_weight)
                except (TypeError, ValueError):
                    continue
                if sw <= 0:
                    continue
                total_w = sw * float(indicator_weight or 1.0)
                sector_totals[sector] = sector_totals.get(sector, 0.0) + (float(score) * total_w)
                sector_weights[sector] = sector_weights.get(sector, 0.0) + total_w

        projections: list[tuple[str, float, str]] = []
        for sector, total in sector_totals.items():
            denom = sector_weights.get(sector, 0.0)
            if denom <= 0:
                continue
            score = total / denom
            if score >= 65.0:
                direction = "BULLISH"
            elif score >= 45.0:
                direction = "NEUTRAL"
            else:
                direction = "BEARISH"
            projections.append((sector, score, direction))

        heuristic_hash = self._projection_hash(weight_rows)
        heuristic_version = await self._next_heuristic_version(heuristic_hash)

        async with get_db() as db:
            cur = await db.execute(
                """
                INSERT INTO diag_sector_projection_runs (
                    run_ts, lookback_days, heuristic_version, status, notes
                ) VALUES (?, ?, ?, 'completed', ?)
                """,
                (run_ts, int(lookback_days), heuristic_version, heuristic_hash),
            )
            run_id = int(cur.lastrowid)
            for sector, score, direction in projections:
                await db.execute(
                    """
                    INSERT INTO diag_sector_projection_values (
                        run_id, sector, score, direction, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, sector, float(score), direction, int(time.time())),
                )
            await db.commit()
        self._record_success("diag_sector_projections")

    def _projection_hash(self, rows: list[tuple[Any, Any, Any]]) -> str:
        canonical: list[dict[str, Any]] = []
        for code, sector_json, weight in rows:
            parsed = self._json_loads(sector_json, {})
            canonical.append(
                {
                    "code": code,
                    "weight": float(weight or 0.0),
                    "sector_weights": {k: parsed[k] for k in sorted(parsed.keys())} if isinstance(parsed, dict) else {},
                }
            )
        payload = json.dumps(sorted(canonical, key=lambda x: x["code"]), separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    async def _next_heuristic_version(self, new_hash: str) -> str:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT heuristic_version, notes
                FROM diag_sector_projection_runs
                ORDER BY run_ts DESC
                LIMIT 1
                """
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return "1.0.0"
        last_version = row[0] or "1.0.0"
        last_hash = row[1] or ""
        if last_hash == new_hash:
            return str(last_version)
        try:
            major, minor, patch = [int(part) for part in str(last_version).split(".")]
        except Exception:
            major, minor, patch = (1, 0, 0)
        minor += 1
        return f"{major}.{minor}.{patch}"

    async def _refresh_news_cache(self) -> None:
        now_ts = int(time.time())
        try:
            items = await asyncio.wait_for(yahoo_news_rss(), timeout=30.0)
        except Exception as exc:
            self._record_failure("diag_news_cache", str(exc))
            return

        async with get_db() as db:
            for item in items:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO diag_news_cache (
                        source, headline, url, published_at, fetched_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        item.get("source", "yahoo_rss"),
                        item.get("headline", ""),
                        item.get("url", ""),
                        int(item.get("published_at") or now_ts),
                        now_ts,
                    ),
                )
            prune_before = now_ts - (72 * 3600)
            await db.execute("DELETE FROM diag_news_cache WHERE published_at<?", (prune_before,))
            await db.commit()
        self._record_success("diag_news_cache")

    def _indicator_row_to_dict(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "code": row[0],
            "name": row[1],
            "source": row[2],
            "frequency": row[3],
            "weight": float(row[4] or 0.0),
            "expected_lag_business_days": int(row[5] or 0),
            "stale_warn_s": float(row[6]) if row[6] is not None else None,
            "stale_critical_s": float(row[7]) if row[7] is not None else None,
            "time": int(row[8]) if row[8] is not None else None,
            "value": float(row[9]) if row[9] is not None else None,
            "score": float(row[10]) if row[10] is not None else None,
            "state": row[11],
            "reason_code": row[12],
            "freshness_status": row[13] or "unknown",
            "age_s": float(row[14]) if row[14] is not None else None,
            "meta": self._json_loads(row[15], {}),
        }

    @staticmethod
    def _json_loads(raw: Any, fallback: Any) -> Any:
        if raw is None:
            return fallback
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return fallback

    @staticmethod
    def _utc_from_unix(ts: int | float) -> datetime:
        # Avoid platform-dependent fromtimestamp failures on negative epochs (Windows).
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return epoch + timedelta(seconds=float(ts))
