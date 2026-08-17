# TradeBot — Evidence-Gated Live-Execution Roadmap v2.1

**Date:** July 27, 2026  
**Release state:** **NO-GO for live autonomous trading**  
**Purpose:** Canonical roadmap for making one bounded TradeBot execution lane safe enough for an attended live canary and, only after additional evidence, bounded autonomy.  
**Document owner:** TBD  
**Risk approver:** TBD  
**Security approver:** TBD  
**Operations owner:** TBD

> This roadmap is a planning and release-governance document. It is not an
> authorization to enable `AUTOPILOT_MODE=LIVE`, use the existing live-flip
> runbook, or place a real-money canary.

---

## 1. Executive Summary

TradeBot has a substantial FastAPI, React/TypeScript, SQLite, and IBKR codebase.
The current working tree passes its automated test, typecheck, and build gates.
Those results establish a healthy development baseline; they do **not** establish
live-trading safety.

The prior roadmap overstated feature completeness and misidentified the live
critical path. The principal blockers are structural:

1. Entries do not receive confirmed broker-native protective orders.
2. Account-level daily-loss and drawdown thresholds are not automatically and
   durably latched from broker/account P&L.
3. Execution ownership, idempotency, and background-loop coordination are not
   safe across workers, replicas, crashes, or unknown broker outcomes.
4. Startup/reconnect reconciliation does not establish convergence across
   broker positions, attached/open/completed orders, executions, commissions,
   cash/margin, and local state.
5. The application has demo bootstrap authentication, no production RBAC or
   step-up authorization, and no distinct global execution-authority state.
6. Automatic rule promotion was wired into the optimizer; Stage 9A changed
   the scheduled optimizer to proposal-only, while production approval/RBAC
   remains open.
7. Operational metrics, structured logging, stale-data monitoring, backup
   scripts, and runbooks exist in pieces, but they are not fully wired,
   independently verified, or supported by completed drills.

Release progression is therefore:

`verified baseline → safety foundation → deterministic paper/fault validation → AI paper/copilot validation → attended one-intent live canary → one-variable-at-a-time expansion → bounded steady-state autonomy`

There is no calendar-based live-flip date. The low-latency screener and swing
screener completion are separate product tracks, not live-execution blockers.

### 1.1 Stage 9A Launch Checkpoint

The engineering program is active. Immediate containment now includes:

- a code-owned Stage 9A fence that rejects `AUTOPILOT_MODE=LIVE` and configured
  real-money broker access—including known live-port flag mismatches—at
  startup, DB-mode synchronization, and manual/runtime control boundaries;
- a real lifespan-held same-host/shared-volume execution lock, one-worker
  container defaults, and fatal topology/configuration validation;
- a SQLite rate cap exercised by synchronized independent processes, with
  blank/in-memory database configurations rejected at startup and use time;
- a hard `SIM_MODE` boundary that skips startup, reconnect, reconciliation,
  shutdown, and manual-connect IBKR calls;
- scheduled AI optimization operating proposal-only, with no automatic rule
  activation or direct-trade queueing;
- metrics unmounted by default, available only under the explicit isolated
  profile, without raw symbol labels; and
- historical LIVE-flip instructions visibly blocked.

These are containment controls, not Phase 1 completion. The ADRs remain
Proposed, human owners/approvers are unassigned, broker-native protection,
durable intent/UNKNOWN recovery, full reconciliation, continuous account risk,
production identity, and drills remain release blockers.

Latest Stage 9A engineering evidence on the dirty development tree:
**829 backend tests passed**, **364 frontend tests passed**, TypeScript
typecheck and the production build passed, and three independent reviews found
no unresolved Critical or High regression in the containment delta. Docker
image builds were not executed because the Docker daemon was unavailable.
This evidence does not change the LIVE **NO-GO** decision.

---

## 2. Verification Snapshot

### 2.1 Repository and Quality Gates

| Item | Verified Result |
|---|---|
| Branch | `feature/ultraplan-v4` |
| HEAD at review | `0bde712c01f3` |
| Backend tests | 72 test files; **784 passed** |
| Frontend tests | 24 test files; **364 passed** |
| TypeScript | `tsc --noEmit` passed |
| Frontend build | Vite 5.4.21 production build passed |
| Dashboard inventory | 15 pages, 20 component directories, about 135 component TSX files |
| Backend inventory | 93 top-level Python modules |

The worktree contained pre-existing uncommitted and untracked changes when these
commands ran. Consequently, this is an informational verification snapshot,
**not pinned release evidence**. A release candidate must be a clean commit with
CI artifacts and immutable deployment/configuration identifiers.

### 2.2 Current Mode Cannot Be Inferred from One Source

The repository's current `backend/.env` requests `AUTOPILOT_MODE=PAPER`, but the
guardrails row in SQLite can override the environment during startup. A roadmap
must not claim that the running system is OFF, PAPER, or LIVE without capturing
runtime status, broker account/port identity, configuration fingerprint, and
the deployed commit/image.

### 2.3 Actual Authority Semantics

