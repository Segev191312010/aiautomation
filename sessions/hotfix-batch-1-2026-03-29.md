# Hotfix Batch 1 — Runtime Integrity and Truth Guards

Date: 2026-03-29
Status: Execution plan only
Priority: Immediate stabilization before more Stage 1/2 architecture work
Owner: Core backend team

## Context

The current codebase has a small set of runtime-integrity bugs that are more important than further refactors.
These are not cosmetic issues. They affect trade truth, risk enforcement, rule promotion truth, and Stage 10 ledger accuracy.

This batch intentionally stops architecture work long enough to fix the highest-value correctness failures first.

## Why This Batch Comes Before More Refactors

Do not continue deeper Stage 1A/1B or Stage 2 work until these are fixed:

1. Direct AI SELL does not use the canonical exit lifecycle.
2. Rule-entry risk checks run on pre-sized quantity instead of final sized quantity.
3. Paper-rule promotion can pass on legacy, non-canonical evidence.
4. Direct AI concentration checks fail open on exception.
5. SQLite foreign-key cascade semantics are declared but not actually enforced.
6. Stage 10 direct-trade decision items are marked applied before execution success.

If these remain open, later architecture cleanup will only make it easier to preserve wrong behavior.

## Global Exit Gate

Hotfix Batch 1 is complete only when:

- [ ] AI BUY entries register tracked positions through the same lifecycle as other entries.
- [ ] AI SELL exits finalize trade outcomes and clear tracked open positions.
- [ ] No trade is risk-checked against a different quantity than the quantity actually sent.
- [ ] Auto-promotion requires canonical validation evidence.
- [ ] Direct AI concentration enforcement fails closed on exception.
- [ ] SQLite foreign keys are actually enabled on all DB connections.
- [ ] Direct-trade decision items are not marked applied until execution success is known.
- [ ] Focused regression tests exist for each fixed path.
- [ ] Full backend suite remains green after the batch.

## Execution Order

1. HB1-05 Enable SQLite foreign keys
2. HB1-03 Canonical-only promotion gate
3. HB1-04 Fail-closed direct concentration
4. HB1-02 Risk after sizing
5. HB1-01 AI direct trade lifecycle integrity
6. HB1-06 Decision item queued vs applied truth

Reasoning:
- HB1-05 is small, safe, and makes declared cascade semantics real.
- HB1-03 is a contained truth-layer guard with high leverage.
- HB1-04 is a small but important fail-closed safety fix.
- HB1-02 is a contained execution-brain logic fix.
- HB1-01 and HB1-06 are more entangled and should land after the smaller guards are in place.

---

## HB1-01 — AI Direct Trade Lifecycle Integrity

### Problem

Direct AI BUY and SELL are both persisted like entries instead of using distinct entry/exit lifecycles.

Observed current behavior:
- `backend/direct_ai_trader.py:148-156` paper mode always writes `opened_at`, `entry_price`, and `position_id=trade.id`, then calls `save_trade(trade)`.
- `backend/direct_ai_trader.py:179-184` live mode does the same entry-style persistence.
- No path calls `position_tracker.register_position(...)` for AI BUY.
- No path routes AI SELL through the existing exit lifecycle in `backend/bot_runner.py:1325-1395`.
- No path calls `database.finalize_trade_outcome(...)` for AI SELL.

This means:
- AI BUY can be saved without tracked open-position registration.
- AI SELL can be saved as if it were a new entry.
- Canonical exit truth and open-position cleanup can be skipped.

### Files

- `backend/direct_ai_trader.py`
- `backend/bot_runner.py`
- `backend/position_tracker.py`
- `backend/database.py`
- `backend/tests/test_direct_ai_trader.py`
- `backend/tests/test_exit_lifecycle.py`
- `backend/tests/test_trade_truth_e2e.py`

### Required Changes

1. Route AI BUY through tracked position registration.
   - After a successful BUY fill, register the position through the same tracking path used by the rest of the system.
   - Do not leave AI BUY as a saved trade with no tracked open-position lifecycle.

