from datetime import datetime, timezone
import asyncio
from types import SimpleNamespace

import aiosqlite
import pytest

from config import cfg
from trading.execution.tws_paper_transport import BrokerErrorEvent, ConnectionClosedEvent
from trading.execution.tws_paper_transport import TWSPaperTransport, TWSPaperTransportConfig
from trading.storage.broker_inbox import SQLiteBrokerInbox
from trading.storage.migrations import apply_migrations


@pytest.mark.asyncio
async def test_inbox_persists_unknown_event_and_rejects_conflicting_retry(tmp_path, monkeypatch):
    path = tmp_path / "inbox.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    async with aiosqlite.connect(path) as database:
        await apply_migrations(database)
    event = BrokerErrorEvent(request_id=-1, code=1100, message="DO_NOT_PERSIST_SECRET")
    scope = dict(session_id="session", sequence=0, owner_id="owner", account="DU_TEST", client_id=17)
    inbox = SQLiteBrokerInbox()
    await inbox.begin_session(session_id="session", owner_id="owner", account="DU_TEST", client_id=17)
    await inbox.append(**scope, event=event)
    await inbox.append(**scope, event=event)
    with pytest.raises(ValueError, match="conflicts"):
        await SQLiteBrokerInbox().append(**scope, event=ConnectionClosedEvent())
    with pytest.raises(RuntimeError, match="scope does not match"):
        await SQLiteBrokerInbox().append(**{**scope, "owner_id": "other"}, event=event)
    async with aiosqlite.connect(path) as database:
        async with database.execute("SELECT payload_json FROM autotrader_broker_inbox") as cursor:
            rows = await cursor.fetchall()
        assert len(rows) == 1
        assert "DO_NOT_PERSIST_SECRET" not in rows[0][0]
        assert '"code":1100' in rows[0][0]
        with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
            await database.execute("DELETE FROM autotrader_broker_inbox")


