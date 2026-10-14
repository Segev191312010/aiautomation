"""Strategy names the PAPER order bridge sends for, guarded where names are made.

The pinned bridge (``scripts_local/paper_bridge.py``) sends every
``shadow_accepted`` decision whose stored strategy name, stripped, starts with
``MOMO_HOD``. That prefix is wider than the owner's grant, and the bridge
cannot be edited without a new execution-safety review, so names are guarded
where they are made:

- the repository creates or publishes a ``MOMO_HOD`` name only when it is
  exactly one of ``SENDABLE_IDENTITIES``;
- the API creates and publishes no ``MOMO_HOD`` or ``AP_`` name at all;
- a sendable record is reused only while its stored spec is the one the code
  builds (``session_runner.ensure_strategy``).

A new sendable identity joins ``SENDABLE_IDENTITIES`` only in a reviewed commit.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("autotrader.shadow.authority")

# The bridge's ORDER_STRATEGY_PREFIX; a test pins the two to the same text.
SENDABLE_NAME_PREFIX = "MOMO_HOD"
SENDABLE_IDENTITIES: tuple[str, ...] = ("MOMO_HOD_V2",)
# Only code creates these: sendable identities and autopilot-managed ones.
API_RESERVED_NAME_PREFIXES: tuple[str, ...] = (SENDABLE_NAME_PREFIX, "AP_")

# Refusal codes: the same literal is logged, raised and returned by the API.
SENDABLE_NAME_REFUSED = "sendable_name_refused"
SENDABLE_SPEC_MISMATCH = "sendable_spec_mismatch"
RESERVED_STRATEGY_NAME = "reserved_strategy_name"


class SendableIdentityError(PermissionError):
    """A strategy name or record that could reach the order bridge was refused."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _normalized(name: str) -> str:
    # The bridge strips the stored name and then matches case-sensitively;
    # upper() keeps every check here at least as wide as the bridge's.
    return name.strip().upper()


def carries_sendable_prefix(name: str) -> bool:
    """Whether the bridge could send for ``name`` (or for a case variant of it)."""
    return _normalized(name).startswith(SENDABLE_NAME_PREFIX)


def is_api_reserved_name(name: str) -> bool:
    """Whether ``name`` belongs to code-created identities the API never makes."""
    normalized = _normalized(name)
    return any(normalized.startswith(prefix) for prefix in API_RESERVED_NAME_PREFIXES)


def require_creatable_name(name: str) -> None:
    """Refuse a ``MOMO_HOD`` name unless it is exactly a sendable identity.

    Every caller that reaches this refusal bypassed the API's own check, so it
    is a code path that must not exist: logged CRITICAL with its stack.
    """
    if carries_sendable_prefix(name) and name not in SENDABLE_IDENTITIES:
        logger.critical(
            "%s: refused to store strategy name %r; sendable identities are %s",
            SENDABLE_NAME_REFUSED,
            name,
            ", ".join(SENDABLE_IDENTITIES),
            stack_info=True,
        )
        raise SendableIdentityError(
            SENDABLE_NAME_REFUSED,
            f"{name!r} would be sent by the PAPER bridge but is not a sendable identity",
        )


__all__ = [
    "API_RESERVED_NAME_PREFIXES",
    "RESERVED_STRATEGY_NAME",
    "SENDABLE_IDENTITIES",
    "SENDABLE_NAME_PREFIX",
    "SENDABLE_NAME_REFUSED",
    "SENDABLE_SPEC_MISMATCH",
    "SendableIdentityError",
    "carries_sendable_prefix",
    "is_api_reserved_name",
    "require_creatable_name",
]
