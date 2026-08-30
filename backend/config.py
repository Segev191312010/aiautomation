"""Minimal environment configuration for the Autotrader V1 rebuild."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_DEV_JWT_SECRET = "autotrader-v1-development-secret"
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent / "trading_bot.db")


class Config:
    APP_ENV: str = os.getenv("APP_ENV", "development").strip().lower()
    APP_VERSION: str = os.getenv("APP_VERSION", "0.1.0-rebuild")
    DB_PATH: str = os.getenv("DB_PATH", DEFAULT_DB_PATH)
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    STRICT_CONFIG: bool = os.getenv("STRICT_CONFIG", "true").lower() == "true"

    JWT_SECRET: str = os.getenv("JWT_SECRET", DEFAULT_DEV_JWT_SECRET)
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "480"))
    JWT_BOOTSTRAP_SECRET: str = os.getenv("JWT_BOOTSTRAP_SECRET", "")
    JWT_BOOTSTRAP_SECRET_FILE: str = os.getenv("JWT_BOOTSTRAP_SECRET_FILE", "")


def _validate_config(config: Config) -> None:
    if not config.DB_PATH.strip():
        raise ValueError("DB_PATH must not be empty")
    if not 1 <= config.PORT <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    if config.JWT_ACCESS_EXPIRE_MINUTES <= 0:
        raise ValueError("JWT_ACCESS_EXPIRE_MINUTES must be positive")


def resolve_jwt_bootstrap_secret() -> str:
    environment_secret = cfg.JWT_BOOTSTRAP_SECRET.strip()
    secret_file = cfg.JWT_BOOTSTRAP_SECRET_FILE.strip()
    if environment_secret and secret_file:
        raise ValueError("Configure exactly one bootstrap secret source")
    if not secret_file:
        return environment_secret
    path = Path(secret_file)
    if not path.is_file():
        raise ValueError("JWT_BOOTSTRAP_SECRET_FILE must reference a readable file")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret or any(character.isspace() for character in secret):
        raise ValueError("Bootstrap secret file must contain one non-empty token")
    return secret


cfg = Config()
_validate_config(cfg)
