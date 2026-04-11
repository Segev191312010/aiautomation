"""
Diagnostics API routes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from auth import get_current_user
from diagnostics_service import DiagnosticsService


def create_diagnostics_router(service: DiagnosticsService) -> APIRouter:
    router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"], dependencies=[Depends(get_current_user)])

    def _require_enabled() -> None:
        if not service.enabled:
            raise HTTPException(status_code=404, detail="Market diagnostics disabled")

    @router.get("/overview")
    async def diagnostics_overview(lookback_days: int = Query(90, ge=90, le=365)):
        _require_enabled()
        return await service.get_overview(lookback_days=lookback_days)

    @router.get("/indicators")
    async def diagnostics_indicators():
        _require_enabled()
        return await service.list_indicators()

    @router.get("/indicators/{code}")
    async def diagnostics_indicator(code: str):
        _require_enabled()
        indicator = await service.get_indicator(code)
        if indicator is None:
            raise HTTPException(status_code=404, detail=f"Unknown indicator: {code}")
        return indicator

    @router.get("/indicators/{code}/history")
    async def diagnostics_indicator_history(code: str, days: int = Query(365, ge=1, le=3650)):
        _require_enabled()
        return await service.get_indicator_history(code, days=days)

    @router.get("/market-map")
    async def diagnostics_market_map(days: int = Query(5, ge=1, le=30)):
        _require_enabled()
        return await service.get_market_map(days=days)

    @router.get("/sector-projections/latest")
    async def diagnostics_sector_projections_latest(
        lookback_days: int = Query(90, ge=90, le=365),
    ):
        _require_enabled()
        data = await service.get_sector_projections_latest(lookback_days=lookback_days)
        if data is None:
            raise HTTPException(status_code=404, detail="No sector projections yet")
        return data

    @router.get("/sector-projections/history")
    async def diagnostics_sector_projections_history(days: int = Query(365, ge=1, le=3650)):
        _require_enabled()
        return await service.get_sector_projections_history(days=days)

    @router.get("/news")
    async def diagnostics_news(
        hours: int = Query(24, ge=1, le=168),
        limit: int = Query(200, ge=1, le=200),
    ):
        _require_enabled()
        return await service.get_news(hours=hours, limit=limit)

    @router.post("/refresh")
    async def diagnostics_refresh():
        _require_enabled()
        result = await service.trigger_refresh(lock_holder="manual", wait=False)
        status = result.get("status")
        if status == "conflict":
            return JSONResponse(
                status_code=409,
                content={
                    "run_id": result.get("run_id"),
                    "locked_by": result.get("locked_by"),
                    "lock_expires_at": result.get("lock_expires_at"),
                },
            )
        if status == "accepted":
            return JSONResponse(
                status_code=202,
                content={"run_id": result.get("run_id"), "status": "running"},
            )
        raise HTTPException(status_code=503, detail="Refresh unavailable")

    @router.get("/refresh/{run_id}")
    async def diagnostics_refresh_run(run_id: int):
        _require_enabled()
        run = await service.get_refresh_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return run

    return router
