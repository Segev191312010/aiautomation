import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "paper_lifecycle_simulator.py"
SPEC = importlib.util.spec_from_file_location("paper_lifecycle_simulator", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_all_offline_paper_lifecycle_scenarios_pass_and_never_authorize_live():
    result = module.run()
    assert result["passed"] is True
    assert result["offline_only"] is True
    assert result["live_authorized"] is False
    assert all(scenario["passed"] for scenario in result["scenarios"])


def test_partial_fill_tracks_cumulative_quantity():
    result = module.scenario_partial_fill()
    assert result["cumulative_filled"] == 10
    assert result["broker_orders"]["SIM-001"]["remaining"] == 0


def test_disconnect_rejects_mutation_until_reconnect():
    result = module.scenario_disconnect_reconnect()
    assert result["rejected_offline"] if "rejected_offline" in result else result["rejected_while_offline"]


def test_reconciliation_mismatch_fails_closed():
    result = module.scenario_reconciliation_mismatch()
    assert result["passed"] is True
    assert result["readiness"] is False
    assert result["live_authorized"] is False
