"""Code-owned PAPER authority for the reviewed execution engine.

PAPER order authority for the engine comes from exactly one place: an
explicit, append-only promotion record (``autotrader_exec_promotions``,
migration 13) written by ``autotraderctl execution promote``. Nothing else can
grant it:

* only strategies whose catalogue name starts with a code-owned prefix
  (``MOMO_HOD``, owner decision 2026-10-12) can be promoted, and a record for
  any other name grants nothing even if it is written into the table by hand;
* a grant binds the SHA-256 of the engine's source files (the engine and every
  trading module it imports), so any change to the order-sending code needs a
  new explicit grant after its review, and the engine refuses to start when the
  code it runs is not the code its authority names;
* a revoke ends every grant of that strategy and account;
* operator configuration (:class:`ExecutionSwitches`) can only switch entries
  off; it has no field that can add a strategy or an account;
* PAPER accounts only: every record names a ``DU`` account.

The engine refuses to start without an active grant for its account and code
digest. No environment variable is read here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

from db.core import get_db, transaction


# Owner decision 2026-10-12: only MOMO_HOD strategies may send PAPER orders.
PROMOTABLE_STRATEGY_PREFIXES: tuple[str, ...] = ("MOMO_HOD",)
# The files whose exact content a promotion binds (relative to backend/): the engine and every
# trading module it imports, the kill switch, the manual-order and bridge records and the
# session calendar included (a test keeps this list complete).
ENGINE_SOURCE_FILES: tuple[str, ...] = (
    "trading/execution/live_executor.py",
    "trading/execution/manual_orders.py",
    "trading/execution/promotion.py",
    "trading/execution/tws_state.py",
    "trading/operations/paths.py",
    "trading/risk/book.py",
    "trading/scheduler/sessions.py",
    "trading/storage/execution_ledger.py",
    "trading/storage/execution_ledger_schema.py",
    "trading/storage/runtime_fence.py",
)
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SWITCH_KEYS = frozenset({"entries_enabled", "disabled_strategies"})

AuthorityCode = Literal[
    "promotion_missing",
    "strategy_not_promotable",
    "strategy_not_promoted",
    "strategy_disabled",
    "entries_disabled",
    "not_paper_account",
    "invalid_record",
    "code_mismatch",
]


class ExecutionAuthorityError(PermissionError):
    """PAPER authority is missing or a request would exceed it."""

    def __init__(self, code: AuthorityCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def engine_code_digest(root: Path | None = None) -> str:
    """SHA-256 over the engine's source files; a missing file fails closed."""
    base = root or _BACKEND_ROOT
    digest = sha256()
    for relative in sorted(ENGINE_SOURCE_FILES):
        path = base / relative
        if not path.is_file():
            raise ExecutionAuthorityError("invalid_record", f"engine source file missing: {relative}")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def is_promotable(strategy_name: str) -> bool:
    name = strategy_name.strip()
    return name == strategy_name and any(name.startswith(prefix) for prefix in PROMOTABLE_STRATEGY_PREFIXES)


def is_paper_account(account: str) -> bool:
    return isinstance(account, str) and account.startswith("DU") and len(account) > 2 and account == account.strip()


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    seq: int
    id: str
    action: Literal["grant", "revoke"]
    strategy_name: str
    account: str
    code_digest: str | None
    actor: str
    reason: str
    created_at: datetime