@pytest.mark.asyncio
async def test_inbox_preserves_distinct_duplicate_callback_observations(tmp_path, monkeypatch):
    path = tmp_path / "inbox.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    async with aiosqlite.connect(path) as database:
        await apply_migrations(database)
    event = ConnectionClosedEvent(received_at=datetime.now(timezone.utc))
    inbox = SQLiteBrokerInbox()
    await inbox.begin_session(session_id="session", owner_id="owner", account="DU_TEST", client_id=17)
    for sequence in (0, 1):
        await inbox.append(
            session_id="session", sequence=sequence, owner_id="owner",
            account="DU_TEST", client_id=17, event=event,
        )
    async with aiosqlite.connect(path) as database:
        async with database.execute("SELECT COUNT(*) FROM autotrader_broker_inbox") as cursor:
            assert (await cursor.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_transport_flushes_real_callback_shapes_once(tmp_path, monkeypatch):
    path = tmp_path / "inbox.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    async with aiosqlite.connect(path) as database:
        await apply_migrations(database)
    transport = TWSPaperTransport(TWSPaperTransportConfig(account="DU_TEST", client_id=17))
    transport._on_error(-1, 1100, "private raw message")
    transport._on_connection_closed()
    store = SQLiteBrokerInbox()
    await store.begin_session(
        session_id=transport._observation_session, owner_id="owner",
        account="DU_TEST", client_id=17,
    )
    transport._session_started = True
    assert await transport.persist_observations(store, owner_id="owner") == 2
    assert await transport.persist_observations(store, owner_id="owner") == 2
    with pytest.raises(ValueError, match="owner cannot change"):
        await transport.persist_observations(store, owner_id="other")
    async with aiosqlite.connect(path) as database:
        async with database.execute("SELECT COUNT(*) FROM autotrader_broker_inbox") as cursor:
            assert (await cursor.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_failed_flush_blocks_execution_and_preserves_cursor():
    from unittest.mock import AsyncMock

    transport = TWSPaperTransport(TWSPaperTransportConfig(account="DU_TEST", client_id=17))
    transport._append_event(ConnectionClosedEvent())
    store = AsyncMock()
    store.append.side_effect = OSError("write failure")
    with pytest.raises(OSError):
        await transport.persist_observations(store, owner_id="owner")
    assert transport._persisted_observations == 0
    assert transport._execution_blocked


@pytest.mark.asyncio
async def test_unresolved_session_blocks_replacement_until_reconciliation(tmp_path, monkeypatch):
    from trading.storage.broker_inbox import UnreconciledBrokerSessionError

    path = tmp_path / "sessions.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    async with aiosqlite.connect(path) as database:
        await apply_migrations(database)
    store = SQLiteBrokerInbox()
    await store.begin_session(
        session_id="crashed", owner_id="owner", account="DU_TEST", client_id=17,
    )
    with pytest.raises(UnreconciledBrokerSessionError, match="requires reconciliation"):
        await store.begin_session(
            session_id="replacement", owner_id="owner", account="DU_TEST", client_id=17,
        )
    await store.close_session(
        session_id="crashed", owner_id="owner", account="DU_TEST", client_id=17,
        requires_reconciliation=True,
    )
    with pytest.raises(UnreconciledBrokerSessionError, match="requires reconciliation"):
        await store.begin_session(
            session_id="replacement", owner_id="owner", account="DU_TEST", client_id=17,
        )


@pytest.mark.asyncio
async def test_clean_data_only_session_allows_replacement(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    async with aiosqlite.connect(path) as database:
        await apply_migrations(database)
    store = SQLiteBrokerInbox()
    await store.begin_session(session_id="first", owner_id="owner", account="DU_TEST", client_id=17)
    await store.close_session(
        session_id="first", owner_id="owner", account="DU_TEST", client_id=17,
        requires_reconciliation=False,
    )
    await store.begin_session(session_id="second", owner_id="owner", account="DU_TEST", client_id=17)


@pytest.mark.asyncio
async def test_background_writer_persists_callback_without_manual_flush(tmp_path, monkeypatch):
    path = tmp_path / "background.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    async with aiosqlite.connect(path) as database:
        await apply_migrations(database)
    persisted = asyncio.Event()
    store = SQLiteBrokerInbox()
    original_append = store.append

    async def append(**kwargs):
        await original_append(**kwargs)
        persisted.set()

    monkeypatch.setattr(store, "append", append)
    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST", client_id=17),
        observation_store=store, owner_id="owner",
    )
    await store.begin_session(
        session_id=transport._observation_session, owner_id="owner", account="DU_TEST", client_id=17,
    )
    transport._session_started = True
    await transport._start_observation_writer()
    try:
        transport._on_error(-1, 1100, "private callback text")
        await asyncio.wait_for(persisted.wait(), timeout=2)
        async with aiosqlite.connect(path) as database:
            async with database.execute("SELECT payload_json FROM autotrader_broker_inbox") as cursor:
                rows = await cursor.fetchall()
        assert len(rows) == 1
        assert "private callback text" not in rows[0][0]
    finally:
        await transport.disconnect()
    assert transport._writer_task.done()


@pytest.mark.asyncio
async def test_background_writer_failure_blocks_and_terminates():
    from unittest.mock import AsyncMock

    store = AsyncMock()
    store.append.side_effect = OSError("disk unavailable")
    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST", client_id=17),
        observation_store=store, owner_id="owner",
    )
    await transport._start_observation_writer()
    transport._append_event(ConnectionClosedEvent())
    with pytest.raises(OSError, match="disk unavailable"):
        await asyncio.wait_for(transport._writer_task, timeout=2)
    assert transport._execution_blocked
    assert transport._persisted_observations == 0
    store.append.side_effect = None
    assert await transport.persist_observations(store, owner_id="owner") == 1
    assert transport._execution_blocked


@pytest.mark.asyncio
async def test_final_disconnect_flush_failure_leaves_session_unreconciled():
    from trading.execution.tws_paper_transport import TWSPaperTransportError
    from unittest.mock import AsyncMock

    store = AsyncMock()
    store.append.side_effect = OSError("final flush failed")
    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST", client_id=17),
        observation_store=store, owner_id="owner",
    )
    transport._session_started = True
    transport._append_event(ConnectionClosedEvent())
    with pytest.raises(TWSPaperTransportError, match="not clean"):
        await transport.disconnect()
    assert store.close_session.await_args.kwargs["requires_reconciliation"] is True
    assert transport._execution_blocked


@pytest.mark.asyncio
async def test_disconnect_joins_callback_thread_before_final_flush():
    from unittest.mock import AsyncMock

    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST", client_id=17),
        observation_store=AsyncMock(), owner_id="owner",
    )
    store = transport._observation_store
    transport._session_started = True

    class CallbackThread:
        alive = True

        def join(self, timeout):
            transport._on_connection_closed()
            self.alive = False

        def is_alive(self):
            return self.alive

    transport._run_thread = CallbackThread()
    transport._client = SimpleNamespace(disconnect=lambda: None)
    await transport.disconnect()
    assert store.append.await_count == 1
    assert isinstance(store.append.await_args.kwargs["event"], ConnectionClosedEvent)
    assert store.close_session.await_args.kwargs["requires_reconciliation"] is False


@pytest.mark.asyncio
async def test_late_execution_during_thread_join_forces_unreconciled_close():
    from unittest.mock import AsyncMock

    store = AsyncMock()
    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST", client_id=17),
        observation_store=store, owner_id="owner",
    )
    transport._session_started = True

    class CallbackThread:
        alive = True

        def join(self, timeout):
            transport._on_execution(
                1, SimpleNamespace(),
                SimpleNamespace(
                    execId="late", orderId=100, permId=1, acctNumber="DU_TEST",
                    shares=1, price=100, side="BOT",
                ),
            )
            self.alive = False

        def is_alive(self):
            return self.alive

    transport._run_thread = CallbackThread()
    transport._client = SimpleNamespace(disconnect=lambda: None)
    await transport.disconnect()
    assert store.append.await_count == 1
    assert store.close_session.await_args.kwargs["requires_reconciliation"] is True
