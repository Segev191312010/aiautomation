"""Shared facts for the tests of the no-limit PAPER test L-T1 (owner, 2026-10-23).

v7c's values are kept here for the tests that protect logic the test window
switches off (the Sentinel's capped daily-loss limits, the manager's slot
count): the logic stays in the code, and ``deploy undo`` brings the values back.
"""
from __future__ import annotations

from types import MappingProxyType, ModuleType

# v7c's Sentinel caps mirror (bridge v7c IDENTITY_CAPS max_entries_per_day).
V7C_SENTINEL_MAX_ENTRIES_PER_DAY = MappingProxyType(
    {"MOMO_HOD_V2": None, "ORB_V2": 1, "VWAP_PB_V2": 2, "GAP_GO_V2": 1}
)
# v7c's MOMO_HOD_V2 position cap, the manager's slot count.
V7C_MANAGER_MAX_OPEN = 3


def bridge_caps(bridge: ModuleType) -> dict[str, tuple[object, object, object]]:
    return {
        name: (caps.max_positions, caps.max_entries_per_day, caps.last_entry_before_close)
        for name, caps in bridge.IDENTITY_CAPS.items()
    }
