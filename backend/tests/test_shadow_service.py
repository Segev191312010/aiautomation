"""Daemon scheduling: the right session is chosen and one bad day never stops it."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading.operations import shadow_service
from trading.scheduler.sessions import market_session


def test_session_to_run_uses_today_until_the_entry_cutoff():
    # Tue 2026-10-13: 13:00 UTC = 09:00 ET (before cutoff), 16:30 UTC = 12:30 ET.
    morning = datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)
    afternoon = datetime(2026, 10, 13, 16, 30, tzinfo=timezone.utc)
    assert shadow_service.session_to_run(morning).session_date.isoformat() == "2026-10-13"
    assert shadow_service.session_to_run(afternoon).session_date.isoformat() == "2026-10-14"
    # Saturday rolls to Monday.
    saturday = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
    assert shadow_service.session_to_run(saturday).session_date.isoformat() == "2026-10-19"


@pytest.mark.anyio
async def test_daemon_survives_a_failed_session_and_reloads_config(monkeypatch):
    clock = {"now": datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)}
    loads: list[int] = []
    sessions: list[str] = []

    async def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    async def fake_session(*, config, owner_id, session, clock, sleep):
        sessions.append(session.session_date.isoformat())
        if len(sessions) == 1:
            raise RuntimeError("TWS was not running")

    monkeypatch.setattr(shadow_service, "run_shadow_session", fake_session)

    def loader():
        loads.append(1)
        return object()

    await shadow_service.run_shadow_daemon(
        config_loader=loader,
        owner_id="demo",
        clock=lambda: clock["now"],
        sleep=sleep,
        max_sessions=2,
    )

    assert sessions == ["2026-10-13", "2026-10-14"]
    assert len(loads) == 2
    assert clock["now"] >= market_session(
        datetime(2026, 10, 14).date()
    ).entry_cutoff_at.astimezone(timezone.utc)


def test_policy_fingerprints_bind_prompt_model_and_config():
    from trading.contracts import RiskPolicy, ShadowRuntimeConfig

    config = ShadowRuntimeConfig(
        paper_account_id="DU1",
        data_client_id=17,
        order_client_id=18,
        model_id="claude-opus-5-5",
        prompt_id="orb-review-v1",
        calendar_id="XNYS",
        calendar_version="2026.1",
        commission_policy_id="ibkr-fixed",
        commission_policy_version=1,
    )
    prints = shadow_service.policy_fingerprints(config, RiskPolicy())
    assert set(prints) == {
        "risk",
        "opening_range_breakout",
        "execution",
        "review_prompt",
        "review_model",
        "runtime_config",
    }
    assert all(len(value) == 64 for value in prints.values())


def test_cli_exposes_shadow_run_daemon_and_agent():
    from trading.cli import _parser

    parser = _parser()
    assert parser.parse_args(["shadow", "run", "--date", "2026-10-13"]).date.isoformat() == "2026-10-13"
    assert parser.parse_args(["shadow", "daemon"]).action == "daemon"
    assert parser.parse_args(["shadow", "agent", "status"]).agent_action == "status"
