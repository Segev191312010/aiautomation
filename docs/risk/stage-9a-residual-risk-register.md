# Stage 9A Residual-Risk Register

**Status:** Open / unsigned  
**Date:** 2026-07-27  
**Overall release decision:** **NO-GO**

Probability is intentionally recorded as `Unknown` until baseline/fault data
exists. No row may be closed by code presence alone; closure requires linked
test/drill evidence and a named approver.

| ID | Risk | Severity | Current evidence | Required treatment / closure evidence | Owner | Status |
|---|---|---|---|---|---|---|
| R01 | Duplicate broker effect after timeout/crash/retry | Critical | `orderRef` only; submit exception becomes `ERROR` | Durable intent/`UNKNOWN`, reconcile-before-retry, crash matrix | Unassigned | Open |
| R02 | Multiple workers/clients execute simultaneously | Critical | `WORKERS=1`, Docker defaults, and lifespan-held file lock have local tests | Prove one deployed replica/fixed client, then durable cross-host lease/fencing | Unassigned | Partially mitigated |
| R03 | Direct submit/cancel bypasses future gateway | Critical | Four `placeOrder` callsites and direct cancels | Private adapter and static architecture test | Unassigned | Open |
| R04 | Filled exposure lacks broker-native hard stop | Critical | Naked entries; local loop only | Paper-proven bracket/protection coverage for every fill | Unassigned | Open |
| R05 | Partial fill/exit hides residual position | Critical | Partial cancelled exit can become full local close | Execution-derived quantities and partial/late-fill tests | Unassigned | Open |
| R06 | Stop and discretionary exit race reverses exposure | Critical | Independent local exit path | Unified intent/OCA/protection graph and race tests | Unassigned | Open |
| R07 | Broker/local drift permits unsafe entry | Critical | Reconcile uses recent `openTrades()` only | Full account/order/execution/commission reconciliation + readiness | Unassigned | Open |
| R08 | Reaper destroys ambiguous submission evidence | Critical | Reap runs before async reconcile | `UNKNOWN` migration/quarantine and startup ordering evidence | Unassigned | Open |
| R09 | Wrong account/client/environment used | Critical | Port/mode checks; client-ID fallback | Exact account assertion, fixed client, conflict/degraded-sync refusal | Unassigned | Open |
| R10 | Daily loss/drawdown breach is not latched | Critical | No active controller; conflicting defaults | Signed policy and continuous durable controller fault tests | Unassigned | Open |
| R11 | DB/risk-state failure fails open | Critical | Guardrail loader can return unlocked defaults | Explicit unavailable state; entry-lock tests on DB faults | Unassigned | Open |
| R12 | Emergency flatten overcloses/reverses/cancels protection | Critical | Uses local positions/direct submit | Broker-truth gateway containment with paper drills | Unassigned | Open |
| R13 | Manual route bypasses safety authority | High | `require_autopilot_authority=False` | Independent execution authority and role/risk checks | Unassigned | Open |
| R14 | Double safety evaluation self-rejects some live paths | High | Rule/proposal/HTTP direct AI check twice | Pure policy + one atomic gateway reservation, E2E contracts | Unassigned | Open |
| R15 | Scheduled AI promotion activates unsafe rule | Critical | Scheduler invokes proposal-only mode; runtime tests prove no Rule Lab/trade queue call | Human version approval plus production RBAC/step-up still required | Unassigned | Partially mitigated |
| R16 | AI/MCP proposal not bound to reviewed signal | Critical | Arbitrary payload possible | Immutable candidate/approval/intent binding tests | Unassigned | Open |
| R17 | Development auth enables high-risk remote actions | Critical | Bootstrap/demo JWT, no RBAC/revocation | Approved OIDC/session/RBAC/step-up architecture and tests | Unassigned | Open |
| R18 | Browser bundle/local storage leaks credentials | Critical | Build-time bootstrap and localStorage token | Prohibit release secret, HttpOnly session, artifact canary scan | Unassigned | Open |
| R19 | Control plane exposed over LAN/HTTP | Critical | Compose host binding and cleartext nginx | Loopback/VPN-only reachability and TLS tests | Unassigned | Open |
| R20 | Webhook/proxy identity spoof or replay | High | Secret/IP/freshness/event dedup | Isolated ingress and trusted-proxy/replay tests | Unassigned | Open |
| R21 | WebSocket resource/entitlement exhaustion | High | No frame/rate/subscription budgets | Limits/backpressure/load tests | Unassigned | Open |
| R22 | Stale/fallback data drives execution | Critical | Freshness mostly observational | Consumer source/age policy in gateway, boundary tests | Unassigned | Open |
| R23 | Health/readiness falsely indicates safe trading | Critical | DB/liveness checks; connection-age heartbeat | Separate trading readiness and supervised-task fault tests | Unassigned | Open |
| R24 | Metrics/logging fail to expose incident or leak data | High | Route default-off; explicit isolated profile; rate-cap symbol label removed | Isolated-listener deployment proof, real lifecycle instrumentation, redaction tests | Unassigned | Partially mitigated |
| R25 | Restore replays consumed intent or revives LIVE | Critical | Integrity-only restore | External generation, disarm, full reconcile, fresh approval drill | Unassigned | Open |
| R26 | Backup absent/stale/unrecoverable | High | Manual local script | Scheduled encrypted off-host backup and oldest/latest restore evidence | Unassigned | Open |
| R27 | Mutable/unscanned artifact changes behavior | Critical | CI unit/build only | Locked inputs, SBOM/scans, signed digest/evidence enforcement | Unassigned | Open |
| R28 | Current baseline includes unrelated dirty work | High | Baseline manifest records dirty tree | Isolated clean commit and immutable build from reviewed delta | Unassigned | Open |
| R29 | Broker partial-child semantics differ from assumption | Critical | Conflicting interface documentation; no contract test | Exact-version paper partial-fill activation evidence | Unassigned | Open |
| R30 | Market gap/halt/slippage exceeds stop/canary loss | Critical | No stop can guarantee price | Signed exposure ceilings, gap policy, operator acceptance | Unassigned | Open |
| R31 | Stage NO-GO is bypassed by mode or configured real-money broker access | Critical | Code-owned fence rejects LIVE, `IS_PAPER=false`, and known live-port mismatches across startup/runtime/manual connect | Replace only through reviewed release change after all mandatory approvals/evidence | Unassigned | Contained, not closed |
| R32 | Order-source provenance is collapsed or accepts untyped values | High | `Trade.source` has a narrow enum while callers include scanner/TV/Claude names; unsafe widening was reverted | Approve one typed provenance vocabulary across persistence, API, analytics, and frontend; add E2E tests | Unassigned | Open |