| Mode | Current Code Behavior | Important Limitation |
|---|---|---|
| `OFF` | AI optimization does not recompute, and `bot_runner` skips new automated rule entries. | Manual order routes remain available. OFF is not a global execution kill switch. |
| `PAPER` | AI optimization runs. Bot and direct-AI paths create simulated paper trades/local positions rather than real IBKR orders. AI parameters remain shadowed outside LIVE. | PAPER is active autonomous simulation, not a passive observation mode and not a human-approval copilot mode. |
| `LIVE` | Code paths exist that could submit real IBKR orders if a future approved release removes the Stage 9A fence. | Startup, DB sync, mode API, broker startup, and manual connect currently reject LIVE/real-money configurations; actual broker account assertion remains unbuilt. |

`AUTOPILOT_MODE`, `IS_PAPER`, and `SIM_MODE` represent different authority and
broker dimensions. Every supported combination must be explicitly modeled and
tested. Invalid combinations must fail startup and fail again immediately
before submission.

### 2.4 Corrected Status Claims

| Area | Verified State |
|---|---|
| AI auto-tighten | Two underperformance levels plus recovery, not the previously documented three-level waterfall. It defaults OFF. Level 2 persists PAPER to DB and sets `ai_params.shadow_mode`, but does not synchronously update `cfg.AUTOPILOT_MODE`; the downgrade is not an atomic runtime safety transition. |
| AI failure breaker | Counts complete model-call chain failures. It does not detect poor trading decisions, execution anomalies, or P&L deterioration. |
| Rule promotion | The scheduled optimizer is proposal-only and no longer invokes automatic promotion or active Rule Lab mutation. Operator promotion still lacks production RBAC/step-up. |
| Decision ledger | Mutable lifecycle audit ledger, not immutable or append-only. |
| Swing screener | Qullamaggie and several technical sections are implemented; Minervini omits market cap; O'Neil is a stub; frontend mock fallback remains enabled. |
| Minimum order value | The `$100` check is not enforced by normal `place_order()` calls because preflight runs without `price_estimate`; this affects market and limit orders. |
| Daily loss/drawdown | Durable boolean entry blocking and manual reset exist. Automatic broker-P&L breach detection/latching is missing. Runtime defaults also conflict (`3%/18%` versus `2%/10%` in `RiskLimits`). |
| Cross-process rate limiting | The order path now uses the SQLite limiter and synchronized independent-process tests enforce one shared cap. This does not replace execution ownership, durable intent, or broker fencing. |
| Reconciliation | Pending local orders are compared with `openTrades()`. Completed offline executions and full broker-versus-local account state are not reconciled. |
| Authentication | Login UI ignores credentials and uses a demo bootstrap token. Bootstrap is disabled when LIVE. There is no production role or step-up authorization model. |
| WebSocket validation | Token/origin checks exist. Market-data inbound JSON lacks a bounded schema, message-size/rate limits, and per-client subscription caps. Validating only outbound broadcast dictionaries is not sufficient. |
| Observability | Data freshness, JSON logging, and Prometheus modules exist. Metrics are default-off and explicitly isolated when mounted; only selected callsites are wired. JSON logging and readiness/degradation coverage remain incomplete. |
| Runbooks/backups | Deployment, live-flip, rollback, paper-review, backup, and restore artifacts exist. Existence is not drill evidence, and live-flip instructions are suspended until this roadmap's pre-live gates pass. |
| Nights Watch | Quick wins, adaptive polling, order confirmation, indicator work, CORS/origin hardening, and the planned test additions are complete. They should not remain in the open work queue. |

### 2.5 Security Audit Re-baseline

The April 2026 list must not be implemented as thirteen unquestioned tickets:

- The alleged WebSocket authentication race is not demonstrated by current
  code. Tokens are validated before connection registration; rejected clients
  are accepted only to receive a close frame.
- An empty bootstrap secret already disables token issuance with HTTP 503.
- S10 and S13 duplicate the same retention identifier concern. Retention
  policies currently construct production table names internally, although an
  explicit identifier allowlist remains useful defense in depth.
- The listed 300/600 HTTP limits do not match current 300-general/10-auth
  middleware settings.
- Timing-safe bootstrap-secret comparison is still open in
  `routers/auth.py`.
- Production authentication, authorization, session security, high-risk action
  approval, inbound WebSocket limits, secrets/logging, dependency/container
  security, and deployment exposure require a fresh threat model.

The security gate is: **no unresolved Critical or High findings**, unless a
named independent security approver documents the residual risk, compensating
control, expiry, and owner. “Zero findings” is not a credible audit objective.

---

## 3. Objective, Scope, and Non-Goals

### 3.1 Objective

Deliver a bounded execution system that can prove, for a specific account,
strategy, instrument set, order policy, and deployment artifact, that:

- no entry bypasses authority, idempotency, data-health, risk, and protection
  checks;
- every filled exposure is broker-protected or automatically contained;
- unknown broker outcomes are reconciled before retry;
- account risk breaches durably produce an entry-locked/exit-only safe state;
- broker and local state converge after normal, failure, restart, and restore
  scenarios; and
- an operator can quickly reduce risk using both in-application and
  out-of-band broker controls.

### 3.2 Initial Launch Scope Must Be Narrow

Phase 0 must choose and sign:

