"""Fixed per-identity size multipliers for the PAPER senders (owner, 2026-10-14/15).

Every sendable identity (``trading.sendable_identities.SENDABLE_IDENTITIES``)
has exactly one row here. The row scales the risk policy's approved quantity
to ``floor(policy quantity x multiplier)`` through the engine's
``size_multiplier`` (``trading.risk.engine``), the same hook the AI day
manager narrows through; the two factors multiply, each in (0, 1].

Fail-closed rule: a sendable identity without a row is NOT sized at 1. It is
``None``, and the pipeline turns every one of its setups into a terminal
``identity_sizing_missing`` block. A name that is not sendable keeps 1 (the
unscaled policy size: shadow identities are sized exactly as before). The
launch preflight and a test pin ``set(IDENTITY_SIZE_MULTIPLIER) ==
set(SENDABLE_IDENTITIES)``, so the ``None`` branch is a tripwire, never a
routine path. Values change only in a reviewed commit (v7b adds three rows);
there is no automatic day-step advancement.

No-limit PAPER test L-T1 (owner, 2026-10-23, decision D2): while
``trading.risk.no_limit_test.NO_LIMIT_TEST`` is on, the shipped table is
``NO_LIMIT_TEST_SIZE_MULTIPLIER``, set by the Step 0 rule fixed before the data:
an identity with at least 30 sent trades and a mean R at or below 0 keeps its
v7c multiplier (``V7C_SIZE_MULTIPLIER``); every other identity is x1 (0.5 %
risk / 15 % notional per order). Step 0 runs Sat 2026-10-24; its result is a
one-line edit of one row below. An import tripwire refuses a table that
disagrees with the mode or a row the rule cannot produce.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType

from trading.risk.engine import require_size_multiplier
from trading.risk.no_limit_test import NO_LIMIT_TEST, NO_LIMIT_TEST_LABEL

# The owner's v7c sizes (2026-10-14/15): what deploy undo restores, and the
# multiplier an identity keeps under the Step 0 rule.
V7C_SIZE_MULTIPLIER: Mapping[str, Decimal] = MappingProxyType(
    {
        "MOMO_HOD_V2": Decimal("1"),
        "ORB_V2": Decimal("0.25"),
        "VWAP_PB_V2": Decimal("0.5"),
        "GAP_GO_V2": Decimal("0.5"),
    }
)
# The no-limit test's sizes, by the Step 0 rule (decision D2): an identity with
# n >= 30 sent trades and mean R <= 0 keeps its V7C_SIZE_MULTIPLIER row; every
# other identity (n < 30 is no evidence) is x1. Each row is either Decimal("1")
# or exactly its v7c row: the Step 0 result is a one-line edit of that row.
NO_LIMIT_TEST_SIZE_MULTIPLIER: Mapping[str, Decimal] = MappingProxyType(
    {
        "MOMO_HOD_V2": Decimal("1"),
        "ORB_V2": Decimal("1"),
        "VWAP_PB_V2": Decimal("1"),
        "GAP_GO_V2": Decimal("1"),
    }
)
# The shipped table: the test's while the mode is on (tripwire below).
IDENTITY_SIZE_MULTIPLIER: Mapping[str, Decimal] = NO_LIMIT_TEST_SIZE_MULTIPLIER

for _table in (V7C_SIZE_MULTIPLIER, NO_LIMIT_TEST_SIZE_MULTIPLIER):
    for _name, _value in _table.items():
        # A row outside (0, 1], or not a Decimal, is refused at import: the table
        # is read at every session start and must never size anything up.
        require_size_multiplier(_value)
        if not isinstance(_value, Decimal):
            raise TypeError(f"identity multiplier for {_name} must be a Decimal")
del _table, _name, _value
if set(NO_LIMIT_TEST_SIZE_MULTIPLIER) != set(V7C_SIZE_MULTIPLIER) or any(
    value not in (Decimal("1"), V7C_SIZE_MULTIPLIER[name]) for name, value in NO_LIMIT_TEST_SIZE_MULTIPLIER.items()
):
    # The Step 0 rule yields x1 or the identity's own v7c row, nothing else.
    raise RuntimeError(f"no-limit test {NO_LIMIT_TEST_LABEL}: a size row the Step 0 rule cannot produce")
if IDENTITY_SIZE_MULTIPLIER is not (NO_LIMIT_TEST_SIZE_MULTIPLIER if NO_LIMIT_TEST else V7C_SIZE_MULTIPLIER):
    # A half-on build (mode on with v7c sizes, or off with the test's) cannot start.
    raise RuntimeError(
        f"no-limit test {NO_LIMIT_TEST_LABEL} is {'on' if NO_LIMIT_TEST else 'off'} but the shipped"
        " identity size table is the other mode's"
    )


def identity_multiplier(name: str, *, sendable: bool) -> Decimal | None:
    """The fixed factor for ``name``; ``None`` for a sendable name with no row.

    ``sendable`` is decided by the caller (``is_sendable_identity``), so this
    module never guesses from the name. A non-sendable name is 1: shadow
    identities keep the unscaled policy size. A sendable name without a row is
    ``None`` and must be refused by the caller, never defaulted to 1.
    """
    if not sendable:
        return Decimal("1")
    value = IDENTITY_SIZE_MULTIPLIER.get(name.strip())
    if value is None:
        return None
    return require_size_multiplier(value)


__all__ = [
    "IDENTITY_SIZE_MULTIPLIER",
    "NO_LIMIT_TEST_SIZE_MULTIPLIER",
    "V7C_SIZE_MULTIPLIER",
    "identity_multiplier",
]
