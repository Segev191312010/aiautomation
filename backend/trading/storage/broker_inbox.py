"""Append-only broker observations, including events with no known bracket."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

from trading.execution.tws_paper_transport import (
    BrokerEvent, BrokerErrorEvent, ConnectionClosedEvent, ExecutionEvent,
    OpenOrderEvent, OrderStatusEvent, PositionEvent,
)


BROKER_INBOX_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS autotrader_broker_inbox (
        session_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK(sequence >= 0),
        owner_id TEXT NOT NULL,
        account TEXT NOT NULL,
        client_id INTEGER NOT NULL CHECK(client_id > 0),
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        fingerprint TEXT NOT NULL,
        PRIMARY KEY(session_id, sequence)
    )""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_broker_inbox_no_update
    BEFORE UPDATE ON autotrader_broker_inbox
    BEGIN SELECT RAISE(ABORT, 'broker inbox is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS autotrader_broker_inbox_no_delete
    BEFORE DELETE ON autotrader_broker_inbox
    BEGIN SELECT RAISE(ABORT, 'broker inbox is immutable'); END""",
)

BROKER_SESSION_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS autotrader_broker_sessions (
        session_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        account TEXT NOT NULL,
        client_id INTEGER NOT NULL CHECK(client_id > 0),
        state TEXT NOT NULL CHECK(state IN ('open','closed','unreconciled')),
        started_at TEXT NOT NULL,
        closed_at TEXT NULL,
        last_sequence INTEGER NOT NULL DEFAULT -1 CHECK(last_sequence >= -1)
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS autotrader_one_unresolved_broker_session
    ON autotrader_broker_sessions(account, client_id)
    WHERE state IN ('open','unreconciled')""",
)


class UnreconciledBrokerSessionError(RuntimeError):
    """A prior broker session must be reconciled before another can begin."""


def _encode(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("broker event contains a nonfinite number")
        return str(value)
    raise TypeError("unsupported broker event value")


class SQLiteBrokerInbox:
    async def begin_session(
        self, *, session_id: str, owner_id: str, account: str, client_id: int,
    ) -> None:
        from db.core import transaction

        if not all(value.strip() for value in (session_id, owner_id, account)) or client_id <= 0:
            raise ValueError("broker session scope is invalid")
        async with transaction() as database:
            async with database.execute(
                "SELECT owner_id, account, client_id, state FROM autotrader_broker_sessions "
                "WHERE session_id=?", (session_id,),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                if tuple(existing) == (owner_id, account, client_id, "open"):
                    return
                raise UnreconciledBrokerSessionError("broker session identity cannot be reused")
            async with database.execute(
                "SELECT 1 FROM autotrader_broker_sessions WHERE account=? AND client_id=? "
                "AND state IN ('open','unreconciled')", (account, client_id),
            ) as cursor:
                if await cursor.fetchone() is not None:
                    raise UnreconciledBrokerSessionError(
                        "prior broker session requires reconciliation"
                    )
            await database.execute(
                "INSERT INTO autotrader_broker_sessions "
                "(session_id, owner_id, account, client_id, state, started_at) "
                "VALUES (?, ?, ?, ?, 'open', ?)",
                (session_id, owner_id, account, client_id, datetime.now(timezone.utc).isoformat()),
            )

    async def close_session(
        self, *, session_id: str, owner_id: str, account: str, client_id: int,
        requires_reconciliation: bool,
    ) -> None:
        from db.core import transaction

        state = "unreconciled" if requires_reconciliation else "closed"
        async with transaction() as database:
            cursor = await database.execute(
                "UPDATE autotrader_broker_sessions SET state=?, closed_at=? "
                "WHERE session_id=? AND owner_id=? AND account=? AND client_id=? AND state='open'",
                (state, datetime.now(timezone.utc).isoformat(), session_id, owner_id, account, client_id),
            )
            if cursor.rowcount != 1:
                raise UnreconciledBrokerSessionError("open broker session was not found")

    async def append(
        self, *, session_id: str, sequence: int, owner_id: str,
        account: str, client_id: int, event: BrokerEvent,
    ) -> None:
        from db.core import transaction

        if not all(value.strip() for value in (session_id, owner_id, account)):
            raise ValueError("broker inbox scope is required")
        if isinstance(sequence, bool) or sequence < 0 or isinstance(client_id, bool) or client_id <= 0:
            raise ValueError("invalid broker inbox sequence or client ID")
        if type(event) not in (
            OpenOrderEvent, OrderStatusEvent, ExecutionEvent, PositionEvent,
            BrokerErrorEvent, ConnectionClosedEvent,
        ):
            raise TypeError("unsupported broker event")
        payload = asdict(event)
        if isinstance(event, BrokerErrorEvent):
            payload.pop("message")
        encoded = json.dumps(payload, default=_encode, sort_keys=True, separators=(",", ":"))
        event_type = type(event).__name__
        digest = sha256((event_type + encoded).encode()).hexdigest()
        values = (owner_id, account, client_id, event_type, encoded, digest)
        async with transaction() as database:
            async with database.execute(
                "SELECT owner_id, account, client_id, state, last_sequence "
                "FROM autotrader_broker_sessions WHERE session_id=?", (session_id,),
            ) as cursor:
                session = await cursor.fetchone()
            if session is None or tuple(session[:4]) != (owner_id, account, client_id, "open"):
                raise UnreconciledBrokerSessionError(
                    "broker observation scope does not match an open session"
                )
            async with database.execute(
                "SELECT owner_id, account, client_id, event_type, payload_json, fingerprint "
                "FROM autotrader_broker_inbox WHERE session_id=? AND sequence=?",
                (session_id, sequence),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                if tuple(existing) != values:
                    raise ValueError("broker inbox sequence conflicts with persisted observation")
                if int(session[4]) < sequence:
                    raise UnreconciledBrokerSessionError("broker session cursor is inconsistent")
                return
            if int(session[4]) != sequence - 1:
                raise UnreconciledBrokerSessionError("broker observation sequence is not contiguous")
            await database.execute(
                "INSERT INTO autotrader_broker_inbox "
                "(session_id, sequence, owner_id, account, client_id, event_type, payload_json, fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, sequence, *values),
            )
            cursor = await database.execute(
                "UPDATE autotrader_broker_sessions SET last_sequence=? "
                "WHERE session_id=? AND owner_id=? AND account=? AND client_id=? "
                "AND state='open' AND last_sequence=?",
                (sequence, session_id, owner_id, account, client_id, sequence - 1),
            )
            if cursor.rowcount != 1:
                raise UnreconciledBrokerSessionError(
                    "broker observation sequence is not attached to an open session"
                )
