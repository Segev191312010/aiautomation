"""The pinned PAPER bridge can import the backend modules it loads lazily.

The bridge (scripts_local/paper_bridge.py, pinned by sha256) imports the
session calendar, ``config`` and the manual-order ledger inside functions,
and survives a failure by degrading: a broken calendar import falls back to
a 16:00 close (wrong on a half day) and a broken ledger import disables
owner orders, each with only a CRITICAL log line. So a backend change that
breaks one of those imports, or anything they import (db.core, migrations,
contracts, the schema modules), would not fail any process loudly.

This test runs the bridge's own import statements, read from its source, in
a fresh interpreter (nothing preloaded, cwd outside the backend) after the
bridge's own ``backend_modules()``, then checks every ledger attribute the
bridge's owner-order desk uses. The bridge itself is never edited.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1]
BRIDGE = BACKEND / "scripts_local" / "paper_bridge.py"
BACKEND_ROOTS = {"auth", "config", "db", "main", "routers", "startup", "trading"}
# What the reviewed bridge imports lazily; a new pinned bridge that changes
# this list must update it here, so the smoke test keeps covering every one.
EXPECTED_LAZY_IMPORTS = (
    "from trading.scheduler.sessions import market_session",
    "from config import cfg",
    "from trading.execution import manual_orders",
)

_SMOKE = r"""
import importlib.util
import json
import sys
from datetime import date

bridge_path, statements, ledger_names = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
spec = importlib.util.spec_from_file_location("paper_bridge_import_smoke", bridge_path)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge  # its dataclasses look their module up while loading
spec.loader.exec_module(bridge)
preloaded = sorted(name for name in ("config", "db", "trading") if name in sys.modules)
bridge.backend_modules()
namespace = {}
for statement in statements:  # in source order, as the bridge reaches them
    exec(statement, namespace)
ledger = namespace["manual_orders"]
half_day = namespace["market_session"](date(2026, 11, 27))
print(json.dumps({
    "preloaded": preloaded,
    "missing": [name for name in ledger_names if not hasattr(ledger, name)],
    "half_day_close": half_day.closes_at.astimezone(bridge.NY).strftime("%H:%M"),
    "db_path": namespace["cfg"].DB_PATH,
}))
"""


def _tree() -> ast.Module:
    return ast.parse(BRIDGE.read_text(encoding="utf-8"))


def _lazy_backend_imports(tree: ast.Module) -> list[str]:
    statements: list[tuple[int, str]] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
            elif isinstance(node, ast.Import):
                root = node.names[0].name.split(".", 1)[0]
            else:
                continue
            if root in BACKEND_ROOTS:
                statements.append((node.lineno, ast.unparse(node)))
    return [statement for _, statement in sorted(set(statements))]


def _ledger_attributes(tree: ast.Module) -> list[str]:
    """Every ``self.mo.<name>`` the owner-order desk reads from the ledger module."""
    return sorted(
        {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mo"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        }
    )


def test_the_bridge_lazily_imports_exactly_these_backend_modules():
    assert tuple(_lazy_backend_imports(_tree())) == EXPECTED_LAZY_IMPORTS
    assert len(_ledger_attributes(_tree())) >= 10


def _imported_file(statement: str) -> str:
    """The backend file one lazy import statement loads, by path under backend/."""
    node = ast.parse(statement).body[0]
    assert isinstance(node, ast.ImportFrom) and node.module and len(node.names) == 1
    module = node.module.replace(".", "/")
    as_module = BACKEND / f"{module}.py"
    if as_module.is_file():  # from trading.scheduler.sessions import market_session
        return as_module.relative_to(BACKEND).as_posix()
    submodule = BACKEND / module / f"{node.names[0].name}.py"  # from trading.execution import manual_orders
    assert submodule.is_file(), statement
    return submodule.relative_to(BACKEND).as_posix()


def test_expected_lazy_imports_equal_pinned_import_keys():
    # P3-B0 (C1): the supervisor's imports pin covers exactly the files the
    # reviewed bridge imports lazily, read from the bridge's own source.
    from trading.operations.supervisor import REVIEWED_BRIDGE_IMPORTS_SHA256

    statements = _lazy_backend_imports(_tree())
    assert tuple(statements) == EXPECTED_LAZY_IMPORTS
    assert sorted(_imported_file(statement) for statement in statements) == sorted(REVIEWED_BRIDGE_IMPORTS_SHA256)


def test_the_bridge_backend_imports_work_in_a_fresh_interpreter(tmp_path):
    tree = _tree()
    database = tmp_path / "never-created.db"
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        {"HOME": str(tmp_path / "home"), "DB_PATH": str(database), "PYTHONDONTWRITEBYTECODE": "1"}
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _SMOKE,
            str(BRIDGE),
            json.dumps(_lazy_backend_imports(tree)),
            json.dumps(_ledger_attributes(tree)),
        ],
        cwd=tmp_path,  # outside the backend: only backend_modules() makes it importable
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-4000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["preloaded"] == []  # every backend import really ran here
    assert report["missing"] == []
    assert report["half_day_close"] == "13:00"  # the calendar the bridge flattens by
    assert report["db_path"] == str(database)
    assert not database.exists()  # importing never opens the database