- one approved IBKR account or capped-capital subaccount;
- one execution source and one strategy;
- long-only, liquid equities unless a separate instrument review approves more;
- explicit account, symbol, order-type, session, position, notional, turnover,
  and loss allowlists/ceilings;
- no margin unless separately approved;
- no extended-hours entry or overnight holding unless explicitly modeled and
  gap-stress tested; and
- a single live intent/position for the first canary.

### 3.3 Non-Goals

The following are not automatic destinations of this roadmap:

- “full universe” or “full notional”;
- extended-hours or options trading;
- automatic AI rule promotion;
- AI authority to loosen/cancel broker protection;
- tick-level screening of every symbol;
- “zero-delay” data delivery;
- home-grown production identity infrastructure without an approved ADR; or
- exactly-once claims that cannot be proven across the broker boundary.

The steady state remains bounded by permanent, versioned ceilings.

---

## 4. Non-Negotiable Safety Invariants

1. **One submission gateway:** every manual, rule, screener, TradingView,
   Claude, MCP, direct-AI, recovery, and resubmission path enters the same
   authority/idempotency/risk/protection gateway.
2. **Global execution authority is separate from AI mode:** AI OFF cannot be
   treated as a global kill switch. The system needs explicit states such as
   entry-disabled, paper-only, live-canary, live-bounded, exit-only, and
   flattening.
3. **Broker truth dominates:** broker account, positions, orders, executions,
   and commissions are authoritative. Disagreements lock new entries; they are
   never silently adopted or discarded.
4. **Reconcile before retry:** after an unknown submit result, timeout, crash,
   or reconnect, the system must query broker truth before another adapter call.
5. **Protection follows filled quantity:** every filled share must have an
   acknowledged broker-native protective order. A submitted child is not proof
   of protection.
6. **No silent unprotected exposure:** if protection cannot be acknowledged
   within a policy-defined interval, the system cancels remaining entry
   quantity and follows a tested cancel/flatten/reconcile policy.
7. **Risk state is durable:** loss, integrity, and reconciliation locks survive
   process restart and cannot be cleared by time rollover alone.
8. **De-risking stays available:** safety locks block risk-increasing actions
   while permitting authenticated, verified reduce-only actions. Protective
   orders are not accidentally cancelled during a pause.
9. **AI cannot weaken deterministic protection:** AI may propose changes.
   Tightening, if eventually authorized, passes deterministic bounds and a
   separate evidence gate. AI cannot loosen/cancel the hard stop.
10. **No silent model behavior change in LIVE:** model, prompt, schema, routing,
    and fallback policy are versioned. An unavailable approved model disables AI
    actions unless the alternate path was separately validated and approved.
11. **Material changes reset affected evidence:** code, configuration, model,
    prompt, dependency, broker/TWS version, account, strategy, and protection
    changes invalidate the relevant soak/canary evidence.
12. **Kill and rollback are explicit actions:** pause entries, cancel working
    entries, enter exit-only, and flatten managed exposure are distinct
    procedures with separate authorization and tests.

---

## 5. Phase 0 — Decisions, Baseline, and Governance

No implementation estimate or live forecast is credible until these decisions
are recorded.

### 5.1 Required ADRs

| ADR | Decision |
|---|---|
| Execution authority | Global state machine, source permissions, entry/exit semantics, and one shared submission gateway |
| Execution ownership | Single executor topology, lease/fencing policy, replica/worker behavior, and broker client identity |
| Durable intent model | Intent ID, atomic claim, state transitions, broker `orderRef`, retry/reconciliation rules, and restore behavior |
| Protection policy | Supported instruments/order types, parent/child transmit policy, stop type, RTH/GTC/outside-RTH behavior, partial fills, gaps, and containment |
| Broker/local truth | Reconciliation scope, overlap window/high-water marks, discrepancy quarantine, readiness SLA, and operator resolution |
| Account risk | Equity baseline, account/currency scope, realized/unrealized P&L, fees, peak persistence, session timezone, external trades, warning/lock/flatten thresholds, and reset policy |
| Authentication boundary | Local-only/VPN versus remotely exposed deployment; identity provider, MFA/session model, roles, route/WS policy, and high-risk step-up approval |
| Data policy | Approved live sources, staleness thresholds, event-time rules, fallback policy, and which degradation states block entries |
| Release evidence | Clean commit/image/config/dependency/model/prompt/broker identifiers, evidence storage, approvers, and invalidation rules |

### 5.2 Phase 0 Deliverables

- Pin a clean baseline commit and CI run.
- Record sanitized configuration and deployment fingerprints.
- Record the approved broker account identifier and paper/live environment
  checks without storing credentials.
- Define the initial risk envelope and one-intent canary policy.
- Produce a threat model before authentication/security implementation.
- Assign a DRI, reviewer, risk approver, security approver, release authority,
  and on-call operator to every P0 work package.
- Define the deterministic and statistical validation plan before observing
  results; sample requirements must follow strategy frequency and confidence
  goals, not a convenient fixed trade count.
- Select one canonical roadmap/runbook set. Mark conflicting live-flip
  documents as historical or blocked.

### 5.3 Phase 0 Exit Gate

- All ADRs approved.
- Initial residual-risk register signed.
- Clean baseline and evidence manifest captured.
- Pre-live test/fault matrix approved.
- No ambiguity about account, execution source, instrument/session scope,
  authority owner, or rollback owner.

