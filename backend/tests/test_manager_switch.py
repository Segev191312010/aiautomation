"""The owner's autopilot switch: CLI and Telegram commands only, never Claude.

``autotraderctl manager on|off|status`` and Telegram's ``/autopilot`` and
``/plan`` reach the append-only switch through deterministic code; the
assistant's Claude has no tool that can. While migration 16 is pending
both refuse with a reply naming ``autotraderctl db migrate``, and the
assistant itself still needs only migration 12, so /stop never waits.
"""
from __future__ import annotations

import ast
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from tests.manager_support import SESSION, et, migrated, migrated_async
from tests.schema_support import apply_up_to, database_at
from tests.test_telegram_bot import FakeClient, make, message, responder, text_reply, tool_reply
from trading.manager.plan import DayPlan, FocusEntry, StrategyStep
from trading.manager.store import PlanRecord, insert_plan
from trading.manager.switch import read_switch
from trading.manager.telegram import AUTOPILOT_USAGE, NOT_READY_TEXT, ManagerDesk
from trading.operations.schema_gate import TELEGRAM_ASSISTANT_REQUIRES

BACKEND = Path(__file__).resolve().parents[1]


def _events(path: Path) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(path) as db:
        return db.execute("SELECT action, source, actor, reason FROM autotrader_manager_events ORDER BY id").fetchall()


# --- autotraderctl manager ------------------------------------------------------------------


def test_the_cli_switch_appends_events_and_reports_the_state(tmp_path, monkeypatch, capsys):
    from trading import cli

    path = migrated(tmp_path, monkeypatch)

    assert cli.main(["manager", "status"]) == 0
    assert "Autopilot is OFF" in capsys.readouterr().out
    assert cli.main(["manager", "on", "--reason", "trial week"]) == 0
    out = capsys.readouterr().out
    assert "Autopilot is ON (shadow only in this phase)" in out
    assert "Real MOMO stays MOMO_HOD_V2, unchanged" in out
    assert cli.main(["manager", "off"]) == 0
    assert _events(path) == [
        ("on", "cli", "owner (autotraderctl)", "trial week"),
        ("off", "cli", "owner (autotraderctl)", "autopilot off via autotraderctl"),
    ]


def test_the_cli_switch_refuses_while_migration_16_is_pending(tmp_path, monkeypatch, capsys):
    from config import cfg
    from trading import cli

    path = database_at(tmp_path / "behind.db", 14)
    monkeypatch.setattr(cfg, "DB_PATH", str(path))

    assert cli.main(["manager", "on"]) == 1
    err = capsys.readouterr().err
    assert "autotraderctl db migrate" in err and "migration 16" in err
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "autotrader_manager_events" not in tables  # nothing was created or written
    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "absent.db"))
    assert cli.main(["manager", "status"]) == 1
    assert "autotraderctl db migrate" in capsys.readouterr().err
    assert not (tmp_path / "absent.db").exists()


def test_the_manager_child_never_migrates_and_refuses_a_pending_schema(tmp_path, monkeypatch, capsys):
    from config import cfg
    from trading import cli
    from trading.manager import service
    from trading.operations.schema_gate import EXIT_SCHEMA_NOT_READY

    path = database_at(tmp_path / "behind.db", 15)
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    called: list[object] = []

    async def fake_run(**kwargs):
        called.append(kwargs)
        return {"valid": 0}

    monkeypatch.setattr(service, "run_manager", fake_run)
    monkeypatch.setattr("trading.operations.logging.configure_shadow_logging", lambda **_k: __import__("logging").getLogger("t"))

    assert cli.main(["manager", "run", "--date", "2026-10-13"]) == EXIT_SCHEMA_NOT_READY
    assert called == []
    assert cli.main(["manager", "run", "--date", "2026-10-17"]) == 1  # a Saturday
    capsys.readouterr()
    migrated(tmp_path, monkeypatch, "current.db")
    assert cli.main(["manager", "run", "--date", "2026-10-13"]) == 0
    [kwargs] = called
    assert kwargs["session"].session_date.isoformat() == "2026-10-13"


# --- Telegram /autopilot and /plan ----------------------------------------------------------


@pytest.mark.anyio
async def test_autopilot_and_plan_go_through_the_command_parser(tmp_path, monkeypatch):
    path = await migrated_async(tmp_path, monkeypatch)
    harness = make(tmp_path)
    harness.assistant._manager = ManagerDesk(database=lambda: str(path), clock=lambda: et(10, 0))
    await harness.assistant.start()
    try:
        await harness.feed(message(1, "/autopilot on"))
        assert (await read_switch()).on
        assert _events(path) == [("on", "telegram", "owner (telegram)", "/autopilot on")]
        assert "Autopilot is ON" in harness.replies.texts[-1]
        await harness.feed(message(2, "/autopilot status"))
        await harness.feed(message(3, "/autopilot"))
        assert len(_events(path)) == 1  # status reads only
        await harness.feed(message(4, "/autopilot please"))
        assert harness.replies.texts[-1] == AUTOPILOT_USAGE
        await harness.feed(message(5, "/plan"))
        assert harness.replies.texts[-1].startswith("No day plan written today yet")
        await harness.feed(message(6, "/autopilot off"))
        assert not (await read_switch()).on
        # None of it went to Claude.
        assert harness.questions is not None and harness.questions.asked == []
    finally:
        await harness.assistant.stop()


