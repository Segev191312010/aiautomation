"""Keep fixtures and broker doubles outside runtime and evidence paths."""
from __future__ import annotations

from pathlib import Path


RUNTIME_ROOTS = (
    Path(__file__).resolve().parents[1] / "trading",
    Path(__file__).resolve().parents[1] / "main.py",
)
FORBIDDEN_IMPORT_MARKERS = (
    "tests.fakes",
    "fake_broker",
    "bracket_executor",
    '"fixture"',
    "'fixture'",
    "data_mode=\"fixture\"",
    "data_mode='fixture'",
)


def test_runtime_and_evidence_do_not_import_test_doubles_or_fixture_data() -> None:
    runtime_files = [
        path
        for root in RUNTIME_ROOTS
        for path in ([root] if root.is_file() else root.rglob("*.py"))
    ]
    violations: list[str] = []
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_IMPORT_MARKERS:
            if marker in source:
                violations.append(f"{path.relative_to(Path(__file__).resolve().parents[1])}: {marker}")
    assert not violations, "runtime fixture firewall failed: " + "; ".join(violations)
