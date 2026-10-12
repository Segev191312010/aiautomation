"""Strict non-secret TOML configuration for real-data shadow sessions."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tomllib

from pydantic import ValidationError

from trading.contracts import ShadowRuntimeConfig
from trading.operations.paths import UserPaths, ensure_private_file


_ROOT_KEYS = frozenset({"shadow"})
_MAX_CONFIG_BYTES = 64 * 1024
_SHADOW_KEYS = frozenset(
    {
        "paper_account_id",
        "client_id",
        "model_id",
        "prompt_id",
        "calendar_id",
        "calendar_version",
        "commission_policy_id",
        "commission_policy_version",
    }
)


class ShadowSettingsError(ValueError):
    """The local shadow configuration is missing, unsafe, or invalid."""


def load_shadow_runtime_config(
    path: str | Path | None = None,
    *,
    user_paths: UserPaths | None = None,
) -> ShadowRuntimeConfig:
    paths = user_paths or UserPaths.current()
    config_path = Path(path).expanduser() if path is not None else paths.config_file
    if not config_path.is_file() or config_path.is_symlink():
        raise ShadowSettingsError(
            "shadow configuration is unavailable at ~/.config/autotrader-v1/config.toml"
        )
    ensure_private_file(config_path)
    try:
        raw_config = config_path.read_bytes()
        if len(raw_config) > _MAX_CONFIG_BYTES:
            raise ShadowSettingsError("shadow configuration exceeds the size limit")
        payload = tomllib.loads(raw_config.decode("utf-8"))
    except ShadowSettingsError:
        raise
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ShadowSettingsError("shadow configuration is not valid TOML") from exc
    if not isinstance(payload, dict) or set(payload) != _ROOT_KEYS:
        raise ShadowSettingsError("shadow configuration must contain only a [shadow] table")
    shadow = payload.get("shadow")
    if not isinstance(shadow, dict) or set(shadow) != _SHADOW_KEYS:
        raise ShadowSettingsError("shadow configuration has missing or unsupported settings")
    try:
        return ShadowRuntimeConfig.model_validate(
            {
                **shadow,
                "tws_host": "127.0.0.1",
                "tws_port": 7497,
                "max_candidates": 20,
                "max_ai_calls": 20,
                "max_ai_cost_usd": Decimal("5"),
            },
            strict=True,
        )
    except ValidationError as exc:
        raise ShadowSettingsError("shadow configuration failed contract validation") from exc


__all__ = ["ShadowSettingsError", "load_shadow_runtime_config"]
