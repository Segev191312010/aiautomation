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
    JWT_SECRET: str = os.getenv("JWT_SECRET", "trading-dev-secret-change-in-prod")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "1440"))

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
    """Validate port/paper consistency at startup."""
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
