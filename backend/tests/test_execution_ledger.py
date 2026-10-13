"""The execution ledger's repository (``ExecutionLedger``), which only the engine uses.

The ledger migration itself (identity, checksum, application, every trigger)
is tested where it ships: ``tests/test_execution_ledger_schema.py``, which the
main line carries on its own.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from config import cfg
from db.core import init_db
from tests.fakes.decision_ledger import DecisionSeeder
from trading.storage.execution_ledger import (
    BracketRecord,
    DecisionAlreadyClaimed,
    ExecutionLedger,
    FillRecord,
    LegRecord,
    RiskBookRefusal,
    book_id,
    order_ref,
    parse_order_ref,
)


ACCOUNT = "DU1234567"
SESSION = date(2026, 10, 13)
NOW = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def ledger_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    asyncio.run(init_db())
    return path


def seed_decisions(count: int) -> list[str]:
    seeder = DecisionSeeder(SESSION)
    return [asyncio.run(seeder.accept(f"SYM{index}", created=NOW, con_id=1000 + index)) for index in range(count)]


# ---- the repository ----------------------------------------------------------------------------


def staged(decision: str, bracket: str, symbol: str, *, quantity: int = 100, first_id: int = 100,
           stop: str = "49.50") -> BracketRecord:
    def leg(code: str, order_id: int, action: str, order_type: str, tif: str, price: str, parent: int | None,
            transmit: bool) -> LegRecord:
        return LegRecord(id=f"{bracket}:{code}", bracket_id=bracket, leg=code, client_id=18,  # type: ignore[arg-type]
                         order_id=order_id, order_ref=order_ref(bracket, code), action=action,  # type: ignore[arg-type]
                         order_type=order_type, tif=tif, quantity=quantity, price=Decimal(price),  # type: ignore[arg-type]
                         parent_order_id=parent, transmit=transmit, state="staged", created_at=NOW)

    return BracketRecord(
        id=bracket, decision_id=decision, account=ACCOUNT, client_id=18, session_date=SESSION,
        strategy_name="MOMO_HOD_V2", symbol=symbol, con_id=abs(hash(symbol)) % 100000 + 1, primary_exchange="NASDAQ",
        quantity=quantity, entry_price=Decimal("50.00"), stop_price=Decimal(stop), target_price=Decimal("52.00"),
        setup_fingerprint=None, state="sending", created_at=NOW,
        legs={
            "P": leg("P", first_id, "BUY", "LMT", "DAY", "50.00", None, False),
            "T": leg("T", first_id + 1, "SELL", "LMT", "GTC", "52.00", first_id, False),
            "S": leg("S", first_id + 2, "SELL", "STP", "GTC", stop, first_id, True),
        },
    )


async def test_staging_rechecks_the_shared_book_atomically(ledger_path) -> None:
    first, second, third = await asyncio.to_thread(seed_decisions, 3)
    ledger = ExecutionLedger(clock=lambda: NOW)
    # 3000 x 1.00 = 3000 risk each against a 500000 book: 0.6% each would break the 0.5% new-trade cap,
    # so use 2500 x 1.00 = 0.5% each; together the three would be 1.5%, the fourth anything more.
    records = [staged(decision, f"{index:012x}", f"S{index}", quantity=2500, first_id=100 + 3 * index, stop="49.00")
               for index, decision in enumerate((first, second, third))]
    results = await asyncio.gather(
        *(ledger.stage_bracket(record, net_liquidation=Decimal("500000")) for record in records),
        return_exceptions=True,
    )
    assert all(isinstance(result, str) for result in results)  # serialized: 1.5% exactly fits
    with pytest.raises(DecisionAlreadyClaimed):
        await ledger.stage_bracket(staged(first, "f" * 12, "AGAIN", first_id=900), net_liquidation=Decimal("500000"))
    [fourth] = await asyncio.to_thread(seed_decisions_named, "S9")
    with pytest.raises(RiskBookRefusal) as refusal:
        await ledger.stage_bracket(staged(fourth, "e" * 12, "S9", quantity=10, first_id=950),
                                   net_liquidation=Decimal("500000"))
    assert refusal.value.check.code == "max_positions_reached"
    assert await ledger.outbox_row(fourth) is None  # a refused stage leaves nothing behind


def seed_decisions_named(symbol: str) -> list[str]:
    seeder = DecisionSeeder(SESSION)
    return [asyncio.run(seeder.accept(symbol, created=NOW, con_id=99_999, strategy="MOMO_HOD_V9"))]


async def test_fills_are_deduplicated_corrections_win_and_conflicts_are_reported(ledger_path) -> None:
    [decision] = await asyncio.to_thread(seed_decisions, 1)
    ledger = ExecutionLedger(clock=lambda: NOW)
    record = staged(decision, "a" * 12, "AAPL")
    await ledger.stage_bracket(record, net_liquidation=Decimal("1000000"))
    leg = f"{record.id}:P"

    def fill(exec_id: str, shares: int, *, commission: str | None = None) -> FillRecord:
        base, _, version = exec_id.rpartition(".")
        return FillRecord(exec_id=exec_id, exec_base=base, exec_version=int(version), leg_id=leg, order_id=100,
                          side="BOT", shares=shares, price=Decimal("50.00"), account=ACCOUNT, exec_time=None,
                          commission=None if commission is None else Decimal(commission),
                          commission_currency=None if commission is None else "USD")

    first = await ledger.record_fills([fill("e1.01", 60), fill("e2.01", 40)])
    again = await ledger.record_fills([fill("e1.01", 60, commission="1.00"), fill("e2.01", 41), fill("e1.02", 50)])
    assert first.inserted == ("e1.01", "e2.01")
    assert (again.inserted, again.commissions_added, again.conflicts) == (("e1.02",), ("e1.01",), ("e2.01",))
    totals = await ledger.fill_totals([leg])
    assert (totals[leg].shares, totals[leg].average) == (90, Decimal("50.0000"))  # e1.02 replaces e1.01
    assert len(await ledger.load_fills([leg])) == 3  # every report is kept for the audit


def test_order_refs_round_trip_and_reject_everything_else() -> None:
    assert order_ref("0123456789ab", "S") == "at1:0123456789ab:S"
    assert parse_order_ref("at1:0123456789ab:S") == ("0123456789ab", "S")
    for ref in ("manual:" + "a" * 32, "autotrader:abc", "at1:0123456789ab:X", "at1:XYZ:P", "", None, "at1::P"):
        assert parse_order_ref(ref) is None
    with pytest.raises(ValueError):
        order_ref("not-hex", "P")
    assert book_id(ACCOUNT, SESSION) == "DU1234567:2026-10-13"


def test_what_a_bracket_claims_from_the_shared_book() -> None:
    record = staged("d", "a" * 12, "AAPL")
    record.state = "working"
    assert (record.book_position().open_risk, record.book_position().holds_slot) == (Decimal("50.00"), True)
    record.state, record.entry_filled, record.avg_entry = "open", 60, Decimal("49.90")
    record.legs["P"].broker_status = "Cancelled"  # a dead parent no longer adds pending risk
    assert record.book_position().open_risk == Decimal("60") * Decimal("0.40")
    record.state = "needs_attention"
    assert record.book_position().holds_slot  # until reconciliation sees it flat at IBKR (M4)
    record.attention_cleared_at = NOW
    assert record.book_position() .holds_slot is False
    for state in ("closed", "cancelled", "unknown"):
        record.state, record.attention_cleared_at = state, None
        assert (record.book_position().open_risk, record.book_position().holds_slot) == (Decimal("0"), False)
