"""Shared runtime state — explicit API for cross-module globals.

Populated during lifespan startup, cleared during shutdown.
Routers call get_*() functions. Lifespan calls initialize/reset.
"""
from __future__ import annotations

from typing import Any

_state: dict[str, Any] = {}


def initialize_runtime_state(
    *,
    ws_manager: Any = None,
    data_health: Any = None,
    diag_service: Any = None,
) -> None:
    """Called once during lifespan startup."""
    _state["ws_manager"] = ws_manager
    _state["data_health"] = data_health
    _state["diag_service"] = diag_service


def reset_runtime_state() -> None:
    """Called during shutdown and in tests."""
    _state.clear()


def get_ws_manager() -> Any:
    return _state.get("ws_manager")


def get_data_health() -> Any:
    return _state.get("data_health")


def get_diag_service() -> Any:
    return _state.get("diag_service")
