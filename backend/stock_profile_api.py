"""
Stock profile API routes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from stock_profile_service import StockProfileService


def create_stock_profile_router(service: StockProfileService) -> APIRouter:
    router = APIRouter(prefix="/api/stock", tags=["stock-profile"])

    @router.get("/{symbol}/overview")
    async def stock_overview(symbol: str):
        try:
            return await service.get_overview(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/key-stats")
    async def stock_key_stats(symbol: str):
        try:
            return await service.get_key_stats(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/financials")
    async def stock_financials(symbol: str):
        try:
            return await service.get_financials(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/analyst")
    async def stock_analyst(symbol: str):
        try:
            return await service.get_analyst(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/ownership")
    async def stock_ownership(symbol: str):
        try:
            return await service.get_ownership(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/events")
    async def stock_events(symbol: str):
        try:
            return await service.get_events(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/narrative")
    async def stock_narrative(symbol: str):
        try:
            return await service.get_narrative(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/financial-statements")
    async def stock_financial_statements(symbol: str):
        try:
            return await service.get_financial_statements(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/analyst-detail")
    async def stock_analyst_detail(symbol: str):
        try:
            return await service.get_analyst_detail(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/rating-scorecard")
    async def stock_rating_scorecard(symbol: str):
        try:
            return await service.get_rating_scorecard(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/company-info")
    async def stock_company_info(symbol: str):
        try:
            return await service.get_company_info(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/stock-splits")
    async def stock_splits(symbol: str):
        try:
            return await service.get_stock_splits(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/earnings-detail")
    async def stock_earnings_detail(symbol: str):
        try:
            return await service.get_earnings_detail(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/{symbol}/profile")
    async def stock_full_profile(symbol: str):
        """Batch endpoint — all modules at once."""
        try:
            return await service.get_all(symbol)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    return router