## Acceptance Rules

- `Critical` risks affecting idempotency, protection, reconciliation, account
  risk, authentication, or restore replay cannot be accepted for LIVE.
- A partially mitigated row remains blocking unless the residual is explicitly
  outside the proposed canary scope and signed by risk/security/release owners.
- Test evidence must identify clean commit, image/config/schema, broker/account
  environment, and exact software versions.
- A material code/config/model/broker/deployment change reopens affected rows.
- “Tests pass” without a relevant failure injection does not close a risk.

## Immediate Control Evidence

| Control | Evidence | Limitation |
|---|---|---|
| Shared per-symbol rate cap | SQLite transaction, synchronized eight-PID cap, and held-lock bounded-failure test | Does not provide intent idempotency or prove deployment storage semantics |
| Single local execution owner | Startup fatal check, Docker defaults, process lock held by actual lifespan | Cross-host replicas/fixed client/durable lease still unproven |
| Scheduled optimizer proposal-only | Runtime scheduler and no-mutation tests | Human approval lacks production RBAC/step-up |
| Metrics default-deny | Profile registration and no-symbol-label tests | Isolated listener/network and lifecycle callsites incomplete |
| Stage 9A real-money fence | Matrix, lifecycle, and API tests cover LIVE, real-broker flags, and known live ports | Actual broker account identity/custom-port truth remains R09; code change can remove fence |