2. Route AI SELL through the exit lifecycle.
   - Reuse existing exit semantics from `bot_runner._place_exit_order()` instead of saving the SELL as an entry-style trade.
   - Ensure the exit path links the exit to the existing open position, finalizes trade outcome, and clears the tracked position when filled.

3. Remove entry-style persistence from raw SELL handling.
   - AI SELL should not set `opened_at` for a fresh position.
   - AI SELL should not set `position_id = trade.id` as if it created a new open position.
   - AI SELL should not be treated as a standalone entry row when it is closing an existing position.

4. Preserve paper/live parity.
   - Paper AI BUY and SELL should follow the same lifecycle semantics as live, even if the actual order-fill source differs.

### Acceptance Criteria

- [ ] Paper AI BUY registers a tracked open position.
- [ ] Live AI BUY registers a tracked open position.
- [ ] Paper AI SELL finalizes trade outcome and clears the tracked open position.
- [ ] Live AI SELL finalizes trade outcome and clears the tracked open position.
- [ ] AI SELL no longer persists with entry-style `opened_at` / `entry_price` / fresh `position_id=trade.id` semantics.
- [ ] Closed AI positions appear as canonical closed outcomes instead of orphaned direct trades.

### Tests

Add or extend:
- `backend/tests/test_direct_ai_trader.py`
  - AI BUY registers position
  - AI SELL with existing open position uses exit lifecycle
  - AI SELL without position still blocks
- `backend/tests/test_exit_lifecycle.py`
  - direct-AI-driven exit reaches `finalize_trade_outcome`
- `backend/tests/test_trade_truth_e2e.py`
  - AI entry + AI exit produces canonical closed outcome

---

## HB1-02 — Risk Check Must Run After Final Sizing

### Problem

Rule-entry risk checks currently run before dynamic position sizing.

Observed current behavior in `backend/bot_runner.py`:
- `check_trade_risk(...)` runs at `bot_runner.py:927-957` using the rule's original quantity.
- Dynamic sizing happens later at `bot_runner.py:959-986`.
- `computed_qty` then overwrites `order_rule.action.quantity`.

This means the trade can be risk-checked against one quantity and executed with another.

### Files

- `backend/bot_runner.py`
- `backend/tests/test_execution_brain.py`
- `backend/tests/test_portfolio_impact.py`

### Required Changes

1. Compute the final intended quantity first.
   - Resolve dynamic position sizing before any size-sensitive risk guard executes.

2. Run all size-sensitive risk checks on the final quantity.
   - `check_trade_risk(...)` must receive the same quantity that would actually be sent.
   - Any downstream concentration or portfolio-impact logic must continue to use the final quantity as well.

3. Preserve cash and rule-evaluation semantics.
   - This is a sequencing fix, not a behavior rewrite.
   - Do not change risk formulas unless required for correctness.

### Acceptance Criteria

- [ ] Dynamic sizing runs before `check_trade_risk(...)`.
- [ ] The quantity used in risk checks matches the quantity used for execution.
- [ ] Existing cash guards still work.
- [ ] Existing concentration logic still runs on final quantity.

### Tests

Add or extend:
- `backend/tests/test_execution_brain.py`
  - dynamically sized trade is risk-checked with computed quantity
- `backend/tests/test_portfolio_impact.py`
  - final quantity flows into impact/concentration checks

---

## HB1-03 — Canonical-Only Promotion Gate

### Problem

Paper-rule validation correctly distinguishes canonical evidence from legacy fallback evidence, but auto-promotion still accepts the latest passing run without requiring canonical data quality.

Observed current behavior:
- `backend/rule_validation.py:121-125` builds canonical primary evidence.
- `backend/rule_validation.py:127-142` builds legacy fallback evidence when no canonical trades exist.
- `backend/rule_validation.py:156`, `:235`, and `:261` persist `data_quality` as either `canonical` or `legacy_fallback`.
- `backend/rule_validation.py:214-219` sets `passed` without requiring canonical evidence.
- `backend/rule_validation.py:84-104` promotion gate checks latest `passed` status and metrics, but does not reject legacy fallback runs.

