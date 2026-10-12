"""Local TOML settings: separate data/order client IDs, strict keys, private file."""
from __future__ import annotations

from pathlib import Path

import pytest

from trading.operations.settings import ShadowSettingsError, load_shadow_runtime_config

_VALID = """\
[shadow]
paper_account_id = "DU1234567"
data_client_id = 17
order_client_id = 18
model_id = "claude-opus-5-5"
prompt_id = "orb-review-v1"
calendar_id = "XNYS"
calendar_version = "2026.1"
commission_policy_id = "ibkr-fixed"
commission_policy_version = 1
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_valid_settings_load_with_distinct_client_ids(tmp_path):
    config = load_shadow_runtime_config(_write(tmp_path, _VALID))
    assert (config.data_client_id, config.order_client_id) == (17, 18)
    assert config.tws_port == 7497


@pytest.mark.parametrize(
    "text",
    [
        _VALID.replace("order_client_id = 18", "order_client_id = 17"),
        _VALID.replace("order_client_id = 18\n", ""),
        _VALID.replace("data_client_id = 17", "client_id = 17"),
    ],
    ids=["colliding", "missing_order_id", "legacy_single_id"],
)
def test_colliding_missing_or_legacy_client_ids_are_rejected(tmp_path, text):
    with pytest.raises(ShadowSettingsError):
        load_shadow_runtime_config(_write(tmp_path, text))
