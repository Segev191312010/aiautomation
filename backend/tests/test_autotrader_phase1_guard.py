from __future__ import annotations

import inspect

import pytest

from trading.authority import EngineAuthority, EngineAuthorityError
from trading.contracts import EngineMode
import trading.engine as engine_module
import trading.workflow as workflow_module


@pytest.mark.parametrize(
    "name",
    [
        "AUTOTRADER_MODE",
        "AUTOPILOT_MODE",
        "LIVE_ALLOWED",
        "AI_AUTONOMY_ENABLED",
        "AI_SHADOW_MODE",
        "CLAUDE_LIVE_TRADING_ENABLED",
        "ENABLE_AUTO_TRADING",
        "AUTO_EXECUTION_ENABLED",
    ],
)
def test_environment_cannot_enable_paper_authority(monkeypatch, name):
    monkeypatch.setenv(name, "LIVE")
    authority = EngineAuthority()

    with pytest.raises(EngineAuthorityError):
        authority.require_mode(EngineMode.PAPER)


def test_live_is_not_a_runtime_mode_or_configurable_authority():
    assert "live" not in {mode.value for mode in EngineMode}
    with pytest.raises(TypeError):
        EngineAuthority(enabled_modes={EngineMode.PAPER})


def test_engine_does_not_accept_an_injected_authority():
    with pytest.raises(TypeError):
        engine_module.AutotraderEngine(authority=EngineAuthority())


def test_phase1_engine_and_workflow_do_not_import_broker_execution_paths():
    source = inspect.getsource(engine_module) + inspect.getsource(workflow_module)

    assert "order_executor" not in source
    assert "ibkr_client" not in source
    assert "submit_bracket" not in source
