# AI System Architecture Overview

## Executive Summary

The trading platform features a sophisticated **AI-driven autonomous trading system** with multiple specialized AI agents, comprehensive safety guardrails, and a complete feedback loop for continuous learning and improvement.

---

## Core AI Components

### 1. **AI Model Router** (`ai_model_router.py`)

**Purpose**: Resilient LLM call layer with automatic fallback

**Key Features**:
- **Primary Model**: Claude Sonnet 4 (claude-sonnet-4-20250514)
- **Fallback Chain**: Primary → Fallback → Haiku (last resort)
- **Circuit Breaker Integration**: Records successes/failures for safety
- **Cost Tracking**: Tracks tokens in/out for every call
- **Latency Monitoring**: Measures response times

**Usage Pattern**:
```python
result = await ai_call(
    system="You are a trading analyst.",
    prompt="Analyze AAPL",
    source="optimizer",
    model=cfg.AI_MODEL_OPTIMIZER,
    max_tokens=2000,
)
```

---

### 2. **AI Optimizer** (`ai_optimizer.py`)

**Purpose**: Multi-layer AI analysis engine for trading optimization

**Architecture**:

#### Layer 1: Data Collection
- Gathers rule performance metrics
- Collects sector performance data
- Analyzes recent trade outcomes

#### Layer 2: Analysis
- **Rule Performance**: Win rates, profit factors, P&L by rule
- **Sector Performance**: Performance breakdown by market sector
- **Score Analysis**: Optimal min_score thresholds by confidence buckets
- **Bracket Analysis**: Stop-loss vs take-profit hit rates

#### Layer 3: Recommendations
- Generates prioritized recommendations (high/medium/low)
- Suggests rule disable/boost/reduce actions
- Identifies sectors to avoid or favor
- Recommends parameter adjustments

#### Layer 4: Auto-Tune
- Automatically applies safe optimizations
- Disables underperforming rules
- Adjusts rule sizing multipliers
- Updates min_score thresholds

#### Layer 5: AI Narrative
- Generates natural language daily reports
- Uses Claude for human-readable summaries
- Falls back to template if AI unavailable

**Key Metrics Tracked**:
- Win rate, Profit factor, Total P&L
- Sharpe ratio, Max drawdown
- Score bucket performance
- Bracket effectiveness

---

### 3. **AI Advisor** (`ai_advisor.py`)

**Purpose**: Real-time AI decision engine for trade recommendations

**Decision Types**:

| Decision Type | Description | Safety Level |
|--------------|-------------|--------------|
| **min_score** | Adjust minimum signal score threshold | Guardrail protected |
| **risk_multiplier** | Adjust position sizing multiplier | Guardrail protected |
| **rule_changes** | Enable/disable/boost/reduce rules | Guardrail protected |
| **rule_actions** | Create/modify/pause/retire rules | Rule Lab integration |
| **direct_trades** | AI-generated trade candidates | Execution Brain queue |

**Flow**:
1. Build context (portfolio, positions, market data, recent trades)
2. Call LLM with structured system prompt
3. Parse JSON decisions
4. Apply through **Enforcer** (safety_kernel)
5. Persist to **Decision Ledger**

**Shadow Mode**:
- Records what WOULD have been done without applying
- Used for backtesting and validation
- Tracks confidence and regime context

---

### 4. **AI Decision Ledger** (`ai_decision_ledger.py`)

**Purpose**: Immutable audit trail of all AI decisions

**Database Schema**:

#### `ai_decision_runs` (Parent)
| Field | Description |
|-------|-------------|
| id | UUID of the decision run |
| source | Who triggered it (optimizer, advisor, manual) |
| mode | autopilot, copilot, shadow |
| provider/model | Which LLM was used |
| prompt_version | Version of the prompt template |
| context_hash | Hash of input context for reproducibility |
| reasoning | AI's explanation |
| aggregate_confidence | Overall confidence score |
| abstained | Whether AI abstained from decisions |
| input/output_tokens | Cost tracking |
| status | running, completed, failed |

#### `ai_decision_items` (Children)
| Field | Description |
|-------|-------------|
| run_id | Parent run reference |
| item_type | min_score, risk_multiplier, rule_change, direct_trade, abstain |
| action_name | Specific action |
| symbol | Related symbol (if applicable) |
| proposed_json | What AI suggested |
| applied_json | What was actually applied |
| gate_status | pending, applied, blocked, shadow, failed |
| gate_reason | Why it was blocked (if applicable) |
| confidence | AI's confidence in this specific item |
| regime | Market regime at decision time |
| realized_pnl | Actual P&L outcome (backfilled) |
| score_status | unscored, scored, pending |

**Key Functions**:
- `create_decision_run()`: Start new decision session
- `add_decision_item()`: Add individual decision
- `mark_decision_item_applied/blocked/shadow()`: Update status
- `get_decision_runs/items()`: Query history

