"""Manual-order parser, validator and ledger (owner Telegram orders, no confirmation step)."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite
import pytest

from config import cfg
from db.core import init_db
import trading.execution.manual_orders as manual
from trading.execution.manual_orders import (
    WORD_TICKERS,
    ContractRef,
    LiveFacts,
    ManualOrderNotFound,
    ManualOrderRow,
    ParsedOrder,
    ParseRefusal,
    Quote,
    Rejection,
    ValidatedOrder,
    bridge_heartbeat_age,
    chat_usage,
    chat_usage_reserve,
    chat_usage_settle,
    claim_next_order,
    get_order,
    is_kill_switch_command,
    is_manual_window_open,
    ledger_facts,
    list_active_orders,
    list_cancel_requests,
    list_orders_updated_since,
    manual_order_window,
    mark_unknown_sending_rows,
    mark_update_processed,
    max_update_id,
    order_ref,
    parse_order_ref,
    parse_order_text,
    queue_order,
    read_bridge_heartbeat,
    record_update,
    request_cancel,
    revalidate_claimed,
    short_id,
    update_order_status,
    validate,
    write_bridge_heartbeat,
)


NY = ZoneInfo("America/New_York")
UTC = timezone.utc
T0 = datetime(2026, 10, 13, 10, 0, tzinfo=NY)  # an ordinary Tuesday XNYS session
CONTRACTS = {
    "AAPL": ContractRef(symbol="AAPL", con_id=265598, primary_exchange="NASDAQ"),
    "TSLA": ContractRef(symbol="TSLA", con_id=76792991, primary_exchange="NASDAQ"),
    "NVDA": ContractRef(symbol="NVDA", con_id=4815747, primary_exchange="NASDAQ"),
    "BRK B": ContractRef(symbol="BRK B", con_id=72063691, primary_exchange="NYSE"),
    "IT": ContractRef(symbol="IT", con_id=12345, primary_exchange="NYSE"),
}


def make_quote(
    *, now: datetime = T0, bid: str | None = "99.99", ask: str | None = "100.01",
    last: str | None = "100.00", age: timedelta = timedelta(seconds=1), real_time: bool = True,
) -> Quote:
    return Quote(
        bid=None if bid is None else Decimal(bid),
        ask=None if ask is None else Decimal(ask),
        last=None if last is None else Decimal(last),
        observed_at=now - age,
        real_time=real_time,
    )


def make_facts(**overrides: Any) -> LiveFacts:
    now = overrides.pop("now", T0)
    values: dict[str, Any] = {
        "now": now,
        "accounts_all_paper": True,
        "kill_switch_active": False,
        "bridge_heartbeat_age_s": 1.0,
        "net_liquidation": Decimal("100000"),
        "quote": make_quote(now=now),
        "owner_position_qty": Decimal("0"),
        "open_manual_sell_qty": 0,
        "resting_stop_qty": Decimal("0"),
        "bot_symbols": frozenset(),
        "manual_open_buy_notional_today": Decimal("0"),
        "recent_manual_orders_10min": 0,
    }
    values.update(overrides)
    return LiveFacts(**values)


def parsed(text: str) -> ParsedOrder:
    result = parse_order_text(text)
    assert isinstance(result, ParsedOrder), result
    return result


def check(
    text: str, facts: LiveFacts | None = None, *, contract: ContractRef | None | str = "auto",
    message_age: timedelta = timedelta(seconds=2),
) -> ValidatedOrder | Rejection:
    order = parsed(text)
    facts = facts or make_facts()
    resolved = CONTRACTS.get(order.symbol or "") if contract == "auto" else contract
    return validate(order, facts=facts, contract=resolved, message_date=facts.now - message_age)  # type: ignore[arg-type]


def accepted(result: ValidatedOrder | Rejection) -> ValidatedOrder:
    assert isinstance(result, ValidatedOrder), result
    return result


def rejected(result: ValidatedOrder | Rejection) -> str:
    assert isinstance(result, Rejection), result
    return result.code


# ---------------------------------------------------------------- parser

ACCEPTED: list[tuple[str, dict[str, Any]]] = [
    ("/buy 100 AAPL stop 95", {"action": "BUY", "source": "command", "symbol": "AAPL", "quantity": 100, "order_type": "MKT", "entry_stop_price": Decimal("95")}),
    ("/buy 100 AAPL 180.5 stop 175", {"action": "BUY", "quantity": 100, "order_type": "LMT", "limit_price": Decimal("180.5"), "entry_stop_price": Decimal("175")}),
    ("/buy $5000 NVDA stop 170", {"action": "BUY", "symbol": "NVDA", "notional": Decimal("5000"), "order_type": "MKT", "entry_stop_price": Decimal("170")}),
    ("/buy 5k NVDA at 180 stop 170", {"notional": Decimal("5000"), "order_type": "LMT", "limit_price": Decimal("180"), "entry_stop_price": Decimal("170")}),
    ("/buy $5k NVDA stop 170", {"notional": Decimal("5000"), "order_type": "MKT", "entry_stop_price": Decimal("170")}),
    ("/buy 5k$ NVDA stop 170", {"notional": Decimal("5000")}),
    ("/buy 5000$ NVDA stop 170", {"notional": Decimal("5000")}),
    ("/buy $5,000 NVDA stop 170", {"notional": Decimal("5000")}),
    ("/buy $2.5k NVDA stop 170", {"notional": Decimal("2500")}),
    ("/buy 5K NVDA stop 170", {"notional": Decimal("5000")}),
    ("/sell 50 TSLA", {"action": "SELL", "symbol": "TSLA", "quantity": 50, "order_type": "MKT"}),
    ("/sell 50 TSLA 250", {"action": "SELL", "quantity": 50, "order_type": "LMT", "limit_price": Decimal("250")}),
    ("/sell all TSLA", {"action": "SELL", "fraction": "all", "order_type": "MKT"}),
    ("/sell half TSLA", {"action": "SELL", "fraction": "half"}),
    ("/sell $2.5k TSLA", {"action": "SELL", "notional": Decimal("2500")}),
    ("/stop TSLA 240", {"action": "SELL_STOP", "symbol": "TSLA", "fraction": "all", "order_type": "STP", "stop_price": Decimal("240")}),
    ("/stop 50 TSLA at 240", {"action": "SELL_STOP", "quantity": 50, "stop_price": Decimal("240")}),
    ("/stop half TSLA 240.50", {"action": "SELL_STOP", "fraction": "half", "stop_price": Decimal("240.50")}),
    ("/close TSLA", {"action": "CLOSE", "symbol": "TSLA", "order_type": None}),
    ("/closeall", {"action": "CLOSE_ALL", "symbol": None}),
    ("/closeall@AutotraderBot", {"action": "CLOSE_ALL"}),
    ("/cancel a3f9c2", {"action": "CANCEL", "cancel_ref": "a3f9c2"}),
    ("/cancel #A3F9C2", {"action": "CANCEL", "cancel_ref": "a3f9c2"}),
    ("/cancel 424242", {"action": "CANCEL", "cancel_ref": "424242"}),
    ("/cancel all", {"action": "CANCEL_ALL", "cancel_ref": None}),
    ("buy 100 AAPL stop 95", {"action": "BUY", "source": "text", "symbol": "AAPL", "quantity": 100, "order_type": "MKT", "entry_stop_price": Decimal("95")}),
    ("buy 100 shares of $AAPL at 180 stop 175", {"source": "text", "quantity": 100, "order_type": "LMT", "limit_price": Decimal("180"), "entry_stop_price": Decimal("175")}),
    ("sell half my TSLA", {"action": "SELL", "source": "text", "fraction": "half"}),
    ("sell all $TSLA", {"action": "SELL", "fraction": "all", "symbol": "TSLA"}),
    ("close NVDA", {"action": "CLOSE", "source": "text", "symbol": "NVDA"}),
    ("close my NVDA position", {"action": "CLOSE", "symbol": "NVDA"}),
    ("cancel a3f9c2", {"action": "CANCEL", "source": "text", "cancel_ref": "a3f9c2"}),
    ("cancel order a3f9c2", {"action": "CANCEL", "cancel_ref": "a3f9c2"}),
    ("cancel all", {"action": "CANCEL_ALL", "source": "text"}),
    ("cancel all my orders", {"action": "CANCEL_ALL"}),
    ("Buy 100 AAPL stop 95.", {"action": "BUY", "quantity": 100, "entry_stop_price": Decimal("95")}),
    ("BUY 100 SHARES OF AAPL AT 180 STOP 170", {"quantity": 100, "limit_price": Decimal("180"), "entry_stop_price": Decimal("170")}),
    ("BUY 100 SHARES OF AAPL NOW STOP 95", {"quantity": 100, "order_type": "MKT"}),
    ("please buy 100 AAPL stop 95 please", {"quantity": 100, "entry_stop_price": Decimal("95")}),
    ("buy 100 AAPL @ 180 stop 175", {"limit_price": Decimal("180")}),
    ("buy 100 AAPL @180 stop 175", {"limit_price": Decimal("180")}),
    ("buy 100 AAPL limit 180.25 stop 175.5", {"limit_price": Decimal("180.25"), "entry_stop_price": Decimal("175.5")}),
    ("buy 100 AAPL at $180.25 stop $175", {"limit_price": Decimal("180.25"), "entry_stop_price": Decimal("175")}),
    ("buy 100 AAPL at market stop 95", {"order_type": "MKT", "limit_price": None}),
    ("/buy 100 AAPL mkt stop 95", {"order_type": "MKT"}),
    ("buy 1,000 F stop 9.5", {"symbol": "F", "quantity": 1000, "entry_stop_price": Decimal("9.5")}),
    ("buy 10 BRK.B stop 290", {"symbol": "BRK B", "quantity": 10}),
    ("buy 10 BRK B stop 290", {"symbol": "BRK B", "quantity": 10}),
    ("buy 10 BRK-B stop 290", {"symbol": "BRK B"}),
    ("buy 10 $brk.b stop 290", {"symbol": "BRK B"}),
    ("/buy 10 BRK B 300 stop 290", {"symbol": "BRK B", "limit_price": Decimal("300"), "entry_stop_price": Decimal("290")}),
    ("buy 100 $IT stop 95", {"symbol": "IT", "quantity": 100}),
    ("buy 100 $aapl stop 95", {"symbol": "AAPL"}),
    ("/buy@AutotraderBot 100 AAPL stop 95", {"source": "command", "quantity": 100}),
    ("/BUY 100 AAPL STOP 95", {"action": "BUY", "source": "command", "entry_stop_price": Decimal("95")}),
    ("  /buy   100   AAPL   stop  95 ", {"quantity": 100, "entry_stop_price": Decimal("95")}),
    ("ｂuy １００ ＡＡＰＬ ｓｔｏｐ ９５", {"symbol": "AAPL", "quantity": 100, "entry_stop_price": Decimal("95")}),  # full-width
    # D5 (v7c): every buy ends with its protective stop; nothing else ever carries one.
    ("/buy 100 AAPL at 180 stop 175", {"action": "BUY", "order_type": "LMT", "limit_price": Decimal("180"), "entry_stop_price": Decimal("175")}),
    ("/buy $5k NVDA stop 170", {"action": "BUY", "order_type": "MKT", "notional": Decimal("5000"), "entry_stop_price": Decimal("170")}),
    ("/sell 50 TSLA", {"action": "SELL", "entry_stop_price": None}),
    ("/stop TSLA 240", {"action": "SELL_STOP", "stop_price": Decimal("240"), "entry_stop_price": None}),
]


@pytest.mark.parametrize(("text", "expected"), ACCEPTED, ids=[case[0] for case in ACCEPTED])
def test_parser_accepts_documented_forms(text: str, expected: dict[str, Any]) -> None:
    result = parsed(text)
    for name, value in expected.items():
        assert getattr(result, name) == value, (name, getattr(result, name))


REFUSED: list[tuple[str, str, bool]] = [
    ("", "empty", False),
    ("   ", "empty", False),
    # questions go to read-only chat, never to an order
    ("should I buy AAPL", "question", False),
    ("buy 100 AAPL?", "question", False),
    ("what is AAPL at", "question", False),
    ("can you sell TSLA", "question", False),
    ("could you close NVDA", "question", False),
    ("would it be smart to buy AAPL", "question", False),
    ("how is TSLA doing", "question", False),
    ("why did the bot sell", "question", False),
    ("is AAPL up", "question", False),
    ("do I own TSLA", "question", False),
    ("/buy 100 AAPL?", "question", True),
    ("hello there", "not_an_order", False),
    ("I want to buy 100 AAPL", "not_an_order", False),
    ("/status", "not_an_order", False),
    ("/stop", "kill_switch_command", False),
    ("/stop@AutotraderBot", "kill_switch_command", False),
    # negations
    ("don't sell TSLA", "negation", True),
    ("don’t sell TSLA", "negation", True),
    ("do not buy AAPL", "negation", True),
    ("never sell TSLA", "negation", True),
    ("sell 50 TSLA not", "negation", True),
    ("buy 100 AAPL no", "negation", True),
    ("buy 100 AAPL cancel", "negation", True),
    ("/sell 50 TSLA dont", "negation", True),
    # conditionals
    ("buy 100 AAPL if it dips", "conditional", True),
    ("if AAPL drops buy 100 AAPL", "conditional", True),
    ("sell all TSLA when it hits 250", "conditional", True),
    ("buy 100 AAPL unless", "conditional", True),
    ("once AAPL breaks out buy 100 AAPL", "conditional", True),
    ("buy 100 AAPL after", "conditional", True),
    ("before close sell all TSLA", "conditional", True),
    ("buy 100 AAPL only if green", "conditional", True),
    ("buy 100 AAPL tomorrow", "conditional", True),
    # shapes we never trade
    ("short 100 TSLA", "short_selling", True),
    ("sell short 100 TSLA", "short_selling", True),
    ("/short 100 TSLA", "short_selling", True),
    ("buy 2 lots AAPL", "unsupported_instrument", True),
    ("buy 2 contracts AAPL", "unsupported_instrument", True),
    ("buy 1 AAPL call", "unsupported_instrument", True),
    ("buy 10 TSLA puts", "unsupported_instrument", True),
    ("buy 1 AAPL option", "unsupported_instrument", True),
    # Rewritten for D5 (v7c): "stop <price>" written last is now a buy's own protective stop,
    # so the bracket refusal is tested on what stays refused around it.
    ("buy 100 AAPL trailing stop 170", "unsupported_order_type", True),
    ("buy 100 AAPL stoploss 170", "unsupported_order_type", True),
    ("/sell 50 TSLA stop 240", "unsupported_order_type", True),
    # D5 (v7c): a buy without its stop, a stop not below the limit, a stop not written last.
    ("/buy 100 AAPL", "missing_entry_stop", True),
    ("buy 100 AAPL at 180", "missing_entry_stop", True),
    ("/buy $5k NVDA", "missing_entry_stop", True),
    ("/buy 100 AAPL 180 stop 180", "stop_not_below_entry", True),
    ("buy 100 AAPL at 180 stop 181", "stop_not_below_entry", True),
    ("/buy 100 AAPL stop", "missing_price", True),
    ("/buy 100 AAPL stop 95 stop 94", "bad_price", True),
    ("/buy 100 AAPL stop 95 at 100", "bad_price", True),
    ("/buy 100 AAPL stop 5k", "bad_price", True),
    ("/buy 100 AAPL stop 0", "bad_price", True),
    ("buy 100 AAPL target 190", "unsupported_order_type", True),
    ("buy 100 AAPL and 100 MSFT", "multiple_actions", True),
    ("sell 50 TSLA buy 50 AAPL", "multiple_actions", True),
    ("trim 50 TSLA", "unknown_verb", True),
    ("/trim 50 TSLA", "unknown_verb", True),
    ("stop TSLA 240", "unknown_verb", True),
    # exactly one symbol and one quantity
    ("buy 100 AAPL MSFT", "multiple_symbols", True),
    ("/buy 100 AAPL $MSFT", "multiple_symbols", True),
    ("close NVDA AAPL", "multiple_symbols", True),
    ("buy 100 200 AAPL", "multiple_quantities", True),
    ("sell half 50 TSLA", "multiple_quantities", True),
    ("buy 100 $5000 AAPL", "multiple_quantities", True),
    ("buy 100 AAPL 180", "multiple_quantities", True),  # sentences need "at" for a price
    ("/sell 50 TSLA 250 260", "multiple_quantities", True),
    ("/sell 50 TSLA half", "multiple_quantities", True),
    ("buy 1.5 AAPL", "fractional_shares", True),
    ("sell 0.5 TSLA", "fractional_shares", True),
    ("sell 1/2 TSLA", "fractional_shares", True),
    ("/buy $5k shares NVDA", "ambiguous_amount", True),
    ("buy AAPL", "missing_quantity", True),
    ("sell TSLA", "missing_quantity", True),
    ("sell my TSLA", "missing_quantity", True),
    ("/buy", "missing_symbol", True),
    ("sell all", "missing_symbol", True),
    ("/close", "missing_symbol", True),
    ("buy 100 at 180", "missing_symbol", True),
    ("/stop TSLA", "missing_price", True),
    ("/stop TSLA at market", "missing_price", True),
    ("buy 100 AAPL limit", "missing_price", True),
    ("buy 0 AAPL", "bad_quantity", True),
    ("buy 1000001 AAPL", "bad_quantity", True),
    ("buy $0.50 AAPL", "bad_quantity", True),
    ("buy all AAPL", "bad_quantity", True),
    ("/stop $5k TSLA 240", "bad_quantity", True),
    ("sell 50% TSLA", "bad_quantity", True),
    ("close 50 TSLA", "bad_quantity", True),
    ("buy 100 AAPL at 0", "bad_price", True),
    ("buy 100 AAPL at 180.12345", "bad_price", True),
    ("buy 100 AAPL at 5k", "bad_price", True),
    ("buy 100 AAPL at 180 at 181", "bad_price", True),
    ("close NVDA at 180", "unsupported_order_type", True),
    ("/close all", "closeall_exact", True),
    ("close all", "closeall_exact", True),
    ("CLOSE ALL", "closeall_exact", True),
    ("sell everything", "closeall_exact", True),
    ("/sell everything", "closeall_exact", True),
    ("/closeall now", "closeall_exact", True),
    ("buy 100 aapl", "ticker_case", True),
    ("buy 100 Aapl", "ticker_case", True),
    ("buy 100 AAPL today", "unknown_word", True),
    ("buy -5 AAPL", "unknown_word", True),
    ("buy 100 AAPL \U0001F680", "unknown_word", True),
    ("/cancel 42", "bad_cancel_ref", True),
    ("/cancel", "bad_cancel_ref", True),
    ("cancel my AAPL order", "bad_cancel_ref", True),
    ("/cancel a3f9c2 b4e8d1", "bad_cancel_ref", True),
    ("buy 100 AT 180", "word_ticker_needs_cashtag", True),
    ("buy 100 ALL", "word_ticker_needs_cashtag", True),
    ("BUY 100 ALL", "word_ticker_needs_cashtag", True),
    ("buy 100 NOW", "word_ticker_needs_cashtag", True),
    ("/stop NOW 240", "word_ticker_needs_cashtag", True),
    ("close IT", "word_ticker_needs_cashtag", True),
    ("buy " + "1" * 3 + " AAPL " + "please " * 40, "too_long", True),
]


@pytest.mark.parametrize(("text", "code", "attempt"), REFUSED, ids=[case[0][:40] or "<empty>" for case in REFUSED])
def test_parser_refuses_with_a_named_reason(text: str, code: str, attempt: bool) -> None:
    result = parse_order_text(text)
    assert isinstance(result, ParseRefusal), result
    assert (result.code, result.is_order_attempt) == (code, attempt), result.reason
    assert result.code in manual.REFUSAL_CODES
    assert result.reason


SPEC_WORD_TICKERS = (
    "A", "ALL", "IT", "ON", "ANY", "ARE", "BE", "CAN", "FOR", "GO", "HAS", "NOW", "ONE", "OUT",
    "SEE", "SO", "TWO", "YOU", "KEY", "LOW", "NEW", "OPEN", "PLAY", "REAL", "SAFE", "TRUE", "WELL",
)


def test_word_ticker_list_covers_the_owner_list() -> None:
    assert set(SPEC_WORD_TICKERS) <= WORD_TICKERS
    assert all(word.isupper() and word.isalpha() for word in WORD_TICKERS)


@pytest.mark.parametrize("word", SPEC_WORD_TICKERS)
def test_word_tickers_need_a_cashtag(word: str) -> None:
    for text in (f"/buy 100 {word}", f"buy 100 {word}", f"sell 10 {word}", f"close {word}"):
        result = parse_order_text(text)
        assert isinstance(result, ParseRefusal), text
        assert result.code in ("word_ticker_needs_cashtag", "closeall_exact"), (text, result)
    assert parsed(f"/buy 100 ${word} stop 9").symbol == word
    assert parsed(f"close ${word}").symbol == word


@pytest.mark.parametrize("word", sorted(WORD_TICKERS))
def test_no_listed_word_ever_parses_as_a_bare_ticker(word: str) -> None:
    for text in (f"/buy 100 {word}", f"sell all {word}", f"/stop {word} 10"):
        assert isinstance(parse_order_text(text), ParseRefusal), text
    assert parsed(f"/buy 100 ${word} stop 9").symbol == word


def test_only_a_bare_stop_is_the_kill_switch() -> None:
    assert is_kill_switch_command("/stop")
    assert is_kill_switch_command("  /STOP  ")
    assert is_kill_switch_command("/stop@AutotraderBot")
    for text in ("/stop TSLA 240", "stop", "/stopall", "/stop now", "", "/resume"):
        assert not is_kill_switch_command(text), text
    assert parsed("/stop TSLA 240").action == "SELL_STOP"


def test_parsed_order_invariants_reject_inconsistent_orders() -> None:
    with pytest.raises(ValueError):
        ParsedOrder(action="BUY", source="text", symbol="AAPL", quantity=1, notional=Decimal("5"), order_type="MKT")
    with pytest.raises(ValueError):
        ParsedOrder(action="BUY", source="text", symbol="AAPL", fraction="all", order_type="MKT")
    with pytest.raises(ValueError):
        ParsedOrder(action="SELL", source="text", symbol="AAPL", quantity=1, order_type="LMT")
    with pytest.raises(ValueError):
        ParsedOrder(action="SELL_STOP", source="text", symbol="AAPL", quantity=1, order_type="STP")
    with pytest.raises(ValueError):
        ParsedOrder(action="CLOSE", source="text", symbol="AAPL", quantity=1)
    with pytest.raises(ValueError):
        ParsedOrder(action="CANCEL", source="text", cancel_ref="42")
    with pytest.raises(ValueError):
        ParsedOrder(action="BUY", source="text", symbol="brk.b", quantity=1, order_type="MKT")
    with pytest.raises(ValueError):
        ParsedOrder(action="BUY", source="text", symbol="AAPL", quantity=True, order_type="MKT")  # type: ignore[arg-type]


_FUZZ_VOCABULARY = (
    "buy", "sell", "close", "cancel", "stop", "/buy", "/sell", "/stop", "/close", "/closeall",
    "/cancel", "Buy", "SELL", "100", "50", "1,000", "1.5", "$5000", "5k", "$5k", "5k$", "all",
    "half", "ALL", "HALF", "AAPL", "TSLA", "$TSLA", "$aapl", "aapl", "BRK", "B", "BRK.B", "at", "@",
    "@180", "180", "180.5", "$180", "limit", "market", "shares", "of", "my", "position", "please",
    "now", "not", "don't", "if", "when", "?", "and", "or", "call", "lots", "short", "IT", "A", "I",
    "NOW", "#a3f9c2", "a3f9c2", "everything", "target", "0", "-5", "1/2", "50%", "’", "!", ",",
)
_NEVER_IN_AN_ORDER = {"not", "don't", "if", "when", "?", "and", "or", "call", "lots", "short", "target"}


def test_parser_never_raises_and_orders_come_only_from_literal_text() -> None:
    import random
    import re

    rng = random.Random(20261013)
    accepted_count = 0
    for _ in range(20_000):
        words = [rng.choice(_FUZZ_VOCABULARY) for _ in range(rng.randint(1, 7))]
        text = " ".join(words)
        result = parse_order_text(text)
        if not isinstance(result, ParsedOrder):
            continue
        accepted_count += 1
        assert parse_order_text(text) == result  # deterministic
        assert not {word.lower() for word in words} & _NEVER_IN_AN_ORDER, text
        if result.symbol is not None:
            assert result.symbol.split(" ")[0] in re.split(r"[\s$.\-]+", text.upper()), text
        if result.quantity is not None:
            assert str(result.quantity) in [word.replace(",", "") for word in words], text
        if result.notional is not None:
            assert "$" in text or "k" in text.lower(), text
    assert accepted_count > 20  # the vocabulary does produce orders


def test_validated_orders_are_internally_consistent() -> None:
    buy = parsed("/buy 1 AAPL stop 99")
    with pytest.raises(ValueError):
        ValidatedOrder(kind="cancel", action="BUY", parsed=buy)
    with pytest.raises(ValueError):
        ValidatedOrder(kind="order", action="SELL", parsed=buy, symbol="AAPL", con_id=1, side="SELL",
                       quantity=1, order_type="MKT")
    with pytest.raises(ValueError):
        ValidatedOrder(kind="order", action="BUY", parsed=buy, symbol="AAPL", con_id=1, side="BUY",
                       quantity=0, order_type="MKT")
    with pytest.raises(ValueError):
        ValidatedOrder(kind="order", action="BUY", parsed=buy, symbol="AAPL", con_id=1, side="BUY",
                       quantity=1, order_type="MKT", tif="GTC")  # type: ignore[arg-type]
    # D5: a valid buy carries the parsed stop below the price it was sized at.
    good = dict(kind="order", action="BUY", parsed=buy, symbol="AAPL", con_id=1, primary_exchange="NASDAQ",
                side="BUY", quantity=1, order_type="MKT", reference_price=Decimal("100.01"),
                entry_stop_price=Decimal("99"))
    assert ValidatedOrder(**good).summary() == "BUY 1 AAPL MKT"  # type: ignore[arg-type]
    assert ValidatedOrder(**good).entry_risk == Decimal("1.01")  # type: ignore[arg-type]
    for overrides in ({"side": "SELL"}, {"order_type": "STP"}, {"limit_price": Decimal("1")},
                      {"primary_exchange": None}, {"kind": "bot_flatten"}, {"entry_stop_price": None},
                      {"entry_stop_price": Decimal("98")}, {"entry_stop_price": Decimal("100.01")},
                      {"reference_price": None}):
        with pytest.raises(ValueError):
            ValidatedOrder(**{**good, **overrides})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ValidatedOrder(kind="close_all", action="CLOSE_ALL", parsed=parsed("/closeall"), quantity=1)
    audit = manual.facts_to_audit(make_facts(bot_symbols=frozenset({"TSLA"})))
    assert audit["quote"]["bid"] == "99.99" and audit["bot_symbols"] == ["TSLA"]  # type: ignore[index]
    import json

    assert json.loads(json.dumps(audit)) == audit


def test_order_refs_round_trip() -> None:
    row_id = "a3f9c2" + "0" * 26
    assert short_id(row_id) == "a3f9c2"
    assert order_ref(row_id) == f"manual:{row_id}"
    assert parse_order_ref(order_ref(row_id)) == row_id
    for ref in ("", "manual:", "manual:xyz", f"bot:{row_id}", row_id, f"manual:{row_id}0"):
        assert parse_order_ref(ref) is None


# ---------------------------------------------------------------- validator


def test_valid_market_buy_is_sized_from_the_ask() -> None:
    order = accepted(check("/buy 100 AAPL stop 99"))
    assert (order.kind, order.side, order.quantity, order.order_type, order.tif) == ("order", "BUY", 100, "MKT", "DAY")
    assert (order.con_id, order.primary_exchange) == (265598, "NASDAQ")
    assert order.reference_price == Decimal("100.01")
    assert order.estimated_notional == Decimal("10001.00")
    assert order.summary() == "BUY 100 AAPL MKT"


def test_notional_buys_floor_to_whole_shares() -> None:
    assert accepted(check("/buy $5000 NVDA stop 99")).quantity == 49  # 5000 / 100.01
    assert accepted(check("/buy 5k NVDA at 100 stop 99")).quantity == 50
    assert rejected(check("/buy $50 NVDA stop 99")) == "amount_too_small"


@pytest.mark.parametrize("text", ["/buy 1 AAPL stop 99", "/sell 1 AAPL", "/cancel a3f9c2", "/closeall", "/close AAPL"])
def test_not_paper_blocks_everything(text: str) -> None:
    assert rejected(check(text, make_facts(accounts_all_paper=False, owner_position_qty=Decimal(5)))) == "not_paper"


@pytest.mark.parametrize(
    ("age", "ok"),
    [
        (timedelta(seconds=60), True),
        (timedelta(seconds=60, microseconds=1), False),
        (timedelta(seconds=-10), True),
        (timedelta(seconds=-10, microseconds=-1), False),
    ],
)
def test_stale_message_boundary(age: timedelta, ok: bool) -> None:
    result = check("/buy 1 AAPL stop 99", message_age=age)
    assert isinstance(result, ValidatedOrder) if ok else rejected(result) == "stale_message"


@pytest.mark.parametrize(
    ("now", "open_"),
    [
        (datetime(2026, 10, 13, 9, 29, 59, 999999, tzinfo=NY), False),
        (datetime(2026, 10, 13, 9, 30, tzinfo=NY), True),
        (datetime(2026, 10, 13, 15, 53, 59, 999999, tzinfo=NY), True),
        (datetime(2026, 10, 13, 15, 54, tzinfo=NY), False),
        (datetime(2026, 10, 13, 16, 30, tzinfo=NY), False),
        (datetime(2026, 10, 17, 11, 0, tzinfo=NY), False),  # Saturday
        (datetime(2026, 11, 26, 11, 0, tzinfo=NY), False),  # Thanksgiving
        (datetime(2026, 11, 27, 12, 53, 59, tzinfo=NY), True),  # half day closes 13:00
        (datetime(2026, 11, 27, 12, 54, tzinfo=NY), False),
    ],
)
def test_market_window_boundaries(now: datetime, open_: bool) -> None:
    assert is_manual_window_open(now) is open_
    assert is_manual_window_open(now.astimezone(UTC)) is open_
    result = check("/buy 1 AAPL stop 99", make_facts(now=now))
    assert isinstance(result, ValidatedOrder) if open_ else rejected(result) == "market_closed"


def test_manual_window_is_derived_from_the_exchange_calendar() -> None:
    full = manual_order_window(datetime(2026, 10, 13).date())
    assert full is not None
    assert (full[0].hour, full[0].minute, full[1].hour, full[1].minute) == (9, 30, 15, 54)
    half = manual_order_window(datetime(2026, 11, 27).date())
    assert half is not None and (half[1].hour, half[1].minute) == (12, 54)
    assert manual_order_window(datetime(2026, 11, 26).date()) is None


def test_market_calendar_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_: object) -> None:
        raise RuntimeError("calendar dependency missing")

    monkeypatch.setattr(manual, "market_session", broken)
    assert rejected(check("/sell 1 AAPL", make_facts(owner_position_qty=Decimal(1)))) == "market_closed"


@pytest.mark.parametrize(
    ("age", "ok"),
    [(None, False), (0.0, True), (-1.0, True), (20.0, True), (20.001, False), (3600.0, False),
     (-20.0, True), (-20.001, False)],
)
def test_bridge_heartbeat_boundary(age: float | None, ok: bool) -> None:
    result = check("/sell 1 AAPL", make_facts(bridge_heartbeat_age_s=age, owner_position_qty=Decimal(1)))
    assert isinstance(result, ValidatedOrder) if ok else rejected(result) == "bridge_offline"


@pytest.mark.parametrize(
    "contract",
    [
        None,
        ContractRef(symbol="MSFT", con_id=272093, primary_exchange="NASDAQ"),
        ContractRef(symbol="AAPL", con_id=0, primary_exchange="NASDAQ"),
        ContractRef(symbol="AAPL", con_id=265598, primary_exchange=" "),
        ContractRef(symbol="AAPL", con_id=265598, primary_exchange="NASDAQ", currency="CAD"),
        ContractRef(symbol="AAPL", con_id=265598, primary_exchange="NASDAQ", security_type="OPT"),
    ],
)
def test_unresolved_or_mismatched_contract_is_unknown_symbol(contract: ContractRef | None) -> None:
    assert rejected(check("/buy 1 AAPL stop 99", contract=contract)) == "unknown_symbol"


def test_bot_symbols_allow_only_close_which_becomes_bot_flatten() -> None:
    facts = make_facts(bot_symbols=frozenset({"AAPL", "brk.b"}), owner_position_qty=Decimal(100), kill_switch_active=True)
    for text in ("/buy 1 AAPL stop 99", "/sell 1 AAPL", "/sell all AAPL", "/stop AAPL 95"):
        assert rejected(check(text, facts)) == "bot_holds_symbol", text
    flatten = accepted(check("/close AAPL", facts))
    assert (flatten.kind, flatten.side, flatten.quantity, flatten.order_type) == ("bot_flatten", "SELL", None, None)
    assert accepted(check("close BRK.B", facts)).kind == "bot_flatten"


def test_kill_switch_blocks_buys_only() -> None:
    facts = make_facts(kill_switch_active=True, owner_position_qty=Decimal(100))
    assert rejected(check("/buy 1 AAPL stop 99", facts)) == "kill_switch"
    assert rejected(check("/buy 1 AAPL 100 stop 99", facts)) == "kill_switch"
    for text in ("/sell 10 AAPL", "/stop AAPL 95", "/close AAPL", "/closeall", "/cancel all"):
        assert isinstance(check(text, facts), ValidatedOrder), text


@pytest.mark.parametrize(("quantity", "ok"), [(250, True), (251, False)])
def test_order_notional_cap_is_25_percent_of_net_liq(quantity: int, ok: bool) -> None:
    result = check(f"/buy {quantity} AAPL 100 stop 99", make_facts())  # 250 x 100 = 25,000 = 25% of 100k
    assert isinstance(result, ValidatedOrder) if ok else rejected(result) == "order_too_large"


def test_market_buy_cap_uses_the_ask() -> None:
    assert accepted(check("/buy 249 AAPL stop 99")).estimated_notional == Decimal("24902.49")
    assert rejected(check("/buy 250 AAPL stop 99")) == "order_too_large"  # 250 x 100.01


@pytest.mark.parametrize(("today", "ok"), [(Decimal("25000"), True), (Decimal("25000.01"), False)])
def test_manual_exposure_cap_is_50_percent_of_net_liq(today: Decimal, ok: bool) -> None:
    result = check("/buy 250 AAPL 100 stop 99", make_facts(manual_open_buy_notional_today=today))
    assert isinstance(result, ValidatedOrder) if ok else rejected(result) == "manual_exposure_cap"


@pytest.mark.parametrize(("recent", "ok"), [(0, True), (9, True), (10, False), (50, False)])
def test_rate_limit_is_ten_buys_per_ten_minutes(recent: int, ok: bool) -> None:
    result = check("/buy 1 AAPL stop 99", make_facts(recent_manual_orders_10min=recent))
    assert isinstance(result, ValidatedOrder) if ok else rejected(result) == "rate_limited"


@pytest.mark.parametrize("net_liq", [None, Decimal("0"), Decimal("-5")])
def test_buys_need_a_known_net_liquidation(net_liq: Decimal | None) -> None:
    assert rejected(check("/buy 1 AAPL stop 99", make_facts(net_liquidation=net_liq))) == "account_unavailable"


EXITS = ["/sell 10 AAPL", "/sell all AAPL", "/sell half AAPL", "/stop AAPL 95", "/close AAPL",
         "/closeall", "/cancel a3f9c2", "/cancel all", "sell 10 AAPL at 100"]


@pytest.mark.parametrize("text", EXITS)
def test_exits_are_never_blocked_by_kill_switch_rate_limit_or_budget(text: str) -> None:
    facts = make_facts(
        kill_switch_active=True, recent_manual_orders_10min=500,
        manual_open_buy_notional_today=Decimal("10000000"), net_liquidation=None,
        owner_position_qty=Decimal(100),
    )
    assert isinstance(check(text, facts), ValidatedOrder)
    assert isinstance(check("/buy 1 AAPL stop 99", facts), Rejection)


def test_cancels_are_never_blocked_by_hours_staleness_or_bridge() -> None:
    facts = make_facts(now=datetime(2026, 10, 17, 3, 0, tzinfo=NY), bridge_heartbeat_age_s=None, quote=None)
    for text in ("/cancel a3f9c2", "cancel all"):
        order = accepted(check(text, facts, message_age=timedelta(hours=2)))
        assert order.kind in ("cancel", "cancel_all")
    assert accepted(check("/cancel all", facts)).cancel_ref == "all"
    assert rejected(check("/closeall", facts, message_age=timedelta(hours=2))) == "stale_message"
    assert rejected(check("/closeall", facts, message_age=timedelta(seconds=1))) == "market_closed"


def test_close_all_checks_only_the_environment() -> None:
    order = accepted(check("/closeall", make_facts(quote=None, kill_switch_active=True)))
    assert (order.kind, order.symbol, order.quantity) == ("close_all", None, None)
    assert rejected(check("/closeall", make_facts(bridge_heartbeat_age_s=25.0))) == "bridge_offline"


@pytest.mark.parametrize(
    ("quote", "code"),
    [
        (None, "no_fresh_quote"),
        (make_quote(real_time=False), "no_fresh_quote"),
        (make_quote(age=timedelta(seconds=5, microseconds=1000)), "no_fresh_quote"),
        (make_quote(age=timedelta(seconds=-3)), "no_fresh_quote"),
        (make_quote(bid=None), "no_fresh_quote"),
        (make_quote(bid="100.02", ask="100.01"), "no_fresh_quote"),
        (make_quote(bid="0", ask="100.01"), "no_fresh_quote"),
        (make_quote(bid="99.49", ask="100.50"), "spread_too_wide"),
    ],
)
def test_market_orders_need_a_fresh_tight_quote(quote: Quote | None, code: str) -> None:
    facts = make_facts(quote=quote, owner_position_qty=Decimal(100))
    for text in ("/buy 1 AAPL stop 99", "/sell 1 AAPL", "/close AAPL"):
        assert rejected(check(text, facts)) == code, text


def test_market_quote_boundaries_are_inclusive() -> None:
    facts = make_facts(quote=make_quote(bid="99.50", ask="100.50", age=timedelta(seconds=5)))
    assert accepted(check("/buy 1 AAPL stop 99", facts)).reference_price == Decimal("100.50")  # spread exactly 1%


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("/buy 1 AAPL 110 stop 99", True),
        ("/buy 1 AAPL 110.01 stop 99", False),
        ("/buy 1 AAPL 90 stop 89", True),
        ("/buy 1 AAPL 89.99 stop 88", False),
        ("/sell 1 AAPL 110", True),
        ("/sell 1 AAPL 110.01", False),
        ("/stop AAPL 90", True),
        ("/stop AAPL 89.99", False),
    ],
)
def test_limit_and_stop_prices_must_be_within_ten_percent(text: str, ok: bool) -> None:
    result = check(text, make_facts(owner_position_qty=Decimal(10)))
    assert isinstance(result, ValidatedOrder) if ok else rejected(result) == "price_out_of_band"


def test_limit_orders_accept_a_minute_old_or_delayed_reference() -> None:
    delayed = make_facts(quote=make_quote(age=timedelta(seconds=60), real_time=False))
    assert accepted(check("/buy 1 AAPL 100 stop 99", delayed)).order_type == "LMT"
    stale = make_facts(quote=make_quote(age=timedelta(seconds=61)))
    assert rejected(check("/buy 1 AAPL 100 stop 99", stale)) == "no_fresh_quote"
    last_only = make_facts(quote=make_quote(bid=None, ask=None, last="100"))
    assert accepted(check("/buy 1 AAPL 105 stop 99", last_only)).limit_price == Decimal("105")
    assert rejected(check("/buy 1 AAPL 100 stop 99", make_facts(quote=None))) == "no_fresh_quote"


def test_prices_must_be_on_a_valid_tick() -> None:
    assert rejected(check("/buy 1 AAPL 100.005 stop 99")) == "invalid_tick"
    penny = make_facts(quote=make_quote(bid="0.5120", ask="0.5130", last="0.5125"))
    assert accepted(check("/buy 100 AAPL 0.5123 stop 0.5", penny)).limit_price == Decimal("0.5123")


def test_sell_stops_must_sit_below_a_live_bid() -> None:
    facts = make_facts(owner_position_qty=Decimal(100))
    assert rejected(check("/stop AAPL 99.99", facts)) == "stop_above_bid"  # equal to the bid
    assert rejected(check("/stop AAPL 100.50", facts)) == "stop_above_bid"
    stop = accepted(check("/stop AAPL 99.98", facts))
    assert (stop.order_type, stop.stop_price, stop.limit_price, stop.quantity, stop.tif) == (
        "STP", Decimal("99.98"), None, 100, "DAY"
    )
    delayed = make_facts(owner_position_qty=Decimal(100), quote=make_quote(real_time=False))
    assert rejected(check("/stop AAPL 95", delayed)) == "no_fresh_quote"
    no_bid = make_facts(owner_position_qty=Decimal(100), quote=make_quote(bid=None))
    assert rejected(check("/stop AAPL 95", no_bid)) == "no_fresh_quote"


@pytest.mark.parametrize(
    ("text", "position", "open_sells", "resting", "expected"),
    [
        ("/sell 50 AAPL", "100", 0, "0", 50),
        ("/sell 100 AAPL", "100", 0, "0", 100),
        ("/sell 101 AAPL", "100", 0, "0", None),
        ("/sell 1 AAPL", "0", 0, "0", None),  # naked short
        ("/sell 1 AAPL", "-50", 0, "0", None),
        ("/sell all AAPL", "100", 30, "0", 70),
        ("/sell all AAPL", "100", 0, "100", None),
        ("/sell 50 AAPL", "100", 0, "60", None),
        ("/sell 40 AAPL", "100", 0, "60", 40),
        ("/sell 40 AAPL", "100", 0, "60.5", None),  # a partial share reserved rounds up
        ("/sell half AAPL", "101", 0, "0", 50),
        ("/sell half AAPL", "100", 60, "0", None),  # half of the holding exceeds what is free
        ("/sell half AAPL", "1", 0, "0", None),
        ("/sell all AAPL", "10.7", 0, "0", 10),  # whole shares only
        ("/sell $5000 AAPL", "100", 0, "0", 50),  # floor(5000 / bid 99.99)
        ("/stop AAPL 95", "100", 25, "0", 75),
        ("/stop 80 AAPL 95", "100", 25, "0", None),
        ("/close AAPL", "100", 0, "0", 100),
        ("/close AAPL", "100", 10, "0", None),  # close never leaves reserved shares behind
        ("/close AAPL", "100", 0, "1", None),
        ("/close AAPL", "0", 0, "0", None),
    ],
)
def test_no_sell_exceeds_unreserved_shares(
    text: str, position: str, open_sells: int, resting: str, expected: int | None
) -> None:
    facts = make_facts(
        owner_position_qty=Decimal(position), open_manual_sell_qty=open_sells, resting_stop_qty=Decimal(resting)
    )
    result = check(text, facts)
    if expected is None:
        assert rejected(result) == "insufficient_shares"
    else:
        order = accepted(result)
        assert order.quantity == expected and order.side == "SELL"
        if order.action == "CLOSE":
            assert order.order_type == "MKT"


def test_sell_notional_too_small() -> None:
    assert rejected(check("/sell $50 AAPL", make_facts(owner_position_qty=Decimal(10)))) == "amount_too_small"


def test_live_facts_validate_their_inputs() -> None:
    with pytest.raises(ValueError):
        make_facts(now=datetime(2026, 10, 13, 10, 0))
    with pytest.raises(ValueError):
        make_facts(open_manual_sell_qty=-1)
    with pytest.raises(ValueError):
        make_facts(resting_stop_qty=Decimal("-1"))
    with pytest.raises(ValueError):
        make_facts(bridge_heartbeat_age_s=float("nan"))
    with pytest.raises(ValueError):
        make_facts(net_liquidation=Decimal("NaN"))
    facts = make_facts(owner_position_qty=7, bot_symbols={"brk.b", "aapl"})
    assert facts.owner_position_qty == Decimal(7)
    assert facts.bot_symbols == frozenset({"BRK B", "AAPL"})
    with pytest.raises(ValueError):
        validate(parsed("/buy 1 AAPL stop 99"), facts=make_facts(), contract=CONTRACTS["AAPL"], message_date=datetime(2026, 10, 13))


def _row(**overrides: Any) -> ManualOrderRow:
    values: dict[str, Any] = {
        "id": "a" * 32, "created_at": T0, "updated_at": T0, "chat_id": 7, "message_id": 1,
        "update_id": 1, "source": "command", "raw_text": "/buy 10 AAPL stop 99", "action": "BUY",
        "symbol": "AAPL", "con_id": 265598, "primary_exchange": "NASDAQ", "quantity": 10,
        "order_type": "MKT", "limit_price": None, "stop_price": None, "target_order_ref": None,
        "status": "sending", "reject_code": None, "error": None, "ibkr_order_id": None,
        "perm_id": None, "filled_qty": 0, "avg_fill_price": None,
        "expires_at": T0 + timedelta(seconds=90), "cancel_requested": False, "audit": {},
    }
    values.update(overrides)
    # D5: a buy row carries its stop (migration 17); no other row ever does.
    values.setdefault("entry_stop_price", Decimal("99") if values["action"] == "BUY" else None)
    return ManualOrderRow(**values)


def test_revalidation_reapplies_rules_with_the_resolved_quantity() -> None:
    later = T0 + timedelta(seconds=30)
    good = accepted(revalidate_claimed(_row(), facts=make_facts(now=later), contract=CONTRACTS["AAPL"]))
    assert (good.quantity, good.order_type, good.kind) == (10, "MKT", "order")
    assert rejected(revalidate_claimed(_row(), facts=make_facts(now=later, kill_switch_active=True),
                                       contract=CONTRACTS["AAPL"])) == "kill_switch"
    assert rejected(revalidate_claimed(_row(), facts=make_facts(now=T0 + timedelta(seconds=90)),
                                       contract=CONTRACTS["AAPL"])) == "stale_message"
    moved = ContractRef(symbol="AAPL", con_id=999, primary_exchange="NASDAQ")
    assert rejected(revalidate_claimed(_row(), facts=make_facts(now=later), contract=moved)) == "state_changed"
    sell = _row(action="SELL", raw_text="/sell 10 AAPL")
    assert rejected(revalidate_claimed(sell, facts=make_facts(now=later, owner_position_qty=Decimal(9)),
                                       contract=CONTRACTS["AAPL"])) == "insufficient_shares"


def test_revalidation_detects_exclusivity_changes() -> None:
    later = T0 + timedelta(seconds=30)
    owner_close = _row(action="CLOSE", order_type="MKT", raw_text="/close AAPL")
    now_bot = make_facts(now=later, owner_position_qty=Decimal(10), bot_symbols=frozenset({"AAPL"}))
    assert rejected(revalidate_claimed(owner_close, facts=now_bot, contract=CONTRACTS["AAPL"])) == "state_changed"
    still_owner = make_facts(now=later, owner_position_qty=Decimal(12), open_manual_sell_qty=2)
    assert accepted(revalidate_claimed(owner_close, facts=still_owner, contract=CONTRACTS["AAPL"])).quantity == 10
    flatten = _row(action="CLOSE", quantity=None, order_type=None, raw_text="/close AAPL")
    assert accepted(revalidate_claimed(flatten, facts=now_bot, contract=CONTRACTS["AAPL"])).kind == "bot_flatten"
    gone = make_facts(now=later, owner_position_qty=Decimal(10))
    assert rejected(revalidate_claimed(flatten, facts=gone, contract=CONTRACTS["AAPL"])) == "state_changed"
    with pytest.raises(ValueError):
        revalidate_claimed(_row(status="queued"), facts=gone, contract=CONTRACTS["AAPL"])
    with pytest.raises(ValueError):
        revalidate_claimed(_row(action="CANCEL", symbol=None), facts=gone, contract=None)


# ---------------------------------------------------------------- persistence


async def _ledger(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    path = str(tmp_path / "manual-orders.db")
    monkeypatch.setattr(cfg, "DB_PATH", path)
    await init_db()
    return path


async def _queue(
    text: str, *, message_id: int, facts: LiveFacts | None = None, chat_id: int = 7,
    message_date: datetime | None = None, now: datetime | None = None,
) -> str:
    facts = facts or make_facts(owner_position_qty=Decimal(1000))
    order = parsed(text)
    stamp = message_date or facts.now - timedelta(seconds=1)
    validated = validate(order, facts=facts, contract=CONTRACTS.get(order.symbol or ""), message_date=stamp)
    assert isinstance(validated, ValidatedOrder), validated
    # Creation order follows message ids so "oldest first" is deterministic.
    created = now or facts.now + timedelta(microseconds=message_id)
    return await queue_order(
        validated, chat_id=chat_id, message_id=message_id, update_id=10_000 + message_id,
        source=order.source, raw_text=text, message_date=stamp, audit={"test": True}, now=created,
    )


async def _row_count(path: str) -> int:
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT COUNT(*) FROM autotrader_manual_orders") as cursor:
            row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.anyio
async def test_telegram_updates_are_recorded_once(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    assert await max_update_id() is None
    assert await record_update(500, chat_id=7, message_id=11, kind="message", received_at=T0) is True
    assert await record_update(500, chat_id=7, message_id=11, kind="message", received_at=T0) is False
    assert await record_update(501, chat_id=0, message_id=None, kind="my_chat_member", received_at=T0) is True
    assert await max_update_id() == 501
    assert await mark_update_processed(500, outcome="queued a3f9c2", processed_at=T0) is True
    assert await mark_update_processed(500, outcome="again", processed_at=T0) is False
    assert await mark_update_processed(999, outcome="missing", processed_at=T0) is False
    with pytest.raises(ValueError):
        await record_update(502, chat_id=7, message_id=1, kind="message", received_at=datetime(2026, 1, 1))


@pytest.mark.anyio
async def test_queue_order_is_idempotent_per_message(tmp_path, monkeypatch) -> None:
    path = await _ledger(tmp_path, monkeypatch)
    first = await _queue("/buy 10 AAPL stop 99", message_id=1)
    again = await _queue("/buy 10 AAPL stop 99", message_id=1)
    other_chat = await _queue("/buy 10 AAPL stop 99", message_id=1, chat_id=8)
    assert first == again != other_chat
    assert await _row_count(path) == 2
    row = await get_order(first)
    assert row is not None
    assert (row.status, row.action, row.symbol, row.quantity, row.order_type, row.con_id) == (
        "queued", "BUY", "AAPL", 10, "MKT", 265598
    )
    assert row.expires_at == T0 - timedelta(seconds=1) + timedelta(seconds=90)
    assert len(row.id) == 32 and row.short_id == row.id[:6] and row.order_ref == f"manual:{row.id}"
    assert row.audit["validation"]["estimated_notional"] == "1000.10"
    assert row.audit["caller"] == {"test": True}
    assert row.audit["events"][0]["status"] == "queued"


@pytest.mark.anyio
async def test_queue_order_refuses_orders_not_derived_from_the_text(tmp_path, monkeypatch) -> None:
    path = await _ledger(tmp_path, monkeypatch)
    facts = make_facts()
    validated = accepted(check("/buy 10 AAPL stop 99", facts))
    common = dict(chat_id=7, update_id=1, message_date=T0, audit={}, now=T0)
    with pytest.raises(ValueError, match="does not derive"):
        await queue_order(validated, message_id=1, source="command", raw_text="/buy 100 AAPL stop 99", **common)
    with pytest.raises(ValueError, match="does not derive"):  # e.g. a model's paraphrase of a question
        await queue_order(validated, message_id=2, source="command", raw_text="should I buy AAPL?", **common)
    with pytest.raises(ValueError, match="source"):
        await queue_order(validated, message_id=3, source="text", raw_text="/buy 10 AAPL stop 99", **common)
    with pytest.raises(TypeError):
        await queue_order(parsed("/buy 10 AAPL stop 99"), message_id=4, source="command", raw_text="/buy 10 AAPL stop 99", **common)  # type: ignore[arg-type]
    assert await _row_count(path) == 0


@pytest.mark.anyio
async def test_claim_takes_the_oldest_and_expires_overdue_rows(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    old = await _queue("/buy 1 AAPL stop 99", message_id=1, message_date=T0 - timedelta(seconds=30), now=T0)
    newer = await _queue("/buy 2 AAPL stop 99", message_id=2, message_date=T0, now=T0 + timedelta(milliseconds=1))
    # old expires at T0+60s, newer at T0+90s
    claimed = await claim_next_order(T0 + timedelta(seconds=59))
    assert claimed is not None and claimed.id == old and claimed.status == "sending"
    assert await claim_next_order(T0 + timedelta(seconds=90)) is None  # newer is overdue now
    expired = await get_order(newer)
    assert expired is not None and expired.status == "expired"
    assert expired.audit["events"][-1]["event"] == "expired"


@pytest.mark.anyio
async def test_claim_boundary_is_strictly_before_expiry(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    row_id = await _queue("/buy 1 AAPL stop 99", message_id=1, message_date=T0)
    assert await claim_next_order(T0 + timedelta(seconds=90)) is None
    row = await get_order(row_id)
    assert row is not None and row.status == "expired"
    second = await _queue("/buy 1 AAPL stop 99", message_id=2, message_date=T0)
    claimed = await claim_next_order(T0 + timedelta(seconds=89, microseconds=999999))
    assert claimed is not None and claimed.id == second


@pytest.mark.anyio
async def test_two_concurrent_claimers_never_share_a_row(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    only = await _queue("/buy 1 AAPL stop 99", message_id=1)
    results = await asyncio.gather(*(claim_next_order(T0) for _ in range(2)))
    winners = [row for row in results if row is not None]
    assert [row.id for row in winners] == [only]
    ids = [await _queue(f"/buy {n} AAPL stop 99", message_id=10 + n) for n in range(1, 6)]
    results = await asyncio.gather(*(claim_next_order(T0) for _ in range(8)))
    claimed = [row.id for row in results if row is not None]
    assert sorted(claimed) == sorted(ids) and len(set(claimed)) == 5


@pytest.mark.anyio
async def test_status_updates_are_monotonic(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    row_id = await _queue("/buy 100 AAPL 100 stop 99", message_id=1)
    assert await update_order_status(row_id, "sending", now=T0) is False  # only claim may do this
    assert await update_order_status(row_id, "sent", now=T0) is False
    claimed = await claim_next_order(T0)
    assert claimed is not None and claimed.id == row_id
    assert await update_order_status(row_id, "sent", now=T0, ibkr_order_id=11, perm_id=0) is True
    assert await update_order_status(row_id, "sent", now=T0, ibkr_order_id=12) is False
    assert await update_order_status(row_id, "partially_filled", now=T0, filled_qty=40,
                                     avg_fill_price=Decimal("99.98"), perm_id=777) is True
    assert await update_order_status(row_id, "partially_filled", now=T0, filled_qty=30) is False
    assert await update_order_status(row_id, "partially_filled", now=T0, perm_id=778) is False
    assert await update_order_status(row_id, "sent", now=T0) is False
    assert await update_order_status(row_id, "filled", now=T0, filled_qty=101) is False
    assert await update_order_status(row_id, "filled", now=T0, filled_qty=100,
                                     avg_fill_price=Decimal("99.99"), event={"exec": "x1"}) is True
    for status in ("sent", "cancelled", "partially_filled", "error", "unknown", "queued"):
        assert await update_order_status(row_id, status, now=T0) is False, status
    assert await update_order_status(row_id, "filled", now=T0, error="late note") is True
    row = await get_order(row_id)
    assert row is not None
    assert (row.status, row.filled_qty, row.avg_fill_price, row.ibkr_order_id, row.perm_id) == (
        "filled", 100, Decimal("99.99"), 11, 777
    )
    assert [event.get("status") for event in row.audit["events"]][-1] == "filled"
    with pytest.raises(ManualOrderNotFound):
        await update_order_status("f" * 32, "sent", now=T0)
    with pytest.raises(ValueError):
        await update_order_status(row_id, "done", now=T0)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_queued_rows_may_only_end_without_being_sent(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    row_id = await _queue("/buy 1 AAPL stop 99", message_id=1)
    assert await update_order_status(row_id, "rejected", now=T0, reject_code="kill_switch") is True
    assert await claim_next_order(T0) is None
    row = await get_order(row_id)
    assert row is not None and (row.status, row.reject_code) == ("rejected", "kill_switch")


@pytest.mark.anyio
async def test_database_triggers_enforce_the_ledger_invariants(tmp_path, monkeypatch) -> None:
    path = await _ledger(tmp_path, monkeypatch)
    row_id = await _queue("/buy 10 AAPL stop 99", message_id=1)
    assert await claim_next_order(T0) is not None
    assert await update_order_status(row_id, "sent", now=T0, ibkr_order_id=5) is True
    async with aiosqlite.connect(path) as db:
        for statement, match in (
            ("UPDATE autotrader_manual_orders SET status='queued'", "backwards"),
            ("UPDATE autotrader_manual_orders SET status='sending'", "backwards"),
            ("UPDATE autotrader_manual_orders SET status='expired'", "backwards"),
            ("UPDATE autotrader_manual_orders SET quantity=1000", "immutable"),
            ("UPDATE autotrader_manual_orders SET symbol='TSLA'", "immutable"),
            ("UPDATE autotrader_manual_orders SET expires_at='2099-01-01'", "immutable"),
            ("UPDATE autotrader_manual_orders SET raw_text='/buy 1000 AAPL'", "immutable"),
            ("UPDATE autotrader_manual_orders SET ibkr_order_id=6", "immutable"),
            ("UPDATE autotrader_manual_orders SET filled_qty=11", "exceed"),
            ("DELETE FROM autotrader_manual_orders", "append-only"),
        ):
            with pytest.raises(aiosqlite.IntegrityError, match=match):
                await db.execute(statement)
            await db.rollback()
        await db.execute("UPDATE autotrader_manual_orders SET filled_qty=4, cancel_requested=1")
        await db.commit()
        for statement, match in (
            ("UPDATE autotrader_manual_orders SET filled_qty=3", "decrease"),
            ("UPDATE autotrader_manual_orders SET cancel_requested=0", "withdrawn"),
        ):
            with pytest.raises(aiosqlite.IntegrityError, match=match):
                await db.execute(statement)
            await db.rollback()
        await db.execute("UPDATE autotrader_manual_orders SET status='filled', filled_qty=10")
        await db.commit()
        with pytest.raises(aiosqlite.IntegrityError, match="backwards"):
            await db.execute("UPDATE autotrader_manual_orders SET status='sent'")
        await db.rollback()


@pytest.mark.anyio
async def test_schema_rejects_malformed_rows(tmp_path, monkeypatch) -> None:
    path = await _ledger(tmp_path, monkeypatch)
    base = {
        "id": "b" * 32, "created_at": "t", "updated_at": "t", "chat_id": 7, "message_id": 1,
        "update_id": 1, "source": "command", "raw_text": "/buy 1 AAPL stop 99", "action": "BUY",
        "symbol": "AAPL", "con_id": 1, "primary_exchange": "NASDAQ", "quantity": 1,
        "order_type": "MKT", "limit_price": None, "stop_price": None, "target_order_ref": None,
        "status": "queued", "expires_at": "t", "audit_json": "{}",
        # D5 (migration 17): the database refuses a buy without its stop.
        "entry_stop_price": "0.5",
    }
    bad_rows = [
        {"id": "B" * 32}, {"id": "b" * 31}, {"quantity": 0}, {"con_id": None}, {"order_type": "STP"},
        {"order_type": "LMT"}, {"limit_price": "10"}, {"action": "SELL_STOP"}, {"status": "done"},
        {"source": "model"}, {"audit_json": "not json"}, {"action": "CANCEL"},
        {"action": "CLOSE_ALL"}, {"message_id": 0}, {"raw_text": ""},
        {"entry_stop_price": None}, {"entry_stop_price": "0"}, {"entry_stop_price": "-1"},
        {"entry_stop_price": "x"}, {"order_type": "LMT", "limit_price": "0.5"},
        {"action": "SELL", "raw_text": "/sell 1 AAPL"},
    ]
    async with aiosqlite.connect(path) as db:
        columns = ", ".join(base)
        marks = ", ".join("?" for _ in base)
        await db.execute(f"INSERT INTO autotrader_manual_orders ({columns}) VALUES ({marks})", tuple(base.values()))
        await db.rollback()
        for overrides in bad_rows:
            row = {**base, **overrides}
            with pytest.raises(aiosqlite.IntegrityError):
                await db.execute(f"INSERT INTO autotrader_manual_orders ({columns}) VALUES ({marks})", tuple(row.values()))
            await db.rollback()


@pytest.mark.anyio
async def test_sending_rows_unknown_to_ibkr_become_unknown_and_are_never_resent(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    known = await _queue("/buy 1 AAPL stop 99", message_id=1)
    lost = await _queue("/buy 2 AAPL stop 99", message_id=2)
    queued = await _queue("/buy 3 AAPL stop 99", message_id=3)
    assert (await claim_next_order(T0)).id == known  # type: ignore[union-attr]
    assert (await claim_next_order(T0)).id == lost  # type: ignore[union-attr]
    marked = await mark_unknown_sending_rows({order_ref(known), "bot:xyz", "manual:junk"}, now=T0)
    assert marked == (lost,)
    assert await mark_unknown_sending_rows(set(), now=T0) == (known,)
    for row_id, status in ((known, "unknown"), (lost, "unknown"), (queued, "queued")):
        row = await get_order(row_id)
        assert row is not None and row.status == status
    claimed = await claim_next_order(T0)
    assert claimed is not None and claimed.id == queued  # the unknown rows are never claimed again
    assert await claim_next_order(T0) is None
    assert await update_order_status(lost, "sending", now=T0) is False
    assert await update_order_status(lost, "filled", now=T0, filled_qty=2) is True  # reconciliation evidence
    assert {row.id for row in await list_active_orders()} == {known, queued}


@pytest.mark.anyio
async def test_request_cancel_stops_queued_rows_and_flags_live_ones(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    live = await _queue("/buy 1 AAPL stop 99", message_id=1)
    assert (await claim_next_order(T0)).id == live  # type: ignore[union-attr]
    waiting = await _queue("/buy 2 AAPL stop 99", message_id=2)
    outcome = await request_cancel(short_id(waiting), now=T0)
    assert (outcome.result, outcome.cancelled_unsent, outcome.flagged_live) == ("applied", (waiting,), ())
    assert await claim_next_order(T0) is None
    row = await get_order(waiting)
    assert row is not None and (row.status, row.cancel_requested) == ("cancelled", True)
    outcome = await request_cancel(short_id(live), now=T0)
    assert outcome.flagged_live == (live,)
    assert [row.id for row in await list_cancel_requests()] == [live]
    row = await get_order(live)
    assert row is not None and (row.status, row.cancel_requested) == ("sending", True)
    assert (await request_cancel(short_id(waiting), now=T0)).result == "no_active_order"
    assert (await request_cancel("000000", now=T0)).result == "no_active_order"
    assert await update_order_status(live, "cancelled", now=T0) is True
    assert (await request_cancel(short_id(live), now=T0)).detail == "order is cancelled"
    assert list(await list_cancel_requests()) == []
    for bad in ("all ", "42", "A3F9C2", "#a3f9c2"):
        with pytest.raises(ValueError):
            await request_cancel(bad, now=T0)


@pytest.mark.anyio
async def test_cancel_all_covers_every_open_manual_order(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    sent = await _queue("/buy 1 AAPL stop 99", message_id=1)
    assert await claim_next_order(T0) is not None
    assert await update_order_status(sent, "sent", now=T0, ibkr_order_id=3) is True
    queued = await _queue("/sell 5 TSLA", message_id=2)
    done = await _queue("/buy 3 AAPL stop 99", message_id=3)
    assert await update_order_status(done, "rejected", now=T0) is True
    outcome = await request_cancel("all", now=T0)
    assert (outcome.cancelled_unsent, outcome.flagged_live) == ((queued,), (sent,))
    assert (await request_cancel("all", now=T0)).flagged_live == (sent,)


@pytest.mark.anyio
async def test_cancel_messages_are_ledger_rows_applied_atomically(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    target = await _queue("/buy 1 AAPL stop 99", message_id=1)
    cancel_text = f"/cancel {short_id(target)}"
    cancel_id = await _queue(cancel_text, message_id=2)
    cancel_row = await get_order(cancel_id)
    assert cancel_row is not None
    assert (cancel_row.action, cancel_row.status, cancel_row.target_order_ref) == ("CANCEL", "sent", short_id(target))
    assert cancel_row.audit["cancel"]["cancelled_unsent"] == [target]
    assert cancel_row.summary() == f"CANCEL #{short_id(target)}"
    assert (await get_order(target)).status == "cancelled"  # type: ignore[union-attr]
    assert await claim_next_order(T0) is None  # neither the target nor the cancel row is sendable
    assert await update_order_status(cancel_id, "filled", now=T0) is False
    assert await _queue(cancel_text, message_id=2) == cancel_id  # a replay applies nothing twice
    missing = await get_order(await _queue("/cancel 000000", message_id=3))
    assert missing is not None and (missing.status, missing.reject_code) == ("rejected", "no_active_order")
    nothing = await get_order(await _queue("cancel all", message_id=4))
    assert nothing is not None and (nothing.action, nothing.status, nothing.target_order_ref) == (
        "CANCEL_ALL", "rejected", "all"
    )


@pytest.mark.anyio
async def test_short_ids_stay_unique(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    candidates = iter(["abcdef" + "0" * 26, "abcdef" + "1" * 26, "123456" + "0" * 26])
    monkeypatch.setattr(manual, "_new_order_id", lambda: next(candidates))
    first = await _queue("/buy 1 AAPL stop 99", message_id=1)
    second = await _queue("/buy 2 AAPL stop 99", message_id=2)
    assert (first, second) == ("abcdef" + "0" * 26, "123456" + "0" * 26)
    monkeypatch.setattr(manual, "_new_order_id", lambda: "abcdef" + "2" * 26)
    with pytest.raises(RuntimeError, match="unique"):
        await _queue("/buy 3 AAPL stop 99", message_id=3)
    monkeypatch.setattr(manual, "_new_order_id", lambda: "Z" * 32)
    with pytest.raises(RuntimeError, match="hex"):
        await _queue("/buy 4 AAPL stop 99", message_id=4)


@pytest.mark.anyio
async def test_ledger_facts_feed_share_reservations_and_buy_budgets(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    yesterday = T0 - timedelta(days=1)
    await _queue("/buy 5 AAPL stop 99", message_id=1, facts=make_facts(now=yesterday))
    sell = await _queue("/sell 30 TSLA", message_id=2)
    stop = await _queue("/stop 20 TSLA 95", message_id=3)
    buy_live = await _queue("/buy 10 AAPL stop 99", message_id=4)  # estimate 10 x 100.01
    buy_filled = await _queue("/buy 100 AAPL 100 stop 99", message_id=5)
    buy_dead = await _queue("/buy 7 AAPL stop 99", message_id=6)
    facts = await ledger_facts(symbol="TSLA", now=T0)
    assert facts.open_manual_sell_qty == 50
    assert facts.counted_order_refs == frozenset({order_ref(sell), order_ref(stop)})
    assert (await ledger_facts(symbol="TSLA", now=T0, exclude_order_id=sell)).open_manual_sell_qty == 20
    assert (await ledger_facts(symbol="AAPL", now=T0)).open_manual_sell_qty == 0
    # Yesterday's buy counts only on its own New York day (5 x ask 100.01).
    assert (await ledger_facts(symbol=None, now=yesterday)).manual_open_buy_notional_today == Decimal("500.05")
    while (claimed := await claim_next_order(T0)) is not None:
        if claimed.id == sell:
            assert await update_order_status(sell, "partially_filled", now=T0, filled_qty=12) is True
        elif claimed.id == buy_filled:
            assert await update_order_status(buy_filled, "filled", now=T0, filled_qty=100,
                                             avg_fill_price=Decimal("99.50")) is True
        elif claimed.id == buy_dead:
            assert await update_order_status(buy_dead, "rejected", now=T0) is True
    facts = await ledger_facts(symbol="TSLA", now=T0)
    assert facts.open_manual_sell_qty == 38  # 30 - 12 filled + 20 resting
    assert facts.manual_open_buy_notional_today == Decimal("1000.10") + Decimal("9950.00")
    assert facts.recent_manual_orders_10min == 3
    assert (await ledger_facts(symbol=None, now=T0, exclude_order_id=buy_live)).recent_manual_orders_10min == 2
    later = T0 + timedelta(minutes=10, seconds=1)
    assert (await ledger_facts(symbol=None, now=later)).recent_manual_orders_10min == 0
    # Expired with no fills: yesterday's buy no longer reserves exposure.
    assert (await ledger_facts(symbol=None, now=yesterday)).manual_open_buy_notional_today == Decimal("0")


@pytest.mark.anyio
async def test_status_watch_lists(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    first = await _queue("/buy 1 AAPL stop 99", message_id=1)
    second = await _queue("/buy 2 AAPL stop 99", message_id=2, now=T0 + timedelta(seconds=1))
    assert [row.id for row in await list_orders_updated_since(T0 - timedelta(seconds=1))] == [first, second]
    assert await claim_next_order(T0 + timedelta(seconds=5)) is not None
    changed = await list_orders_updated_since(T0 + timedelta(seconds=2))
    assert [(row.id, row.status) for row in changed] == [(first, "sending")]
    assert [row.id for row in await list_active_orders()] == [first, second]


@pytest.mark.anyio
async def test_chat_budget_is_reserved_before_and_settled_after(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    day = T0.date()
    for _ in range(3):
        assert await chat_usage_reserve(day, Decimal("3"), Decimal("1")) is True
    assert await chat_usage_reserve(day, Decimal("3"), Decimal("0.01")) is False
    await chat_usage_settle(day, Decimal("1"), Decimal("0.08"))
    usage = await chat_usage(day)
    assert (usage.calls, usage.reserved_usd, usage.spent_usd) == (3, Decimal("2"), Decimal("0.08"))
    assert await chat_usage_reserve(day, Decimal("3"), Decimal("0.92")) is True
    assert await chat_usage_reserve(day, Decimal("3"), Decimal("0.01"), max_calls=4) is False
    other = (await chat_usage(day.replace(day=14)))
    assert (other.calls, other.spent_usd) == (0, Decimal(0))
    with pytest.raises(ValueError):
        await chat_usage_reserve(T0, Decimal("3"), Decimal("1"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await chat_usage_reserve(day, Decimal("3"), Decimal("-1"))


@pytest.mark.anyio
async def test_bridge_heartbeat_age(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    assert await bridge_heartbeat_age(T0) is None
    await write_bridge_heartbeat("starting", now=T0, pid=4242)
    await write_bridge_heartbeat("running", now=T0 + timedelta(seconds=2), pid=4242)
    heartbeat = await read_bridge_heartbeat()
    assert heartbeat is not None and (heartbeat.phase, heartbeat.pid) == ("running", 4242)
    assert await bridge_heartbeat_age(T0 + timedelta(seconds=7)) == pytest.approx(5.0)
    await write_bridge_heartbeat("running")
    assert (await read_bridge_heartbeat()).pid > 0  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        await write_bridge_heartbeat("", now=T0)


@pytest.mark.anyio
async def test_queued_close_rows_keep_their_kind(tmp_path, monkeypatch) -> None:
    await _ledger(tmp_path, monkeypatch)
    owner_close = await _queue("/close TSLA", message_id=1, facts=make_facts(owner_position_qty=Decimal(40)))
    bot_close = await _queue("/close AAPL", message_id=2, facts=make_facts(bot_symbols=frozenset({"AAPL"})))
    close_all = await _queue("/closeall", message_id=3)
    rows = {row.id: row for row in await list_active_orders()}
    assert (rows[owner_close].kind, rows[owner_close].quantity, rows[owner_close].order_type) == ("order", 40, "MKT")
    assert (rows[bot_close].kind, rows[bot_close].quantity, rows[bot_close].con_id) == ("bot_flatten", None, 265598)
    assert (rows[close_all].kind, rows[close_all].symbol, rows[close_all].summary()) == ("close_all", None, "CLOSE_ALL")
    claimed = await claim_next_order(T0)
    assert claimed is not None and claimed.id == owner_close
    revalidated = revalidate_claimed(
        claimed, facts=make_facts(owner_position_qty=Decimal(40)), contract=CONTRACTS["TSLA"]
    )
    assert accepted(revalidated).quantity == 40
    replaced = replace(claimed, quantity=41)
    assert rejected(revalidate_claimed(replaced, facts=make_facts(owner_position_qty=Decimal(40)),
                                       contract=CONTRACTS["TSLA"])) == "insufficient_shares"


# ---------------------------------------------------------------- D5 (v7c): a buy carries its stop


@pytest.mark.anyio
async def test_v7c_manual_buy_without_stop_refused_by_parser_and_db_trigger(tmp_path, monkeypatch) -> None:
    # The parser: no stop, no order (the old "buy first, then /stop" advice is gone).
    for text in ("/buy 100 AAPL", "/buy $5k NVDA", "buy 100 AAPL at 180", "/buy 100 AAPL 180"):
        refusal = parse_order_text(text)
        assert isinstance(refusal, ParseRefusal) and refusal.code == "missing_entry_stop", text
        assert "stop" in refusal.reason and "then /stop" not in refusal.reason
    # The model: a buy without its stop cannot even be represented.
    with pytest.raises(ValueError, match="protective stop"):
        ParsedOrder(action="BUY", source="command", symbol="AAPL", quantity=1, order_type="MKT")
    with pytest.raises(ValueError, match="takes no entry stop"):
        ParsedOrder(action="SELL", source="command", symbol="AAPL", quantity=1, order_type="MKT",
                    entry_stop_price=Decimal("90"))
    # The bridge's re-check: a buy row from before migration 17 is never sent.
    later = T0 + timedelta(seconds=30)
    assert rejected(revalidate_claimed(_row(entry_stop_price=None), facts=make_facts(now=later),
                                       contract=CONTRACTS["AAPL"])) == "missing_entry_stop"
    # The database: migration 17's trigger, whatever wrote the row.
    path = await _ledger(tmp_path, monkeypatch)
    columns = ("id, created_at, updated_at, chat_id, message_id, update_id, source, raw_text, action, symbol,"
               " con_id, primary_exchange, quantity, order_type, status, expires_at, audit_json")
    values = ("c" * 32, "t", "t", 7, 1, 1, "command", "/buy 1 AAPL", "BUY", "AAPL", 1, "NASDAQ", 1, "MKT",
              "queued", "t", "{}")
    async with aiosqlite.connect(path) as db:
        with pytest.raises(aiosqlite.IntegrityError, match="manual_buy_needs_stop"):
            await db.execute(f"INSERT INTO autotrader_manual_orders ({columns}) VALUES ({', '.join('?' * 17)})",
                             values)
    assert await _row_count(path) == 0


def test_buy_stop_must_be_below_the_bid_for_market_and_below_the_limit() -> None:
    # A market buy fills at the ask; a stop at or above the bid would fire at once.
    assert rejected(check("/buy 1 AAPL stop 99.99")) == "stop_not_below_entry"  # bid 99.99
    assert accepted(check("/buy 1 AAPL stop 99.98")).entry_stop_price == Decimal("99.98")
    refusal = parse_order_text("/buy 1 AAPL 100 stop 100")
    assert isinstance(refusal, ParseRefusal) and refusal.code == "stop_not_below_entry"
    order = accepted(check("/buy 1 AAPL 100 stop 99.98"))  # below the limit and the bid 99.99
    assert (order.limit_price, order.entry_stop_price, order.entry_risk) == (
        Decimal("100"), Decimal("99.98"), Decimal("0.02")
    )
    assert rejected(check("/buy 1 AAPL stop 99.995")) == "invalid_tick"
    assert accepted(check("/buy 100 AAPL 0.5123 stop 0.5001", make_facts(
        quote=make_quote(bid="0.5122", ask="0.5124", last="0.5123")))).entry_stop_price == Decimal("0.5001")


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("/buy 100 AAPL stop 90.01", True),  # 100 x (100.01 - 90.01) = 1,000.00 = 1% of 100k
        ("/buy 100 AAPL stop 90", False),  # 1,001.00
        ("/buy 100 AAPL 100 stop 90", True),  # 100 x 10 = 1,000 at the limit
        ("/buy 101 AAPL 100 stop 90", False),  # 1,010
        ("/buy $5k NVDA stop 80", True),  # 49 x 20.01 = 980.49
        ("/buy $5k NVDA stop 79", False),  # 49 x 21.01 = 1,029.49
    ],
)
def test_manual_entry_risk_is_at_most_1pct_of_net_liq(text: str, ok: bool) -> None:
    assert manual.MAX_MANUAL_ENTRY_RISK_FRACTION_OF_NET_LIQ == Decimal("0.01")
    result = check(text)
    if ok:
        order = accepted(result)
        assert order.entry_risk is not None and order.entry_risk <= Decimal("1000")
    else:
        assert rejected(result) == "manual_entry_risk_over_1pct"
        assert "over 1% of net liquidation" in result.message  # type: ignore[union-attr]


def test_exits_never_see_the_entry_stop_rules() -> None:
    # A net liquidation where a 1-share buy fits the notional caps but its risk is over 1%
    # (10.01 > 5): exits still pass, and never carry a stop.
    tiny = make_facts(net_liquidation=Decimal("500"), owner_position_qty=Decimal(100))
    assert rejected(check("/buy 1 AAPL stop 90", tiny)) == "manual_entry_risk_over_1pct"
    for text in ("/sell 10 AAPL", "/sell all AAPL", "/stop AAPL 95", "/close AAPL", "sell 10 AAPL at 100"):
        order = accepted(check(text, tiny))
        assert order.entry_stop_price is None and order.entry_risk is None, text
    for text in ("/closeall", "/cancel a3f9c2", "/cancel all"):
        assert accepted(check(text, tiny)).entry_stop_price is None


def test_revalidation_reapplies_the_entry_stop_rules_to_live_facts() -> None:
    later = T0 + timedelta(seconds=30)
    row = _row(entry_stop_price=Decimal("99"))
    good = accepted(revalidate_claimed(row, facts=make_facts(now=later), contract=CONTRACTS["AAPL"]))
    assert (good.entry_stop_price, good.entry_risk) == (Decimal("99"), Decimal("10.10"))
    fallen = make_facts(now=later, quote=make_quote(now=later, bid="98.99", ask="99.01", last="99.00"))
    assert rejected(revalidate_claimed(row, facts=fallen, contract=CONTRACTS["AAPL"])) == "stop_not_below_entry"
    # 10 x (100.01 - 90) = 100.10 fits 1% of 100k when queued, not of a 5k account at the send.
    wide = _row(entry_stop_price=Decimal("90"))
    assert accepted(revalidate_claimed(wide, facts=make_facts(now=later), contract=CONTRACTS["AAPL"]))
    poorer = make_facts(now=later, net_liquidation=Decimal("5000"))
    assert rejected(revalidate_claimed(wide, facts=poorer, contract=CONTRACTS["AAPL"])) == (
        "manual_entry_risk_over_1pct"
    )


@pytest.mark.anyio
async def test_queue_order_records_the_entry_stop_and_its_risk(tmp_path, monkeypatch) -> None:
    path = await _ledger(tmp_path, monkeypatch)
    row_id = await _queue("/buy 10 AAPL stop 99", message_id=1)
    row = await get_order(row_id)
    assert row is not None and row.entry_stop_price == Decimal("99") and row.stop_price is None
    validation = row.audit["validation"]
    assert validation["entry_stop_price"] == "99" and validation["entry_risk"] == "10.10"  # type: ignore[index]
    async with aiosqlite.connect(path) as db:
        with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
            await db.execute("UPDATE autotrader_manual_orders SET entry_stop_price='50' WHERE id=?", (row_id,))
    sell = await get_order(await _queue("/sell 10 AAPL", message_id=2))
    assert sell is not None and sell.entry_stop_price is None


@pytest.mark.anyio
async def test_manual_schema_pending_refuses_only_buys(tmp_path, monkeypatch) -> None:
    from tests.schema_support import apply_up_to

    path = tmp_path / "v16.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await apply_up_to(path, 16)
    assert await manual.entry_stop_ready() is False
    # Every exit and cancel is recorded and read back as before (the stop column reads NULL).
    sell = await _queue("/sell 10 AAPL", message_id=1)
    cancel = await _queue("/cancel a3f9c2", message_id=2)  # no such order: recorded, rejected
    rows = {row.id: row for row in await list_orders_updated_since(T0 - timedelta(days=1))}
    assert set(rows) == {sell, cancel} and rows[sell].entry_stop_price is None
    assert (await claim_next_order(T0)).id == sell  # type: ignore[union-attr]
    # A buy cannot be recorded without its stop column: nothing is written.
    with pytest.raises(manual.ManualSchemaPending) as pending:
        await _queue("/buy 10 AAPL stop 99", message_id=3)
    assert pending.value.code == "manual_schema_pending" == manual.MANUAL_SCHEMA_PENDING
    assert await _row_count(str(path)) == 2
    # After `autotraderctl db migrate` (v17) the same buy is recorded with its stop.
    await init_db()
    assert await manual.entry_stop_ready() is True
    row = await get_order(await _queue("/buy 10 AAPL stop 99", message_id=4))
    assert row is not None and row.entry_stop_price == Decimal("99")


def test_no_override_path() -> None:
    # D5: no same-day override. Nothing in the owner-order path or the bridge offers a
    # way around the entry-stop rules or the risk limits.
    from pathlib import Path
    import re

    backend = Path(manual.__file__).resolve().parents[2]
    words = re.compile(r"override|--force|i-know|i_know|bypass", re.IGNORECASE)
    for relative in ("trading/execution/manual_orders.py", "trading/operations/telegram_bot.py",
                     "scripts_local/paper_bridge.py"):
        found = [line for line in (backend / relative).read_text().splitlines() if words.search(line)]
        assert found == [], (relative, found)


def test_a_marketable_limit_buy_needs_its_stop_below_the_bid() -> None:
    # Limit 102 with the bid at 99.99 fills near the bid: a stop at 100 would fire at once.
    refused = check("/buy 1 AAPL 102 stop 100")
    assert rejected(refused) == "stop_not_below_entry" and "below the bid 99.99" in refused.message  # type: ignore[union-attr]
    assert accepted(check("/buy 1 AAPL 102 stop 99.98")).entry_risk == Decimal("2.02")  # risk at the limit
    # Below the market the limit itself is the floor (the parser already refuses a stop at it).
    assert isinstance(parse_order_text("/buy 1 AAPL 98 stop 98"), ParseRefusal)
    assert accepted(check("/buy 1 AAPL 98 stop 97.99")).entry_stop_price == Decimal("97.99")
