AI AUTOPILOT — STAGE 2 AI / AUTOPILOT CORRECTNESS
==================================================
DATE: 2026-03-27
STATUS: READY AFTER STAGE 1 BOUNDARIES START TO EXIST
OWNER: AUTOPILOT / AI TEAM
GOAL: Make AI decision generation, replay, evaluation, scoring, and learning modular, explainable, and safe to trust.

PURPOSE
-------
Stage 2 exists because the AI/autopilot system is now powerful enough that architectural ambiguity is operational risk.

Current pain points:
- Replay, evaluator, optimizer, and learning are still partially coupled.
- Some Stage 10 helper seams exist, but the code is not fully decomposed.
- Operator evidence is still not strong enough in every path.
- Ledger-backed truth exists, but not every user-facing conclusion cites it clearly.

STAGE 2 IS NOT:
---------------
- More AI authority by default
- New speculative AI features
- Fancy UI without better evidence underneath

STAGE 2 IS:
-----------
- Correctness hardening
- Explainability
- Explicit replay semantics
- Stronger operator evidence

GLOBAL EXIT GATE
----------------
Stage 2 is complete only when:
[ ] replay, evaluator, optimizer, and learning do not depend on each other’s private helpers
[ ] replay modes are explicit and test-covered
[ ] rule replay fails closed without replay metadata
[ ] learning/economic metrics are ledger-backed by default
[ ] operator-facing evidence includes run IDs, mode, data quality, and score coverage

================================================================
PHASE 2.1 — FINISH HELPER EXTRACTION
================================================================
SCOPE
- Complete the helper/module extraction started in Stage 10 work

FILES
- [ ] backend/ai_optimizer.py
- [ ] backend/ai_replay.py
- [ ] backend/ai_evaluator.py
- [ ] backend/ai_learning.py
- [ ] backend/ai_guardrails.py
- [ ] backend/ai_decision_ledger.py
- [ ] backend/evaluation_math.py
- [ ] backend/context_utils.py
- [ ] backend/decision_item_factory.py
- [ ] backend/candidate_registry.py
- [ ] backend/rule_replay_adapter.py
- [ ] backend/replay_scoring.py
- [ ] backend/evaluation_query.py
- [ ] backend/evaluation_presenters.py

TASKS
1) Confirm helper ownership
   [ ] `evaluation_math.py` owns metric formulas only.
   [ ] `context_utils.py` owns stable context normalization and hashing.
   [ ] `decision_item_factory.py` owns item normalization from AI payloads.
   [ ] `candidate_registry.py` owns candidate resolution and generation.
   [ ] `rule_replay_adapter.py` owns replayability checks and rule-to-backtest translation.
   [ ] `replay_scoring.py` owns score-state assignment policy.
   [ ] `evaluation_query.py` owns reusable filtering/query helpers.
   [ ] `evaluation_presenters.py` owns response shaping, not business logic.

2) Remove duplicated formulas and transforms
   [ ] Eliminate duplicated metric math.
   [ ] Eliminate duplicated context hashing.
   [ ] Eliminate duplicated item-shaping logic.

DELIVERABLE
- [ ] Shared logic lives in stable helper modules instead of being reimplemented across AI modules.

================================================================
PHASE 2.2 — REMOVE PRIVATE CROSS-MODULE IMPORTS
================================================================
SCOPE
- Stop AI modules from reaching into each other’s private internals

FILES
- [ ] backend/ai_replay.py
- [ ] backend/ai_optimizer.py
- [ ] backend/ai_evaluator.py
- [ ] backend/ai_learning.py

TASKS
1) Replay import cleanup
   [ ] Remove replay -> optimizer private helper imports.
   [ ] Replace them with helper-module imports.

2) Evaluator import cleanup
   [ ] Remove evaluator -> replay private helper imports.
   [ ] Replace them with shared helpers.

3) Learning import cleanup
   [ ] Ensure learning reads ledger/evaluation outputs, not runtime internals.

4) Add explicit module boundaries
   [ ] Keep generation logic in generation modules.
   [ ] Keep scoring logic in scoring modules.
   [ ] Keep presentation logic out of core runtime modules.

DELIVERABLE
- [ ] No Stage 2 module needs another module’s private helper to do its job.

================================================================
PHASE 2.3 — REPLAY SEMANTICS HARDENING
================================================================
SCOPE
- Make replay behavior explicit, real, and safely bounded

FILES
- [ ] backend/ai_replay.py
- [ ] backend/candidate_registry.py
- [ ] backend/rule_replay_adapter.py
- [ ] backend/autopilot_api.py
- [ ] backend/api_contracts.py

