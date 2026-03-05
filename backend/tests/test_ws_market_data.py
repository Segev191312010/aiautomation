"""
Tests for market-data websocket quote fanout (IBKR priority + Yahoo fallback).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_PATH", "test_trading.db")
os.environ.setdefault("SIM_MODE", "true")

import main


def test_is_crypto_usd_symbol():
    assert main._is_crypto_usd_symbol("BTC-USD") is True
    assert main._is_crypto_usd_symbol("ETH-USD") is True
    assert main._is_crypto_usd_symbol("AAPL") is False
    assert main._is_crypto_usd_symbol("BRK.B") is False


def test_fetch_coinbase_spot_success(monkeypatch: pytest.MonkeyPatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"data":{"amount":"68300.12"}}'

    monkeypatch.setattr(main.urllib.request, "urlopen", lambda *_args, **_kwargs: _Resp())
    resolved = main._fetch_coinbase_spot("BTC-USD")
    assert resolved is not None
    price, quote_ts, market_state = resolved
    assert price == 68300.12
    assert isinstance(quote_ts, int)
    assert market_state == "open"


def test_fetch_coinbase_spot_prefers_exchange_ticker(monkeypatch: pytest.MonkeyPatch):
    class _Resp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._payload

    def _urlopen(req, **_kwargs):
        url = getattr(req, "full_url", str(req))
        if "api.exchange.coinbase.com" in url:
            return _Resp(b'{"price":"70123.45","time":"2026-03-04T08:41:30.240155965Z"}')
        return _Resp(b'{"data":{"amount":"68300.12"}}')

    monkeypatch.setattr(main.urllib.request, "urlopen", _urlopen)
    resolved = main._fetch_coinbase_spot("BTC-USD")
    assert resolved is not None
    price, quote_ts, market_state = resolved
    assert price == 70123.45
    assert quote_ts > 0
    assert market_state == "open"


def test_fetch_coinbase_spot_failure(monkeypatch: pytest.MonkeyPatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main.urllib.request, "urlopen", _boom)
    assert main._fetch_coinbase_spot("BTC-USD") is None


@pytest.mark.asyncio
async def test_ws_collect_quotes_prefers_ibkr(monkeypatch: pytest.MonkeyPatch):
    now = int(time.time())
    main._ws_ibkr_quotes.clear()
    main._ws_ibkr_quotes["AAPL"] = (201.25, now, "open")

    async def _noop_sync() -> None:
        return

    called = False

    async def _fake_yahoo(symbols: list[str]) -> dict[str, dict]:
        nonlocal called
        called = True
        return {sym: {"type": "quote", "symbol": sym, "price": 1.0, "time": now, "source": "yahoo", "market_state": "open", "stale_s": 0.0} for sym in symbols}

    monkeypatch.setattr(main, "_ws_sync_ibkr_subscriptions", _noop_sync)
    monkeypatch.setattr(main, "_ws_batch_prices", _fake_yahoo)
    monkeypatch.setattr(main.ibkr, "is_connected", lambda: True)

    quotes = await main._ws_collect_quotes(["AAPL"])
    assert quotes["AAPL"]["source"] == "ibkr"
    assert "market_state" in quotes["AAPL"]
    assert "stale_s" in quotes["AAPL"]
    assert called is False


@pytest.mark.asyncio
async def test_ws_collect_quotes_falls_back_to_yahoo(monkeypatch: pytest.MonkeyPatch):
    now = int(time.time())
    main._ws_ibkr_quotes.clear()

    async def _noop_sync() -> None:
        return

    async def _fake_yahoo(symbols: list[str]) -> dict[str, dict]:
        return {
            sym: {
                "type": "quote",
                "symbol": sym,
                "price": 99.5,
                "time": now,
                "source": "yahoo",
                "market_state": "closed",
                "stale_s": 0.0,
            }
            for sym in symbols
        }

    monkeypatch.setattr(main, "_ws_sync_ibkr_subscriptions", _noop_sync)
    monkeypatch.setattr(main, "_ws_batch_prices", _fake_yahoo)
    monkeypatch.setattr(main.ibkr, "is_connected", lambda: False)

    quotes = await main._ws_collect_quotes(["MSFT"])
    assert quotes["MSFT"]["source"] == "yahoo"
    assert quotes["MSFT"]["market_state"] == "closed"


@pytest.mark.asyncio
async def test_ws_ref_counting_subscribe_unsubscribe(monkeypatch: pytest.MonkeyPatch):
    main._ws_symbol_ref_counts.clear()
    main._ws_ibkr_subscribed_symbols.clear()

    async def _noop_sync() -> None:
        return

    unsubscribed: list[str] = []
    monkeypatch.setattr(main, "_ws_sync_ibkr_subscriptions", _noop_sync)
    monkeypatch.setattr(main, "unsubscribe_realtime", lambda sym: unsubscribed.append(sym))

    await main._ws_add_symbol_refs(["AAPL"])
    await main._ws_add_symbol_refs(["AAPL"])
    assert main._ws_symbol_ref_counts["AAPL"] == 2

    await main._ws_remove_symbol_refs(["AAPL"])
    assert main._ws_symbol_ref_counts["AAPL"] == 1
    assert unsubscribed == []

    await main._ws_remove_symbol_refs(["AAPL"])
    assert "AAPL" not in main._ws_symbol_ref_counts
    assert unsubscribed == ["AAPL"]


class _FakeWebSocket:
    def __init__(self, incoming: list[str], delay_first_receive_s: float = 0.0) -> None:
        self.headers = {}
        self._incoming = incoming
        self._delay_first_receive_s = delay_first_receive_s
        self._receive_calls = 0
        self.sent: list[str] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        return

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        self._receive_calls += 1
        if self._receive_calls == 1 and self._delay_first_receive_s > 0:
            await asyncio.sleep(self._delay_first_receive_s)
        if self._incoming:
            return self._incoming.pop(0)
        raise WebSocketDisconnect()


@pytest.mark.asyncio
async def test_ws_market_data_ping_pong(monkeypatch: pytest.MonkeyPatch):
    async def _noop_quotes(_symbols: list[str]) -> dict[str, dict]:
        return {}

    monkeypatch.setattr(main, "_ws_collect_quotes", _noop_quotes)

    ws = _FakeWebSocket([json.dumps({"action": "ping"})])
    await main.ws_market_data(ws)

    sent = [json.loads(m) for m in ws.sent]
    assert any(msg.get("type") == "pong" for msg in sent)


@pytest.mark.asyncio
async def test_ws_market_data_subscribe_accepts_type_alias(monkeypatch: pytest.MonkeyPatch):
    added: list[list[str]] = []

    async def _fake_add_symbol_refs(symbols: list[str]) -> None:
        added.append(symbols)

    monkeypatch.setattr(main, "_ws_add_symbol_refs", _fake_add_symbol_refs)
    ws = _FakeWebSocket([json.dumps({"type": "subscribe", "symbols": ["BTC-USD"]})])
    await main.ws_market_data(ws)

    assert added and "BTC-USD" in added[0]


@pytest.mark.asyncio
async def test_ws_market_data_heartbeat(monkeypatch: pytest.MonkeyPatch):
    async def _noop_quotes(_symbols: list[str]) -> dict[str, dict]:
        return {}

    monkeypatch.setattr(main, "_ws_collect_quotes", _noop_quotes)
    monkeypatch.setattr(main, "_WS_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(main, "_WS_PUSH_INTERVAL", 0.02)

    ws = _FakeWebSocket([], delay_first_receive_s=0.08)
    await main.ws_market_data(ws)

    sent = [json.loads(m) for m in ws.sent]
    assert any(msg.get("type") == "heartbeat" for msg in sent)
