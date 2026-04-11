"""
Configuration — loaded from .env file or environment variables.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── IBKR connection ──────────────────────────────────────────────────────
    # Port guide: TWS live=7496, TWS paper=7497, Gateway live=4001, Gateway paper=4002
    IBKR_HOST: str = os.getenv("IBKR_HOST", "127.0.0.1")
    IBKR_PORT: int = int(os.getenv("IBKR_PORT", "7497"))
    IBKR_CLIENT_ID: int = int(os.getenv("IBKR_CLIENT_ID", "1"))

    # ── Safety ───────────────────────────────────────────────────────────────
    # IS_PAPER=true → connects to IBKR paper account (real API, fake money)
    IS_PAPER: bool = os.getenv("IS_PAPER", "true").lower() == "true"

    # ── Simulation mode ──────────────────────────────────────────────────────
    # SIM_MODE=true → orders go to virtual account, IBKR is NOT called at all.
    # Use this for back-testing rules without needing IB Gateway running.
    SIM_MODE: bool = os.getenv("SIM_MODE", "false").lower() == "true"
    SIM_INITIAL_CASH: float = float(os.getenv("SIM_INITIAL_CASH", "100000.0"))
    SIM_COMMISSION: float = float(os.getenv("SIM_COMMISSION", "1.0"))  # $ per order

    # ── Auto-reconnect ───────────────────────────────────────────────────────
    # Seconds between reconnect attempts when IBKR connection drops. 0 = disabled.
    RECONNECT_INTERVAL: int = int(os.getenv("RECONNECT_INTERVAL", "30"))

    # ── Bot behaviour ────────────────────────────────────────────────────────
    BOT_INTERVAL_SECONDS: int = int(os.getenv("BOT_INTERVAL_SECONDS", "900"))  # 15 min default; fits ~1000-stock scans

    # ── Trading risk limits ───────────────────────────────────────────────────
    SHORT_ALLOWED: bool = os.getenv("SHORT_ALLOWED", "false").lower() == "true"
    RISK_PER_TRADE_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
    MAX_TOTAL_DRAWDOWN: float = float(os.getenv("MAX_TOTAL_DRAWDOWN", "0.18"))
    MAX_DAILY_RISK: float = float(os.getenv("MAX_DAILY_RISK", "0.03"))
    MAX_POSITIONS_TOTAL: int = int(os.getenv("MAX_POSITIONS_TOTAL", "100"))
    MAX_POSITIONS_PER_SECTOR: int = int(os.getenv("MAX_POSITIONS_PER_SECTOR", "3"))
    MAX_TRADES_PER_CYCLE: int = int(os.getenv("MAX_TRADES_PER_CYCLE", "50"))

    # ── AI Advisor ─────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ADVISOR_MIN_TRADES: int = int(os.getenv("ADVISOR_MIN_TRADES", "5"))
    ADVISOR_LOOKBACK_DAYS: int = int(os.getenv("ADVISOR_LOOKBACK_DAYS", "90"))

    # ── AI Autopilot Control Plane ───────────────────────────────────────────
    # AUTOPILOT_MODE controls AI authority. Separate from SIM_MODE/IS_PAPER (broker env).
    # OFF   = AI does nothing (manual trading only)
    # PAPER = AI creates draft/paper rules, logs decisions, no live orders
    # LIVE  = AI manages rules AND places orders with real money
    AUTOPILOT_MODE: str = os.getenv("AUTOPILOT_MODE", "OFF").upper()
    # Backward compat — derived from AUTOPILOT_MODE
    _apm = os.getenv("AUTOPILOT_MODE", "OFF").upper()
    AI_AUTONOMY_ENABLED: bool = _apm in ("PAPER", "LIVE")
    AI_SHADOW_MODE: bool = _apm == "OFF"
    AI_OPTIMIZE_INTERVAL_SECONDS: int = int(os.getenv("AI_OPTIMIZE_INTERVAL_SECONDS", "3600"))  # 1h default
    AI_MODEL_OPTIMIZER: str = os.getenv("AI_MODEL_OPTIMIZER", "claude-sonnet-4-20250514")
    AI_MODEL_NARRATIVE: str = os.getenv("AI_MODEL_NARRATIVE", "claude-sonnet-4-20250514")
    AI_MODEL_REGIME: str = os.getenv("AI_MODEL_REGIME", "claude-sonnet-4-20250514")
    AI_MODEL_PORTFOLIO: str = os.getenv("AI_MODEL_PORTFOLIO", "claude-sonnet-4-20250514")
    AI_MODEL_FALLBACK: str = os.getenv("AI_MODEL_FALLBACK", "claude-haiku-4-5-20251001")

    # ── Circuit breaker / AI resilience ─────────────────────────────────────
    AI_CONSECUTIVE_FAILURE_THRESHOLD: int = int(os.getenv("AI_CONSECUTIVE_FAILURE_THRESHOLD", "3"))
    AI_FALLBACK_ENABLED: bool = os.getenv("AI_FALLBACK_ENABLED", "true").lower() == "true"

    # ── Direct AI candidate queue ───────────────────────────────────────────
    # TTL for persisted AI direct candidates before they are treated as stale.
    AI_DIRECT_CANDIDATE_TTL_SECONDS: int = int(os.getenv("AI_DIRECT_CANDIDATE_TTL_SECONDS", "900"))

    # ── Bull/Bear debate telemetry ──────────────────────────────────────────
    # Number of JSON parse failures within a 24h window before emitting a
    # MetricEvent so the operator can notice silent degradation.
    AI_DEBATE_FAILURE_THRESHOLD: int = int(os.getenv("AI_DEBATE_FAILURE_THRESHOLD", "5"))

    # ── Shadow → Live gating ────────────────────────────────────────────────
    SHADOW_TO_LIVE_MIN_DECISIONS: int = int(os.getenv("SHADOW_TO_LIVE_MIN_DECISIONS", "100"))
    SHADOW_TO_LIVE_MIN_DAYS: int = int(os.getenv("SHADOW_TO_LIVE_MIN_DAYS", "15"))
    SHADOW_TO_LIVE_HIT_RATE: float = float(os.getenv("SHADOW_TO_LIVE_HIT_RATE", "0.55"))
    SHADOW_TO_LIVE_EFFECT_SIZE: float = float(os.getenv("SHADOW_TO_LIVE_EFFECT_SIZE", "0.0"))

    # ── Auto-tighten thresholds ─────────────────────────────────────────────
    AUTO_TIGHTEN_7D_HIT_RATE: float = float(os.getenv("AUTO_TIGHTEN_7D_HIT_RATE", "0.45"))
    AUTO_TIGHTEN_7D_MIN_DECISIONS: int = int(os.getenv("AUTO_TIGHTEN_7D_MIN_DECISIONS", "40"))
    AUTO_TIGHTEN_30D_HIT_RATE: float = float(os.getenv("AUTO_TIGHTEN_30D_HIT_RATE", "0.50"))
    AUTO_TIGHTEN_30D_MIN_DECISIONS: int = int(os.getenv("AUTO_TIGHTEN_30D_MIN_DECISIONS", "100"))

    # ── Trade windows for shadow/learning evaluation ────────────────────────
    SHADOW_MIN_TRADES_PER_WINDOW: int = int(os.getenv("SHADOW_MIN_TRADES_PER_WINDOW", "20"))
    SHADOW_TRADE_WINDOW_SIZE: int = int(os.getenv("SHADOW_TRADE_WINDOW_SIZE", "50"))
    LEARNING_MIN_TRADES_PER_WINDOW: int = int(os.getenv("LEARNING_MIN_TRADES_PER_WINDOW", "20"))

    # ── Alert engine ──────────────────────────────────────────────────────────
    ALERT_CHECK_INTERVAL_SECONDS: int = int(os.getenv("ALERT_CHECK_INTERVAL_SECONDS", "30"))

    # ── Exit logic (position tracker) ────────────────────────────────────────
    # Hard stop: entry_price - ATR_STOP_MULT × ATR(14) — never moves after entry
    ATR_STOP_MULT: float = float(os.getenv("ATR_STOP_MULT", "3.0"))
    # Trailing stop: high_watermark - ATR_TRAIL_MULT × ATR(14)_current
    ATR_TRAIL_MULT: float = float(os.getenv("ATR_TRAIL_MULT", "2.0"))

    # ── Position sizing ───────────────────────────────────────────────────────
    # Each trade = POSITION_SIZE_PCT × NetLiquidation / price
    POSITION_SIZE_PCT: float = float(os.getenv("POSITION_SIZE_PCT", "0.005"))  # 0.5%

    # ── Database ─────────────────────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "trading_bot.db")
    WS_PUSH_INTERVAL_SECONDS: float = float(os.getenv("WS_PUSH_INTERVAL_SECONDS", "0.5"))
    WS_CACHE_TTL_SECONDS: float = float(os.getenv("WS_CACHE_TTL_SECONDS", "0.5"))
    WS_STALE_WARN_SECONDS: int = int(os.getenv("WS_STALE_WARN_SECONDS", "10"))
    WS_STALE_CRITICAL_SECONDS: int = int(os.getenv("WS_STALE_CRITICAL_SECONDS", "30"))
    ENABLE_MARKET_DIAGNOSTICS: bool = os.getenv("ENABLE_MARKET_DIAGNOSTICS", "false").lower() == "true"

    # ── Phase 2 hardening feature flags ─────────────────────────────────────
    # Default to true when AUTOPILOT_MODE=LIVE for safety
    ENABLE_PORTFOLIO_CONCENTRATION_ENFORCEMENT: bool = os.getenv(
        "ENABLE_PORTFOLIO_CONCENTRATION_ENFORCEMENT",
        "true" if os.getenv("AUTOPILOT_MODE", "OFF").upper() == "LIVE" else "false",
    ).lower() == "true"
    ENABLE_RULE_BACKTEST_GATE: bool = os.getenv(
        "ENABLE_RULE_BACKTEST_GATE",
        "true" if os.getenv("AUTOPILOT_MODE", "OFF").upper() == "LIVE" else "false",
    ).lower() == "true"
    ENABLE_BOT_HEALTH_MONITORING: bool = os.getenv("ENABLE_BOT_HEALTH_MONITORING", "false").lower() == "true"
    ENABLE_ENHANCED_REGIME: bool = os.getenv("ENABLE_ENHANCED_REGIME", "false").lower() == "true"
    DIAG_INTRADAY_INTERVAL_SECONDS: int = int(os.getenv("DIAG_INTRADAY_INTERVAL_SECONDS", "300"))
    DIAG_LOCK_TTL_SECONDS: int = int(os.getenv("DIAG_LOCK_TTL_SECONDS", "600"))
    DIAG_NEWS_HOURS_DEFAULT: int = int(os.getenv("DIAG_NEWS_HOURS_DEFAULT", "24"))
    DIAG_NEWS_LIMIT_DEFAULT: int = int(os.getenv("DIAG_NEWS_LIMIT_DEFAULT", "200"))
    DIAG_MARKET_MAP_DAYS_DEFAULT: int = int(os.getenv("DIAG_MARKET_MAP_DAYS_DEFAULT", "5"))
    DIAG_SCHEDULER_TIMEZONE: str = os.getenv("DIAG_SCHEDULER_TIMEZONE", "America/New_York")

    # ── API server ───────────────────────────────────────────────────────────
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # ── JWT / Auth ─────────────────────────────────────────────────────────
    JWT_SECRET: str = os.getenv("JWT_SECRET", "trading-dev-secret-MUST-SET-IN-ENV")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "1440"))
    # Bootstrap secret for /api/auth/token — must be set before any remote exposure
    JWT_BOOTSTRAP_SECRET: str = os.getenv("JWT_BOOTSTRAP_SECRET", "")

    # ── Strict config validation ──────────────────────────────────────────────
    STRICT_CONFIG: bool = os.getenv("STRICT_CONFIG", "true").lower() == "true"

    # ── React dashboard build directory ──────────────────────────────────────
    # After `npm run build` in dashboard/, serve the SPA from /app
    DASHBOARD_BUILD_DIR: str = os.getenv("DASHBOARD_BUILD_DIR", "../dashboard/dist")

    # ── Logging ──────────────────────────────────────────────────────────────
    # LOG_LEVEL: DEBUG | INFO | WARNING | ERROR  (default: INFO)
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    # LOG_FILE: optional path for a rotating file handler (empty = stdout only)
    LOG_FILE: str = os.getenv("LOG_FILE", "")

    # ── Application version ───────────────────────────────────────────────────
    APP_VERSION: str = os.getenv("APP_VERSION", "2.0.0")


cfg = Config()


def _validate_config(c: Config) -> None:
    """Validate config consistency at startup."""
    # AUTOPILOT_MODE must be a known value
    valid_modes = {"OFF", "PAPER", "LIVE"}
    if c.AUTOPILOT_MODE not in valid_modes:
        raise ValueError(
            f"AUTOPILOT_MODE='{c.AUTOPILOT_MODE}' is invalid. Must be one of: {valid_modes}"
        )

    # JWT_SECRET must not be the dev placeholder in production
    if c.AUTOPILOT_MODE == "LIVE" and "MUST-SET" in c.JWT_SECRET:
        import warnings
        warnings.warn("JWT_SECRET is using dev default — set it in .env for production", stacklevel=2)

    # Port guide: 7496=TWS live, 7497=TWS paper, 4001=GW live, 4002=GW paper
    paper_ports = {7497, 4002}
    live_ports = {7496, 4001}
    if c.STRICT_CONFIG:
        if c.IS_PAPER and c.IBKR_PORT in live_ports:
            raise ValueError(f"IS_PAPER=true cannot use live port {c.IBKR_PORT}")
        if not c.IS_PAPER and c.IBKR_PORT in paper_ports:
            raise ValueError(f"IS_PAPER=false cannot use paper port {c.IBKR_PORT}")
    else:
        import warnings
        if c.IS_PAPER and c.IBKR_PORT in live_ports:
            warnings.warn(f"IS_PAPER=true but port={c.IBKR_PORT} (live)", stacklevel=2)
        if not c.IS_PAPER and c.IBKR_PORT in paper_ports:
            warnings.warn(f"IS_PAPER=false but port={c.IBKR_PORT} (paper)", stacklevel=2)


_validate_config(cfg)
