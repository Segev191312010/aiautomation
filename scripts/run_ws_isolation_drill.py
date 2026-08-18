#!/usr/bin/env python3
"""Run a deterministic two-user WebSocket routing drill.

This is a local, dependency-light contract check.  It exercises the same
``ConnectionManager`` used by ``/ws`` and deliberately verifies that private
events never cross user buckets, while unclassified events fail closed.  It
does not replace the authenticated browser drill against a running service.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from ws_manager import ConnectionManager, PRIVATE_EVENT_TYPES, PUBLIC_EVENT_TYPES  # noqa: E402


class DrillSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        del subprotocol

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


async def run() -> dict[str, object]:
    manager = ConnectionManager()
    alice = DrillSocket()
    bob = DrillSocket()
    await manager.connect(alice, "alice")
    await manager.connect(bob, "bob")

    private_results: dict[str, bool] = {}
    for event_type in sorted(PRIVATE_EVENT_TYPES):
        before_alice = len(alice.messages)
        before_bob = len(bob.messages)
        await manager.route_event({"type": event_type, "drill": True}, owner_user_id="alice")
        private_results[event_type] = (
            len(alice.messages) == before_alice + 1
            and len(bob.messages) == before_bob
        )

    before = (len(alice.messages), len(bob.messages))
    await manager.route_event({"type": "unknown_private_candidate", "drill": True}, owner_user_id="alice")
    unknown_dropped = (len(alice.messages), len(bob.messages)) == before

    await manager.route_event({"type": "positions_update", "drill": True})
    missing_owner_dropped = (len(alice.messages), len(bob.messages)) == before

    before_public = (len(alice.messages), len(bob.messages))
    await manager.route_event({"type": "bar", "drill": True})
    public_fanout = (
        (len(alice.messages), len(bob.messages))
        == (before_public[0] + 1, before_public[1] + 1)
    )

    passed = all(private_results.values()) and unknown_dropped and missing_owner_dropped and public_fanout
    return {
        "passed": passed,
        "private_event_types": sorted(PRIVATE_EVENT_TYPES),
        "public_event_types": sorted(PUBLIC_EVENT_TYPES),
        "private_isolation": private_results,
        "unknown_event_dropped": unknown_dropped,
        "missing_owner_dropped": missing_owner_dropped,
        "public_fanout": public_fanout,
        "metrics": dict(manager.metrics),
    }


def main() -> int:
    result = asyncio.run(run())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
