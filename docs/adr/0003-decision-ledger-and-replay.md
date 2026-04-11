# ADR 0003: Decision Ledger and Replay Semantics

**Status:** Accepted
**Date:** 2026-04-11
**Supersedes:** N/A

## Context

The AI layer (`ai_optimizer`, `ai_advisor`, `ai_learning`, `direct_ai_trader`, `ai_rule_lab`) proposes changes to the bot: new parameters, new rules, direct trades, rule edits. Before Stage 10 of the review roadmap, these proposals had no persistent audit trail. An operator could ask "why did the bot raise the minimum score threshold from 55 to 62 at 3:47pm yesterday?" and the answer was somewhere in log files, maybe, or gone.

Worse, the AI's proposals were impossible to replay. If a rule change looked wrong in hindsight, there was no way to re-run the same decision with a different prompt, a different model, or against a different trade window to check whether the original output was a bad call or the right call that got applied badly.

Stage 10 of the review roadmap (2026-03) introduced the decision ledger tables (`ai_decision_runs`, `ai_decision_items`, `ai_evaluation_runs`, `ai_evaluation_slices`) and the replay subsystem (`ai_replay.py`, `candidate_registry.py`, `rule_replay_adapter.py`, `replay_scoring.py`). This ADR codifies what those tables mean, what the three replay modes do, and what replay is NOT allowed to do.

## Decision

### The ledger is append-only and authoritative

- Every AI-originated decision writes exactly one row to `ai_decision_runs` via `start_decision_run()`.
- Every item the decision touches (a rule to pause, a rule to promote, a direct trade, an abstain, a score threshold change) writes exactly one row to `ai_decision_items` linked by `FOREIGN KEY(run_id) REFERENCES ai_decision_runs(id) ON DELETE CASCADE`.
- The cascade is real: HB1-05 regression test (`test_hb1_05_decision_run_delete_cascades_to_items`) proves `PRAGMA foreign_keys=ON` is honored on every connection. Deleting a parent run removes its children atomically.
- An item progresses through `pending → applied | blocked | shadow` states. **An item must never be marked `applied` before the corresponding code path actually executed successfully** (HB1-06 — `direct_ai_trader.py` waits for `place_order` to return non-ERROR before calling `mark_decision_item_applied`). This is a load-bearing invariant; the regression test `test_hb1_06_failed_live_trade_does_not_mark_decision_item_applied` is its guard.

### Three replay modes, no more, no less

The replay endpoint accepts exactly these modes (`ai_replay.py`, `autopilot_api.py::POST /api/autopilot/evaluation/replay`):

1. **`stored_context_existing`** — replay a run against its stored context and re-score the EXISTING candidate items. No AI call. No new candidates. Used to apply a new scoring policy to historical decisions. Deterministic.

2. **`stored_context_generate`** — replay a run against its stored context but re-CALL the AI (possibly with a different model or prompt version) to generate new candidate items. The stored context is the input; the AI output is new. Used for prompt A/B testing and model comparison. Non-deterministic (temperature > 0). Filters on `window`, `symbols`, `action_types`, and `min_confidence` are honored (verified in Stage 2 Phase 2.3 work).

3. **`rule_backtest`** — replay a rule (not a run) against historical bars via the real backtester. No AI call. Used when asking "what would this rule have done last quarter?". Rejects rules without replay metadata (fail-closed behavior, no invented exits). The adapter (`rule_replay_adapter.py`) translates rule shape into backtester input.

### What replay is NOT allowed to do

- **No metadata relabeling as replay.** A replay must produce new candidate items or new scoring; simply changing a status label on existing items is not a replay result.
- **No fabricated exit semantics.** If a rule doesn't have `replay_config` metadata, `rule_backtest` mode must reject it with a reason, not invent defaults.
- **No implicit side effects.** Replay never mutates live params, never places trades, never writes to `open_positions`. The ledger records `status='replayed'` on the new run but does not link it to any realized trade.
- **No AI API call in `stored_context_existing` mode.** If the replay path calls `ai_call` in this mode, the regression test suite fails (this is not currently tested; Phase B should add it).

### What gets stored per run

The decision ledger `context_json` column holds everything the AI saw when it made the decision, not just the prompt. P1-4 (commit `9717bd0`) expanded it from 4 fields to 11, so replay can actually reproduce state. Fields include:
- `rules` snapshot at decision time
- `trade_history` window
- `current_regime`
- `market_snapshot`
- `rule_performance` rows
- `pnl_summary`
- `sector_performance`
- `time_patterns`
- `score_analysis`
- `bracket_analysis`
- `current_params`