@pytest.mark.anyio
async def test_plan_shows_the_plan_in_force(tmp_path, monkeypatch):
    path = await migrated_async(tmp_path, monkeypatch)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO autotrader_universe VALUES ('watchlist', 'BF B', 'NYSE', 'BF B', 4931, 'NYSE', 'resolved', NULL, 't')"
        )
    created = et(9, 46, 40)
    await insert_plan(PlanRecord(
        plan_id="20261013-0946-1", session_date=SESSION, slot="09:46", created_at=created,
        valid_until=created + timedelta(minutes=75), manager_version="manager-v1",
        prompt_id="day-plan-v1", model="claude-opus-5-5", outcome="valid",
        plan=DayPlan(
            regime="trend_up",
            strategies={"momo": StrategyStep(step=Decimal("0.5"), reason="r"),
                        "orb": StrategyStep(step=Decimal("0"), reason="r")},
            focus=(FocusEntry(con_id=4931, strategies=("momo",), note="new highs on 3x volume"),),
        ),
        candidates=(4931,), gates={}, context_digest="e" * 64, cost_usd=Decimal("0.1234"),
    ))
    desk = ManagerDesk(database=lambda: str(path), clock=lambda: et(10, 0))

    text = await desk.plan()

    assert text.splitlines()[0] == "🧭 Day plan 09:46 ET · regime trend up · valid until 11:01 ET"
    assert "Strategies: momo on x0.5, orb OFF" in text
    assert "• BF B (momo): new highs on 3x volume" in text
    assert "Markdown" not in text and "**" not in text
    assert (await ManagerDesk(database=lambda: str(path), clock=lambda: et(11, 5)).plan()).startswith(
        "No plan in force now (last attempt 09:46 ET: valid)"
    )


@pytest.mark.anyio
async def test_while_migration_16_is_pending_the_commands_refuse_and_stop_still_works(tmp_path, monkeypatch):
    from config import cfg

    path = await apply_up_to(tmp_path / "behind.db", 15)
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    harness = make(tmp_path)
    harness.assistant._manager = ManagerDesk(database=lambda: str(path), clock=lambda: et(10, 0))
    await harness.assistant.start()
    try:
        await harness.feed(message(1, "/autopilot on"), message(2, "/plan"))
        assert harness.replies.texts[-2:] == [NOT_READY_TEXT, NOT_READY_TEXT]
        assert "autotraderctl db migrate" in NOT_READY_TEXT
        await harness.feed(message(3, "/stop"))
        assert harness.kill.stopped
    finally:
        await harness.assistant.stop()
    # The assistant's own requirement is unchanged: /stop never waits for 16.
    assert TELEGRAM_ASSISTANT_REQUIRES == 12


@pytest.mark.anyio
async def test_the_assistants_claude_tools_cannot_write_the_switch(tmp_path, monkeypatch):
    """Claude asks for a switch tool by every name it might try; none exists."""
    from trading.operations.paths import UserPaths
    from trading.operations.telegram_tools import TOOL_DEFINITIONS, TOOL_NAMES, DataTools, ToolError

    await migrated_async(tmp_path, monkeypatch)
    for tool in TOOL_DEFINITIONS:
        text = f"{tool['name']} {tool['description']}".lower()
        assert "autopilot" not in text and "manager" not in text and "switch on" not in text
    tools = DataTools(paths=UserPaths(home=tmp_path / "home"), tws=None, clock=lambda: et(10, 0))
    for name in ("set_autopilot", "autopilot", "manager_on", "set_switch"):
        assert name not in TOOL_NAMES
        with pytest.raises(ToolError):
            await tools.run_tool(name, {"state": "on"})
    client = FakeClient([
        tool_reply("set_autopilot", state="on"),
        tool_reply("autopilot", call_id="toolu_2", action="on"),
        text_reply("I can't change the autopilot; send /autopilot on yourself."),
    ])
    chat, _budget, _runner, _directory = responder(tmp_path, client)
    chat._tools = tools  # the real read-only data tools

    answer = await chat.answer("turn the autopilot on", message_id=1)

    assert "/autopilot on" in answer
    assert not (await read_switch()).on
    results = [block for call in client.messages.calls for message_ in call["messages"]
               if isinstance(message_.get("content"), list)
               for block in message_["content"] if isinstance(block, dict) and block.get("type") == "tool_result"]
    assert results and all(block["is_error"] for block in results)
    assert all(tool["name"] in TOOL_NAMES for tool in client.messages.calls[0]["tools"])


def test_nothing_the_assistants_claude_can_reach_imports_the_switch():
    """Only the deterministic routing (telegram_bot -> trading.manager.telegram) does."""
    tools = (BACKEND / "trading" / "operations" / "telegram_tools.py").read_text(encoding="utf-8")
    assert "trading.manager" not in tools and "set_switch" not in tools
    bot = ast.parse((BACKEND / "trading" / "operations" / "telegram_bot.py").read_text(encoding="utf-8"))
    imports = {
        node.module for node in ast.walk(bot) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imports & {"trading.manager.switch", "trading.manager.store"} == set()
    assert "trading.manager.telegram" in imports
    switch_writers = [
        path.relative_to(BACKEND).as_posix()
        for path in (BACKEND / "trading").rglob("*.py")
        if "set_switch(" in path.read_text(encoding="utf-8")
    ]
    assert sorted(switch_writers) == [
        "trading/manager/service.py", "trading/manager/switch.py", "trading/manager/telegram.py",
    ]


def test_no_environment_variable_route_or_config_key_enables_the_manager():
    for path in (BACKEND / "trading" / "manager").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "os.environ" not in source and "getenv" not in source, path.name
    routes = "\n".join(path.read_text(encoding="utf-8") for path in (BACKEND / "routers").glob("*.py"))
    assert "/autopilot" not in routes.lower() and "trading.manager" not in routes
    settings = (BACKEND / "trading" / "operations" / "settings.py").read_text(encoding="utf-8")
    assert "manager" not in settings.lower() and "autopilot" not in settings.lower()