---

## 6. Phase 1 — Pre-Live Safety Foundation

### SF1 — Shared Execution Authority and Gateway

**Current gap:** AI mode is being used as a partial authority control, while
manual and other sources have different safety flags and paths.

**Required outcome:**

- Introduce an explicit execution-authority state independent of
  `AUTOPILOT_MODE`.
- Route every order source through one gateway.
- Apply account/source/symbol/order/session allowlists, risk-increasing versus
  reduce-only classification, data health, risk state, intent state, and
  protection policy at that gateway.
- Define global emergency actions without making legitimate exits impossible.
- Prove that no route or internal call can bypass the gateway.

**Evidence:** route/call-graph audit; negative tests for every source; tests that
entry-disabled and exit-only states survive restart; tests proving protective
orders are preserved during pause/cancel procedures.

### SF2 — Durable Intent, Idempotency, and Execution Ownership

**Current gap:** in-process dedup/rate state remains in the live order path;
`db/rate_limits.py` is not integrated; every Uvicorn worker can start lifespan
background loops; `orderRef` provides correlation but not safe retry semantics.

**Required outcome:**

- Enforce one execution owner immediately: one replica, `WORKERS=1`, and startup
  refusal when the deployed topology violates the approved profile.
- Add a durable executor lease with fencing so a stale owner cannot submit after
  leadership changes.
- Give each intent a stable idempotency key and atomic claim.
- Add database uniqueness/transition constraints and an outbox/state-machine
  boundary around broker submission.
- Use broker `orderRef` for reconciliation.
- Represent `SUBMITTING/UNKNOWN` explicitly; never retry UNKNOWN until broker
  reconciliation proves that no order/execution exists.
- Integrate the cross-process rate limiter as defense in depth.
- Test concurrent calls, separate processes, executor failover, timeouts, and
  crashes before/after every persistence and adapter boundary.

The goal is at-most-once intent handling plus reconcile-before-retry behavior,
not an unsupported “exactly once” claim.

### SF3 — Broker-Native Protective Orders

**Current gap:** entries submit only market/limit orders. Stops exist only in
local position logic.

**Required outcome:**

- Define a supported parent/child or bracket state machine.
- Acknowledge broker-native protection for 100% of cumulative filled quantity.
- Handle parent partial fills, child sizing, child rejection, cancel/replace,
  order modification, late fills, reconnect, restart, and broker/TWS restart.
- Prevent child transmit/order-ID races.
- If protection cannot be confirmed, cancel unfilled entry quantity and execute
  the approved containment policy for filled exposure.
- Reconcile parent and all children continuously.
- Clearly document that stop orders reduce but do not eliminate gap, halt, and
  slippage loss.

A take-profit child is optional unless the strategy requires it; protective
stop coverage is mandatory.

### SF4 — Continuous Durable Account-Risk Controller

**Current gap:** a persisted daily-loss flag can block automated entries and can
be reset manually, but no production loop automatically derives and latches it
from authoritative account P&L. Drawdown and daily-risk defaults conflict.

**Required outcome:**

- Consolidate risk limits into one validated configuration.
- Continuously compute the approved loss/drawdown measures using broker
  NetLiquidation, realized and unrealized P&L, commissions/fees, currency
  conversion, external/manual activity, and the approved session boundary.
- Persist the session baseline, peak, current state, inputs, and trigger reason.
- On breach, atomically enter `ENTRY_LOCKED/EXIT_ONLY`, cancel risk-increasing
  working entries, and preserve protective/reduce-only exits.
- Fail closed for new automated entries when account/risk data is stale,
  non-finite, unavailable, or inconsistent.
- Require reconciliation, authenticated step-up, cause note, and applicable
  cooldown before reset. Do not auto-clear solely at UTC midnight.
- Define warning, entry-lock, and flatten thresholds from the approved risk
  envelope; do not hard-code the roadmap's example percentages or dollars.

### SF5 — Broker/Local Reconciliation and Trading Readiness

**Current gap:** pending records are compared only with `openTrades()`.

**Required outcome:**

- Verify approved broker account identity before any trading readiness.
- Reconcile positions, cash, NetLiquidation/margin, open and attached orders,
  completed orders, executions/fills, commissions, cumulative quantities,
  protective quantities, and local trades/positions.
- Use execution overlap windows and durable high-water marks so offline fills
  are not missed.
- Classify broker-only/manual/external activity; quarantine mismatches rather
  than silently creating or deleting local records.
- Keep liveness available while trading readiness is false.
- Block new entries until discrepancies are resolved within the approved SLA or
  explicitly transferred to a named operator.
- Run reconciliation on startup, reconnect, periodically, before retrying
  UNKNOWN, before live arming, and after restore.

### SF6 — AI Governance Corrections

**Required outcome:**

- Remove or disable scheduled automatic rule promotion in LIVE.
- Make promotion a versioned proposal with out-of-sample evidence and explicit
  authorized approval.
- Raise the current five-trade promotion default only after a statistical design
  defines an appropriate sample and multiple-testing controls.
- Make auto-tighten mode downgrade atomic across DB configuration, runtime
  configuration, parameter authority, direct-candidate processing, and the
  execution gateway.
