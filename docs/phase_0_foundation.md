# Phase 0 — Reproducible Foundation and Paper Smoke

**Status:** Ready to execute  
**Estimated duration:** 1–2 focused weeks; evidence gates, not elapsed time, decide completion  
**Branch at planning time:** `feature/ultraplan-v4`  
**Release authority:** SIM and IBKR PAPER only  
**LIVE status:** **NO-GO** — the Stage 9A real-money fence must remain in force

## 1. Mission

Turn the current working repository into a clean, reproducible development
baseline and prove one honest end-to-end SIM/PAPER workflow:

```text
start application
→ authenticate locally
→ observe explicit runtime/broker/readiness state
→ load a real or deterministic market-data fixture
→ submit one test intent
→ pass or fail deterministic safety checks
→ persist the resulting paper state
→ display the result and audit trail
→ restart without loss or duplication
```

Stage 0 consolidates and characterizes the systems already present. It does not
replace working subsystems merely to introduce a new directory layout.

## 2. Starting facts

At plan creation, the dirty development tree has the following informational
baseline:

- Backend: **829 tests passed**.
- Dashboard: **364 tests passed** across 24 files.
- Dashboard TypeScript typecheck: passed.
- Dashboard production build: passed.
- The working tree contains substantial modified and untracked work.
- `aiautomation/`, `aiautomation-pr3/`, `stocksdashboard/`, and `frontend/`
  exist beside the canonical `dashboard/`.
- Existing code already includes a safety kernel, risk management, persisted
  guardrails, kill controls, an IBKR client, reconnect behavior, market-data
  WebSockets, order reconciliation, rate limiting, and a mature chart module.

These results are not release evidence until reproduced from a clean,
identified commit and configuration.

## 3. Non-negotiable invariants

1. Do not enable `AUTOPILOT_MODE=LIVE`, configure a real-money broker account,
   remove the Stage 9A fence, or submit a real-money order.
2. Supported validation targets are `SIM_MODE=true` and an explicitly asserted
   IBKR paper account only.
3. Preserve all existing and nested-repository work before moving or excluding
   it. Do not delete unknown files.
4. Broker truth must never be replaced with fabricated data. Deterministic
   fixtures must be visibly labeled as SIM/test data.
5. Safety locks block risk-increasing entries while authenticated reduce-only
   and protective actions remain conceptually distinct. Stage 0 must not
   introduce a blanket "block all orders" rule.
6. Do not create a second safety kernel, gateway singleton, WebSocket bus, or
   chart implementation without an approved disposition proving replacement is
   necessary.
7. Do not perform the broad `backend/tradebot/` package migration in Stage 0.
8. Do not replace `lightweight-charts`; it is already TradingView Lightweight
   Charts and is used throughout the dashboard.
9. Every five file edits, run the smallest relevant quality checkpoint. Run all
   project quality gates before the stage commit.
10. Preserve unrelated user changes and keep every commit single-purpose.

## 4. Out of scope

The following are deliberately deferred:

- LIVE or attended-live canary execution.
- Broker-native bracket/protection redesign.
- Durable intent/`UNKNOWN` implementation beyond characterization and a
  follow-up design contract.
- Cross-host execution leases or production replica topology.
- Continuous account-level loss/drawdown controller.
- Production identity, RBAC, MFA, or step-up authorization.
- Full broker/local reconciliation redesign.
- Large backend package reorganization.
- Market page redesign or chart-library replacement.
- New screener, AI, analytics, or strategy features.

These become later stages after the baseline and paper workflow are proven.

## 5. Required deliverables

### D0.1 Repository disposition manifest

A tracked document must record every pre-existing modified, deleted, and
untracked path and classify it as one of:

- Stage 9A intended change;
- earlier product work;
- generated artifact;
- nested repository/reference frontend;
- local configuration;
- unknown — requires owner decision.

For each duplicate/reference frontend, capture:

- path and size;
- whether it is a Git repository or linked worktree;
- branch, HEAD, status, and remotes where applicable;
- unique commits or files relative to the canonical repository;
- references from active source/build configuration;
- preservation location and recovery procedure;
- final disposition: retain, archive, ignore, or separately track.

No directory may be moved until its preservation evidence exists. Moving
directories, if approved by that evidence, must be a dedicated commit and must
not use a broad command whose targets have not been individually validated.

### D0.2 Clean reproducible baseline

Create an isolated clean branch/worktree from an explicitly recorded base
commit. Apply only reviewed, intentional changes. The baseline record must
contain:

- commit SHA and branch;
- Python and Node versions;
- dependency lockfile identity;
- sanitized configuration fingerprint;
- database schema/version identity;
- exact gate commands and results;
- Docker image build result;
- known warnings and open failures.

Do not claim the baseline is clean while nested repositories, local secrets,
generated databases, or unrelated edits appear in `git status`.