def _now(value: datetime | None) -> str:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("promotion timestamps must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _require_text(value: str, name: str, limit: int) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > limit:
        raise ExecutionAuthorityError("invalid_record", f"{name} must be 1-{limit} characters")
    return text


async def _append(
    *, action: Literal["grant", "revoke"], strategy_name: str, account: str, code_digest: str | None,
    actor: str, reason: str, now: datetime | None,
) -> PromotionRecord:
    record_id = uuid4().hex
    created_at = _now(now)
    async with transaction() as db:
        cursor = await db.execute(
            """INSERT INTO autotrader_exec_promotions
            (id, action, strategy_name, account, code_digest, actor, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (record_id, action, strategy_name, account, code_digest, actor, reason, created_at),
        )
        seq = int(cursor.lastrowid or 0)
    return PromotionRecord(seq=seq, id=record_id, action=action, strategy_name=strategy_name, account=account,
                           code_digest=code_digest, actor=actor, reason=reason,
                           created_at=datetime.fromisoformat(created_at))


def require_grantable(strategy_name: str, account: str) -> None:
    """Refuse a grant that could never be valid, before anything is written."""
    if not is_promotable(strategy_name):
        raise ExecutionAuthorityError(
            "strategy_not_promotable",
            f"{strategy_name!r} is not a promotable strategy (code-owned prefixes: "
            f"{', '.join(PROMOTABLE_STRATEGY_PREFIXES)})",
        )
    if not is_paper_account(account):
        raise ExecutionAuthorityError("not_paper_account", "promotions name a DU paper account only")


async def grant_promotion(
    *, strategy_name: str, account: str, actor: str, reason: str, code_digest: str | None = None,
    now: datetime | None = None,
) -> PromotionRecord:
    """Record the owner's explicit PAPER grant for one strategy, account and engine build."""
    require_grantable(strategy_name, account)
    return await _append(
        action="grant", strategy_name=strategy_name, account=account,
        code_digest=code_digest or engine_code_digest(), actor=_require_text(actor, "actor", 128),
        reason=_require_text(reason, "reason", 500), now=now,
    )


async def revoke_promotion(
    *, strategy_name: str, account: str, actor: str, reason: str, now: datetime | None = None,
) -> PromotionRecord:
    """End every grant of a strategy on an account. Always allowed: it only removes authority."""
    if not is_paper_account(account):
        raise ExecutionAuthorityError("not_paper_account", "promotions name a DU paper account only")
    name = _require_text(strategy_name, "strategy name", 120)
    return await _append(
        action="revoke", strategy_name=name, account=account, code_digest=None,
        actor=_require_text(actor, "actor", 128), reason=_require_text(reason, "reason", 500), now=now,
    )


async def promotion_history(*, account: str | None = None) -> list[PromotionRecord]:
    query = (
        "SELECT seq, id, action, strategy_name, account, code_digest, actor, reason, created_at"
        " FROM autotrader_exec_promotions"
    )
    params: tuple[object, ...] = ()
    if account is not None:
        query += " WHERE account=?"
        params = (account,)
    async with get_db() as db:
        async with db.execute(query + " ORDER BY seq", params) as cursor:
            rows = await cursor.fetchall()
    return [
        PromotionRecord(
            seq=int(row[0]), id=str(row[1]), action=str(row[2]),  # type: ignore[arg-type]
            strategy_name=str(row[3]), account=str(row[4]), code_digest=None if row[5] is None else str(row[5]),
            actor=str(row[6]), reason=str(row[7]), created_at=datetime.fromisoformat(str(row[8])),
        )
        for row in rows
    ]


def active_strategies(records: list[PromotionRecord], *, account: str, code_digest: str) -> frozenset[str]:
    """Strategies whose newest record for this account is a grant of this exact code (pure)."""
    state: dict[str, bool] = {}
    for record in sorted(records, key=lambda item: item.seq):
        if record.account != account:
            continue
        if record.action == "revoke":
            state[record.strategy_name] = False
        elif record.code_digest == code_digest:
            state[record.strategy_name] = True
    # Code owns the eligible set: a hand-written record for any other name grants nothing.
    return frozenset(name for name, active in state.items() if active and is_promotable(name))


@dataclass(frozen=True, slots=True)
class ExecutionSwitches:
    """Operator configuration for the engine: it can only switch entries off."""

    entries_enabled: bool = True
    disabled_strategies: frozenset[str] = frozenset()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object] | None) -> "ExecutionSwitches":
        """Parse an ``[execution]`` config table; anything unexpected fails closed."""
        if raw is None:
            return cls()
        unknown = set(raw) - _SWITCH_KEYS
        if unknown:
            raise ExecutionAuthorityError(
                "invalid_record", f"unknown execution switches {sorted(unknown)} (config can only disable)"
            )
        enabled = raw.get("entries_enabled", True)
        if not isinstance(enabled, bool):
            raise ExecutionAuthorityError("invalid_record", "entries_enabled must be true or false")
        disabled = raw.get("disabled_strategies", ())
        if isinstance(disabled, str) or not all(isinstance(item, str) for item in disabled):  # type: ignore[union-attr]
            raise ExecutionAuthorityError("invalid_record", "disabled_strategies must be a list of names")
        return cls(entries_enabled=enabled, disabled_strategies=frozenset(disabled))  # type: ignore[arg-type]


class EngineAuthority:
    """The promotions that let this engine build send entries for one PAPER account."""

    def __init__(
        self, *, account: str, switches: ExecutionSwitches | None = None, code_digest: str | None = None,
    ) -> None:
        if not is_paper_account(account):
            raise ExecutionAuthorityError("not_paper_account", "the engine trades a DU paper account only")
        self.account = account
        self.switches = switches or ExecutionSwitches()
        self.code_digest = code_digest or engine_code_digest()
        self.active: frozenset[str] = frozenset()

    async def refresh(self) -> frozenset[str]:
        self.active = active_strategies(
            await promotion_history(account=self.account), account=self.account, code_digest=self.code_digest
        )
        return self.active

    async def require_start(self) -> frozenset[str]:
        """The engine never starts without an explicit grant for this account and code."""
        active = await self.refresh()
        if not active:
            raise ExecutionAuthorityError(
                "promotion_missing",
                f"no active PAPER promotion for {self.account} and engine code {self.code_digest[:12]}",
            )
        return active

    def entry_refusal(self, strategy_name: str) -> tuple[AuthorityCode, str] | None:
        """Why a strategy's new entry is not allowed, or None. Exits never ask."""
        if not is_promotable(strategy_name):
            return "strategy_not_promotable", "shadow-only strategy"
        if strategy_name not in self.active:
            return "strategy_not_promoted", "strategy has no active PAPER promotion"
        if not self.switches.entries_enabled:
            return "entries_disabled", "entries disabled by configuration"
        if strategy_name in self.switches.disabled_strategies:
            return "strategy_disabled", "strategy disabled by configuration"
        return None


__all__ = [
    "ENGINE_SOURCE_FILES",
    "PROMOTABLE_STRATEGY_PREFIXES",
    "AuthorityCode",
    "EngineAuthority",
    "ExecutionAuthorityError",
    "ExecutionSwitches",
    "PromotionRecord",
    "active_strategies",
    "engine_code_digest",
    "grant_promotion",
    "is_paper_account",
    "is_promotable",
    "promotion_history",
    "require_grantable",
    "revoke_promotion",
]
