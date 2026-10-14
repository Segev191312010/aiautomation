"""Atomic risk-reservation ledger for approved V1 trade intents."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from typing import Literal
from uuid import uuid4

import aiosqlite

from db.core import get_db, transaction
from trading.contracts import PortfolioSnapshot, RiskApproval, RiskPolicy


class ReservationError(ValueError):
    """Base error for a rejected or invalid reservation operation."""


class DuplicateReservationError(ReservationError):
    """A natural reservation key has already been used."""


class ReservationExpiredError(ReservationError):
    """An approval or reservation is no longer usable."""


ReservationState = Literal["active", "consumed", "released", "expired"]


@dataclass(frozen=True)
class ReservationRecord:
    id: str
    run_id: str
    candidate_id: str
    symbol: str
    execution_key: str
    setup_fingerprint: str
    ai_fingerprint: str
    policy_fingerprint: str
    portfolio_fingerprint: str
    quantity: int
    risk_amount: Decimal
    notional_amount: Decimal
    state: ReservationState
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None


def _now(value: datetime | None = None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return observed.astimezone(timezone.utc)


def _token_hash(token: str) -> str:
    if not token:
        raise ReservationError("approval token is required")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _record(row: tuple[object, ...]) -> ReservationRecord:
    return ReservationRecord(
        id=str(row[0]),
        run_id=str(row[1]),
        candidate_id=str(row[2]),
        symbol=str(row[3]),
        execution_key=str(row[4]),
        setup_fingerprint=str(row[5]),
        ai_fingerprint=str(row[6]),
        policy_fingerprint=str(row[7]),
        portfolio_fingerprint=str(row[8]),
        quantity=int(row[9]),
        risk_amount=Decimal(str(row[10])),
        notional_amount=Decimal(str(row[11])),
        state=str(row[12]),
        expires_at=datetime.fromisoformat(str(row[13])),
        created_at=datetime.fromisoformat(str(row[14])),
        consumed_at=datetime.fromisoformat(str(row[15])) if row[15] else None,
    )


class RiskReservationRepository:
    """Persist and consume risk approvals with SQLite write serialization."""

    _SELECT = """
        SELECT rr.id, rr.run_id, rr.candidate_id, rr.symbol, rr.execution_key,
               rr.setup_fingerprint, rr.ai_fingerprint, rr.policy_fingerprint,
               rr.portfolio_fingerprint, rr.quantity, rr.risk_amount,
               rr.notional_amount, rr.state, rr.expires_at, rr.created_at,
               rr.consumed_at
        FROM autotrader_risk_reservations rr
        JOIN autotrader_runs r ON r.id=rr.run_id
    """

    async def reserve(
        self,
        *,
        owner_id: str,
        run_id: str,
        candidate_id: str,
        approval: RiskApproval,
        policy: RiskPolicy,
        portfolio: PortfolioSnapshot,
        now: datetime | None = None,
    ) -> ReservationRecord:
        observed = _now(now)
        if not approval.approved or approval.intent is None:
            raise ReservationError("only approved risk results can be reserved")
        intent = approval.intent
        if approval.policy_fingerprint != policy.fingerprint():
            raise ReservationError("risk approval policy fingerprint mismatch")
        if approval.portfolio_fingerprint != portfolio.fingerprint:
            raise ReservationError("risk approval portfolio fingerprint mismatch")
        if intent.expires_at != approval.expires_at:
            raise ReservationError("risk approval and intent expiry mismatch")
        if approval.expires_at <= observed:
            raise ReservationExpiredError("risk approval has expired")
        if intent.risk_amount <= 0 or intent.notional_amount <= 0:
            raise ReservationError("risk approval amounts must be positive")

        risk_budget = portfolio.net_liquidation * policy.max_risk_per_trade_pct / Decimal("100")
        position_budget = portfolio.net_liquidation * policy.max_position_notional_pct / Decimal("100")
        total_budget = portfolio.net_liquidation * policy.max_total_notional_pct / Decimal("100")
        daily_loss = portfolio.daily_loss
        if daily_loss >= portfolio.net_liquidation * policy.max_daily_loss_pct / Decimal("100"):
            raise ReservationError("daily loss limit reached")
        if intent.risk_amount > risk_budget:
            raise ReservationError("trade risk exceeds policy limit")
        if intent.notional_amount > position_budget:
            raise ReservationError("position notional exceeds policy limit")

        async with transaction() as db:
            scope = await self._scope(
                db,
                owner_id=owner_id,
                run_id=run_id,
                candidate_id=candidate_id,
                symbol=intent.symbol,
                strategy_id=intent.strategy_id,
                strategy_version=intent.strategy_version,
            )
            if scope is None:
                raise ReservationError("risk approval scope is incoherent")
            if scope[0] != "admitted":
                raise ReservationError("risk reservation requires an admitted candidate")

            async with db.execute(
                """
                SELECT symbol, risk_amount, notional_amount
                FROM autotrader_risk_reservations
                WHERE run_id=? AND state='active'
                """,
                (run_id,),
            ) as cursor:
                active_rows = await cursor.fetchall()
            if any(str(row[0]) == intent.symbol for row in active_rows):
                raise DuplicateReservationError("an active reservation already covers this symbol")

            async with db.execute(
                """
                SELECT 1 FROM autotrader_risk_reservations
                WHERE run_id=? AND symbol=? AND state IN ('active','consumed')
                LIMIT 1
                """,
                (run_id, intent.symbol),
            ) as cursor:
                if await cursor.fetchone() is not None:
                    raise DuplicateReservationError("symbol already used in this run")

            active_symbols = {str(row[0]) for row in active_rows}
            occupied_symbols = {position.symbol for position in portfolio.positions}
            occupied_symbols.update(portfolio.pending_symbols)
            occupied_symbols.update(active_symbols)
            if intent.symbol in occupied_symbols:
                raise ReservationError("position or pending order already covers this symbol")
            if len(occupied_symbols) >= policy.max_open_positions:
                raise ReservationError("maximum open positions reached")

            active_risk = sum((Decimal(str(row[1])) for row in active_rows), Decimal("0"))
            active_notional = sum((Decimal(str(row[2])) for row in active_rows), Decimal("0"))
            reserved_risk = max(portfolio.reserved_risk, active_risk)
            reserved_notional = max(portfolio.reserved_notional, active_notional)
            current_notional = sum(
                (position.market_value for position in portfolio.positions), Decimal("0")
            )
            if intent.risk_amount + reserved_risk > risk_budget:
                raise ReservationError("risk reservation budget exceeded")
            if intent.notional_amount + current_notional + reserved_notional > total_budget:
                raise ReservationError("total notional budget exceeded")

            reservation_id = str(uuid4())
            created_at = observed.isoformat()
            try:
                await db.execute(
                    """
                    INSERT INTO autotrader_risk_reservations
                    (id, run_id, candidate_id, symbol, execution_key, approval_token_hash,
                     setup_fingerprint, ai_fingerprint, policy_fingerprint, portfolio_fingerprint,
                     quantity, risk_amount, notional_amount, state, expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        reservation_id,
                        run_id,
                        candidate_id,
                        intent.symbol,
                        intent.execution_key,
                        _token_hash(intent.approval_token),
                        approval.setup_fingerprint,
                        approval.ai_review_fingerprint,
                        approval.policy_fingerprint,
                        approval.portfolio_fingerprint,
                        intent.quantity,
                        str(intent.risk_amount),
                        str(intent.notional_amount),
                        approval.expires_at.isoformat(),
                        created_at,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise DuplicateReservationError("risk approval has already been reserved") from exc

        return ReservationRecord(
            id=reservation_id,
            run_id=run_id,
            candidate_id=candidate_id,
            symbol=intent.symbol,
            execution_key=intent.execution_key,
            setup_fingerprint=approval.setup_fingerprint,
            ai_fingerprint=approval.ai_review_fingerprint,
            policy_fingerprint=approval.policy_fingerprint,
            portfolio_fingerprint=approval.portfolio_fingerprint,
            quantity=intent.quantity,
            risk_amount=intent.risk_amount,
            notional_amount=intent.notional_amount,
            state="active",
            expires_at=approval.expires_at,
            created_at=observed,
            consumed_at=None,
        )

    async def get(self, *, reservation_id: str, owner_id: str) -> ReservationRecord | None:
        async with get_db() as db:
            async with db.execute(
                f"{self._SELECT} WHERE rr.id=? AND r.owner_id=?", (reservation_id, owner_id)
            ) as cursor:
                row = await cursor.fetchone()
        return _record(row) if row else None

    async def release(
        self, *, reservation_id: str, owner_id: str, now: datetime | None = None
    ) -> ReservationRecord:
        observed = _now(now)
        expired = False
        async with transaction() as db:
            record = await self._owned_record(db, reservation_id, owner_id)
            if record is None:
                raise ReservationError("risk reservation not found")
            if record.state != "active":
                raise ReservationError("risk reservation is no longer active")
            if record.expires_at <= observed:
                await db.execute(
                    "UPDATE autotrader_risk_reservations SET state='expired' WHERE id=? AND state='active'",
                    (reservation_id,),
                )
                expired = True
            else:
                await db.execute(
                    "UPDATE autotrader_risk_reservations SET state='released' WHERE id=? AND state='active'",
                    (reservation_id,),
                )
        if expired:
            raise ReservationExpiredError("risk reservation has expired")
        return replace(record, state="released")

    async def consume(
        self,
        *,
        reservation_id: str,
        owner_id: str,
        approval_token: str,
        now: datetime | None = None,
    ) -> ReservationRecord:
        observed = _now(now)
        token_hash = _token_hash(approval_token)
        expired = False
        async with transaction() as db:
            record = await self._owned_record(db, reservation_id, owner_id)
            if record is None:
                raise ReservationError("risk reservation not found")
            if record.state != "active":
                raise ReservationError("risk reservation is no longer active")
            if record.expires_at <= observed:
                await db.execute(
                    "UPDATE autotrader_risk_reservations SET state='expired' WHERE id=? AND state='active'",
                    (reservation_id,),
                )
                expired = True
            else:
                async with db.execute(
                    """
                    SELECT 1 FROM autotrader_risk_reservations
                    WHERE id=? AND approval_token_hash=? AND state='active'
                    """,
                    (reservation_id, token_hash),
                ) as cursor:
                    if await cursor.fetchone() is None:
                        raise ReservationError("approval token is invalid")
                consumed_at = observed.isoformat()
                cursor = await db.execute(
                    """
                    UPDATE autotrader_risk_reservations
                    SET state='consumed', consumed_at=?
                    WHERE id=? AND approval_token_hash=? AND state='active'
                    """,
                    (consumed_at, reservation_id, token_hash),
                )
                if cursor.rowcount != 1:
                    raise ReservationError("risk reservation was already consumed")
        if expired:
            raise ReservationExpiredError("risk reservation has expired")
        return replace(record, state="consumed", consumed_at=observed)

    async def expire(self, *, now: datetime | None = None) -> int:
        observed = _now(now)
        async with transaction() as db:
            cursor = await db.execute(
                """
                UPDATE autotrader_risk_reservations
                SET state='expired'
                WHERE state='active' AND expires_at<=?
                """,
                (observed.isoformat(),),
            )
            return int(cursor.rowcount)

    async def _owned_record(
        self, db: aiosqlite.Connection, reservation_id: str, owner_id: str
    ) -> ReservationRecord | None:
        async with db.execute(
            f"{self._SELECT} WHERE rr.id=? AND r.owner_id=?", (reservation_id, owner_id)
        ) as cursor:
            row = await cursor.fetchone()
        return _record(row) if row else None

    @staticmethod
    async def _scope(
        db: aiosqlite.Connection,
        *,
        owner_id: str,
        run_id: str,
        candidate_id: str,
        symbol: str,
        strategy_id: str,
        strategy_version: int,
    ) -> tuple[str, ...] | None:
        async with db.execute(
            """
            SELECT c.admission_status
            FROM autotrader_runs r
            JOIN autotrader_scanner_runs sr ON sr.run_id=r.id
            JOIN autotrader_candidates c ON c.scanner_run_id=sr.id
            WHERE r.id=? AND r.owner_id=? AND c.id=? AND c.symbol=?
              AND r.strategy_id=? AND r.strategy_version=?
            """,
            (run_id, owner_id, candidate_id, symbol, strategy_id, strategy_version),
        ) as cursor:
            row = await cursor.fetchone()
        return tuple(str(value) for value in row) if row else None


__all__ = [
    "DuplicateReservationError",
    "ReservationError",
    "ReservationExpiredError",
    "ReservationRecord",
    "RiskReservationRepository",
]