### D0.3 Existing-system architecture matrix

Create `docs/architecture/stage-0-current-system-matrix.md` with these sections:

1. **Execution sources:** manual, bot/rule, direct AI, TradingView, Claude/MCP,
   recovery, exit, cancel, and replace paths.
2. **Broker mutation inventory:** every `placeOrder`, `cancelOrder`, connect,
   disconnect, and account-selection callsite.
3. **Safety controls:** current implementation, persistence, callers,
   fail-open/fail-closed behavior, and known overlap.
4. **Database ownership:** relevant tables, writers, readers, migrations, and
   retention/recovery behavior.
5. **Runtime modes:** tested matrix for `AUTOPILOT_MODE`, `IS_PAPER`, and
   `SIM_MODE`.
6. **Data path:** IBKR/Yahoo/fixture source → cache/API/WebSocket → stores → UI.
7. **Health path:** liveness, dependency health, broker status, data freshness,
   and trading readiness.
8. **Known gaps:** map each gap to the residual-risk register and a later stage.

The matrix must distinguish verified code behavior from documentation claims.

### D0.4 Safe startup profiles

Define and test two explicit development profiles:

#### SIM profile

- `SIM_MODE=true`.
- No IBKR connect, reconnect, reconcile, disconnect, or broker mutation.
- Test/fixture data is labeled visibly in API responses and the dashboard.
- The application can complete the golden smoke workflow offline.

#### IBKR PAPER profile

- `SIM_MODE=false`.
- `IS_PAPER=true`.
- `AUTOPILOT_MODE` is `OFF` or `PAPER` according to the tested workflow.
- The paper port is validated.
- The connected account identifier is asserted against an explicit sanitized
  allowlist or expected value before trading readiness becomes true.
- A live-port mismatch, real-money account, unknown account, or ambiguous
  account state fails closed.

The configuration layer must continue to reject LIVE and known real-money
combinations at startup and immediately before broker mutation.

### D0.5 Honest health and readiness contract

The application must expose and document separate states for:

- process liveness;
- database health;
- authentication readiness;
- market-data readiness and freshness;
- broker connection and asserted environment/account;
- execution ownership;
- safety/risk lock state;
- overall trading readiness.

`HTTP 200` process health must not imply that trading is ready. Each degraded
state needs a machine-readable reason code and an operator-facing message.

### D0.6 Deterministic end-to-end smoke workflow

Implement one integration test that uses the existing application surfaces and
proves:

1. Start with an empty temporary database.
2. Initialize the schema successfully.
3. Authenticate through the supported local development flow.
4. Confirm the SIM profile and non-LIVE fence.
5. Load deterministic, clearly labeled fixture bars/quote data.
6. Submit one idempotent test intent through an existing public API boundary.
7. Exercise the real deterministic safety decision path.
8. Persist the simulated/paper result and audit event.
9. Read the result through the API used by the dashboard.
10. Restart the application against the same temporary database.
11. Confirm the intent/trade/position is neither lost nor duplicated.

The test must not patch away the safety kernel, persistence layer, or public API
contract it claims to validate. Mocking is allowed only at the external broker
or external market-data boundary.

### D0.7 Operator startup and smoke documentation

Update the relevant README/deployment documentation with:

- prerequisites;
- exact SIM startup command;
- exact IBKR PAPER startup command;
- environment variable descriptions using the application's real names;
- expected health/readiness responses;
- smoke-test procedure;
- common failure explanations;
- explicit LIVE prohibition;
- shutdown and recovery steps.

Do not include real credentials, account identifiers, tokens, or `.env`
contents.

## 6. Ordered implementation work

### Work package A — Preserve and classify

1. Read `LEARNED.md`, the latest handoff, residual-risk register, and Stage 9A
   ADRs.
2. Capture `git status`, branch, HEAD, remotes, and diff statistics.
3. Inspect every duplicate/reference frontend individually.
4. Search active code and build files for dependencies on those directories.
5. Produce D0.1 without moving anything.
6. Stop for an owner decision on any path classified `unknown`.

**Gate A:** disposition manifest reviewed; every prospective move recoverable.

### Work package B — Establish a clean baseline

1. Select and record the base commit.
2. Isolate intentional Stage 9A/product changes from unrelated work.
3. Create the clean implementation branch/worktree.
4. Apply reviewed changes in coherent groups.
5. Run the complete gates, including Docker builds.
6. Record immutable results in the baseline evidence document.

**Gate B:** clean status, reviewed diff, all required gates green.

### Work package C — Characterize before changing

1. Produce the execution and broker-call inventories.
2. Map safety/risk controls and database ownership.
3. Map data, WebSocket, health, and chart behavior.
4. Verify the runtime-mode matrix through existing tests and targeted additions.
5. Identify duplicated responsibilities and choose one canonical owner for each.
6. Create follow-up tickets/stage references for unresolved safety risks.

