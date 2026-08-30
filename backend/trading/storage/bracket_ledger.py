"""SQLite ledger for staged IBKR PAPER bracket intents.

The executor saves every group and leg before it performs any broker I/O.  The
ledger only records observed state; it never issues a broker command or tries
to repair an unsafe bracket.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

import aiosqlite

from db.core import transaction
from trading.execution.ibkr_paper import (
    BracketGroupRecord,
    BracketGroupState,
    BracketLegRecord,
    BracketLegState,
)
from trading.contracts import TradeIntent
from trading.execution.ibkr_paper import PaperSubmissionContext


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteBracketLedger:
    """Persist immutable bracket intent followed by monotonic safety observations."""

    async def stage(
        self, group: BracketGroupRecord, legs: tuple[BracketLegRecord, ...]
    ) -> None:
        if tuple(leg.leg_type for leg in legs) != ("parent", "target", "stop"):
            raise ValueError("PAPER brackets require exactly parent, target, and protective stop legs")
        if any(leg.group_id != group.id or leg.state != "staged" for leg in legs):
            raise ValueError("all staged legs must belong to the staged bracket group")
        if tuple(leg.transmit for leg in legs) != (False, False, True):
            raise ValueError("only the final protective stop leg may transmit")
        if len({leg.broker_order_id for leg in legs}) != 3 or any(
            leg.broker_order_id <= 0 for leg in legs
        ):
            raise ValueError("bracket order IDs must be unique positive values")
        if any(leg.quantity != group.quantity or leg.quantity <= 0 for leg in legs):
            raise ValueError("bracket leg quantities must match the approved group quantity")
        if legs[1].oca_group != group.oca_group or legs[2].oca_group != group.oca_group:
            raise ValueError("protective bracket legs must share the group OCA identifier")
        if legs[0].oca_group is not None:
            raise ValueError("the parent order cannot be part of the protective OCA group")
        async with transaction() as db:
            await self._validate_stage_scope(db, group)
            await db.execute(
                """
                INSERT INTO autotrader_bracket_groups
                (id, owner_id, run_id, candidate_id, risk_reservation_id, execution_key,
                 account, client_id, symbol, quantity, entry_price, stop_price, target_price,
                 oca_group, state, manual_intervention_reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', NULL, ?, ?)
                """,
                (
                    group.id,
                    group.context.owner_id,
                    group.context.run_id,
                    group.context.candidate_id,
                    group.context.risk_reservation_id,
                    group.execution_key,
                    group.account,
                    group.client_id,
                    group.symbol,
                    group.quantity,
                    str(group.entry_price),
                    str(group.stop_price),
                    str(group.target_price),
                    group.oca_group,
                    group.created_at.isoformat(),
                    group.updated_at.isoformat(),
                ),
            )
            for leg in legs:
                await db.execute(
                    """
                    INSERT INTO autotrader_bracket_legs
                    (id, group_id, leg_type, broker_order_id, parent_order_id, perm_id, order_ref,
                     oca_group, quantity, price, transmit, state)
                    VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 'staged')
                    """,
                    (
                        leg.id,
                        leg.group_id,
                        leg.leg_type,
                        str(leg.broker_order_id),
                        None if leg.leg_type == "parent" else str(legs[0].broker_order_id),
                        leg.order_ref,
                        leg.oca_group,
                        leg.quantity,
                        str(leg.price),
                        int(leg.transmit),
                    ),
                )

    async def update_group(
        self,
        group_id: str,
        *,
        owner_id: str,
        state: BracketGroupState,
        reason: str | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("bracket group owner is required")
        if state == "manual_intervention" and not reason:
            raise ValueError("manual intervention requires a non-secret reason")
        async with transaction() as db:
            async with db.execute(
                "SELECT state FROM autotrader_bracket_groups WHERE id=? AND owner_id=?",
                (group_id, owner_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise LookupError("bracket group not found")
            current = str(row[0])
            allowed = {
                "staged": {"submitted", "failed", "manual_intervention"},
                "submitted": {"filled", "canceled", "manual_intervention"},
                "filled": set(),
                "canceled": set(),
                "failed": set(),
                "manual_intervention": set(),
            }
            if state not in allowed.get(current, set()):
                raise ValueError(f"unsafe bracket state transition: {current} -> {state}")
            cursor = await db.execute(
                """
                UPDATE autotrader_bracket_groups
                SET state=?, manual_intervention_reason=?, updated_at=?
                WHERE id=? AND owner_id=? AND state=?
                """,
                (state, reason, _timestamp(), group_id, owner_id, current),
            )
            if cursor.rowcount != 1:
                raise LookupError("bracket group not found")

    async def update_leg(
        self,
        leg_id: str,
        *,
        owner_id: str,
        state: BracketLegState,
        perm_id: str | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("bracket leg owner is required")
        async with transaction() as db:
            async with db.execute(
                """
                SELECT leg.state
                FROM autotrader_bracket_legs leg
                JOIN autotrader_bracket_groups bracket ON bracket.id=leg.group_id
                WHERE leg.id=? AND bracket.owner_id=?
                """,
                (leg_id, owner_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise LookupError("bracket leg not found")
            current = str(row[0])
            allowed = {
                "staged": {"accepted", "canceled", "rejected", "unknown"},
                "accepted": {"filled", "canceled", "unknown"},
                "submitted": {"accepted", "canceled", "rejected", "unknown"},
                "filled": set(),
                "canceled": set(),
                "rejected": set(),
                "unknown": set(),
            }
            if state not in allowed.get(current, set()):
                raise ValueError(f"unsafe bracket leg transition: {current} -> {state}")
            cursor = await db.execute(
                """
                UPDATE autotrader_bracket_legs
                SET state=?, perm_id=?
                WHERE id=? AND state=?
                  AND group_id IN (
                    SELECT id FROM autotrader_bracket_groups WHERE owner_id=?
                  )
                """,
                (state, perm_id, leg_id, current, owner_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("bracket leg not found")

    async def execution_key_exists(self, execution_key: str) -> bool:
        async with transaction() as db:
            async with db.execute(
                "SELECT 1 FROM autotrader_bracket_groups WHERE execution_key=?", (execution_key,)
            ) as cursor:
                return await cursor.fetchone() is not None

    async def fence(self, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("execution fence reason is required")
        async with transaction() as db:
            await db.execute(
                """
                INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at)
                VALUES ('paper', 1, ?, ?)
                ON CONFLICT(name) DO UPDATE SET stopped=1, reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (reason, _timestamp()),
            )
    async def validate_reservation(
        self, *, intent: TradeIntent, context: PaperSubmissionContext
    ) -> None:
        """Require the still-active reservation to bind this exact risk-approved intent."""
        token_hash = sha256(intent.approval_token.encode("utf-8")).hexdigest()
        async with transaction() as db:
            async with db.execute(
                """
                SELECT rr.execution_key, rr.approval_token_hash, rr.setup_fingerprint,
                       rr.quantity, rr.symbol, rr.policy_fingerprint, rr.state, rr.expires_at,
                       run.owner_id, run.strategy_id, run.strategy_version,
                       candidate.con_id, candidate.symbol, candidate.contract_json
                FROM autotrader_risk_reservations rr
                JOIN autotrader_runs run ON run.id=rr.run_id
                JOIN autotrader_scanner_runs scanner_run ON scanner_run.run_id=run.id
                JOIN autotrader_candidates candidate ON candidate.scanner_run_id=scanner_run.id
                WHERE rr.id=? AND rr.run_id=? AND rr.candidate_id=? AND candidate.id=?
                """,
                (
                    context.risk_reservation_id,
                    context.run_id,
                    context.candidate_id,
                    context.candidate_id,
                ),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise PermissionError("risk reservation is unavailable")
        (
            execution_key,
            approval_hash,
            setup_fingerprint,
            quantity,
            reservation_symbol,
            _policy_fingerprint,
            state,
            expires_at,
            owner_id,
            strategy_id,
            strategy_version,
            con_id,
            candidate_symbol,
            contract_json,
        ) = row
        try:
            contract = json.loads(str(contract_json))
        except json.JSONDecodeError as exc:
            raise PermissionError("qualified candidate contract is invalid") from exc
        if str(owner_id) != context.owner_id:
            raise PermissionError("risk reservation owner mismatch")
        if str(strategy_id) != intent.strategy_id or int(strategy_version) != intent.strategy_version:
            raise PermissionError("risk reservation strategy mismatch")
        if str(reservation_symbol) != intent.symbol or str(candidate_symbol) != intent.symbol:
            raise PermissionError("risk reservation symbol mismatch")
        if int(con_id) != context.con_id:
            raise PermissionError("qualified candidate contract mismatch")
        if (
            contract.get("currency") != context.currency
            or contract.get("security_type") != context.security_type
            or contract.get("stock_type") != "COMMON"
            or contract.get("primary_exchange") != context.primary_exchange
        ):
            raise PermissionError("qualified candidate security metadata mismatch")
        if str(execution_key) != intent.execution_key:
            raise PermissionError("risk reservation execution key mismatch")
        if str(approval_hash) != token_hash:
            raise PermissionError("risk reservation approval token mismatch")
        if str(setup_fingerprint) != intent.setup_fingerprint:
            raise PermissionError("risk reservation setup mismatch")
        if int(quantity) != intent.quantity:
            raise PermissionError("risk reservation quantity mismatch")
        if str(state) != "active":
            raise PermissionError("risk reservation is not active")
        if datetime.fromisoformat(str(expires_at)) <= datetime.now(timezone.utc):
            raise PermissionError("risk reservation has expired")

    @staticmethod
    async def _validate_stage_scope(
        db: aiosqlite.Connection, group: BracketGroupRecord
    ) -> None:
        """Bind an immutable bracket record to one admitted, owned risk reservation."""
        context = group.context
        async with db.execute(
            """
            SELECT rr.symbol, rr.quantity, rr.execution_key, rr.state,
                   run.owner_id, candidate.con_id, candidate.symbol, candidate.contract_json
            FROM autotrader_risk_reservations rr
            JOIN autotrader_runs run ON run.id=rr.run_id
            JOIN autotrader_scanner_runs scanner_run ON scanner_run.run_id=run.id
            JOIN autotrader_candidates candidate ON candidate.scanner_run_id=scanner_run.id
            WHERE rr.id=? AND rr.run_id=? AND rr.candidate_id=? AND candidate.id=?
            """,
            (context.risk_reservation_id, context.run_id, context.candidate_id, context.candidate_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise PermissionError("bracket risk reservation is unavailable")
        (
            reservation_symbol,
            reservation_quantity,
            reservation_execution_key,
            reservation_state,
            owner_id,
            con_id,
            candidate_symbol,
            contract_json,
        ) = row
        try:
            contract = json.loads(str(contract_json))
        except json.JSONDecodeError as exc:
            raise PermissionError("qualified candidate contract is invalid") from exc
        if (
            str(owner_id) != context.owner_id
            or str(reservation_symbol) != group.symbol
            or str(candidate_symbol) != group.symbol
            or int(reservation_quantity) != group.quantity
            or str(reservation_execution_key) != group.execution_key
            or str(reservation_state) != "active"
            or int(con_id) != context.con_id
        ):
            raise PermissionError("bracket scope does not match the active risk reservation")
        if (
            contract.get("currency") != context.currency
            or contract.get("security_type") != context.security_type
            or contract.get("stock_type") != "COMMON"
            or contract.get("primary_exchange") != context.primary_exchange
        ):
            raise PermissionError("bracket context does not match the qualified common stock")
