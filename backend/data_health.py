"""
Lightweight in-memory data freshness monitor.

Tracks success/failure timestamps for named data sources and computes
fresh/stale status snapshots for operators and dashboards.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round_age(age: float | None) -> float | None:
    if age is None:
        return None
    return round(max(age, 0.0), 3)


@dataclass
class SourceHealth:
    stale_after_s: float
    last_attempt_ts: float | None = None
    last_success_ts: float | None = None
    last_attempt: str | None = None
    last_success: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_duration_ms: float | None = None
    last_count: int = 0


class DataFreshnessMonitor:
    """
    Thread-safe recorder for source freshness and failures.

    Status model:
    - unknown: no successful updates yet
    - fresh:   age <= stale_after_s
    - stale:   stale_after_s < age <= stale_after_s * 3
    - critical: age > stale_after_s * 3 or repeated failures
    """

    def __init__(self, defaults: dict[str, float] | None = None) -> None:
        self._lock = threading.Lock()
        self._sources: dict[str, SourceHealth] = {}
        for source, stale_after_s in (defaults or {}).items():
            self.register_source(source, stale_after_s)

    def register_source(self, source: str, stale_after_s: float) -> None:
        with self._lock:
            if source not in self._sources:
                self._sources[source] = SourceHealth(stale_after_s=max(0.1, float(stale_after_s)))
            else:
                self._sources[source].stale_after_s = max(0.1, float(stale_after_s))

    def record_success(
        self,
        source: str,
        *,
        count: int = 0,
        duration_ms: float | None = None,
        stale_after_s: float | None = None,
    ) -> None:
        now_ts = time.time()
        now_iso = _utc_now_iso()
        with self._lock:
            if source not in self._sources:
                self._sources[source] = SourceHealth(stale_after_s=max(0.1, float(stale_after_s or 30.0)))
            state = self._sources[source]
            if stale_after_s is not None:
                state.stale_after_s = max(0.1, float(stale_after_s))
            state.last_attempt_ts = now_ts
            state.last_success_ts = now_ts
            state.last_attempt = now_iso
            state.last_success = now_iso
            state.last_error = None
            state.consecutive_failures = 0
            state.total_successes += 1
            state.last_count = max(0, int(count))
            if duration_ms is not None:
                state.last_duration_ms = round(float(duration_ms), 3)

    def record_failure(
        self,
        source: str,
        error: str,
        *,
        stale_after_s: float | None = None,
        duration_ms: float | None = None,
    ) -> None:
        now_ts = time.time()
        now_iso = _utc_now_iso()
        with self._lock:
            if source not in self._sources:
                self._sources[source] = SourceHealth(stale_after_s=max(0.1, float(stale_after_s or 30.0)))
            state = self._sources[source]
            if stale_after_s is not None:
                state.stale_after_s = max(0.1, float(stale_after_s))
            state.last_attempt_ts = now_ts
            state.last_attempt = now_iso
            state.last_error = error
            state.consecutive_failures += 1
            state.total_failures += 1
            if duration_ms is not None:
                state.last_duration_ms = round(float(duration_ms), 3)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            sources: dict[str, dict[str, Any]] = {}
            overall = "fresh"

            for name, state in sorted(self._sources.items()):
                age_s = None
                if state.last_success_ts is not None:
                    age_s = now - state.last_success_ts

                status = self._status_for(state, age_s)
                overall = self._worse_status(overall, status)

                sources[name] = {
                    "status": status,
                    "stale_after_s": round(state.stale_after_s, 3),
                    "age_s": _round_age(age_s),
                    "last_attempt": state.last_attempt,
                    "last_success": state.last_success,
                    "last_error": state.last_error,
                    "consecutive_failures": state.consecutive_failures,
                    "total_successes": state.total_successes,
                    "total_failures": state.total_failures,
                    "last_duration_ms": state.last_duration_ms,
                    "last_count": state.last_count,
                }

            if not sources:
                overall = "unknown"

            return {
                "timestamp": _utc_now_iso(),
                "overall_status": overall,
                "sources": sources,
            }

    @staticmethod
    def _status_for(state: SourceHealth, age_s: float | None) -> str:
        if state.last_success_ts is None:
            return "unknown"
        if state.consecutive_failures >= 5:
            return "critical"
        if age_s is None:
            return "unknown"
        if age_s <= state.stale_after_s:
            return "fresh"
        if age_s <= state.stale_after_s * 3:
            return "stale"
        return "critical"

    @staticmethod
    def _worse_status(current: str, incoming: str) -> str:
        rank = {"fresh": 0, "unknown": 1, "stale": 2, "critical": 3}
        if rank.get(incoming, 3) > rank.get(current, 3):
            return incoming
        return current