- Separate breakers for model availability, malformed output, stale data,
  execution rejection/duplication, reconciliation mismatch, abnormal turnover
  or slippage, account loss, and loop health.
- Do not describe three failed model calls as protection from bad decisions.

### SF7 — Production Authentication and Security

**Required outcome:**

- Choose an established, MFA-capable identity/session design after the Phase 0
  threat model. Do not assume a custom JWT blacklist is the correct solution.
- Remove demo bootstrap authentication from the production profile.
- Apply least-privilege roles to HTTP and WebSocket operations.
- Require step-up authorization for LIVE arming, risk increases, protective
  policy changes, kill/loss-lock reset, and other high-risk actions.
- Keep immediate pause/de-risk actions fast; they must not wait for dual
  approval.
- Add secure session storage/cookies or equivalent controls, TLS/proxy trust,
  CSRF protections where relevant, token/session revocation, secret rotation,
  and sanitized audit events.
- Add bounded inbound WebSocket schemas, message-size/rate limits, action
  allowlists, and subscription caps.
- Replace ordinary bootstrap-secret comparison with timing-safe comparison.
- Re-audit logging/error paths, dynamic identifiers, CORS/origin production
  profiles, CSP, dependencies, containers, and network exposure.

### SF8 — Operational Observability and Safe Degradation

**Required outcome:**

- Wire and protect the existing Prometheus router.
- Instrument the actual order gateway, fills, rejects, protection state,
  UNKNOWN intents, reconciliation, risk state, data staleness, broker
  disconnects, mode/authority changes, and background-loop ownership.
- Activate structured JSON logging and correlation/intent IDs in the production
  profile.
- Define alerts, owners, escalation paths, and an out-of-band page for
  unprotected exposure, reconciliation failure, risk-lock activation, stale
  required streams, execution-owner loss, DB/disk failure, and broker mismatch.
- Turn stale/invalid required data into explicit safe degradation; do not
  silently substitute Yahoo or another unapproved source for live automation.
- Monitor clock synchronization, DB integrity/space, backup age, and broker
  connectivity.

### SF9 — Backup, Restore, Kill, and Rollback Drills

**Required outcome:**

- Audit the existing backup/restore scripts and define RPO and RTO.
- Prove restore integrity and post-restore broker reconciliation.
- Ensure a restored trading DB cannot restore stale live authority or replay an
  already-consumed intent.
- Exercise four distinct operator actions: pause entries, cancel working
  entries, enter exit-only, and flatten managed positions.
- Confirm protective orders are retained until exposure is closed or
  replacement protection is accepted.
- Document and rehearse an out-of-band IBKR/TWS/mobile procedure.
- Measure detection, entry-stop, reconciliation, and rollback times.

### Phase 1 Exit Gate

- Every SF work package has a DRI, independent reviewer, evidence artifact, and
  approved residual risk.
- No open Critical/High security or trading-safety finding without signed,
  expiring risk acceptance.
- Production-like PAPER startup proves the approved deployment/account/config
  identity and refuses all unsafe combinations.
- All automated tests and quality gates pass on a clean release candidate.
- No live authorization is granted yet.

---

## 7. Phase 2 — Deterministic Paper and Fault Validation

Use the same execution gateway, broker adapter, intent state machine,
protection logic, reconciliation, risk controller, and deployment topology
planned for live. Application-only synthetic fills may supplement tests but
cannot be the sole evidence for broker behavior.

### 7.1 Required Validation Matrix

| Scenario | Required Assertion |
|---|---|
| Normal entry/fill/exit | Intent, broker, protection, position, cash, fees, and ledger reach the expected terminal states |
| Parent partial fill | Protective quantity follows cumulative filled quantity; no share remains unprotected |
| Child reject/inactive | Remaining entry is cancelled and filled exposure follows the containment policy |
| Crash before/after intent persist | Restart produces one durable intent and no duplicate adapter call |
| Crash/timeout around broker submit | Intent becomes UNKNOWN; reconciliation precedes any retry |
| Late fill after cancel | Position/protection are detected and reconciled; no false flat state |
| Concurrent duplicate requests | One intent wins; other calls are rejected or join the same outcome |
| Worker/replica failover | Fencing prevents stale owner submission; new owner reconciles first |
| IBKR/TWS disconnect/restart | Subscriptions and state recover; entries remain locked until reconciliation passes |
| Stale, NaN, out-of-order, or missing data | Automated entries fail closed and alert; deterministic protection remains |
| Loss/drawdown breach | Entry lock latches, working entries are cancelled, protective/reduce-only exits remain |
| DB lock/unavailable/disk pressure | New risk fails closed; existing broker protection and operator alerts remain |
| Mode/account/port/config mismatch | Startup and pre-submit refuse authority |
| Restore from older backup | Restore generation/evidence prevents intent replay and stale authority |
| Market open/close, DST, holiday, halt/gap | Session and protection policies behave as approved |
| Malformed/timeout/model outage | AI actions stop safely without changing to an unapproved behavior |

### 7.2 Evidence Rules

- Freeze and identify the release candidate, configuration, dependencies,
  strategy, model/prompt/schema, broker/TWS version, and test environment.