If replay ever discovers that a stored context is missing a field the replay path needs, the answer is to expand `context_json` going forward, never to fabricate the missing field.

### Evaluation runs

`ai_evaluation_runs` + `ai_evaluation_slices` record the results of replay when it's used to benchmark a candidate against a baseline. Slices are per-dimension (by symbol, by regime, by hour-of-day, etc.). The evaluation response includes:
- `evaluation_run_id`
- `replay_mode` used
- `data_quality` (fresh / degraded / stale)
- `score_coverage` (how many candidate items have a scored counterpart)
- `calibration_context` (whether the scoring policy matches production)

These are required fields, not optional. A response missing them is a degraded response and must be marked as such in the API envelope.

## Consequences

### Positive
- Every AI decision has a full paper trail that a human can audit, diff, and re-run.
- The "why did the bot do X yesterday?" question has a DB-answerable form.
- Replay lets us compare models, prompt versions, and scoring policies without risking live state.
- The pending → applied discipline (HB1-06) guarantees the ledger reflects reality, not intent.

### Negative
- Ledger writes add DB load. A scan cycle with 19 rules can write 19+ item rows per iteration. At WAL + `synchronous=FULL`, each write is an fsync. Tested at current scale; will need revisiting if the rule count grows 10x.
- `context_json` grows over time. Future work: TTL-based archival of runs older than 90 days to a separate `ai_decision_runs_archive` table.
- Three replay modes force the API to accept a `mode` discriminator on every replay request. Early versions allowed a default mode; this was removed because it was the source of "I thought I was running a rule backtest" bugs.
- Replay correctness depends on `context_json` completeness. If the replay path needs a field that wasn't stored, the replay silently drifts from the original decision context. This is why `ai_replay.py` must reject incomplete contexts loudly, not infer.

### Rejected alternatives

**Alternative A: Log to files, not DB.** Rejected because (a) querying JSONL log files for "all decisions that touched rule X in the last 7 days" is painful, and (b) log rotation would silently drop history.

**Alternative B: Ledger inside the `ai_audit_log` table only.** Rejected because `ai_audit_log` is a flat event stream without relational structure. You can't cascade-delete children of a run, you can't query items by `item_type`, and you can't link a trade back to its originating decision cleanly.

**Alternative C: One replay mode that auto-detects intent.** Rejected because auto-detection is the source of the "metadata relabeling masquerades as replay" bug. Explicit modes force the caller to choose and the response to be honest about what was actually run.

**Alternative D: Replay mutates live state.** Rejected on principle. Replay is a read-mostly subsystem; the worst a replay should do is write new ledger rows with `status='replayed'`. Any live mutation must go through the normal `ai_optimizer → enforcer` path with full audit.

## Compliance notes

- `ai_decision_runs.context_hash` is computed in `context_utils.hash_context()` and used for dedup detection. Two runs with the same hash against the same prompt version should produce identical `stored_context_existing` replay output.
- `replay_scoring.py` owns the policy for how replayed items receive scores. It is the only module allowed to write to `ai_decision_items.score_source` and `.score_status`.
- The `evaluation_math.py` module owns metric formulas. No other module should have its own copy of Sharpe, expectancy, or hit-rate math. Phase 2.1 of Stage 2 review enforced this.
- Evidence-field requirements on the replay response are defined in `api_contracts.py::EvaluationRunResponse`. A response with `runs_evaluated: 0` and no `evaluation_run_id` is a failed replay and should return 500, not a 200 with empty slices.

## Links

- `backend/ai_decision_ledger.py` — run + item CRUD
- `backend/ai_replay.py` — three replay modes
- `backend/candidate_registry.py` — candidate resolution and generation
- `backend/rule_replay_adapter.py` — rule → backtester translation
- `backend/replay_scoring.py` — score assignment policy
- `backend/evaluation_query.py` — reusable query helpers
- `backend/evaluation_presenters.py` — response shaping (no business logic)
- `backend/context_utils.py` — stable context normalization and hashing
- `backend/evaluation_math.py` — metric formulas
- `backend/tests/test_ai_decision_ledger.py::test_hb1_05_decision_run_delete_cascades_to_items` — cascade guard
- `backend/tests/test_direct_ai_trader.py::test_hb1_06_failed_live_trade_does_not_mark_decision_item_applied` — pending → applied discipline guard
- `sessions/review-stage-2-ai-autopilot-correctness.md` — Stage 2 work that hardened replay semantics
- ADR 0001 (trade truth) — the decision ledger is part of the trade truth chain
