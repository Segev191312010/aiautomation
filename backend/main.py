"""Canonical FastAPI entry point for Autotrader V1."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from middleware import RateLimitMiddleware, RequestLogMiddleware, SecurityHeadersMiddleware
from routers.auth import router as auth_router
from routers.autotrader_routes import router as autotrader_router
from routers.live_manager_routes import router as live_manager_router
from routers.live_routes import router as live_router
from startup import validate_startup
from trading.operations.schema_gate import require_current_schema


@asynccontextmanager
async def lifespan(_: FastAPI):
    await validate_startup()
    # The API never migrates: only `autotraderctl db migrate` changes the
    # schema (lock, verified backup, outside the trading day). A pending
    # migration stops startup with the named token instead.
    require_current_schema(process="api")
    yield


app = FastAPI(title="Autotrader V1", version="0.1.0", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLogMiddleware)
app.add_middleware(RateLimitMiddleware)
app.include_router(auth_router)
app.include_router(autotrader_router)
app.include_router(live_router)
app.include_router(live_manager_router)


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "service": "autotrader-v1",
        "status": "ok",
        "execution_enabled": False,
        "enabled_modes": ["backtest", "shadow"],
    }
