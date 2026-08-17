# Stage 9A Handoff — Live-Safety Foundation

**Date:** 2026-07-27  
**Branch:** `feature/ultraplan-v4`  
**Starting HEAD:** `0bde712c01f3cc16f45c1e36a21d2fcac7fa3f8a`  
**Engineering stage:** Complete  
**Live release decision:** **NO-GO**

## Outcome

The evidence-gated engineering program has been launched. Stage 9A adds
development-time containment and governance without enabling real-money
trading. A code-owned fence rejects LIVE mode and configured real-money broker
access. Removing that fence is not authorized by this handoff.

No application lifespan was started, no broker connection or broker-state
mutation was attempted, no order was submitted/cancelled/replaced, and no
runtime mode, broker setting, database mode, or `.env` value was changed.

## Delivered

### Governance and evidence

- Canonical evidence-gated roadmap, with prior LIVE-flip material marked
  historical/blocked.
- Proposed ADRs for execution authority/intent, broker
  protection/reconciliation/account risk, and security/data/release evidence.
- Threat model, unsigned residual-risk register, pre-live fault matrix, and a
  sanitized development-baseline manifest.
- Human owners and approvers remain explicitly unassigned; no draft is
  represented as accepted.

### Immediate containment

- Code-owned LIVE and configured-real-money fence at startup, persisted-mode
  synchronization, runtime mode API, broker startup, manual connect, and the
  IBKR client boundary.
- Known live-port/paper-flag mismatches rejected. SIM mode performs no IBKR
  connect/reconnect/disconnect work.
- Fatal startup validation for unsafe worker topology, weak auth/bootstrap
  combinations, and blank/in-memory SQLite.
- A same-host/shared-volume execution lock held for the actual FastAPI
  lifespan, with one-worker container and deployment defaults.
- SQLite-backed cross-process per-symbol order cap. Lock contention uses a
  bounded retry window and blocks the order path closed.
- Scheduled AI optimization is proposal-only: no scheduled active-rule
  mutation, automatic promotion, or direct-trade queueing.
- Metrics are unmounted by default and can only be mounted under the explicit
  isolated profile; the new cap counter has no raw-symbol label.
- Regression tests characterize the legacy orphan reaper honestly as unsafe
  rather than treating its current behavior as idempotent.

## Verification

The final gates were run against the stabilized dirty development tree:

| Gate | Result |
|---|---|
| Focused containment suite | 84 passed |
| Full backend pytest | **829 passed** |
| Dashboard TypeScript typecheck | passed |
| Dashboard production build | passed |
| Dashboard Vitest | **364 passed** |
| `git diff --check` | passed |
| Independent code review | no unresolved Critical/High regression |
| Independent security review | no unresolved Critical/High regression |
| Independent broker/risk review | no unresolved Critical/High regression |

One earlier focused test overlapped another agent's run and collided through
repository-local temporary SQLite files. The affected tests, the complete
focused suite, and the full backend suite subsequently passed in isolation.
This exposes test-harness isolation debt; it is not being counted as a product
test pass or failure.

Docker image builds were not run because the Docker daemon socket was
unavailable. Therefore the complete six-step quality-gate script has **not**
passed and must not be represented as complete release evidence.

## Remaining LIVE blockers

Stage 9A contains immediate hazards; it does not make the system live-ready.
The unsigned risk register remains authoritative. Major blockers include:

- no single execution gateway covering every submit/cancel source;
- no durable intent state machine, `UNKNOWN` outcome handling, or
  reconcile-before-retry guarantee;
- no cross-host lease/fencing or proven fixed broker-client ownership;
- no broker-native protection for every filled quantity, including
  partial/late-fill and replacement races;
- incomplete broker truth reconciliation and unsafe legacy orphan
  terminalization;
- no continuous durable account-loss/drawdown controller;
- no asserted broker account/client identity, especially for custom ports;
- no proven reduce-only/flatten semantics or production execution authority;
- no production identity, RBAC, step-up authorization, or complete
  HTTP/WebSocket boundary proof;
- incomplete data-readiness, observability, restore, backup, artifact/SBOM,
  paper-broker, soak, and operator-drill evidence; and
- order-source provenance remains untyped/inconsistent.

R31 (the Stage 9A release fence) is contained, not closed. Risks R01–R30 and
R32 remain open or partially mitigated as recorded in the residual-risk
register.

## Source-control state

No commit or tag was created. The repository already contained modified and
untracked user work, including files that overlap this stage. Committing the
mixed tree would falsely attribute unrelated changes and would not create
immutable release evidence.

Before the next implementation stage:

1. Assign human architecture, risk, security, operations, and release owners.
2. Review and approve—or revise—the proposed ADRs and pre-live evidence
   contract.
3. Isolate the Stage 9A delta into a clean branch/worktree and rerun every gate,
   including both Docker builds.
4. Create a reviewed conventional commit and immutable build evidence.
5. Implement Phase 1 in order: shared execution authority/gateway, then durable
   intent/UNKNOWN recovery and deployment-grade ownership fencing.

Do not begin a live canary, remove the real-money fence, or follow a historical
LIVE-flip runbook at this point.

