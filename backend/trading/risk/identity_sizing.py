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
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType

from trading.risk.engine import require_size_multiplier

IDENTITY_SIZE_MULTIPLIER: Mapping[str, Decimal] = MappingProxyType(
    {
        "MOMO_HOD_V2": Decimal("1"),
    }
)
for _name, _value in IDENTITY_SIZE_MULTIPLIER.items():
    # A row outside (0, 1], or not a Decimal, is refused at import: the table
    # is read at every session start and must never size anything up.
    require_size_multiplier(_value)
    if not isinstance(_value, Decimal):
        raise TypeError(f"identity multiplier for {_name} must be a Decimal")
del _name, _value


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


__all__ = ["IDENTITY_SIZE_MULTIPLIER", "identity_multiplier"]