- Predeclare required natural-event samples and statistical tolerances based on
  expected strategy frequency. Do not generate trades merely to hit a roadmap
  count.
- Execute every deterministic state transition and fault scenario.
- Cover the relevant market-session boundaries and approved operating window.
- Record zero prohibited actions escaping controls. Correctly blocked unsafe
  attempts are expected evidence.
- Resolve every broker/local discrepancy within the approved readiness SLA; a
  transient detected mismatch is not itself failure, but an unresolved or
  silently ignored mismatch is.
- Demonstrate no duplicate broker submission for one intent and no unprotected
  filled quantity outside the approved containment interval.
- Any material artifact change resets affected evidence.

### Phase 2 Exit Gate

- Validation matrix complete with machine-readable and human-reviewed evidence.
- Risk, protection, reconciliation, and recovery drills pass.
- RPO/RTO and stop/rollback timings meet the approved Phase 0 policy.
- Release authority signs PAPER reliability only; live remains disarmed.

---

## 8. Phase 3 — AI PAPER and Copilot Validation

### 8.1 PAPER Evaluation

Current PAPER mode is autonomous simulation: the optimizer runs and local paper
trades can be created. Use it to evaluate decision quality, calibration,
economic attribution, and rule behavior without real broker exposure.

Required evidence:

- walk-forward/out-of-sample evaluation with realistic costs and fill
  assumptions;
- model, prompt, schema, context, data, and routing versions on every decision;
- confidence calibration and abstention behavior;
- outcome attribution that separates AI decisions from baseline strategy
  behavior;
- operator review of a predeclared sample selected without cherry-picking;
- automatic promotion disabled; and
- malformed/output/model outage tests.

### 8.2 Copilot Requires an Explicit New Gate

PAPER is not a live-broker human-approval mode. If copilot behavior is desired,
implement a separate proposal/approval state:

- proposal cannot itself create a broker intent;
- approval is authenticated, scoped, expiring, and bound to exact symbol,
  side, quantity, prices, protection, account, strategy, model/prompt version,
  and risk snapshot;
- material market/risk changes expire the approval;
- the shared execution gateway revalidates everything at submission; and
- AI stop proposals cannot loosen or cancel mandatory broker protection.

Phase 3 may run alongside later Phase 2 observation only after the Phase 1
foundation exists and only in an environment incapable of live submission.

### Phase 3 Exit Gate

- Statistical plan and decision-quality tolerances pass.
- Safety controls reject all deliberately unsafe AI proposals.
- No automatic rule promotion or unapproved fallback behavior remains.
- Risk owner approves only the exact AI authority proposed for the canary.

---

## 9. Phase 4 — Attended One-Intent Live Canary

This phase requires explicit user/release-authority approval after Phases 0–3.
It is not triggered by elapsed time.

### 9.1 Canary Constraints

- Dedicated capped-capital/no-margin account or the closest broker-supported
  equivalent approved in Phase 0.
- Exact account, one strategy/source, one allowlisted liquid instrument, one
  direction, one order type, and one session policy.
- RTH-only entry unless separately approved.
- One expiring, one-use live intent authorization; broad unattended LIVE
  authority is not the canary mechanism.
- One position maximum.
- Quantity/notional/risk are the strictest of absolute, percent-equity,
  stop-distance, liquidity, and gap-stress ceilings from the signed policy.
- Broker-native protection must be acknowledged for filled quantity.
- Human operator and release authority present for the complete intent,
  protection, closeout, and reconciliation lifecycle.
- Out-of-band broker cancellation/flattening path ready.
- AI rule promotion, strategy swapping, stop loosening, and unrelated execution
  sources disabled.

### 9.2 Canary Safe-State Triggers

Immediately disarm new entries and follow the tested containment policy for:

- unprotected filled quantity;
- UNKNOWN or duplicate intent/order state;
- broker/local reconciliation mismatch;
- stale/unavailable required account, risk, market, or broker streams;
- execution-owner/fencing failure;
- protection or execution rejection bursts;
- account/margin/risk breach;
- configuration/artifact/account identity mismatch;
- DB integrity/storage failure; or
- unauthorized mode, policy, model, prompt, or strategy change.

“Three AI failures” is not a trading-safety trigger. Model-call availability
uses its own breaker; performance deterioration uses a predeclared
confidence-based review/pause rule.

### 9.3 Canary Exit Gate

- Intent, orders, protection, fills, fees, position, cash, and audit state are
  terminal and reconciled.
- Stop/rollback timing and operator actions are recorded.
- No unsafe action escaped controls.
- All anomalies have disposition and named owners.
- Independent execution, risk, security, and operations reviewers sign the
  evidence.

A successful canary proves only the tested mechanics for that exact artifact and
scope. It does not prove profitability or authorize broad expansion.

---

## 10. Phase 5 — One-Variable-at-a-Time Expansion

Each cohort changes only one dimension:

- capital/risk ceiling;
- number of positions;
- instrument allowlist;
- strategy/source;
- session/holding policy; or
- AI authority.

For every cohort:

1. issue a new versioned authorization and risk envelope;
2. repeat affected fault/reconciliation/protection tests;
3. observe the predeclared natural-event/statistical sample;
4. compare execution quality, slippage, risk, reconciliation, and operator load
   with the prior cohort;
