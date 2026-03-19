"""
Stage 8 — Event Replay & Observability.

Serializes events to JSONL for deterministic re-runs and debugging.
Tracks performance metrics for monitoring.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from events import Event, EventType

log = logging.getLogger(__name__)

LOGS_DIR = Path("data/event_logs")


class EventLogger:
    """Serialize events to JSONL files for replay and debugging."""

    def __init__(self, session_id: str | None = None):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.log_path = LOGS_DIR / f"events_{self.session_id}.jsonl"
        self._count = 0

    def log_event(self, event: Event) -> None:
        """Append event to JSONL file."""
        record = {
            "type": event.type.value,
            "timestamp": event.timestamp.isoformat(),
            **{k: v for k, v in event.__dict__.items() if k not in ("type", "timestamp")},
        }
        # Convert non-serializable types
        for k, v in record.items():
            if isinstance(v, datetime):
                record[k] = v.isoformat()
            elif isinstance(v, EventType):
                record[k] = v.value

        with open(self.log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
        self._count += 1

    @property
    def event_count(self) -> int:
        return self._count

    @staticmethod
    def replay(log_path: str | Path) -> list[dict]:
        """Load events from a JSONL file for replay."""
        events = []
        path = Path(log_path)
        if not path.exists():
            return events
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    @staticmethod
    def list_sessions() -> list[dict]:
        """List all available event log sessions."""
        sessions = []
        if not LOGS_DIR.exists():
            return sessions
        for f in sorted(LOGS_DIR.glob("events_*.jsonl"), reverse=True):
            stat = f.stat()
            sessions.append({
                "session_id": f.stem.replace("events_", ""),
                "file": str(f),
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        return sessions


class MetricsCollector:
    """Collect and expose performance metrics for monitoring."""

    def __init__(self):
        self._metrics: dict[str, list[tuple[datetime, float]]] = {}

    def record(self, name: str, value: float, ts: datetime | None = None) -> None:
        if ts is None:
            ts = datetime.now(timezone.utc)
        if name not in self._metrics:
            self._metrics[name] = []
        self._metrics[name].append((ts, value))
        # Keep last 10000 points per metric
        if len(self._metrics[name]) > 10000:
            self._metrics[name] = self._metrics[name][-5000:]

    def get(self, name: str, last_n: int = 100) -> list[dict]:
        points = self._metrics.get(name, [])[-last_n:]
        return [{"timestamp": ts.isoformat(), "value": v} for ts, v in points]

    def get_latest(self, name: str) -> float | None:
        points = self._metrics.get(name)
        return points[-1][1] if points else None

    def summary(self) -> dict:
        return {
            name: {
                "count": len(points),
                "latest": points[-1][1] if points else None,
                "min": min(v for _, v in points) if points else None,
                "max": max(v for _, v in points) if points else None,
            }
            for name, points in self._metrics.items()
        }
