"""Database package — re-exports all functions for backward compatibility."""
from db.core import DB_PATH, get_db, init_db, transaction  # noqa: F401
from db.rules import (  # noqa: F401
    get_rules, get_rule, save_rule, delete_rule,
    save_rule_version, get_rule_versions, persist_rule_revision,
)
from db.trades import (  # noqa: F401
    save_trade, get_trades, get_trade, get_trade_by_order_id,
    update_trade_status, finalize_trade_outcome,
)
from db.validation import (  # noqa: F401
    save_rule_validation_run, get_rule_validation_runs,
    open_manual_intervention, get_manual_interventions,
    acknowledge_manual_intervention, resolve_manual_intervention,
    _seed_starter_rules,
)
from db.screener import (  # noqa: F401
    get_screener_presets, save_screener_preset, delete_screener_preset,
    _BUILT_IN_PRESETS, _seed_screener_presets,
)
from db.backtests import (  # noqa: F401
    save_backtest, get_backtests, get_backtest, delete_backtest,
)
from db.alerts import (  # noqa: F401
    get_alerts, get_enabled_alerts_all, get_alert, save_alert, delete_alert,
    get_alert_history, save_alert_history,
)
from db.positions import (  # noqa: F401
    save_open_position, get_open_positions, get_open_position, delete_open_position,
)
from db.direct_candidates import (  # noqa: F401
    queue_candidate, drain_candidates, mark_candidate_status,
    purge_expired_candidates, get_candidate_status,
)