5. retain automatic safe-state triggers and permanent hard ceilings; and
6. roll back to a safer authority state on breach—do not merely change a phase
   label while orders/positions remain.

Extended hours, overnight exposure, options, leverage, “full universe,” and
unbounded notional require separate risk cases. They are not automatic rungs.

---

## 11. Phase 6 — Bounded Steady-State Autonomy

There is no “full autonomy” state without limits.

The approved steady state specifies:

- exact accounts, strategies, sources, instruments, order types, sessions, and
  holding periods;
- permanent per-intent, per-position, gross/net exposure, turnover, margin,
  daily-loss, drawdown, and concentration ceilings;
- deterministic broker protection and continuous risk/reconciliation loops;
- exact AI actions allowed, actions requiring approval, and actions prohibited;
- model/prompt/schema/fallback versions;
- alert/on-call/incident SLOs;
- periodic access, security, model, strategy, and risk reviews; and
- automatic downgrade/entry-lock triggers.

Human approval remains mandatory for rule promotion until a separate,
independently reviewed evidence package authorizes a narrower alternative.

---

## 12. Parallel Product Tracks

### 12.1 Low-Latency Screener

This track may run only when it cannot consume the execution team's critical
capacity or destabilize shared IBKR/data infrastructure.

Dependency order:

1. benchmark current source-to-screen latency and capacity;
2. document account-specific IBKR entitlements, pacing, and subscription limits;
3. define event-time/bar-close semantics, data quality, backpressure, reconnect,
   resubscription, and degraded-source behavior;
4. extract and validate bounded WebSocket subscription/message contracts;
5. build IBKR scan-to-WS diff delivery;
6. add filter-stack subscriptions and alert unification;
7. add incremental indicator state with warmup/restart parity tests; and
8. consider sub-minute/tick screening only after measured capacity and a
   business case.

SLOs must specify p50/p95/p99, measurement boundaries, universe size,
concurrent clients/subscriptions, market-data entitlements, market session, and
failure behavior. Targets are set after the baseline experiment, not copied
from aspirational estimates.

### 12.2 Swing Screener Completion

| Task | Exit Evidence |
|---|---|
| Minervini market-cap enrichment | Correctness fixtures, missing-data policy, cache/source behavior |
| O'Neil fundamentals | Source contract, freshness, point-in-time/no-lookahead policy, tests |
| Breadth/industry completeness | Declared universes, duplicate/missing symbol handling, tests |
| Remove mock fallback | Backend availability/error UI tests; no silent production mocks |
| Optional real-time updates | Separate measured SLO and capacity evidence |

Neither parallel track is a prerequisite for the one-intent live canary.

---

## 13. Delivery Model, Ownership, and Estimates

### 13.1 Required Accountability

Every work package has:

- one DRI;
- one code reviewer;
- one independent risk or security approver where applicable;
- one operations/runbook owner;
- dependencies and blocked-by fields;
- invariant and acceptance tests;
- evidence artifact location;
- rollback/failure action;
- residual risk and expiry; and
- person-day estimate with confidence and assumptions.

For a single-developer project, execution may be sequential, but Critical
trading-safety and security work still requires independent review before live.

### 13.2 Initial Discovery Ranges

These are low-confidence **engineer-day** ranges, not elapsed-time commitments.
They include implementation, tests, documentation, review fixes, and deployment
integration, but exclude observation time and remediation discovered by the
threat model. Re-estimate after Phase 0.

| Package | Initial Range | Confidence |
|---|---:|---|
| Phase 0 ADRs, threat model, baseline, test design | 5–10 | Low |
| Shared gateway, authority, durable intent, fencing/idempotency | 12–25 | Low |
| Broker-native protection state machine | 8–15 | Low |
| Continuous account-risk controller | 8–15 | Low |
| Full reconciliation/readiness barrier | 10–20 | Low |
| AI governance corrections | 3–8 | Medium |
| Authentication/security | 5–30+ | Very low; depends on local/VPN vs remote/IdP scope |
| Observability, alerts, backup/restore, operational drills | 8–15 | Low |
| Deterministic fault harness and evidence automation | 10–20 | Low |
| AI evaluation/copilot approval path | 7–15 | Low |
| Low-latency screener track | Re-estimate after capacity benchmark | Unknown |

The proposed four-person model can parallelize threat-informed auth,
execution/protection, risk/reconciliation, and operations work after ADRs.
With one developer, complete the live safety path before starting low-latency
product work.

### 13.3 Repository Stage Protocol

Each implementation stage follows the repository protocol:

1. read/create the stage session prompt;
2. implement in dependency order with quality gates every five edits;
3. run typecheck, build, backend tests, and frontend tests;
4. obtain required code/risk/security review;
5. commit with the conventional format and stage reference;
6. generate the dated handoff;
7. update `learning-log.md`; and
8. run the session wrap-up.

No stage is complete merely because code exists; its evidence, operations
artifact, and handoff must also be complete.

---

## 14. Risk Register

Probabilities remain **Unknown** until evidence supports a rating.