TASKS
1) Explicit replay modes
   [ ] Keep three modes only:
       - stored_context_existing
       - stored_context_generate
       - rule_backtest
   [ ] Remove ambiguous internal behavior.

2) Candidate generation integrity
   [ ] Ensure prompt/model comparison actually generates candidate items from stored contexts.
   [ ] Do not treat metadata relabeling as a replay result.

3) Rule replay fail-closed behavior
   [ ] Reject non-replayable rules explicitly.
   [ ] Return a reason instead of inventing exit semantics.

4) Replay request validation
   [ ] Ensure filters like window, symbols, action types, and minimum confidence are actually honored.
   [ ] Keep API response clear about what was evaluated and what was excluded.

DELIVERABLE
- [ ] Replay modes are explicit and operator-safe.

================================================================
PHASE 2.4 — LEDGER-BACKED LEARNING ONLY
================================================================
SCOPE
- Make ledger-backed evaluation the default truth for AI performance and economics

FILES
- [ ] backend/ai_learning.py
- [ ] backend/ai_decision_ledger.py
- [ ] backend/ai_evaluator.py
- [ ] backend/autopilot_api.py

TASKS
1) Learning metrics source of truth
   [ ] Ensure `/learning-metrics` reads decision runs, decision items, realized outcomes, and evaluation slices.
   [ ] Remove hidden fallback to heuristic audit-log windows as if equivalent.

2) Economic report source of truth
   [ ] Ensure cost reporting is model-aware.
   [ ] Ensure ROI reporting is evaluation-backed, not rough proxy-only.

3) Degraded-mode truthfulness
   [ ] If fallback data is used, mark the response degraded.
   [ ] Do not present degraded metrics as if they are fully evaluated.

DELIVERABLE
- [ ] Learning and economic endpoints tell the truth about how certain the numbers are.

================================================================
PHASE 2.5 — OPERATOR EVIDENCE QUALITY
================================================================
SCOPE
- Improve the evidence operators see in API responses and UI payloads

FILES
- [ ] backend/autopilot_api.py
- [ ] backend/api_contracts.py
- [ ] dashboard/src/types/advisor.ts
- [ ] dashboard/src/pages/AutopilotPage.tsx
- [ ] dashboard/src/components/autopilot/*

TASKS
1) Evidence fields
   [ ] Include evaluation run IDs where relevant.
   [ ] Include replay mode used.
   [ ] Include data quality / degraded status.
   [ ] Include score coverage.
   [ ] Include calibration or confidence context where relevant.

2) Compare/baseline clarity
   [ ] Make baseline vs candidate comparison easier to interpret.
   [ ] Ensure operators can tell whether an evaluation result came from realized outcomes, replay scoring, or proxy scoring.

3) Promotion/shadow evidence
   [ ] Ensure promotion/shadow-to-live gates can cite real evaluation artifacts.

DELIVERABLE
- [ ] Operator-facing AI evidence is strong enough to support real intervention decisions.

================================================================
PHASE 2.6 — TESTS AND SAFETY GATE
================================================================
SCOPE
- Lock Stage 2 behavior with targeted tests

FILES
- [ ] backend/tests/test_ai_decision_ledger.py
- [ ] backend/tests/test_ai_replay.py
- [ ] backend/tests/test_ai_evaluator.py
- [ ] backend/tests/test_ai_learning.py
- [ ] backend/tests/test_rule_replay_adapter.py
- [ ] backend/tests/test_candidate_registry.py
- [ ] dashboard/src/components/autopilot/__tests__/*

TASKS
1) Replay tests
   [ ] stored_context_existing never calls generation path
   [ ] stored_context_generate calls candidate generation path
   [ ] rule_backtest rejects non-replayable rules

2) Evidence tests
   [ ] evaluation responses include expected evidence fields
   [ ] learning/economic responses surface degraded status when appropriate

3) Integration tests
   [ ] decision_run -> decision_item -> trade/rule linkage remains intact
   [ ] realized trade outcomes flow back into item scoring

VALIDATION COMMANDS
- [ ] `cd backend && python -m pytest tests -v`
- [ ] `cd dashboard && npm run typecheck`
- [ ] `cd dashboard && npm run build`
- [ ] `cd dashboard && npx vitest run`

STAGE 2 FINAL CHECKLIST
-----------------------
[ ] Phase 2.1 complete
[ ] Phase 2.2 complete
[ ] Phase 2.3 complete
[ ] Phase 2.4 complete
[ ] Phase 2.5 complete
[ ] Phase 2.6 complete

Once all boxes are checked, Stage 2 is DONE and the AI/autopilot layer becomes modular and evidence-driven instead of coupled and partially opaque.
