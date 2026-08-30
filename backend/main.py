"""Canonical FastAPI entry point for Autotrader V1."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from db.core import init_db
from middleware import RateLimitMiddleware, RequestLogMiddleware, SecurityHeadersMiddleware
from routers.auth import router as auth_router
from routers.autotrader_routes import router as autotrader_router
from startup import validate_startup


@asynccontextmanager
async def lifespan(_: FastAPI):
    await validate_startup()
    await init_db()
    yield


app = FastAPI(title="Autotrader V1", version="0.1.0", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLogMiddleware)
app.add_middleware(RateLimitMiddleware)
app.include_router(auth_router)
app.include_router(autotrader_router)


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "service": "autotrader-v1",
        "status": "ok",
        "execution_enabled": False,
        "enabled_modes": ["backtest", "shadow"],
    }
