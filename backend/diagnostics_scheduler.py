"""
Diagnostics scheduler with ET-based cadence.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from zoneinfo import ZoneInfo

from config import cfg
from diagnostics_service import DiagnosticsService

log = logging.getLogger(__name__)


class DiagnosticsScheduler:
    def __init__(self, service: DiagnosticsService) -> None:
        self._service = service
        self._task: asyncio.Task | None = None
        self._tz = ZoneInfo(str(cfg.DIAG_SCHEDULER_TIMEZONE))
        self._last_intraday_key = ""
        self._last_full_key = ""
        self._last_recon_key = ""

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())
        log.info("Diagnostics scheduler started")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        log.info("Diagnostics scheduler stopped")

    async def _run(self) -> None:
        # Warm snapshot + initial backfill attempt.
        try:
            await self._service.backfill_if_needed(days=365)
        except Exception as exc:
            log.warning("Diagnostics startup warmup failed: %s", exc)

        while True:
            now_utc = datetime.now(timezone.utc)
            now_et = now_utc.astimezone(self._tz)

            await self._tick_intraday(now_et)
            await self._tick_full_etl(now_et)
            await self._tick_reconciliation(now_et)

            await asyncio.sleep(5)

    async def _tick_intraday(self, now_et: datetime) -> None:
        if not self._is_intraday_window(now_et):
            return
        interval_s = max(60, int(cfg.DIAG_INTRADAY_INTERVAL_SECONDS))
        bucket = int(now_et.timestamp()) // interval_s
        key = str(bucket)
        if key == self._last_intraday_key:
            return
        self._last_intraday_key = key
        await self._service.trigger_refresh(lock_holder="scheduler", wait=False)

    async def _tick_full_etl(self, now_et: datetime) -> None:
        if now_et.weekday() >= 5:
            return
        if now_et.hour not in {8, 12, 16, 20}:
            return
        if now_et.minute != 0:
            return
        key = now_et.strftime("%Y%m%d%H")
        if key == self._last_full_key:
            return
        self._last_full_key = key
        await self._service.trigger_refresh(lock_holder="scheduler", wait=False)

    async def _tick_reconciliation(self, now_et: datetime) -> None:
        if now_et.weekday() >= 5:
            return
        if now_et.hour != 20 or now_et.minute != 5:
            return
        key = now_et.strftime("%Y%m%d")
        if key == self._last_recon_key:
            return
        self._last_recon_key = key
        await self._service.trigger_refresh(lock_holder="scheduler", wait=False)

    @staticmethod
    def _is_intraday_window(now_et: datetime) -> bool:
        if now_et.weekday() >= 5:
            return False
        minutes = now_et.hour * 60 + now_et.minute
        return (4 * 60) <= minutes < (20 * 60)