---

### 5. **AI Evaluator** (`ai_evaluator.py`)

**Purpose**: Comprehensive evaluation framework for AI performance

**Evaluation Modes**:

#### A. Stored-Context Replay
- **Existing**: Summarize already-persisted decisions (no LLM cost)
- **Generate**: Re-run stored contexts through different models/prompts

#### B. Rule Backtest Replay
- Deterministic backtesting of rule snapshots
- Uses `rule_replay_adapter` for fail-closed safety
- Only replayable rules with explicit config can be tested

**Slice Metrics**:
Evaluations are sliced by:
- **Overall**: Aggregate metrics
- **Action Type**: min_score, risk_multiplier, rule_change, etc.
- **Symbol**: Performance by ticker
- **Regime**: Performance in different market conditions
- **Confidence Bucket**: Performance by confidence level

**Metrics Computed**:
- Hit rate (win rate)
- Net P&L
- Expectancy
- Max drawdown
- Coverage (scored vs unscored)
- Abstain rate
- Average confidence
- Calibration error (confidence vs actual hit rate)

**Database Tables**:
- `ai_evaluation_runs`: Evaluation sessions
- `ai_evaluation_slices`: Metrics by slice

---

### 6. **AI Replay** (`ai_replay.py`)

**Purpose**: Historical replay and backtesting engine

**Modes**:

#### Stored-Context Existing
```python
run_stored_context_existing(
    window_days=90,
    limit_runs=500,
    min_confidence=0.7,
    symbols=["AAPL", "MSFT"],
    action_types=["direct_trade"]
)
```
- NO LLM calls - uses already-generated decisions
- Fast and cost-free
- Honors all filters

#### Stored-Context Generate
```python
run_stored_context_generate(
    candidate_key="claude-sonnet-4",
    baseline_key="claude-3-5-sonnet",
    window_days=90,
    limit_runs=500
)
```
- Re-runs historical contexts through different models
- Scores against historical outcomes
- Used for A/B testing models

#### Rule Backtest Replay
- Deterministic replay of rule configurations
- Tests against historical market data
- Validates rule changes before deployment

---

### 7. **AI Learning Loop** (`ai_learning.py`)

**Purpose**: Self-evaluation, cost tracking, and graded autonomy

**Runs Every 6 Hours**:

#### 1. Evaluate Past Decisions
- Scores AI decisions across 7/30/90 day windows
- Uses ledger data when available
- Falls back to audit log heuristic

#### 2. Check Auto-Tighten (3-Level Safety Waterfall)
```
Level 1: Hit rate < 45% for 7d → Reduce risk multiplier by 20%
Level 2: Hit rate < 40% for 7d → Disable autopilot, switch to copilot
Level 3: Hit rate < 35% for 7d → Disable AI entirely, manual only
```

#### 3. Compute Cost Report
- Aggregates Claude API costs from decision runs
- Model-aware pricing (Sonnet vs Haiku)
- Daily breakdown with token counts

#### 4. Compute Economic Report
- ROI analysis: Is AI paying for itself?
- Compares AI costs vs trading profits
- Tracks cost per decision, cost per winning trade

**Cost Tracking**:
```python
MODEL_PRICING = {
    "claude-sonnet-4-20250514": (3.0, 15.0),    # $3/MTok in, $15/MTok out
    "claude-haiku-4-5-20251001": (0.25, 1.25),  # $0.25/MTok in, $1.25/MTok out
}
```

---

## Safety & Guardrails

### Safety Kernel (`safety_kernel.py`)

**Multi-Layer Protection**:

1. **Circuit Breaker**: Trips on repeated AI failures
2. **Enforcer**: Validates all AI decisions before application
3. **Guardrails**: Parameter bounds and change limits
4. **Shadow Mode**: Test without applying

### AI Parameters (`ai_params.py`)

**Centralized Configuration**:
- Risk multipliers
- Min score thresholds
- Rule sizing multipliers
- Shadow mode flag

### Guardrails (`ai_guardrails.py`)

**Safety Constraints**:
- Max risk multiplier changes
- Min score bounds (40-80)
- Required confidence thresholds
- Change rate limiting

---

## Integration Points

### With Rule System
- AI can create/modify/pause/retire rules via `ai_rule_lab.py`
- Rule changes go through safety validation
- Rule replay adapter ensures deterministic backtesting

### With Execution System
- Direct trades queued via `execution_brain.py`
- Decision items linked to actual trades
- P&L outcomes backfilled to ledger

### With Portfolio
- Real-time position data in AI context
- Risk calculations use live portfolio state
- Sector exposure analysis

### With Market Data
- Regime detection feeds into decisions
- Technical indicators in context
- Market regime tracked per decision

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                      AI DECISION FLOW                        │
└─────────────────────────────────────────────────────────────┘

