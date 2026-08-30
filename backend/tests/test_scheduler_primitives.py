from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256

import aiosqlite
import pytest

from trading.scheduler import (
    LeaseLostError,
    acquire_lease,
    decision_key,
    get_lease,
    market_session,
    next_market_session,
    release_lease,
    renew_lease,
    require_lease,
)


@pytest.fixture
async def lease_db():
    async with aiosqlite.connect(":memory:") as db:
        await db.execute(
            """
            CREATE TABLE autotrader_scheduler_leases (
                name TEXT PRIMARY KEY,
                holder_id TEXT NOT NULL,
                fencing_token INTEGER NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()
        yield db


def test_decision_key_is_stable_and_bound_to_strategy_snapshot():
    snapshot = sha256(b"bar-snapshot").hexdigest()
    first = decision_key(strategy_id="orb", strategy_version=1, snapshot_fingerprint=snapshot)
    second = decision_key(strategy_id=" orb ", strategy_version=1, snapshot_fingerprint=snapshot)

    assert first == second
    assert first != decision_key(strategy_id="orb", strategy_version=2, snapshot_fingerprint=snapshot)
    assert first != decision_key(
        strategy_id="orb", strategy_version=1, snapshot_fingerprint=sha256(b"other").hexdigest()
    )


def test_decision_key_rejects_invalid_identity():
    with pytest.raises(ValueError, match="snapshot_fingerprint"):
        decision_key(strategy_id="orb", strategy_version=1, snapshot_fingerprint="not-a-hash")
    with pytest.raises(ValueError, match="strategy_version"):
        decision_key(strategy_id="orb", strategy_version=0, snapshot_fingerprint="a" * 64)


def test_market_session_uses_new_york_dst_and_five_minute_boundaries():
    before_dst = market_session(date(2026, 3, 6))
    after_dst = market_session(date(2026, 3, 9))

    assert before_dst is not None and after_dst is not None
    assert before_dst.opens_at.astimezone(timezone.utc).hour == 14
    assert after_dst.opens_at.astimezone(timezone.utc).hour == 13
    assert before_dst.opening_range_ends_at == before_dst.opens_at + timedelta(minutes=15)
    assert before_dst.scanner_at == before_dst.opens_at + timedelta(minutes=15, seconds=5)
    assert before_dst.first_signal_at == before_dst.opens_at + timedelta(minutes=20)
    assert before_dst.entry_cutoff_at.hour == 12


def test_market_session_holidays_half_days_and_next_session():
    assert market_session(date(2026, 1, 1)) is None
    thanksgiving = market_session(date(2026, 11, 27))
    assert thanksgiving is not None and thanksgiving.half_day
    assert thanksgiving.closes_at.hour == 13
    assert next_market_session(date(2026, 11, 26)).session_date == date(2026, 11, 27)
    assert next_market_session(date(2026, 11, 27)).session_date == date(2026, 11, 30)


def test_session_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        next_market_session(datetime(2026, 1, 2, 12, 0))


@pytest.mark.anyio
async def test_lease_claim_renew_release_and_fencing(lease_db):
    now = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)
    first = await acquire_lease(
        lease_db, name="session", holder_id="worker-a", ttl=timedelta(seconds=30), now=now
    )
    assert first is not None and first.fencing_token == 1
    assert await acquire_lease(
        lease_db, name="session", holder_id="worker-b", ttl=timedelta(seconds=30), now=now
    ) is None
    assert await require_lease(lease_db, claim=first, now=now) is None

    renewed = await renew_lease(
        lease_db, claim=first, ttl=timedelta(seconds=30), now=now + timedelta(seconds=10)
    )
    assert renewed is not None and renewed.expires_at == now + timedelta(seconds=40)
    assert await release_lease(lease_db, claim=first, now=now + timedelta(seconds=11))
    assert await get_lease(lease_db, name="session") is not None

    second = await acquire_lease(
        lease_db, name="session", holder_id="worker-b", ttl=timedelta(seconds=30), now=now + timedelta(seconds=12)
    )
    assert second is not None and second.fencing_token == 2
    assert await renew_lease(
        lease_db, claim=first, ttl=timedelta(seconds=30), now=now + timedelta(seconds=13)
    ) is None
    with pytest.raises(LeaseLostError):
        await require_lease(lease_db, claim=first, now=now + timedelta(seconds=13))


@pytest.mark.anyio
async def test_expired_lease_can_be_taken_over_only_with_new_fence(lease_db):
    now = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)
    first = await acquire_lease(
        lease_db, name="session", holder_id="worker-a", ttl=timedelta(seconds=5), now=now
    )
    assert first is not None
    second = await acquire_lease(
        lease_db, name="session", holder_id="worker-b", ttl=timedelta(seconds=5), now=now + timedelta(seconds=6)
    )
    assert second is not None and second.fencing_token == first.fencing_token + 1
