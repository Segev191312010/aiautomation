from __future__ import annotations

import ast
from pathlib import Path

def test_app_exposes_only_v1_and_auth_routes():
    from main import app

    paths = set(app.openapi()["paths"])
    assert "/api/health" in paths
    assert "/api/auth/token" in paths
    assert "/api/engine/v1/status" in paths
    assert not any(path.startswith("/api/autopilot") for path in paths)
    assert not any(path.startswith("/api/bot") for path in paths)
    assert not any(path.startswith("/api/orders") for path in paths)


def test_app_has_no_legacy_authority_imports():
    source = Path(__file__).parents[1].joinpath("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_roots.isdisjoint(
        {"advisor_api", "autopilot_api", "bot_runner", "direct_ai_trader", "execution_brain", "order_executor"}
    )
