"""W1: direct_candidates supports the TradingView review state machine.

Scanner:     queued -> draining -> applied|failed|expired
TradingView: pending_review -> in_review -> applied|declined_by_ai|failed|expired
"""
import pytest

from db.core import init_db
from db.direct_candidates import (
    get_candidate_status,
    mark_candidate_status,
    queue_candidate,
)


async def test_tv_status_round_trip():
    await init_db()
    await queue_candidate("sig-tv-1", "MSFT", {"action": "buy"})
    await mark_candidate_status("sig-tv-1", "pending_review")
    assert await get_candidate_status("sig-tv-1") == "pending_review"
    await mark_candidate_status("sig-tv-1", "in_review")
    assert await get_candidate_status("sig-tv-1") == "in_review"
    await mark_candidate_status("sig-tv-1", "applied")
    assert await get_candidate_status("sig-tv-1") == "applied"


async def test_declined_by_ai_is_accepted_terminal():
    await init_db()
    await queue_candidate("sig-tv-2", "NVDA", {"action": "buy"})
    await mark_candidate_status("sig-tv-2", "in_review")
    await mark_candidate_status("sig-tv-2", "declined_by_ai")
    assert await get_candidate_status("sig-tv-2") == "declined_by_ai"


async def test_invalid_status_still_rejected():
    # An unknown status must raise before any DB write.
    with pytest.raises(ValueError):
        await mark_candidate_status("sig-whatever", "executed")
