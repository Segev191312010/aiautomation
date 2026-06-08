# AI System Architecture Overview

## Executive Summary

The trading platform features a sophisticated **AI-driven autonomous trading system** with multiple specialized AI agents, comprehensive safety guardrails, and a complete feedback loop for continuous learning and improvement.

---

## Core AI Components

### 1. **AI Model Router** (`ai_model_router.py`)

**Purpose**: Resilient LLM call layer with automatic fallback

**Key Features**:
- **Primary Model**: Claude Sonnet 4 (claude-sonnet-4-20250514), via `cfg.AI_MODEL_OPTIMIZER`
- **Fallback Chain**: `_build_model_chain()` builds an ordered, deduplicated list: Primary → Fallback (`AI_MODEL_FALLBACK`) → hardcoded last-resort `claude-haiku-4-5-20251001`. At the **default** config, `AI_MODEL_FALLBACK` already equals the last-resort id, so the chain dedups to **2 entries** `[primary, haiku]`. Three distinct tiers exist only when `AI_MODEL_FALLBACK` is set to a separate middle model.
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

**Purpose**: LLM decision-application engine — calls Claude for structured parameter recommendations on a schedule, then applies them through the guardrail enforcer.

**Structure** (functions, not analysis layers):

#### `_build_context()`
- Delegates to `context_builder.build_optimizer_context(lookback_days=cfg.ADVISOR_LOOKBACK_DAYS)`
- Returns the full context dict (rule/sector/time performance, score & bracket analysis, market snapshot, current params)