**Gate C:** D0.3 is complete and every proposed Stage 0 code change maps to a
verified gap.

### Work package D — Safe startup and readiness

1. Add profile-level validation without introducing a second config system.
2. Make SIM broker isolation observable and testable.
3. Add paper-account assertion at the broker boundary if missing.
4. Separate liveness from trading readiness.
5. Surface readiness reason codes in the dashboard.
6. Add regression tests for every invalid configuration and degraded state.

**Gate D:** SIM starts without broker calls; PAPER requires an asserted paper
account; LIVE and ambiguous configurations fail closed.

### Work package E — Golden smoke path

1. Choose one existing public submission path with the least authority.
2. Build deterministic external-boundary fixtures.
3. Add the empty-database/startup/API/persistence/restart integration test.
4. Add a small dashboard contract/component test showing the resulting state.
5. Ensure fixture and broker-derived states cannot be visually confused.
6. Document and manually rehearse the SIM smoke workflow.

**Gate E:** D0.6 passes repeatedly without network or IBKR access.

### Work package F — Closeout

1. Run all quality gates from the clean candidate.
2. Run `git diff --check` and review the complete diff.
3. Run a secrets scan if the approved tool is available.
4. Create a conventional commit with an allowed scope.
5. Generate the Stage 0 handoff.
6. Update `learning-log.md` with facts, caveats, and exact test totals.
7. Mark residual risks only as characterized or mitigated when linked evidence
   supports that status. Do not mark LIVE risks closed from SIM tests.

## 7. Test and quality protocol

### Focused checkpoints

After at most five edited files, run the smallest relevant subset, such as:

```bash
cd backend && .venv/bin/python -m pytest tests/test_startup_config.py -v
cd dashboard && npm run typecheck
```

### Full stage gates

```bash
cd backend && .venv/bin/python -m pytest tests/ -v
cd dashboard && npm run typecheck
cd dashboard && npm run build
cd dashboard && npx vitest run
git diff --check
```

Also run both repository-defined Docker image builds. If Docker is unavailable,
Stage 0 is not complete; record the blocker rather than representing the gate as
passed.

Existing React `act(...)` warnings must either be fixed in touched tests or
recorded with a tracking ID. Test warnings are not silently discarded.

## 8. Acceptance criteria

Stage 0 is complete only when all of the following are true:

- [ ] Every initial dirty/untracked path has an explicit disposition.
- [ ] Duplicate/reference frontends are preserved and recoverable.
- [ ] A clean candidate commit and sanitized configuration are identified.
- [ ] Backend, frontend, typecheck, build, diff, and Docker gates pass.
- [ ] The current-system architecture matrix is code-verified.
- [ ] All broker mutation callsites are inventoried.
- [ ] The SIM profile performs zero IBKR lifecycle or mutation calls.
- [ ] The PAPER profile requires confirmed paper environment/account identity.
- [ ] LIVE and real-money configurations remain rejected.
- [ ] Health and trading readiness are separate and machine-readable.
- [ ] An empty database initializes successfully.
- [ ] The deterministic golden smoke workflow passes through restart.
- [ ] No intent/trade/position is lost or duplicated in that workflow.
- [ ] Fixture data is visibly distinguishable from broker data.
- [ ] Operator documentation matches commands verified during the stage.
- [ ] A reviewed conventional commit, handoff, and learning-log entry exist.

## 9. Stop conditions

Stop implementation and request owner direction if:

- unique or unpushed work cannot be safely preserved;
- the selected baseline excludes changes whose ownership is unclear;
- a test requires weakening the Stage 9A release fence;
- SIM unexpectedly performs an IBKR call;
- an IBKR connection cannot prove paper-account identity;
- a proposed change broadens execution authority;
- unrelated dirty changes overlap required files and cannot be isolated;
- a destructive cleanup operation would be required;
- full gates regress and the root cause is outside Stage 0 scope.

## 10. Follow-on stages

Stage 0 creates evidence and a dependable paper foundation. Recommended next
stages are:

1. **Stage 1 — Unified execution authority and durable intent:** one mutation
   gateway, typed provenance, `UNKNOWN`, reconcile-before-retry, architecture
   enforcement.
2. **Stage 2 — Broker protection and fill correctness:** protective orders for
   every filled quantity, partial/late-fill handling, exit/stop race control.
3. **Stage 3 — Broker-truth reconciliation and account risk:** full state
   convergence, durable loss/drawdown controller, fail-closed risk state.
4. **Stage 4 — Production identity and operational controls:** RBAC, step-up,
   inbound limits, observability, backup/restore, operator drills.
5. **Stage 5 — Multi-session paper soak:** failure injection plus evidence-bound
   paper sessions.

None of these stages implicitly authorizes LIVE. A later attended canary needs
its own approved scope, clean artifact, evidence contract, and explicit human
authorization.

