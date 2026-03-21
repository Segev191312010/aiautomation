"""
Contract tests — validate that advisor API responses match Pydantic models.
"""
import pytest
from pydantic import ValidationError

from api_contracts import (
    AdvisorReportResponse,
    AuditLogEntryResponse,
    AuditLogResponse,
    AutoTuneResultResponse,
    AIStatusResponse,
    BracketAnalysisResponse,
    CostReportResponse,
    DailyCostEntry,
    GatingCondition,
    GuardrailConfigResponse,
    ParamTypeMetrics,
    RecommendationResponse,
    RulePerformanceResponse,
    ScoreAnalysisResponse,
    ScoreBucket,
    SectorPerformanceResponse,
    ShadowPerformanceResponse,
    TimePatternResponse,
    UncertainValue,
    AIDecisionPayload,
    AIRuleChange,
)


# ── Fixture data that mirrors realistic backend output ───────────────────────

SAMPLE_RULE_PERF = {
    "rule_id": "test-rule-1",
    "rule_name": "RSI Oversold Bounce",
    "total_trades": 15,
    "win_rate": 60.0,
    "profit_factor": 1.8,
    "total_pnl": 345.50,
    "avg_pnl": 23.03,
    "avg_win": 48.20,
    "avg_loss": -14.10,
    "avg_hold_hours": 72.5,
    "verdict": "hold",
    "status": "good",
}

SAMPLE_SECTOR_PERF = {
    "sector": "Technology",
    "trade_count": 10,
    "win_rate": 70.0,
    "total_pnl": 520.0,
    "verdict": "favor",
}

SAMPLE_TIME_PATTERN = {
    "hour": 10,
    "trade_count": 8,
    "win_rate": 62.5,
    "avg_pnl": 15.30,
    "total_pnl": 122.40,
}

SAMPLE_SCORE_ANALYSIS = {
    "available": True,
    "buckets": [
        {"range": "50-55", "count": 5, "avg_pnl": 10.0, "win_rate": 60.0},
        {"range": "55-60", "count": 3, "avg_pnl": 25.0, "win_rate": 66.7},
    ],
    "optimal_min_score": 55,
    "current_min_score": 50,
}

SAMPLE_BRACKET_ANALYSIS = {
    "total_closed": 20,
    "sl_hits": 6,
    "tp_hits": 8,
    "other_exits": 6,
    "sl_hit_pct": 30.0,
    "tp_hit_pct": 40.0,
    "brackets_too_tight": False,
}

SAMPLE_RECOMMENDATION = {
    "type": "boost",
    "priority": "medium",
    "message": "Scale up 'RSI Oversold' — 65% win rate",
    "rule_id": "test-rule-1",
    "category": "rule",
}


# ── Individual model validation ──────────────────────────────────────────────


class TestRulePerformanceContract:
    def test_valid(self):
        m = RulePerformanceResponse(**SAMPLE_RULE_PERF)
        assert m.rule_id == "test-rule-1"
        assert m.avg_hold_hours == 72.5
        assert m.verdict == "hold"

    def test_missing_avg_hold_hours_fails(self):
        data = {**SAMPLE_RULE_PERF}
        del data["avg_hold_hours"]
        with pytest.raises(ValidationError):
            RulePerformanceResponse(**data)

    def test_invalid_verdict_fails(self):
        data = {**SAMPLE_RULE_PERF, "verdict": "INVALID"}
        with pytest.raises(ValidationError):
            RulePerformanceResponse(**data)


class TestSectorPerformanceContract:
    def test_valid(self):
        m = SectorPerformanceResponse(**SAMPLE_SECTOR_PERF)
        assert m.sector == "Technology"
        assert m.verdict == "favor"

    def test_invalid_verdict(self):
        data = {**SAMPLE_SECTOR_PERF, "verdict": "buy"}
        with pytest.raises(ValidationError):
            SectorPerformanceResponse(**data)


class TestRecommendationContract:
    def test_valid(self):
        m = RecommendationResponse(**SAMPLE_RECOMMENDATION)
        assert m.type == "boost"
        assert m.category == "rule"

    def test_missing_rule_id_ok(self):
        data = {**SAMPLE_RECOMMENDATION}
        del data["rule_id"]
        m = RecommendationResponse(**data)
        assert m.rule_id is None


class TestScoreAnalysisContract:
    def test_valid(self):
        m = ScoreAnalysisResponse(**SAMPLE_SCORE_ANALYSIS)
        assert len(m.buckets) == 2
        assert m.optimal_min_score == 55

    def test_empty_buckets_ok(self):
        m = ScoreAnalysisResponse(available=False, buckets=[], optimal_min_score=50)
        assert not m.available


class TestBracketAnalysisContract:
    def test_valid(self):
        m = BracketAnalysisResponse(**SAMPLE_BRACKET_ANALYSIS)
        assert m.total_closed == 20


class TestAutoTuneContract:
    def test_valid(self):
        m = AutoTuneResultResponse(
            applied=False,
            changes=["Disable 'Bad Rule' — 20% win rate"],
            warnings=["Rule X has only 3 trades"],
            rules_to_disable=["rule-id-1"],
        )
        assert not m.applied
        assert len(m.changes) == 1