#### `_get_ai_decisions(context)`
- Calls Claude via `ai_model_router.ai_call` (source `optimizer`, model `cfg.AI_MODEL_OPTIMIZER`, `temperature=0`)
- Parses the JSON response (handles ` ```json ` fences) into a decisions payload
- Returns `None` when no `ANTHROPIC_API_KEY` is set or the call fails

#### `_apply_decisions(decisions, context, run_id, item_ids)`
- Applies each decision through a `GuardrailEnforcer` instance (`enforcer.execute_with_audit`)
- Handles `min_score`, `risk_multiplier`, `rule_changes`, `rule_actions` (via `ai_rule_lab.apply_rule_actions`), and `direct_trades` (via `execution_brain.queue_direct_candidates`)
- Returns `{"applied": [...], "blocked": [...], "shadow": [...]}`

#### `run_full_optimization()` (orchestrator)
- Checks `emergency_stop` (fail-closed), builds context, gets AI decisions
- Records the decision run + items in the **Decision Ledger** (`start_decision_run` / `record_decision_items`), applies decisions, then `finalize_decision_run`
- Persists optimized params (`ai_params.save_to_db`) so they survive restart

**Decision Types** (handled by `_apply_decisions`):

| Decision Type | Description | Safety Path |
|--------------|-------------|--------------|
| **min_score** | Adjust minimum signal score threshold | `score_threshold` via enforcer |
| **risk_multiplier** | Adjust position sizing multiplier | `risk_adjust` via enforcer |
| **rule_changes** | Enable/disable/boost/reduce rules | `rule_*` via enforcer |
| **rule_actions** | Create/modify/pause/retire rules | `ai_rule_lab.apply_rule_actions` |
| **direct_trades** | AI-generated trade candidates | `execution_brain.queue_direct_candidates` |

**Shadow Mode**:
- When `ai_params.shadow_mode` is true, `_apply_decisions` logs what WOULD have been done (`log_shadow_decision`) and marks ledger items `shadow` instead of applying them
- The optimizer loop sets `ai_params.shadow_mode = (autopilot_mode == "OFF")` each cycle

---

### 3. **AI Advisor** (`ai_advisor.py`)

**Purpose**: Performance-analysis, reporting, and auto-tune module. It analyzes bot performance, generates prioritized recommendations, computes/optionally applies auto-tune, and writes a narrative daily report. It does **not** call the guardrail enforcer or the Decision Ledger.

**Layers** (orchestrated by `build_full_report(lookback_days=90, apply_tune=False)`):

1. **Data Collection** — `fetch_advisor_data()` pulls trades + rules from the DB over `ADVISOR_LOOKBACK_DAYS` (default 90) and computes realized P&L.
2. **Analysis Engine** (pure Python) — per-rule win rate / profit factor / verdict, per-sector P&L, time-of-day patterns, score buckets, and bracket (SL vs TP) hit rates.
3. **Recommendations** — `generate_recommendations()` produces a prioritized (high/medium/low) actionable list.
4. **Auto-tune** — `compute_auto_tune()` computes rule disables, sizing changes, and a new min_score; `apply_auto_tune()` applies them to the DB + `ai_params` (only when `apply_tune=True`).
5. **AI Narrative** — `generate_daily_report()` writes a plain-prose daily briefing via `ai_call` (model `AI_MODEL_NARRATIVE`), falling back to a template if AI is unavailable.

**Flow**: fetch trades + rules → pure-Python analysis layers → recommendations → compute (and optionally apply) auto-tune → narrative daily report.

> The `min_score` / `risk_multiplier` / `rule_changes` / `rule_actions` / `direct_trades` decision pipeline, the **Enforcer**, the **Decision Ledger**, and **Shadow Mode** belong to the **AI Optimizer** (Section 2), not the Advisor.

---

### 4. **AI Decision Ledger** (`ai_decision_ledger.py`)

**Purpose**: Immutable audit trail of all AI decisions

**Database Schema**:

#### `ai_decision_runs` (Parent)
| Field | Description |
|-------|-------------|
| id | UUID of the decision run |
| source | Who triggered it (optimizer, advisor, manual) |
| mode | `cfg.AUTOPILOT_MODE` at run time (OFF, PAPER, LIVE) |
| provider/model | Which LLM was used |
| prompt_version | Version of the prompt template |
| context_hash | Hash of input context for reproducibility |
| reasoning | AI's explanation |
| aggregate_confidence | Overall confidence score |
| abstained | Whether AI abstained from decisions |
| input/output_tokens | Cost tracking |
| status | created (initial), completed, error (`running` is never used) |

#### `ai_decision_items` (Children)
| Field | Description |
|-------|-------------|
| run_id | Parent run reference |
| item_index | Position of the item within the run |
| item_type | score_threshold, risk_adjust, rule_change, rule_action, direct_trade, abstain |
| action_name | Specific action |
| target_key | Parameter target (e.g. `min_score`, `risk_multiplier`, rule_id) |
| symbol | Related symbol (if applicable) |
| proposed_json | What AI suggested |
| applied_json | What was actually applied |
| gate_status | pending, applied, blocked, shadow |
| gate_reason | Why it was blocked (if applicable) |
| confidence | AI's confidence in this specific item |
| regime | Market regime at decision time |
| origin_rule_id | Rule that produced this item (if any) |
| created_rule_id | Rule created by this item (if any) |
| created_trade_id / realized_trade_id | Linked trades |
| realized_pnl | Actual P&L outcome (backfilled) |
| realized_at | When the outcome was realized |
| score_status | unscored, direct_realized, replay_scored, proxy_scored |
| score_source | How the item was scored |
| notes | Free-text notes |

**Key Functions**:
- `start_decision_run()`: Start new decision session
- `record_decision_items()`: Bulk-insert decision items for a run
- `mark_decision_item_applied/blocked/shadow()`: Update status
- `get_decision_runs()` / `get_decision_run(run_id)` (singular) / `get_decision_items()`: Query history

---

### 5. **AI Evaluator** (`ai_evaluator.py`)

**Purpose**: Metrics computation and persistence framework for AI performance.

**Key Functions**:
- `create_evaluation_run()`: Open an evaluation session
- `compute_slice_metrics()`: Compute metrics for a set of decision items
- `save_evaluation_slices()`: Persist per-slice metrics
- `get_evaluation_runs()` / `get_evaluation_slices()`: Query results
- `compare_evaluations()`: Diff two evaluation runs

> The replay/generation engine itself (Stored-Context Replay, Rule Backtest Replay, `rule_replay_adapter`) lives in `ai_replay.py` — see Section 6.

**Slice Metrics**:
Evaluations are sliced by (`slice_type`):
- **Overall**: Aggregate metrics
- **Action Type** (`item_type`): score_threshold, risk_adjust, rule_change, rule_action, direct_trade, abstain
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
    candidate_type="model_version",
    baseline_key=None,
    window_days=90,
    limit_runs=500
)
```
- Re-runs historical contexts through a resolved candidate (`candidate_type` default `"model_version"`)
- Generates candidate items and scores only those matchable against historical outcomes (`score_candidate_item_against_historical`)
- `baseline_key` is accepted but **unused in scoring** — reserved/metadata only (not A/B model comparison)

#### Rule Backtest Replay (`run_rule_backtest_replay`)
- Deterministic replay of rule snapshots against historical market data
- Uses `rule_replay_adapter` for fail-closed safety — only rules with explicit `replay_config` are replayable
- Validates rule changes before deployment

---

### 7. **AI Learning Loop** (`ai_learning.py`)

**Purpose**: Self-evaluation, cost tracking, and graded autonomy

**Runs Every 6 Hours**:

#### 1. Evaluate Past Decisions
- Scores AI decisions across 7/30/90 day windows
- Uses ledger data when available
- Falls back to audit log heuristic

#### 2. Check Auto-Tighten (2 levels + recovery)
`check_auto_tighten()` has two tightening levels and a recovery branch (thresholds configurable in the guardrail config):
```
Level 1 (7d):  hit_rate < 0.45 → halve max_changes_per_day,
               max_position_size_increase_pct, and max_weight_change_pct (×0.5),
               and raise min_score_floor by 2. (No "risk multiplier" field exists.)
Level 2 (30d): hit_rate < 0.50 (requires Level 1 already tripped)
               → revert autopilot_mode to 'PAPER'
Recovery:      30d hit_rate > 0.55 with 50+ scored decisions → restore defaults
```

#### 3. Compute Cost Report
- Aggregates Claude API costs from decision runs
- Model-aware pricing (Sonnet vs Haiku)
- Daily breakdown with token counts

#### 4. Compute Economic Report
- ROI analysis: Is AI paying for itself?
- Compares AI costs vs trading profits
- Fields from `compute_economic_report()`: `cost_per_decision`, `roi_estimate`, `cost_as_pct_pnl`, `decisions_per_day`

**Cost Tracking**:
```python
MODEL_PRICING = {
    "claude-sonnet-4-20250514":   (3.0, 15.0),    # $3/MTok in, $15/MTok out
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-haiku-4-5-20251001":  (0.25, 1.25),   # $0.25/MTok in, $1.25/MTok out
    "claude-3-5-haiku-20241022":  (0.25, 1.25),
}
```

---

## Safety & Guardrails

### Safety Kernel (`safety_kernel.py`)

Runtime entry-gate checks for AI-controlled (and operator-rule) orders. `check_all()` composes these hard checks:

1. **`assert_emergency_stop_not_active`**: operator kill switch — blocks all new entries
2. **`assert_autopilot_authority`**: AI-authority gate — AI entries require `AUTOPILOT_MODE` ≠ OFF
3. **`assert_daily_loss_not_locked`**: blocks new entries when the daily loss lock is active
4. **`assert_risk_budget`**: enforces the 1% per-trade risk rule
5. **`assert_no_shorts`**: blocks sell-to-open entries
6. **`assert_not_duplicate`**: dedup within a rolling window
7. **Consecutive-failure circuit breaker**: after N consecutive AI failures, auto-activates emergency stop

> `assert_not_killed()` is now only a back-compat wrapper combining the kill-switch + AI-authority checks. The **Enforcer** and **Shadow Mode** are NOT in the safety kernel (see AI Optimizer / Guardrails / AI Parameters).

### AI Parameters (`ai_params.py`)

**Centralized Configuration** (`AIParameterStore`):
- Risk multipliers
- Min score thresholds
- Rule sizing multipliers
- **Shadow mode flag** (`shadow_mode`) — when set, the store returns defaults and the optimizer logs decisions without applying them

### Guardrails (`ai_guardrails.py`)

**Safety Constraints** (enforced by `GuardrailEnforcer`):
- Max position-size increase / weight-change limits
- Min score bounds (35-80) — `min_score_floor=35`, `min_score_ceiling=80`
- **No** min-confidence gate — instead, confidence **scales** the limits: `max_allowed = base_limit * confidence`
- Change rate limiting (`max_changes_per_day`, cooldown between changes)

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

5. SAFETY VALIDATION
   ├── GuardrailEnforcer checks (ai_guardrails)
   ├── Shadow mode check (ai_params)
   └── Runtime entry gates (safety_kernel) at order time

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
- All AI decisions go through the `GuardrailEnforcer`
- Circuit breaker prevents cascade failures
- Shadow mode for testing
- 2-level auto-tighten waterfall + recovery branch

### 2. **Full Observability**
- Every decision logged with context hash
- Token costs tracked per call
- P&L outcomes backfilled
- Confidence vs performance correlation

### 3. **Gradual Autonomy**
- `AUTOPILOT_MODE` progression: OFF → PAPER → LIVE
- Performance-based level advancement
- Automatic rollback on degradation (Level 2 auto-tighten reverts LIVE → PAPER)

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

# Models
AI_MODEL_OPTIMIZER=claude-sonnet-4-20250514
AI_MODEL_NARRATIVE=claude-sonnet-4-20250514
AI_MODEL_REGIME=claude-sonnet-4-20250514
AI_MODEL_PORTFOLIO=claude-sonnet-4-20250514
AI_MODEL_FALLBACK=claude-haiku-4-5-20251001
AI_FALLBACK_ENABLED=true

# Safety gating
AUTOPILOT_MODE=OFF                              # OFF | PAPER | LIVE
ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF=false
FULLY_AUTO_RULES_ENABLED=false                  # defaults true in PAPER / SIM_MODE
FULLY_AUTO_RULE_UNIVERSE=etfs
FULLY_AUTO_RULE_QUANTITY=1
```

### Database Tables
- `ai_decision_runs` - Decision sessions
- `ai_decision_items` - Individual decisions
- `ai_evaluation_runs` - Evaluation sessions
- `ai_evaluation_slices` - Metrics by slice
- `ai_audit_log` - Legacy audit trail
- `ai_guardrails` - Safety parameters
- `ai_parameter_snapshots` - Parameter history
- `ai_shadow_decisions` - Shadow-mode decision log
- `ai_rule_versions` - Versioned rule snapshots
- `ai_rule_validation_runs` - Rule validation/backtest results
- `direct_candidates` - TTL queue of AI direct trade candidates

---

## Usage Examples

### Run AI Optimizer
```python
from ai_optimizer import run_full_optimization
result = await run_full_optimization()
```

### Build Advisor Report
```python
from ai_advisor import build_full_report
result = await build_full_report(lookback_days=90, apply_tune=False)
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