This allows paper rules to become promotion-eligible on non-rule-linked evidence.

### Files

- `backend/rule_validation.py`
- `backend/tests/test_rule_validation.py`
- `backend/tests/test_trade_truth_e2e.py`

### Required Changes

1. Keep legacy fallback for diagnostic visibility only.
   - Do not remove it completely if it still helps explain lack of canonical data.
   - But do not let it satisfy auto-promotion eligibility.

2. Require canonical evidence for promotion.
   - `evaluate_promotion_gate(...)` must reject latest runs where `details.data_quality != "canonical"`.
   - Add an explicit rejection reason.

3. Align `passed` semantics if needed.
   - Safest path: keep validation metrics calculation as-is, but make promotion eligibility explicitly require canonical evidence.
   - If you tighten `passed` itself, update tests and docs accordingly.

### Acceptance Criteria

- [ ] Rules with only legacy fallback evidence are not promotion-eligible.
- [ ] Promotion gate returns a clear rejection reason when data quality is not canonical.
- [ ] Canonical passing rules remain promotable.
- [ ] Validation history still records legacy fallback runs for diagnostics.

### Tests

Add or extend:
- `backend/tests/test_rule_validation.py`
  - latest legacy-fallback run is not promotion-eligible
  - canonical passing run remains promotion-eligible
- `backend/tests/test_trade_truth_e2e.py`
  - promotion succeeds only after canonical closed trade evidence exists

---

## HB1-04 — Direct AI Concentration Checks Must Fail Closed

### Problem

Direct AI trade concentration checks currently fail open on exception.

Observed current behavior in `backend/bot_runner.py:803-825`:
- `preview_direct_trade(...)` and `check_portfolio_impact(...)` are called before execution.
- If those checks explicitly block, the trade is skipped.
- If those checks raise an exception, the code logs a debug line and continues execution anyway.

This bypasses concentration control whenever broker/data/precheck logic is degraded.

### Files

- `backend/bot_runner.py`
- `backend/tests/test_portfolio_impact.py`
- `backend/tests/test_direct_ai_trader.py`

### Required Changes

1. Change exception behavior from fail-open to fail-closed.
   - If preview or impact evaluation throws, do not execute the direct AI trade.
   - Log a warning with enough context to debug the failure.

2. Keep explicit block behavior unchanged.
   - If the check returns a valid blocking verdict, continue treating it as blocked.

3. Do not silently convert errors into successful execution.

### Acceptance Criteria

- [ ] Direct AI trade does not execute when concentration precheck throws.
- [ ] Warning/error logging includes the symbol and exception context.
- [ ] Explicit non-error block behavior remains unchanged.

### Tests

Add or extend:
- `backend/tests/test_portfolio_impact.py`
  - preview/impact exception blocks execution
- `backend/tests/test_direct_ai_trader.py`
  - direct trade path is skipped when concentration precheck errors

---

## HB1-05 — Enable SQLite Foreign Keys For Real Cascade Behavior

### Problem

Decision/evaluation tables declare foreign keys with cascade semantics, but SQLite foreign-key enforcement is never turned on.

Observed current behavior:
- `backend/database.py:24-31` `get_db()` enables WAL, sync, and busy timeout, but not foreign keys.
- `backend/database.py:488-510` `init_db()` enables the same pragmas, but not foreign keys.
- FK declarations exist in Stage 10 tables, but without `PRAGMA foreign_keys=ON` they do not enforce cascade behavior.

This allows orphaned rows to accumulate silently.

### Files

- `backend/database.py`
- `backend/tests/test_ai_decision_ledger.py`
- `backend/tests/test_s10_e2e.py`

### Required Changes

1. Enable foreign keys in all DB connection paths.
   - Add `PRAGMA foreign_keys = ON` in `get_db()`.
   - Add `PRAGMA foreign_keys = ON` in `init_db()`.

2. Add regression coverage for cascade behavior.
   - Deleting a parent decision/evaluation record should remove dependent rows through real FK enforcement, not best-effort cleanup.

### Acceptance Criteria