class TestGuardrailConfigContract:
    def test_defaults(self):
        m = GuardrailConfigResponse()
        assert m.shadow_mode is True
        assert not m.ai_autonomy_enabled
        assert not m.emergency_stop
        assert m.max_changes_per_day == 10
        assert m.min_score_floor == 35
        assert m.min_score_ceiling == 80
        assert m.shadow_to_live_min_decisions == 100
        assert m.shadow_to_live_hit_rate_threshold == 0.55

    def test_custom_values(self):
        m = GuardrailConfigResponse(
            ai_autonomy_enabled=True,
            max_rules_disabled_per_day=5,
            emergency_stop=True,
        )
        assert m.ai_autonomy_enabled
        assert m.max_rules_disabled_per_day == 5


class TestAuditLogContract:
    def test_entry(self):
        m = AuditLogEntryResponse(
            id=1,
            timestamp="2026-03-20T10:00:00Z",
            action_type="rule_disable",
            category="auto_tune",
            description="Disabled rule X",
            reason="Win rate 22%",
            confidence=0.85,
            status="applied",
        )
        assert m.action_type == "rule_disable"
        assert m.confidence == 0.85

    def test_paginated_response(self):
        m = AuditLogResponse(entries=[], total=0, offset=0, limit=50)
        assert m.total == 0


class TestAIStatusContract:
    def test_defaults(self):
        m = AIStatusResponse()
        assert not m.autonomy_active
        assert m.shadow_mode
        assert m.daily_budget_remaining == 10


class TestUncertainValueContract:
    def test_valid(self):
        m = UncertainValue(value=55, lower=50, upper=60)
        assert m.value == 55
        assert m.lower < m.upper


class TestAIDecisionPayloadContract:
    def test_minimal(self):
        m = AIDecisionPayload(reasoning="No changes needed", confidence=0.6)
        assert m.rule_changes == []
        assert m.signal_weights is None

    def test_with_rule_changes(self):
        m = AIDecisionPayload(
            rule_changes=[
                AIRuleChange(rule_id="r1", action="disable", reason="Low win rate"),
                AIRuleChange(rule_id="r2", action="boost", sizing_mult=1.3, reason="Strong PF"),
            ],
            reasoning="Two rule adjustments",
            confidence=0.78,
        )
        assert len(m.rule_changes) == 2
        assert m.rule_changes[1].sizing_mult == 1.3


class TestCostReportContract:
    def test_valid(self):
        m = CostReportResponse(
            days=30,
            total_cost_usd=2.10,
            total_calls=60,
            daily=[
                DailyCostEntry(
                    date="2026-03-20",
                    calls=6,
                    input_tokens=12000,
                    output_tokens=8000,
                    estimated_cost_usd=0.07,
                )
            ],
        )
        assert m.total_cost_usd == 2.10


class TestShadowPerformanceContract:
    def test_ready_for_live(self):
        m = ShadowPerformanceResponse(
            total_decisions=120,
            decisions_with_data=100,
            overall_hit_rate=0.58,
            overall_effect_size_avg=0.12,
            active_days=20,
            by_param_type={
                "min_score": ParamTypeMetrics(count=50, hit_rate=0.62, effect_size_avg=0.15, avg_confidence=0.7),
            },
            gating_conditions=[
                GatingCondition(name="min_decisions", met=True, actual=100, required=50),
                GatingCondition(name="hit_rate", met=True, actual=0.58, required=0.55),
            ],
            ready_for_live=True,
            ready_reasons=["min_decisions: 100 >= 50", "hit_rate: 0.58 >= 0.55"],
        )
        assert m.ready_for_live
        assert m.decisions_with_data == 100
        assert len(m.gating_conditions) == 2

    def test_not_ready(self):
        m = ShadowPerformanceResponse(
            total_decisions=10,
            decisions_with_data=5,
            overall_hit_rate=0.40,
            overall_effect_size_avg=-0.05,
            active_days=3,
            by_param_type={},
            gating_conditions=[
                GatingCondition(name="min_decisions", met=False, actual=5, required=50),
            ],
            ready_for_live=False,
        )
        assert not m.ready_for_live
        assert m.overall_hit_rate == 0.40

    def test_empty_decisions(self):
        m = ShadowPerformanceResponse(total_decisions=0)
        assert not m.ready_for_live
        assert m.gating_conditions == []


# ── Full report contract ─────────────────────────────────────────────────────

class TestAdvisorReportContract:
    def test_full_report(self):
        m = AdvisorReportResponse(
            generated_at="2026-03-20T12:00:00Z",
            lookback_days=90,
            pnl_summary={"total_pnl": 500, "win_rate": 55, "profit_factor": 1.4, "trade_count": 30},
            performance={"sharpe_ratio": 1.2, "max_drawdown_pct": -8.5},
            rule_performance=[RulePerformanceResponse(**SAMPLE_RULE_PERF)],
            sector_performance=[SectorPerformanceResponse(**SAMPLE_SECTOR_PERF)],
            time_patterns=[TimePatternResponse(**SAMPLE_TIME_PATTERN)],
            score_analysis=ScoreAnalysisResponse(**SAMPLE_SCORE_ANALYSIS),
            bracket_analysis=BracketAnalysisResponse(**SAMPLE_BRACKET_ANALYSIS),
            recommendations=[RecommendationResponse(**SAMPLE_RECOMMENDATION)],
            auto_tune_preview=AutoTuneResultResponse(
                applied=False, changes=[], warnings=[], rules_to_disable=[]
            ),
            report="Bot performed well over the last 90 days...",
            trade_count=30,
            data_warning=None,
        )
        assert m.trade_count == 30
        assert len(m.rule_performance) == 1
        assert m.rule_performance[0].avg_hold_hours == 72.5
