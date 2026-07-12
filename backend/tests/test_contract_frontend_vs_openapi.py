"""Tests for the frontend runtime route/OpenAPI CI contract gate."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_contract_frontend_vs_openapi import (
    Endpoint,
    check_contract,
    main,
    scan_frontend,
)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _schema(*operations: tuple[str, str]) -> dict[str, object]:
    paths: dict[str, dict[str, object]] = {}
    for method, path in operations:
        paths.setdefault(path, {})[method.lower()] = {"responses": {"200": {}}}
    return {"openapi": "3.1.0", "paths": paths}


def test_scan_frontend_extracts_helpers_templates_queries_and_fetch(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "services/api/routes.ts",
        """
import { get as read, post as send, del } from './client'

read<{ ok: boolean }>('/api/status')
read(`/api/watchlist${symbols ? `?symbols=${encodeURIComponent(symbols)}` : ''}`)
send<Record<string, { ok: boolean }>>(
  `/api/orders/${orderId}?dry_run=${dryRun}`,
  {},
)
del(`/api/rules/${ruleId}`)
fetch('/api/auth/token', { method: 'POST' })
fetch(`/api/yahoo/${symbol}/bars?period=1d`)
fetch('https://example.test/feed')
// fetch('/api/commented-out')
""",
    )

    result = scan_frontend(tmp_path)

    assert not result.issues
    assert {call.endpoint for call in result.calls} == {
        Endpoint("GET", "/api/status"),
        Endpoint("GET", "/api/watchlist"),
        Endpoint("POST", "/api/orders/{param}"),
        Endpoint("DELETE", "/api/rules/{param}"),
        Endpoint("POST", "/api/auth/token"),
        Endpoint("GET", "/api/yahoo/{param}/bars"),
    }


def test_check_contract_matches_different_openapi_parameter_names(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "services/api/routes.ts",
        """
import { get, del } from './client'
get(`/api/stock/${symbol}/overview`)
del(`/api/orders/${id}`)
""",
    )
    document = _schema(
        ("GET", "/api/stock/{ticker}/overview"),
        ("DELETE", "/api/orders/{order_id}"),
    )

    report = check_contract(tmp_path, document)

    assert report.ok
    assert not report.missing
    assert report.unique_frontend_operation_count == 2


def test_check_contract_reports_missing_route_and_wrong_method(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "services/api/routes.ts",
        """
import { get, post } from './client'
get('/api/missing')
post('/api/orders', {})
""",
    )
    document = _schema(("GET", "/api/orders"))

    report = check_contract(tmp_path, document)

    assert not report.ok
    assert [(call.method, call.path) for call in report.missing] == [
        ("GET", "/api/missing"),
        ("POST", "/api/orders"),
    ]


def test_scan_frontend_fails_closed_for_dynamic_helper_url(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "services/api/routes.ts",
        """
import { get } from './client'
const endpoint = '/api/status'
get(endpoint)
""",
    )

    result = scan_frontend(tmp_path)

    assert not result.calls
    assert len(result.issues) == 1
    assert "URL argument must be a string or template literal" in result.issues[0].message


def test_scan_frontend_excludes_tests_specs_and_transport(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "services/api/routes.ts",
        "import { get } from './client'\nget('/api/runtime')\n",
    )
    _write(tmp_path, "services/api/client.ts", "fetch('/api/transport-internal')\n")
    _write(
        tmp_path,
        "feature/__tests__/route.ts",
        "fetch('/api/test-only')\n",
    )
    _write(tmp_path, "feature/route.spec.ts", "fetch('/api/spec-only')\n")
    _write(tmp_path, "feature/route.test.tsx", "fetch('/api/test-only-2')\n")

    result = scan_frontend(tmp_path)

    assert [(call.method, call.path) for call in result.calls] == [("GET", "/api/runtime")]


def test_cli_uses_supplied_openapi_and_returns_nonzero_on_drift(
    tmp_path: Path, capsys
) -> None:
    frontend_root = tmp_path / "src"
    _write(frontend_root, "route.ts", "fetch('/api/status')\n")
    schema_path = tmp_path / "openapi.json"
    schema_path.write_text(json.dumps(_schema(("GET", "/api/status"))), encoding="utf-8")

    assert main(["--frontend-root", str(frontend_root), "--openapi", str(schema_path)]) == 0
    assert "Contract check passed" in capsys.readouterr().out

    schema_path.write_text(json.dumps(_schema(("POST", "/api/status"))), encoding="utf-8")
    assert main(["--frontend-root", str(frontend_root), "--openapi", str(schema_path)]) == 1
    failure_output = capsys.readouterr().out
    assert "Missing FastAPI operations" in failure_output
    assert "route.ts:1 GET /api/status" in failure_output