- [ ] Every DB connection used by runtime code enables SQLite foreign keys.
- [ ] Stage 10 cascade semantics work in tests.
- [ ] No new runtime behavior changes beyond enforcing declared FK rules.

### Tests

Add or extend:
- `backend/tests/test_ai_decision_ledger.py`
  - parent delete cascades to dependent decision/evaluation rows
- `backend/tests/test_s10_e2e.py`
  - cascade behavior holds in a higher-level flow

---

## HB1-06 — Decision Items Must Not Be Marked Applied Before Execution

### Problem

Stage 10 direct-trade decision items are currently marked applied as soon as they are queued, before execution succeeds.

Observed current behavior in `backend/ai_optimizer.py:593-618`:
- direct-trade candidates are queued via `queue_direct_candidates(...)`
- queued items immediately call `mark_decision_item_applied(...)` at `ai_optimizer.py:612`
- blocked items correctly call `mark_decision_item_blocked(...)`

This means the ledger can say an action was applied even when the bot later rejects or fails to execute it.

### Files

- `backend/ai_optimizer.py`
- `backend/ai_decision_ledger.py`
- `backend/direct_ai_trader.py`
- `backend/bot_runner.py`
- `backend/tests/test_ai_decision_ledger.py`
- `backend/tests/test_s10_e2e.py`
- `backend/tests/test_direct_ai_trader.py`

### Required Changes

1. Do not introduce a new public `gate_status` casually.
   - Current surface already uses `pending`, `applied`, `blocked`, `shadow`, and `error` semantics.
   - Do not invent `queued` unless contracts/UI/schema are deliberately updated together.

2. Use a truthful interim state.
   - Safer hotfix path: leave queued direct-trade items as `pending` until execution success is confirmed.
   - Only call `mark_decision_item_applied(...)` after actual execution succeeds.
   - On execution failure or rejection, mark blocked or error as appropriate.

3. Push the final status update into the execution path.
   - The component that knows whether the order really succeeded must own the transition to `applied`.

### Acceptance Criteria

- [ ] Queued direct-trade items remain truthful before execution.
- [ ] Failed or rejected direct trades do not appear as applied.
- [ ] Successful direct trades are marked applied from the actual execution path.
- [ ] Ledger summary counts remain correct.

### Tests

Add or extend:
- `backend/tests/test_ai_decision_ledger.py`
  - pending item does not become applied merely by being queued
- `backend/tests/test_s10_e2e.py`
  - direct trade item becomes applied only after execution success
- `backend/tests/test_direct_ai_trader.py`
  - failed execution does not mark applied

---

## Batch Verification

After each ticket:

```powershell
cd C:\Users\segev\sdvesdaW\trading\backend
python -m pytest tests/ -v
```

Recommended focused runs during development:

```powershell
cd C:\Users\segev\sdvesdaW\trading\backend
python -m pytest tests/test_direct_ai_trader.py -v
python -m pytest tests/test_exit_lifecycle.py -v
python -m pytest tests/test_rule_validation.py -v
python -m pytest tests/test_trade_truth_e2e.py -v
python -m pytest tests/test_ai_decision_ledger.py -v
python -m pytest tests/test_s10_e2e.py -v
python -m pytest tests/test_execution_brain.py -v
python -m pytest tests/test_portfolio_impact.py -v
```

## Final Acceptance

Hotfix Batch 1 is done only when:

- [ ] All six tickets land.
- [ ] The direct AI path no longer corrupts entry/exit truth.
- [ ] Rule-entry risk checks use final sized quantity.
- [ ] Promotion is canonical-only.
- [ ] Direct AI concentration failures block execution.
- [ ] SQLite FK cascades actually enforce declared relationships.
- [ ] Stage 10 direct-trade ledger status is truthful.
- [ ] Full backend suite is green.

## After This Batch

Only after this batch is green should the team resume:
- Stage 1A router extraction batches beyond already-landed work
- Stage 1B database split planning
- additional Stage 2 AI/autopilot architecture work

This batch restores runtime truth so later refactors are moving correct behavior instead of preserving broken behavior.
