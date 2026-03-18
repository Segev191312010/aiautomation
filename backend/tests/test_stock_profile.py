"""
Tests for StockProfileService and the stock profile API routes.

Covers:
- get_overview() shape and computed fields
- get_key_stats() shape
- get_narrative() strengths/risks logic
- Cache hit/miss behaviour
- All API endpoints via FastAPI TestClient (service mocked)
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

MOCK_INFO = {
    "shortName": "Apple Inc.",
    "longName": "Apple Inc.",
    "exchange": "NMS",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "longBusinessSummary": "Apple designs, manufactures...",
    "fullTimeEmployees": 164000,
    "website": "https://www.apple.com",
    "currentPrice": 185.42,
    "previousClose": 183.11,
    "marketCap": 2870000000000,
    "fiftyTwoWeekHigh": 199.62,
    "fiftyTwoWeekLow": 164.08,
    "trailingPE": 30.45,
    "forwardPE": 28.12,
    "trailingEps": 6.09,
    "forwardEps": 6.59,
    "volume": 54321000,
    "averageVolume": 58123000,
    "dividendYield": 0.005,
    "beta": 1.28,
    "fiftyDayAverage": 182.50,
    "twoHundredDayAverage": 178.30,
    "totalRevenue": 383285000000,
    "revenueGrowth": 0.02,
    "netIncomeToCommon": 96995000000,
    "operatingMargins": 0.303,
    "grossMargins": 0.458,
    "profitMargins": 0.253,
    "debtToEquity": 176.3,
    "currentRatio": 0.988,
    "recommendationMean": 2.0,
    "recommendationKey": "buy",
    "targetMeanPrice": 210.50,
    "targetHighPrice": 250.00,
    "targetLowPrice": 180.00,
    "targetMedianPrice": 212.00,
    "numberOfAnalystOpinions": 42,
    "heldPercentInstitutions": 0.623,
    "heldPercentInsiders": 0.0007,
    "exDividendDate": 1699574400,
}

MOCK_RECOMMENDATION_SUMMARY = {
    "period": "0m",
    "strong_buy": 8,
    "buy": 12,
    "hold": 4,
    "sell": 1,
    "strong_sell": 0,
}

MOCK_ANALYST_PRICE_TARGETS = {
    "current": 185.42,
    "high": 250.00,
    "low": 180.00,
    "mean": 210.50,
    "median": 212.00,
}

MOCK_MAJOR_HOLDERS = {
    "held_pct_institutions": 0.6118,
    "held_pct_insiders": 0.0007,
    "total_institutional_holders": 4825,
}

MOCK_INSTITUTIONAL_HOLDERS = [
    {
        "name": "Vanguard Group, Inc.",
        "shares": 1312312312,
        "pct": 0.0832,
        "value": 242123123123.0,
        "date_reported": "2025-12-31",
    }
]

MOCK_FUND_HOLDERS = [
    {
        "name": "Vanguard Total Stock Market Index Fund",
        "shares": 401231231,
        "pct": 0.0254,
        "value": 74123123123.0,
        "date_reported": "2025-12-31",
    }
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_with_mock_service(mock_service) -> FastAPI:
    """Build a minimal FastAPI app wired to a mock StockProfileService."""
    from stock_profile_api import create_stock_profile_router
    app = FastAPI()
    app.include_router(create_stock_profile_router(mock_service))
    return app


def _clear_profile_cache():
    """Wipe the module-level cache so tests do not bleed into each other."""
    import stock_profile_service as svc
    svc._cache.clear()


# ---------------------------------------------------------------------------
# 1. StockProfileService.get_overview()
# ---------------------------------------------------------------------------

class TestGetOverview:
    @pytest.mark.asyncio
    async def test_returns_expected_keys(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            service = StockProfileService()
            result = await service.get_overview("AAPL")

        expected_keys = {
            "symbol", "name", "exchange", "sector", "industry",
            "description", "employees", "website",
            "price", "change", "change_pct", "fetched_at",
        }
        assert expected_keys.issubset(result.keys())

    @pytest.mark.asyncio
    async def test_symbol_is_uppercased(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_overview("aapl")

        assert result["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_name_uses_short_name(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_overview("AAPL")

        assert result["name"] == "Apple Inc."

    @pytest.mark.asyncio
    async def test_price_and_change_computed_correctly(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_overview("AAPL")

        # price = currentPrice = 185.42, previousClose = 183.11
        assert result["price"] == pytest.approx(185.42)
        assert result["change"] == pytest.approx(185.42 - 183.11, abs=0.01)
        expected_pct = (185.42 - 183.11) / 183.11 * 100
        assert result["change_pct"] == pytest.approx(expected_pct, abs=0.01)

    @pytest.mark.asyncio
    async def test_static_fields_pass_through(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_overview("AAPL")

        assert result["exchange"] == "NMS"
        assert result["sector"] == "Technology"
        assert result["industry"] == "Consumer Electronics"
        assert result["employees"] == 164000
        assert result["website"] == "https://www.apple.com"

    @pytest.mark.asyncio
    async def test_empty_info_returns_safe_defaults(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value={}):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_overview("UNKNOWN")

        assert result["price"] is None
        assert result["change"] is None
        assert result["change_pct"] is None
        assert result["name"] == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_falls_back_to_long_name_when_short_name_absent(self):
        _clear_profile_cache()
        info = {**MOCK_INFO, "shortName": None}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_overview("AAPL")

        assert result["name"] == "Apple Inc."


# ---------------------------------------------------------------------------
# 2. StockProfileService.get_key_stats()
# ---------------------------------------------------------------------------

class TestGetKeyStats:
    @pytest.mark.asyncio
    async def test_returns_expected_keys(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_key_stats("AAPL")

        expected_keys = {
            "market_cap", "fifty_two_week_high", "fifty_two_week_low",
            "trailing_pe", "forward_pe", "trailing_eps", "forward_eps",
            "volume", "avg_volume", "dividend_yield", "beta",
            "fifty_day_ma", "two_hundred_day_ma", "fetched_at",
        }
        assert expected_keys.issubset(result.keys())

    @pytest.mark.asyncio
    async def test_numeric_values_correct(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_key_stats("AAPL")

        assert result["market_cap"] == pytest.approx(2870000000000.0)
        assert result["fifty_two_week_high"] == pytest.approx(199.62)
        assert result["fifty_two_week_low"] == pytest.approx(164.08)
        assert result["trailing_pe"] == pytest.approx(30.45)
        assert result["forward_pe"] == pytest.approx(28.12)
        assert result["trailing_eps"] == pytest.approx(6.09)
        assert result["forward_eps"] == pytest.approx(6.59)
        assert result["beta"] == pytest.approx(1.28)
        assert result["dividend_yield"] == pytest.approx(0.005)

    @pytest.mark.asyncio
    async def test_integer_cast_for_volume(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_key_stats("AAPL")

        assert result["volume"] == 54321000
        assert result["avg_volume"] == 58123000
        assert isinstance(result["volume"], int)
        assert isinstance(result["avg_volume"], int)

    @pytest.mark.asyncio
    async def test_missing_fields_return_none(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value={}):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_key_stats("AAPL")

        assert result["market_cap"] is None
        assert result["trailing_pe"] is None
        assert result["beta"] is None


# ---------------------------------------------------------------------------
# 3. StockProfileService.get_narrative()
# ---------------------------------------------------------------------------

class TestGetNarrative:
    @pytest.mark.asyncio
    async def test_high_margins_produce_strength(self):
        """operatingMargins=0.303 (>0.20) → strength; profitMargins=0.253 (>0.15) → strength."""
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        strengths_text = " ".join(result["strengths"])
        assert "operating margins" in strengths_text.lower()
        assert "profit margins" in strengths_text.lower()

    @pytest.mark.asyncio
    async def test_high_debt_produces_risk(self):
        """debtToEquity=176.3 (>150) → risk."""
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        risks_text = " ".join(result["risks"])
        assert "debt" in risks_text.lower()

    @pytest.mark.asyncio
    async def test_strong_analyst_consensus_produces_strength(self):
        """recommendationMean=2.0 (<=2.0) → 'Strong analyst buy consensus' strength."""
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        strengths_text = " ".join(result["strengths"])
        assert "analyst" in strengths_text.lower()

    @pytest.mark.asyncio
    async def test_thin_margins_produce_risk(self):
        """operatingMargins=0.03 (<0.05) → risk."""
        _clear_profile_cache()
        info = {**MOCK_INFO, "operatingMargins": 0.03}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        risks_text = " ".join(result["risks"])
        assert "thin operating margins" in risks_text.lower()

    @pytest.mark.asyncio
    async def test_high_revenue_growth_produces_strength(self):
        """revenueGrowth=0.15 (>0.10) → strength."""
        _clear_profile_cache()
        info = {**MOCK_INFO, "revenueGrowth": 0.15}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        strengths_text = " ".join(result["strengths"])
        assert "revenue growing" in strengths_text.lower()

    @pytest.mark.asyncio
    async def test_declining_revenue_produces_risk(self):
        """revenueGrowth=-0.10 (<-0.05) → risk."""
        _clear_profile_cache()
        info = {**MOCK_INFO, "revenueGrowth": -0.10}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        risks_text = " ".join(result["risks"])
        assert "revenue declining" in risks_text.lower()

    @pytest.mark.asyncio
    async def test_low_debt_produces_strength(self):
        """debtToEquity=30 (<50) → 'Low leverage' strength."""
        _clear_profile_cache()
        info = {**MOCK_INFO, "debtToEquity": 30}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        strengths_text = " ".join(result["strengths"])
        assert "low leverage" in strengths_text.lower()

    @pytest.mark.asyncio
    async def test_weak_analyst_sentiment_produces_risk(self):
        """recommendationMean=3.8 (>=3.5) → risk."""
        _clear_profile_cache()
        info = {**MOCK_INFO, "recommendationMean": 3.8}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        risks_text = " ".join(result["risks"])
        assert "analyst" in risks_text.lower()

    @pytest.mark.asyncio
    async def test_premium_valuation_produces_risk(self):
        """trailingPE=65 (>50) → risk."""
        _clear_profile_cache()
        info = {**MOCK_INFO, "trailingPE": 65}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        risks_text = " ".join(result["risks"])
        assert "premium valuation" in risks_text.lower()

    @pytest.mark.asyncio
    async def test_attractive_valuation_produces_strength(self):
        """trailingPE=12 (<15) → strength."""
        _clear_profile_cache()
        info = {**MOCK_INFO, "trailingPE": 12}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        strengths_text = " ".join(result["strengths"])
        assert "attractive valuation" in strengths_text.lower()

    @pytest.mark.asyncio
    async def test_dividend_yield_above_threshold_produces_strength(self):
        """dividendYield=0.04 (>0.02) → strength."""
        _clear_profile_cache()
        info = {**MOCK_INFO, "dividendYield": 0.04}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        strengths_text = " ".join(result["strengths"])
        assert "dividend" in strengths_text.lower()

    @pytest.mark.asyncio
    async def test_high_beta_produces_risk(self):
        """beta=1.8 (>1.5) → risk."""
        _clear_profile_cache()
        info = {**MOCK_INFO, "beta": 1.8}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        risks_text = " ".join(result["risks"])
        assert "volatility" in risks_text.lower()

    @pytest.mark.asyncio
    async def test_empty_info_returns_fallback_messages(self):
        """With no data, narrative returns the 'No notable ...' fallbacks."""
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value={}):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("UNKNOWN")

        assert result["strengths"] == ["No notable strengths identified"]
        assert result["risks"] == ["No notable risks identified"]

    @pytest.mark.asyncio
    async def test_narrative_has_required_keys(self):
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        assert "strengths" in result
        assert "risks" in result
        assert "outlook" in result
        assert "fetched_at" in result
        assert isinstance(result["strengths"], list)
        assert isinstance(result["risks"], list)
        assert isinstance(result["outlook"], str)

    @pytest.mark.asyncio
    async def test_outlook_is_bullish_when_rec_mean_leq_2(self):
        """recommendationMean=2.0 → 'Analysts are bullish' in outlook."""
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        assert "bullish" in result["outlook"].lower()

    @pytest.mark.asyncio
    async def test_outlook_reflects_revenue_momentum(self):
        """Positive revenueGrowth → 'positive revenue momentum' in outlook."""
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        assert "revenue momentum" in result["outlook"].lower()

    @pytest.mark.asyncio
    async def test_narrative_uses_real_recommendation_summary_when_info_mean_missing(self):
        _clear_profile_cache()
        info = {**MOCK_INFO, "recommendationMean": None}
        summary = {
            "period": "0m",
            "strong_buy": 0,
            "buy": 1,
            "hold": 3,
            "sell": 7,
            "strong_sell": 5,
        }
        with (
            patch("stock_profile_service._fetch_info", return_value=info),
            patch("stock_profile_service._fetch_recommendation_summary", return_value=summary),
        ):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("AAPL")

        assert "weak analyst sentiment" in " ".join(result["risks"]).lower()


class TestGetAnalyst:
    @pytest.mark.asyncio
    async def test_prefers_real_summary_and_price_target_tables(self):
        _clear_profile_cache()
        info = {
            **MOCK_INFO,
            "recommendationMean": 4.7,
            "recommendationKey": "sell",
            "targetMeanPrice": 150.0,
            "targetHighPrice": 160.0,
            "targetLowPrice": 120.0,
            "targetMedianPrice": 145.0,
            "numberOfAnalystOpinions": 3,
        }
        with (
            patch("stock_profile_service._fetch_info", return_value=info),
            patch("stock_profile_service._fetch_recommendation_summary", return_value=MOCK_RECOMMENDATION_SUMMARY),
            patch("stock_profile_service._fetch_analyst_price_targets", return_value=MOCK_ANALYST_PRICE_TARGETS),
        ):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_analyst("AAPL")

        assert result["recommendation_period"] == "0m"
        assert result["strong_buy"] == 8
        assert result["buy"] == 12
        assert result["hold"] == 4
        assert result["sell"] == 1
        assert result["strong_sell"] == 0
        assert result["num_analyst_opinions"] == 25
        assert result["recommendation_mean"] == pytest.approx(1.92)
        assert result["recommendation_key"] == "buy"
        assert result["current_price"] == pytest.approx(185.42)
        assert result["target_mean_price"] == pytest.approx(210.50)
        assert result["target_high_price"] == pytest.approx(250.00)
        assert result["target_low_price"] == pytest.approx(180.00)
        assert result["target_median_price"] == pytest.approx(212.00)

    @pytest.mark.asyncio
    async def test_falls_back_to_info_when_analyst_tables_are_missing(self):
        _clear_profile_cache()
        with (
            patch("stock_profile_service._fetch_info", return_value=MOCK_INFO),
            patch("stock_profile_service._fetch_recommendation_summary", return_value=None),
            patch("stock_profile_service._fetch_analyst_price_targets", return_value={}),
        ):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_analyst("AAPL")

        assert result["recommendation_key"] == "buy"
        assert result["recommendation_mean"] == pytest.approx(2.0)
        assert result["recommendation_period"] is None
        assert result["strong_buy"] is None
        assert result["current_price"] is None
        assert result["target_mean_price"] == pytest.approx(210.50)
        assert result["num_analyst_opinions"] == 42


class TestGetAnalystDetail:
    @pytest.mark.asyncio
    async def test_includes_latest_recommendation_snapshot_and_target_revisions(self):
        _clear_profile_cache()
        upgrades = [{
            "date": "2026-03-03",
            "firm": "Wedbush",
            "to_grade": "Outperform",
            "from_grade": "Outperform",
            "action": "main",
            "price_target_action": "Raises",
            "price_target": 300.0,
            "prior_price_target": 230.0,
        }]
        trend = [
            {"period": "-1m", "strong_buy": 7, "buy": 11, "hold": 4, "sell": 1, "strong_sell": 0},
            {"period": "0m", "strong_buy": 8, "buy": 12, "hold": 4, "sell": 1, "strong_sell": 0},
        ]
        with (
            patch("stock_profile_service._fetch_upgrades_downgrades", return_value=upgrades),
            patch("stock_profile_service._fetch_recommendation_trend", return_value=trend),
        ):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_analyst_detail("NVDA")

        assert result["latest_recommendation"]["period"] == "0m"
        assert result["latest_recommendation"]["buy"] == 12
        assert result["upgrades_downgrades"][0]["price_target_action"] == "Raises"
        assert result["upgrades_downgrades"][0]["price_target"] == pytest.approx(300.0)
        assert result["upgrades_downgrades"][0]["prior_price_target"] == pytest.approx(230.0)


class TestGetOwnership:
    @pytest.mark.asyncio
    async def test_preserves_holder_metadata_and_fund_holders(self):
        _clear_profile_cache()
        with (
            patch("stock_profile_service._fetch_info", return_value=MOCK_INFO),
            patch("stock_profile_service._fetch_institutional_holders", return_value=MOCK_INSTITUTIONAL_HOLDERS),
            patch("stock_profile_service._fetch_mutual_fund_holders", return_value=MOCK_FUND_HOLDERS),
            patch("stock_profile_service._fetch_major_holders_summary", return_value=MOCK_MAJOR_HOLDERS),
        ):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_ownership("NVDA")

        assert result["top_holders"][0]["value"] == pytest.approx(242123123123.0)
        assert result["top_holders"][0]["date_reported"] == "2025-12-31"
        assert result["mutual_fund_holders"][0]["name"] == "Vanguard Total Stock Market Index Fund"
        assert result["mutual_fund_holders"][0]["date_reported"] == "2025-12-31"
        assert result["total_institutional_holders"] == 4825

    @pytest.mark.asyncio
    async def test_falls_back_to_major_holders_when_info_ownership_is_missing(self):
        _clear_profile_cache()
        sparse_info = {
            "heldPercentInstitutions": None,
            "heldPercentInsiders": None,
            "institutionCount": None,
        }
        with (
            patch("stock_profile_service._fetch_info", return_value=sparse_info),
            patch("stock_profile_service._fetch_institutional_holders", return_value=[]),
            patch("stock_profile_service._fetch_mutual_fund_holders", return_value=[]),
            patch("stock_profile_service._fetch_major_holders_summary", return_value=MOCK_MAJOR_HOLDERS),
        ):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_ownership("NVDA")

        assert result["held_pct_institutions"] == pytest.approx(0.6118)
        assert result["held_pct_insiders"] == pytest.approx(0.0007)
        assert result["total_institutional_holders"] == 4825


# ---------------------------------------------------------------------------
# 4. Cache hit/miss
# ---------------------------------------------------------------------------

class TestCaching:
    @pytest.mark.asyncio
    async def test_second_call_uses_cache_not_fetcher(self):
        """_fetch_info should be called exactly once; second call returns cached data."""
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO) as mock_fetch:
            from stock_profile_service import StockProfileService
            service = StockProfileService()
            first = await service.get_overview("AAPL")
            second = await service.get_overview("AAPL")

        # _fetch_info is called via asyncio.to_thread inside _info(); the _info
        # result is cached under ("AAPL", "_info") and overview under ("AAPL", "overview").
        # Either way the underlying _fetch_info should only run once.
        assert mock_fetch.call_count == 1
        assert first["symbol"] == second["symbol"]
        assert first["price"] == second["price"]

    @pytest.mark.asyncio
    async def test_cache_miss_after_ttl_expiry(self):
        """Once a cache entry is older than CACHE_TTL it should be refetched."""
        _clear_profile_cache()
        import stock_profile_service as svc

        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO) as mock_fetch:
            service = svc.StockProfileService()
            await service.get_overview("AAPL")

            # Manually expire both cache entries
            for key in list(svc._cache.keys()):
                svc._cache[key].fetched_at = time.time() - (svc.CACHE_TTL + 1)

            await service.get_overview("AAPL")

        # _fetch_info should have been called again after the TTL expired
        assert mock_fetch.call_count == 2

    @pytest.mark.asyncio
    async def test_different_symbols_cached_independently(self):
        """AAPL and MSFT caches must not bleed into each other."""
        _clear_profile_cache()
        msft_info = {**MOCK_INFO, "shortName": "Microsoft Corp.", "currentPrice": 415.0, "previousClose": 410.0}

        with patch("stock_profile_service._fetch_info", side_effect=[MOCK_INFO, msft_info]):
            from stock_profile_service import StockProfileService
            service = StockProfileService()
            aapl = await service.get_overview("AAPL")
            msft = await service.get_overview("MSFT")

        assert aapl["name"] == "Apple Inc."
        assert msft["name"] == "Microsoft Corp."
        assert aapl["symbol"] == "AAPL"
        assert msft["symbol"] == "MSFT"

    @pytest.mark.asyncio
    async def test_key_stats_cached_independently_from_overview(self):
        """Each module has its own cache slot; fetching key_stats after overview
        must not re-fetch yfinance (shared _info cache should cover it)."""
        _clear_profile_cache()
        with patch("stock_profile_service._fetch_info", return_value=MOCK_INFO) as mock_fetch:
            from stock_profile_service import StockProfileService
            service = StockProfileService()
            await service.get_overview("AAPL")
            await service.get_key_stats("AAPL")

        # _info was populated during get_overview; get_key_stats reuses it
        assert mock_fetch.call_count == 1


# ---------------------------------------------------------------------------
# 5. API endpoints via FastAPI TestClient (service mocked)
# ---------------------------------------------------------------------------

def _mock_service(**overrides):
    """Return an AsyncMock of StockProfileService with sensible return values."""
    svc = MagicMock()
    overview_data = {
        "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NMS",
        "sector": "Technology", "industry": "Consumer Electronics",
        "description": "Apple designs...", "employees": 164000,
        "website": "https://www.apple.com", "price": 185.42,
        "change": 2.31, "change_pct": 1.26, "fetched_at": 1700000000.0,
    }
    key_stats_data = {
        "market_cap": 2870000000000.0, "fifty_two_week_high": 199.62,
        "fifty_two_week_low": 164.08, "trailing_pe": 30.45,
        "forward_pe": 28.12, "trailing_eps": 6.09, "forward_eps": 6.59,
        "volume": 54321000, "avg_volume": 58123000, "dividend_yield": 0.005,
        "beta": 1.28, "fifty_day_ma": 182.50, "two_hundred_day_ma": 178.30,
        "fetched_at": 1700000000.0,
    }
    financials_data = {
        "total_revenue": 383285000000.0, "revenue_growth": 0.02,
        "net_income": 96995000000.0, "operating_margins": 0.303,
        "gross_margins": 0.458, "profit_margins": 0.253,
        "debt_to_equity": 176.3, "current_ratio": 0.988,
        "quarterly_revenue": None, "quarterly_net_income": None,
        "fetched_at": 1700000000.0,
    }
    analyst_data = {
        "recommendation_mean": 2.0, "recommendation_key": "buy",
        "recommendation_period": "0m",
        "strong_buy": 8, "buy": 12, "hold": 4, "sell": 1, "strong_sell": 0,
        "current_price": 185.42,
        "target_mean_price": 210.50, "target_high_price": 250.00,
        "target_low_price": 180.00, "target_median_price": 212.00,
        "num_analyst_opinions": 42, "fetched_at": 1700000000.0,
    }
    analyst_detail_data = {
        "upgrades_downgrades": [{
            "date": "2026-03-03", "firm": "Wedbush", "to_grade": "Outperform",
            "from_grade": "Outperform", "action": "main",
            "price_target_action": "Raises", "price_target": 300.0,
            "prior_price_target": 230.0,
        }],
        "recommendation_trend": [{
            "period": "0m", "strong_buy": 8, "buy": 12, "hold": 4, "sell": 1, "strong_sell": 0,
        }],
        "latest_recommendation": {
            "period": "0m", "strong_buy": 8, "buy": 12, "hold": 4, "sell": 1, "strong_sell": 0,
        },
        "fetched_at": 1700000000.0,
    }
    rating_scorecard_data = {
        "overall_score": 71.5, "overall_grade": "B",
        "categories": [], "fetched_at": 1700000000.0,
    }
    ownership_data = {
        "held_pct_institutions": 0.623, "held_pct_insiders": 0.0007,
        "top_holders": MOCK_INSTITUTIONAL_HOLDERS,
        "mutual_fund_holders": MOCK_FUND_HOLDERS,
        "total_institutional_holders": 4825,
        "fetched_at": 1700000000.0,
    }
    events_data = {
        "next_earnings_date": "2024-01-25", "ex_dividend_date": "2023-11-10",
        "fetched_at": 1700000000.0,
    }
    narrative_data = {
        "strengths": ["Strong operating margins (30%)", "Strong analyst buy consensus"],
        "risks": ["High debt-to-equity ratio (176)"],
        "outlook": "Analysts are bullish with positive revenue momentum.",
        "fetched_at": 1700000000.0,
    }
    all_data = {
        "overview": overview_data, "key_stats": key_stats_data,
        "financials": financials_data, "analyst": analyst_data,
        "ownership": ownership_data, "events": events_data,
        "narrative": narrative_data, "analyst_detail": analyst_detail_data,
        "rating_scorecard": rating_scorecard_data,
    }

    svc.get_overview = AsyncMock(return_value={**overview_data, **overrides})
    svc.get_key_stats = AsyncMock(return_value=key_stats_data)
    svc.get_financials = AsyncMock(return_value=financials_data)
    svc.get_analyst = AsyncMock(return_value=analyst_data)
    svc.get_ownership = AsyncMock(return_value=ownership_data)
    svc.get_events = AsyncMock(return_value=events_data)
    svc.get_narrative = AsyncMock(return_value=narrative_data)
    svc.get_analyst_detail = AsyncMock(return_value=analyst_detail_data)
    svc.get_rating_scorecard = AsyncMock(return_value=rating_scorecard_data)
    svc.get_all = AsyncMock(return_value=all_data)
    return svc


class TestStockProfileAPI:
    def setup_method(self):
        self.service = _mock_service()
        self.app = _make_app_with_mock_service(self.service)
        self.client = TestClient(self.app)

    # --- /overview -----------------------------------------------------------

    def test_overview_200(self):
        resp = self.client.get("/api/stock/AAPL/overview")
        assert resp.status_code == 200

    def test_overview_returns_symbol(self):
        resp = self.client.get("/api/stock/AAPL/overview")
        body = resp.json()
        assert body["symbol"] == "AAPL"

    def test_overview_service_called_with_correct_symbol(self):
        self.client.get("/api/stock/TSLA/overview")
        self.service.get_overview.assert_called_once_with("TSLA")

    def test_overview_503_when_service_raises(self):
        self.service.get_overview.side_effect = RuntimeError("yfinance down")
        resp = self.client.get("/api/stock/AAPL/overview")
        assert resp.status_code == 503

    # --- /key-stats ----------------------------------------------------------

    def test_key_stats_200(self):
        resp = self.client.get("/api/stock/AAPL/key-stats")
        assert resp.status_code == 200

    def test_key_stats_contains_market_cap(self):
        resp = self.client.get("/api/stock/AAPL/key-stats")
        body = resp.json()
        assert "market_cap" in body
        assert body["market_cap"] == pytest.approx(2870000000000.0)

    def test_key_stats_503_when_service_raises(self):
        self.service.get_key_stats.side_effect = RuntimeError("timeout")
        resp = self.client.get("/api/stock/AAPL/key-stats")
        assert resp.status_code == 503

    # --- /financials ---------------------------------------------------------

    def test_financials_200(self):
        resp = self.client.get("/api/stock/AAPL/financials")
        assert resp.status_code == 200

    def test_financials_contains_margins(self):
        resp = self.client.get("/api/stock/AAPL/financials")
        body = resp.json()
        assert body["operating_margins"] == pytest.approx(0.303)
        assert body["profit_margins"] == pytest.approx(0.253)

    # --- /analyst ------------------------------------------------------------

    def test_analyst_200(self):
        resp = self.client.get("/api/stock/AAPL/analyst")
        assert resp.status_code == 200

    def test_analyst_contains_recommendation(self):
        resp = self.client.get("/api/stock/AAPL/analyst")
        body = resp.json()
        assert body["recommendation_key"] == "buy"
        assert body["num_analyst_opinions"] == 42
        assert body["strong_buy"] == 8
        assert body["recommendation_period"] == "0m"

    def test_analyst_detail_200(self):
        resp = self.client.get("/api/stock/AAPL/analyst-detail")
        assert resp.status_code == 200

    def test_analyst_detail_contains_recommendation_snapshot(self):
        resp = self.client.get("/api/stock/AAPL/analyst-detail")
        body = resp.json()
        assert body["latest_recommendation"]["period"] == "0m"
        assert body["upgrades_downgrades"][0]["price_target_action"] == "Raises"

    # --- /ownership ----------------------------------------------------------

    def test_ownership_200(self):
        resp = self.client.get("/api/stock/AAPL/ownership")
        assert resp.status_code == 200

    def test_ownership_contains_institutional_pct(self):
        resp = self.client.get("/api/stock/AAPL/ownership")
        body = resp.json()
        assert body["held_pct_institutions"] == pytest.approx(0.623)

    def test_ownership_contains_holder_and_fund_metadata(self):
        resp = self.client.get("/api/stock/AAPL/ownership")
        body = resp.json()
        assert body["top_holders"][0]["date_reported"] == "2025-12-31"
        assert body["mutual_fund_holders"][0]["value"] == pytest.approx(74123123123.0)
        assert body["total_institutional_holders"] == 4825

    # --- /events -------------------------------------------------------------

    def test_events_200(self):
        resp = self.client.get("/api/stock/AAPL/events")
        assert resp.status_code == 200

    def test_events_contains_earnings_date(self):
        resp = self.client.get("/api/stock/AAPL/events")
        body = resp.json()
        assert body["next_earnings_date"] == "2024-01-25"
        assert body["ex_dividend_date"] == "2023-11-10"

    # --- /narrative ----------------------------------------------------------

    def test_narrative_200(self):
        resp = self.client.get("/api/stock/AAPL/narrative")
        assert resp.status_code == 200

    def test_narrative_has_lists_and_outlook(self):
        resp = self.client.get("/api/stock/AAPL/narrative")
        body = resp.json()
        assert isinstance(body["strengths"], list)
        assert isinstance(body["risks"], list)
        assert isinstance(body["outlook"], str)
        assert len(body["strengths"]) > 0

    def test_narrative_503_when_service_raises(self):
        self.service.get_narrative.side_effect = ValueError("bad symbol")
        resp = self.client.get("/api/stock/AAPL/narrative")
        assert resp.status_code == 503

    # --- /profile (batch) ----------------------------------------------------

    def test_profile_200(self):
        resp = self.client.get("/api/stock/AAPL/profile")
        assert resp.status_code == 200

    def test_profile_contains_all_modules(self):
        resp = self.client.get("/api/stock/AAPL/profile")
        body = resp.json()
        for module in ("overview", "key_stats", "financials", "analyst",
                       "ownership", "events", "narrative"):
            assert module in body, f"Missing module: {module}"

    def test_profile_503_when_service_raises(self):
        self.service.get_all.side_effect = Exception("total failure")
        resp = self.client.get("/api/stock/AAPL/profile")
        assert resp.status_code == 503

    # --- Symbol passthrough --------------------------------------------------

    def test_lowercase_symbol_forwarded_to_service(self):
        """The router passes raw path param; service is responsible for upper-casing."""
        self.client.get("/api/stock/msft/overview")
        self.service.get_overview.assert_called_once_with("msft")


# ---------------------------------------------------------------------------
# 6. Edge cases / parametrized
# ---------------------------------------------------------------------------

class TestNarrativeEdgeCases:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("op_margin,expect_in_strengths,expect_in_risks", [
        (0.25, True, False),   # >0.20 → strength
        (0.10, False, False),  # between 0.05 and 0.20 → neither
        (0.03, False, True),   # <0.05 → risk
    ])
    async def test_operating_margin_thresholds(self, op_margin, expect_in_strengths, expect_in_risks):
        _clear_profile_cache()
        info = {**MOCK_INFO, "operatingMargins": op_margin,
                # Neutralise other signals so they don't interfere
                "profitMargins": 0.10, "revenueGrowth": 0.05,
                "debtToEquity": 80, "recommendationMean": 2.5,
                "trailingPE": 20, "dividendYield": 0.01, "beta": 1.0}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("TEST")

        strengths_text = " ".join(result["strengths"]).lower()
        risks_text = " ".join(result["risks"]).lower()

        if expect_in_strengths:
            assert "operating margins" in strengths_text
        if expect_in_risks:
            assert "thin operating margins" in risks_text
        if not expect_in_strengths and not expect_in_risks:
            assert "operating margins" not in strengths_text
            assert "thin operating margins" not in risks_text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rec_mean,expected_label", [
        (1.5, "Strong analyst buy consensus"),
        (2.0, "Strong analyst buy consensus"),
        (3.8, "Weak analyst sentiment (hold/sell)"),
    ])
    async def test_analyst_recommendation_thresholds(self, rec_mean, expected_label):
        _clear_profile_cache()
        info = {**MOCK_INFO, "recommendationMean": rec_mean,
                "operatingMargins": 0.10, "profitMargins": 0.10,
                "revenueGrowth": 0.05, "debtToEquity": 80,
                "trailingPE": 20, "dividendYield": 0.01, "beta": 1.0}
        with patch("stock_profile_service._fetch_info", return_value=info):
            from stock_profile_service import StockProfileService
            result = await StockProfileService().get_narrative("TEST")

        all_signals = result["strengths"] + result["risks"]
        assert expected_label in all_signals