| Risk | Probability | Impact | Primary Trigger | Mitigation / Contingency | Owner |
|---|---|---|---|---|---|
| Unprotected or under-protected fill | Unknown | Critical | Filled qty exceeds acknowledged protective qty | Cancel remaining entry; tested containment/flatten; entry lock | TBD |
| Duplicate/unknown broker order | Unknown | Critical | Duplicate intent/orderRef or submit timeout | Fence executor; UNKNOWN state; reconcile before retry | TBD |
| Broker/local state divergence | Unknown | Critical | Positions/orders/executions/cash mismatch | Trading readiness false; quarantine and reconcile | TBD |
| Loss controller misses or misstates breach | Unknown | Critical | Stale/invalid P&L, baseline, fees, FX, external trade | Independent continuous controller; fail closed; alerts | TBD |
| Configuration/account/port authority error | Unknown | Critical | Runtime fingerprint differs from signed profile | Startup and pre-submit refusal | TBD |
| Operator or credential compromise | Unknown | Critical | Unauthorized LIVE/reset/risk increase | MFA, least privilege, step-up, revocation, audit | TBD |
| Stale/bad market or account data | Unknown | High | Freshness/integrity threshold breached | Entry lock; no unapproved live fallback | TBD |
| Broker/TWS outage, pacing, or client-ID drift | Unknown | High | Disconnect/reject/pacing events | Reconcile, resubscribe, safe degradation, escalation | TBD |
| SQLite lock, corruption, disk full, or stale restore | Unknown | High | DB/storage health or generation mismatch | Fail closed; backup/restore/fence controls | TBD |
| Model/prompt/schema/vendor drift | Unknown | High | Artifact mismatch or output degradation | Version pin, validation, disable AI actions | TBD |
| Strategy deterioration/overfit | Unknown | High | Confidence-based performance threshold | Pause cohort; out-of-sample review; no auto-promotion | TBD |
| Parallel screener overload affects execution | Unknown | High | Shared resource latency/backpressure | Isolate capacity; prioritize execution; circuit break product track | TBD |

---

## 15. Gate Card and Evidence Template

Every release gate is recorded as:

| Field | Required Content |
|---|---|
| Gate ID and invariant | Exact safety/property claim |
| Scope | Commit/image/config/account/strategy/model/prompt/broker versions |
| Environment | Simulator, IBKR paper, or explicitly authorized live canary |
| Preconditions | Dependencies and authority state |
| Test/scenario | Normal, boundary, fault, and recovery steps |
| Threshold/SLA | Predeclared measurable condition and denominator |
| Evidence | Machine-readable report, logs, broker export, screenshots if needed |
| DRI/reviewer/approver | Named people, not only roles |
| Failure action | Entry lock, rollback, containment, investigation owner |
| Residual risk | Description, compensating control, owner, expiry |
| Evidence invalidators | Changes that require repetition |

Green unit tests are necessary but not sufficient. A gate cannot be satisfied by
elapsed time, UI visibility, or a test count alone.

---

## 16. Key Code and Operations References

| File | Relevance |
|---|---|
| `backend/config.py` | Broker/simulation/autopilot/risk configuration |
| `backend/main.py` | Startup, background loops, WS endpoints, DB mode sync |
| `backend/startup.py` | Current mode/broker/auth matrix validation |
| `backend/order_executor.py` | Preflight, broker submission, pending reconciliation |
| `backend/db/rate_limits.py` | Unintegrated cross-process rate limiter |
| `backend/safety_kernel.py` | AI authority, risk budget, kill/loss checks |
| `backend/risk_config.py` / `risk_manager.py` | Conflicting risk sources and unused live checks |
| `backend/bot_exits.py` / `position_tracker.py` | Application-local exit logic |
| `backend/ai_optimizer.py` | Optimizer, rule/direct actions, current auto-promotion call |
| `backend/ai_learning.py` | Auto-tighten implementation and incomplete runtime downgrade |
| `backend/rule_validation.py` | Five-trade default validation and auto-promotion |
| `backend/routers/auth.py` | Demo bootstrap and ordinary secret comparison |
| `backend/autopilot_api.py` | Mode/kill/loss-reset actions without role separation |
| `backend/metrics.py` / `log_config.py` | Existing but incompletely wired observability |
| `dashboard/src/components/auth/LoginPage.tsx` | Credentials ignored in demo login flow |
| `backend/swing_screeners.py` | Minervini partial, O'Neil stub |
| `dashboard/src/hooks/useSwingDashboard.ts` | Production mock fallback still enabled |
| `docs/LIVE_FLIP_RUNBOOK.md` | Suspended until this roadmap's pre-live gates pass |
| `scripts/backup_db.sh` / `restore_db.sh` | Existing backup/restore helpers requiring drill evidence |

---

## 17. Document Control

- **Version:** 2.0
- **Date:** July 27, 2026
- **Replaces:** `ROADMAP_TEAM_PLAN.md` v1.0
- **Live decision:** NO-GO
- **Canonical status:** This roadmap controls planning; it does not supersede a
  future signed release authorization.
- **Existing live-flip instructions:** Suspended until Phases 0–3 pass and the
  exact Phase 4 one-intent canary is separately approved.
- **Next review:** Phase 0 completion or any material change to execution,
  account, broker, risk, authority, authentication, or protection design.

