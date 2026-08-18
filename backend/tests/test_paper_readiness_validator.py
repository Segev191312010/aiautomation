from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "validate_paper_readiness.py"
SPEC = importlib.util.spec_from_file_location("paper_readiness", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def paper_env() -> dict[str, str]:
    return {
        "AUTOPILOT_MODE": "PAPER", "IS_PAPER": "true", "SIM_MODE": "false",
        "IBKR_PORT": "7497", "CLAUDE_WORKER_ENABLED": "true",
        "TV_WEBHOOK_SECRET": "secret-is-not-printed", "TV_IP_STRICT": "true",
        "TV_ALLOWED_IPS": "192.0.2.1", "METRICS_EXPOSURE_PROFILE": "isolated",
    }


def test_paper_environment_passes_without_network_access():
    assert validator.check_paper_environment(paper_env()) == []


def test_live_mode_and_simulation_fail_closed():
    env = paper_env() | {"AUTOPILOT_MODE": "LIVE", "SIM_MODE": "true", "IBKR_PORT": "7496"}
    failures = validator.check_paper_environment(env)
    assert "AUTOPILOT_MODE must be PAPER" in failures
    assert "SIM_MODE must be false for the real IBKR PAPER drill" in failures
    assert any("IBKR_PORT" in failure for failure in failures)


def test_bundle_requires_operator_artifacts(tmp_path: Path):
    failures = validator.check_bundle(tmp_path)
    assert len(failures) == len(validator.REQUIRED_BUNDLE_FILES)


def test_env_loader_does_not_require_export_prefix(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nexport AUTOPILOT_MODE=PAPER\nIS_PAPER='true'\n", encoding="utf-8")
    assert validator.load_env_file(env_file) == {"AUTOPILOT_MODE": "PAPER", "IS_PAPER": "true"}
