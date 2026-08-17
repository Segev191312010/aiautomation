"""Architecture tripwires for the pre-gateway execution surface.

These tests do not bless the current direct broker calls. They freeze the
known bypass inventory so a new one cannot appear unnoticed while ADR 0006 is
implemented. The allowlist must shrink to the private adapter, not grow.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BROKER_MUTATIONS = {"placeOrder", "cancelOrder"}

# Known debt from the Stage 9A audit. Counts matter because emergency flatten
# has two submit branches in one function.
EXPECTED_MUTATION_SITES = Counter(
    {
        ("order_executor.py", "place_order", "placeOrder"): 1,
        ("order_executor.py", "cancel_order", "cancelOrder"): 1,
        ("order_executor.py", "_convert_mkt_orders_to_limit", "cancelOrder"): 1,
        ("order_executor.py", "_convert_mkt_orders_to_limit", "placeOrder"): 1,
        ("safety_kernel.py", "_emergency_close_all_positions", "placeOrder"): 2,
    }
)


class _MutationVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.function_stack: list[str] = []
        self.sites: list[tuple[str, str, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in BROKER_MUTATIONS:
            function = self.function_stack[-1] if self.function_stack else "<module>"
            self.sites.append((self.relative_path, function, node.func.attr))
        self.generic_visit(node)


def _broker_mutation_sites() -> Counter[tuple[str, str, str]]:
    sites: list[tuple[str, str, str]] = []
    for path in BACKEND_ROOT.rglob("*.py"):
        relative_parts = path.relative_to(BACKEND_ROOT).parts
        if "tests" in relative_parts or ".venv" in relative_parts or ".tmp" in relative_parts:
            continue
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        visitor = _MutationVisitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative))
        sites.extend(visitor.sites)
    return Counter(sites)


def test_no_new_direct_broker_mutation_bypass() -> None:
    """Fail on any new submit/cancel site while the gateway is being built."""
    actual = _broker_mutation_sites()
    assert actual == EXPECTED_MUTATION_SITES, (
        "Direct broker mutation inventory changed. Do not extend the allowlist; "
        "route the new call through the ADR 0006 gateway/private adapter. "
        f"expected={EXPECTED_MUTATION_SITES}, actual={actual}"
    )

