"""Read-only preparation diagnostics; never grant execution authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import socket
from collections.abc import Callable

from trading.operations.paths import PathSecurityError
from trading.operations.settings import ShadowSettingsError, load_shadow_runtime_config
from trading.market.tws_actor import (
    TWSActorConfig,
    TWSActorError,
    TWSPaperActor,
    documented_line_allocation,
)


def _paper_actor(config: TWSActorConfig) -> TWSPaperActor:
    return TWSPaperActor(config, capacity_checker=documented_line_allocation)


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    status: str
    detail: str


def check_local_preparation() -> dict[str, object]:
    checks: list[ReadinessCheck] = []
    try:
        config = load_shadow_runtime_config()
        if config.client_id <= 0:
            raise ShadowSettingsError("dedicated client ID required")
    except (ShadowSettingsError, PathSecurityError, OSError):
        checks.append(ReadinessCheck(
            "configuration", "blocked",
            "Complete ~/.config/autotrader-v1/config.toml with verified settings; values are not displayed.",
        ))
    else:
        checks.append(ReadinessCheck(
            "configuration", "passed",
            "Configuration format is valid; account, policies and model are not yet verified.",
        ))
    try:
        with socket.create_connection(("127.0.0.1", 7497), timeout=2):
            pass
    except OSError:
        checks.append(ReadinessCheck("tws_socket", "blocked", "Local port 7497 is unreachable."))
    else:
        checks.append(ReadinessCheck(
            "tws_socket", "passed", "TCP reachable only; this does not verify the TWS API or PAPER account.",
        ))
    for name in (
        "paper_account", "client_identity", "realtime_entitlements", "reconciliation",
        "ai_provider", "commission_policy", "session_service", "execution_review",
    ):
        checks.append(ReadinessCheck(name, "unverified", "Separate verification required."))
    return {
        "kind": "local_preparation_only",
        "ready": False,
        "orders_enabled": False,
        "checks": [asdict(check) for check in checks],
    }


async def check_shadow_preflight(
    *, actor_factory: Callable[[TWSActorConfig], TWSPaperActor] = _paper_actor
) -> dict[str, object]:
    """Perform a read-only TWS handshake without requesting orders or market data."""

    local = check_local_preparation()
    checks = {
        str(item["name"]): ReadinessCheck(
            name=str(item["name"]), status=str(item["status"]), detail=str(item["detail"])
        )
        for item in local["checks"]
    }
    try:
        config = load_shadow_runtime_config()
    except (ShadowSettingsError, PathSecurityError, OSError):
        return local

    actor = actor_factory(
        TWSActorConfig(account=config.paper_account_id, client_id=config.client_id)
    )
    try:
        await actor.connect()
        report = await actor.preflight(required_data_lines=config.max_candidates)
    except TWSActorError:
        checks["paper_account"] = ReadinessCheck(
            "paper_account", "blocked", "TWS handshake or configured PAPER account verification failed."
        )
        checks["client_identity"] = ReadinessCheck(
            "client_identity", "blocked", "Dedicated TWS client identity was not verified."
        )
        checks["scanner_capability"] = ReadinessCheck(
            "scanner_capability", "blocked", "Frozen IBKR scanner capability was not verified."
        )
    else:
        checks["tws_socket"] = ReadinessCheck(
            "tws_socket", "passed", "Official IBKR API handshake completed on loopback port 7497."
        )
        checks["paper_account"] = ReadinessCheck(
            "paper_account",
            "passed" if report.account_verified else "blocked",
            "Configured PAPER account was returned by TWS."
            if report.account_verified
            else "Configured PAPER account was not returned by TWS.",
        )
        checks["client_identity"] = ReadinessCheck(
            "client_identity", "passed", "Dedicated positive TWS client ID completed the handshake."
        )
        checks["scanner_capability"] = ReadinessCheck(
            "scanner_capability",
            "passed" if report.scanner_supported else "blocked",
            "Frozen scanner request is supported by the connected API client."
            if report.scanner_supported
            else "Frozen scanner request is unavailable.",
        )
        checks["realtime_entitlements"] = ReadinessCheck(
            "realtime_entitlements",
            "unverified",
            "Handshake does not prove real-time quotes; a market-data probe is still required.",
        )
        checks["data_line_capacity"] = ReadinessCheck(
            "data_line_capacity",
            "passed" if report.data_line_capacity.passed else "unverified",
            "IBKR's documented baseline line allocation covers sequential quotes."
            if report.data_line_capacity.passed
            else "Market-data line capacity does not cover the candidate count.",
        )
    finally:
        await actor.disconnect()

    ordered = list(checks.values())
    return {
        "kind": "broker_handshake_preflight",
        "ready": False,
        "orders_enabled": False,
        "checks": [asdict(check) for check in ordered],
    }
