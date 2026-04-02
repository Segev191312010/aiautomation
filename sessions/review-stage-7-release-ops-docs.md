AI AUTOPILOT — STAGE 7 RELEASE, OPS, AND DOCUMENTATION DISCIPLINE
==================================================================
DATE: 2026-03-27 (updated 2026-03-31 with audit findings)
STATUS: TREAT AS RELEASE GATE, NOT OPTIONAL CLEANUP

AUDIT UPDATE (2026-03-31)
-------------------------
Full codebase audit found 4 issues in this stage. 0 fixed, all OPEN.
See: `sessions/audit-findings-2026-03-31.md` (section: STAGE 7)

OPEN (CRITICAL for LIVE deployment):
  - F7-01: Auth effectively unauthenticated — demo user fallback, no auth on mutating routes
           (auth.py:111, routers/auth.py:16, autopilot_api.py:126, routers/orders.py:37)
           Mitigated: running on localhost only. MUST fix before remote access.
  - F7-02: Hardening middleware (rate limit, security headers) exists but never mounted
           (middleware.py:25, main.py:296)
  - F7-03: No API rate limiting on any endpoint
  - F7-04: Bootstrap/placeholder files in shipping tree (_write_files.py, _bootstrap.py)
OWNER: CORE TEAM + OPS OWNERS
GOAL: Make the platform operable, releasable, and handoff-safe for a team instead of depending on tribal knowledge.

PURPOSE
-------
Stage 7 exists because a system can be technically powerful and still be unsafe to operate if its decisions, modes, release steps, and rollback paths are undocumented.

STAGE 7 IS NOT:
---------------
- A docs-only vanity pass
- Optional polish after “real work”

STAGE 7 IS:
-----------
- Release discipline
- Operational clarity
- Better handoff
- Safer intervention and rollback behavior

GLOBAL EXIT GATE
----------------
Stage 7 is complete only when:
[ ] key architectural decisions are written down
[ ] release checks are explicit and repeatable
[ ] rollback and intervention paths are documented
[ ] code ownership and debugging entry points are clearer
[ ] another engineer could operate the platform without relying on tribal memory

================================================================
PHASE 7.1 — ARCHITECTURE DECISION RECORDS (ADRs)
================================================================
SCOPE
- Write the design decisions that future work must not quietly violate

FILES
- [ ] docs/adr/0001-trade-truth.md
- [ ] docs/adr/0002-autopilot-mode-semantics.md
- [ ] docs/adr/0003-decision-ledger-and-replay.md
- [ ] docs/adr/0004-degraded-data-and-fallback-policy.md
- [ ] docs/adr/0005-simulation-vs-broker-vs-autopilot-authority.md

TASKS
[ ] Write the problem statement for each ADR.
[ ] Record the chosen policy.
[ ] Record tradeoffs and rejected alternatives.
[ ] Link each ADR to the stage/spec that established it.

DELIVERABLE
- [ ] Core platform semantics are written down and reviewable.

================================================================
PHASE 7.2 — RELEASE CHECKLIST
================================================================
SCOPE
- Define the minimum safe checklist before backend/frontend release or runtime rollout

FILES
- [ ] docs/release-checklist.md
- [ ] README.md or docs/operations.md

TASKS
[ ] Include backend tests.
[ ] Include frontend typecheck/build/tests.
[ ] Include DB migration safety checks.
[ ] Include startup validation.
[ ] Include broker mode confirmation.
[ ] Include autopilot authority confirmation.
[ ] Include degraded/fallback review for operator-facing surfaces.
[ ] Include rollback owner and rollback path.

DELIVERABLE
- [ ] Releases stop depending on memory or ad hoc rituals.

================================================================
PHASE 7.3 — ROLLBACK AND INTERVENTION RUNBOOKS
================================================================
SCOPE
- Document what operators do when live behavior is wrong or risky

FILES
- [ ] docs/runbooks/autopilot-intervention.md
- [ ] docs/runbooks/trading-runtime-recovery.md
- [ ] docs/runbooks/data-provider-degraded-mode.md
- [ ] docs/runbooks/replay-evaluation-interpretation.md

TASKS
[ ] Document reconnect behavior.
[ ] Document daily loss lock and kill switch usage.
[ ] Document how to disable or degrade autopilot safely.
[ ] Document how to interpret replay/evaluation outputs.
[ ] Document what signals mean “stop trusting this subsystem”.

DELIVERABLE
- [ ] High-risk intervention paths are written and usable.

================================================================
PHASE 7.4 — OWNERSHIP AND HANDOFF NOTES
================================================================
SCOPE
- Make it easier for a small team to know what belongs together and how to debug it

FILES
- [ ] docs/ownership.md
- [ ] docs/debugging/*.md (new, as needed)
- [ ] README.md or CONTRIBUTING.md

TASKS
[ ] Group codebase areas by domain ownership.
[ ] Document where temporary scripts belong.
[ ] Add short “how to debug this domain” notes for:
    - trading runtime
    - autopilot / AI
    - analytics / diagnostics
    - market data / stock profile
    - frontend operator pages

DELIVERABLE
- [ ] Another engineer can enter a subsystem without hunting for tribal knowledge first.

================================================================
PHASE 7.5 — FINAL HANDOFF PACKAGE
================================================================
SCOPE
- Make the repo shippable as a maintained system

FILES
- [ ] sessions/review-roadmap-index.md
- [ ] stage files in sessions/
- [ ] docs/adr/*
- [ ] docs/runbooks/*
- [ ] docs/release-checklist.md
- [ ] docs/ownership.md

TASKS
[ ] Ensure the roadmap, ADRs, runbooks, and ownership docs reference each other cleanly.
[ ] Ensure stale docs are removed or clearly deprecated.
[ ] Ensure the current baseline and operating mode semantics remain easy to find.

VALIDATION COMMANDS
-------------------
[ ] `cd backend && python -m pytest tests -v`
[ ] `cd dashboard && npm run typecheck`
[ ] `cd dashboard && npm run build`
[ ] `cd dashboard && npx vitest run`

STAGE 7 FINAL CHECKLIST
-----------------------
[ ] Phase 7.1 complete
[ ] Phase 7.2 complete
[ ] Phase 7.3 complete
[ ] Phase 7.4 complete
[ ] Phase 7.5 complete

Once all boxes are checked, Stage 7 is DONE and the platform becomes maintainable and operable by a team instead of only by the current owner.
