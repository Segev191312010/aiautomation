"""The live strategy registry as research sees it, without the TWS and Claude stack.

``trading.operations.shadow_service.STRATEGIES`` is the registry; importing it
would load the broker and model clients, which research never needs. These
literal mirrors are pinned to it by a test: the same keys, the same evaluator
factories (the very same objects) and the catalogue names their specs carry.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from trading.research.replay import REGISTERED_STRATEGIES, RULES_VERSIONS
from trading.rules.all_session import gap_go_v2_strategy, orb_v2_strategy, vwap_pb_v2_strategy
from trading.rules.gap_go import GapGoStrategy
from trading.rules.momo import MomoStrategy
from trading.rules.strategy import ORBStrategy, StrategyEvaluator, entry_window_utc
from trading.rules.vwap_pb import VwapPbStrategy
from trading.scheduler.sessions import market_session

UTC = timezone.utc
EVALUATOR_FACTORIES: dict[str, Callable[[], StrategyEvaluator]] = {
    "orb": ORBStrategy,
    "momo": MomoStrategy,
    "vwap": VwapPbStrategy,
    "gap": GapGoStrategy,
    "orb-v2": orb_v2_strategy,
    "vwap-v2": vwap_pb_v2_strategy,
    "gap-v2": gap_go_v2_strategy,
}
# Catalogue name (what the ledger stores) -> registry key.
REGISTRY_KEYS: dict[str, str] = {RULES_VERSIONS[key]: key for key in REGISTERED_STRATEGIES}
# A strategy research does not know (e.g. a later managed identity) is given
# the order sender's own last entry: 30 minutes before the close.
LAST_ENTRY_BEFORE_CLOSE = timedelta(minutes=30)


def catalogue_name(registry_key: str) -> str:
    return RULES_VERSIONS[registry_key]


def evaluator_for(strategy_name: str) -> StrategyEvaluator | None:
    """The registered evaluator behind a catalogue name; ``None`` when unregistered."""
    key = REGISTRY_KEYS.get(strategy_name.strip())
    return None if key is None else EVALUATOR_FACTORIES[key]()


def entry_cutoff(strategy_name: str, session_date: date) -> tuple[datetime, str]:
    """When an unfilled entry of ``strategy_name`` stops on ``session_date``, and why.

    The registered strategy's own cutoff; otherwise the order sender's last
    entry (close - 30 minutes).
    """
    evaluator = evaluator_for(strategy_name)
    if evaluator is not None:
        return entry_window_utc(evaluator, session_date)[1], "strategy_cutoff"
    session = market_session(session_date)
    if session is None:
        raise ValueError(f"{session_date.isoformat()} is not an XNYS session")
    return (session.closes_at - LAST_ENTRY_BEFORE_CLOSE).astimezone(UTC), "session_last_entry"


__all__ = [
    "EVALUATOR_FACTORIES",
    "LAST_ENTRY_BEFORE_CLOSE",
    "REGISTRY_KEYS",
    "catalogue_name",
    "entry_cutoff",
    "evaluator_for",
]