1. TRIGGER
   ├── Scheduled (every 6 hours)
   ├── Manual (user request)
   └── Event-driven (market condition)

2. CONTEXT BUILDING
   ├── Portfolio state (positions, P&L)
   ├── Market data (prices, indicators)
   ├── Rule performance metrics
   └── Recent trade outcomes

3. LLM CALL (via ai_model_router)
   ├── Primary model attempt
   ├── Fallback if needed
   └── Circuit breaker tracking

4. DECISION PARSING
   └── Extract structured decisions from JSON

5. SAFETY VALIDATION (via safety_kernel)
   ├── Enforcer checks
   ├── Guardrail validation
   └── Shadow mode check

6. EXECUTION
   ├── Applied → Live trading
   ├── Blocked → Logged with reason
   └── Shadow → Logged only

7. PERSISTENCE (to ai_decision_ledger)
   ├── Run record
   └── Item records

8. OUTCOME TRACKING
   ├── Link to actual trades
   ├── Backfill P&L
   └── Update score_status

9. EVALUATION (via ai_evaluator)
   ├── Slice metrics
   ├── Compare candidates
   └── Generate reports

10. LEARNING (via ai_learning)
    ├── Cost analysis
    ├── Performance review
    └── Auto-tighten if needed
```

---

## Key Design Principles

### 1. **Safety First**
- All AI decisions go through enforcer
- Circuit breaker prevents cascade failures
- Shadow mode for testing
- 3-level auto-tighten waterfall

### 2. **Full Observability**
- Every decision logged with context hash
- Token costs tracked per call
- P&L outcomes backfilled
- Confidence vs performance correlation

### 3. **Gradual Autonomy**
- Shadow mode → Copilot → Autopilot progression
- Performance-based level advancement
- Automatic rollback on degradation

### 4. **Cost Consciousness**
- Model-aware pricing
- Cost vs P&L tracking
- Fallback to cheaper models
- Existing mode for zero-cost evaluation

### 5. **Reproducibility**
- Context hashes for every run
- Stored context replay
- Deterministic rule backtesting
- Versioned prompts

---

## Configuration

### Environment Variables
```bash
ANTHROPIC_API_KEY=sk-ant-...
AI_MODEL_OPTIMIZER=claude-sonnet-4-20250514
AI_MODEL_FALLBACK=claude-3-5-sonnet-20241022
AI_FALLBACK_ENABLED=true
```

### Database Tables
- `ai_decision_runs` - Decision sessions
- `ai_decision_items` - Individual decisions
- `ai_evaluation_runs` - Evaluation sessions
- `ai_evaluation_slices` - Metrics by slice
- `ai_audit_log` - Legacy audit trail
- `ai_guardrails` - Safety parameters
- `ai_param_snapshots` - Parameter history

---

## Usage Examples

### Run AI Optimizer
```python
from ai_optimizer import run_ai_optimizer
result = await run_ai_optimizer(
    mode="autopilot",
    use_ai=True,
    apply_changes=True
)
```

### Get AI Advice
```python
from ai_advisor import get_ai_advice
result = await get_ai_advice(
    context={...},
    mode="copilot"
)
```

### Evaluate Performance
```python
from ai_evaluator import run_stored_context_existing
result = await run_stored_context_existing(
    window_days=30,
    min_confidence=0.7
)
```

### Check Costs
```python
from ai_learning import compute_cost_report
report = await compute_cost_report(days=30)
# Returns: total_cost_usd, total_calls, daily breakdown
```

---

## Monitoring & Alerts

### Key Metrics to Watch

| Metric | Target | Alert If |
|--------|--------|----------|
| Hit Rate | > 50% | < 45% for 7d |
| Calibration Error | < 0.1 | > 0.2 |
| Cost per Decision | < $0.10 | > $0.50 |
| AI ROI | > 2x | < 1x |
| Circuit Breaker Trips | 0 | > 3/day |

### Dashboard Views
- Decision volume by source/mode
- Hit rate trends (7/30/90d)
- Cost vs P&L correlation
- Confidence calibration
- Slice performance heatmap

---

## Future Enhancements

1. **Multi-Model Ensemble**: Combine predictions from multiple models
2. **Reinforcement Learning**: RL-based parameter optimization
3. **Real-Time Adaptation**: Dynamic prompt selection based on regime
4. **Explainability**: SHAP values for decision attribution
5. **A/B Testing Framework**: Built-in experiment management

---

## Summary

The AI system provides:
- ✅ **Autonomous decision-making** with safety guardrails
- ✅ **Complete audit trail** for every decision
- ✅ **Cost tracking** and ROI analysis
- ✅ **Continuous learning** from outcomes
- ✅ **Multi-model resilience** with fallback
- ✅ **Gradual autonomy** with performance-based progression
- ✅ **Comprehensive evaluation** across multiple dimensions

This is a production-grade AI trading system with enterprise-level observability, safety, and cost management.
