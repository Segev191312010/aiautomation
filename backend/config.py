"""
Configuration — loaded from .env file or environment variables.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── IBKR connection ──────────────────────────────────────────────────────
    # Port guide: TWS live=7496, TWS paper=7497, IB Gateway live=4001, paper=4002
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

    # ── Mock mode ────────────────────────────────────────────────────────────
    # MOCK_MODE=true → generate realistic GBM price data when IBKR is offline.
    # Automatically activates for endpoints that need market data but lack IBKR.
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "true").lower() == "true"

    # ── Auto-reconnect ───────────────────────────────────────────────────────
    # Seconds between reconnect attempts when IBKR connection drops. 0 = disabled.
    RECONNECT_INTERVAL: int = int(os.getenv("RECONNECT_INTERVAL", "30"))

    # ── Bot behaviour ────────────────────────────────────────────────────────
    BOT_INTERVAL_SECONDS: int = int(os.getenv("BOT_INTERVAL_SECONDS", "60"))

    # ── Database ─────────────────────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "trading_bot.db")

    # ── API server ───────────────────────────────────────────────────────────
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # ── React dashboard build directory ──────────────────────────────────────
    # After `npm run build` in dashboard/, serve the SPA from /app
    DASHBOARD_BUILD_DIR: str = os.getenv("DASHBOARD_BUILD_DIR", "../dashboard/dist")


cfg = Config()
