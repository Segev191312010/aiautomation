# Stage 9A Threat Model

**Status:** Draft for security/risk approval  
**Date:** 2026-07-27  
**System status:** Isolated development; remote/public deployment NO-GO

## Scope

This model covers the browser dashboard, nginx, FastAPI HTTP/WebSocket
interfaces, TradingView webhook, background workers, AI decision paths,
SQLite/volumes/backups, IBKR, Yahoo/other market data, Anthropic/model calls,
CI/build artifacts, configuration, logs, and operator procedures.

It does not authorize LIVE, select an identity provider, or approve any
Internet-facing architecture.

## Assets

- Broker capital, positions, orders, buying power, and protective orders
- Execution authority, trading readiness, risk policy, safety locks, and reset
  approvals
- Broker, identity/session, webhook, and AI credentials
- Durable intent/idempotency/order/fill/protection/risk/reconciliation records
- Market/account data and model/prompt provenance
- Audit evidence, logs, backups, release manifests, and artifact integrity
- Trading/control availability and out-of-band recovery access

## Actors and Failure Sources

- Unauthenticated LAN or Internet client
- Malicious website, browser extension, or XSS payload
- Stolen viewer/operator/admin session
- Compromised webhook sender, proxy, container, dependency, CI runner, or host
- Compromised/incorrect AI output or MCP tool invocation
- Accidental or fatigued operator
- Duplicate/stale application worker
- Broker/data outage, delayed/duplicate/missing callback, half-open connection
- Database/disk/clock failure
- Restore of obsolete authority or consumed intent state

## Trust Boundaries

```text
Browser
  | TLS + session + CSRF
  v
Trusted nginx / private ingress
  | exact upstream network identity
  v
FastAPI control plane
  | durable intent / readiness boundary
  v
Singleton execution service
  | fixed account + client identity
  v
IBKR

TradingView -> isolated webhook ingress -> candidate store (no broker authority)
FastAPI/Workers -> Yahoo/Anthropic (untrusted external data/output)
Application -> SQLite/volume -> encrypted off-host backup
Source/CI -> immutable image/evidence -> deployment host
Operator -> out-of-band broker controls
```

## Primary Threat Scenarios

| ID | Scenario | Impact | Existing control | Blocking gap / required control |
|---|---|---|---|---|
| T01 | Unauthenticated remote control-plane access | Critical | Some bearer dependencies/CORS | Compose exposure and cleartext ingress; support loopback/VPN only |
| T02 | Bootstrap secret recovered from browser asset/source map | Critical | Bootstrap can be disabled in LIVE | Secret can enter build; prohibit release bootstrap and scan artifact |
| T03 | XSS/extension steals `localStorage` token | Critical | JWT signature/expiry | Server session, HttpOnly cookie, CSRF, short TTL, revocation |
| T04 | Low-privilege token performs high-risk mutation | Critical | Authentication on many routes | Deny-by-default RBAC and exact-action step-up |
| T05 | Attacker arms LIVE/real broker or widens risk/resets lock | Critical | Code-owned Stage 9A fence covers mode, broker flag, and known live ports | Broker account assertion, risk-admin role, one-use step-up, durable authority transition |
| T06 | Forged/replayed TradingView event | High | Secret, IP, freshness, DB event dedup | Isolated ingress, trusted proxy identity, bound approval payload |
| T07 | MCP/AI approves A but submits B | Critical | Some safety checks | Cryptographic/canonical payload binding to candidate/revision |
| T08 | AI automatically promotes unsafe rule | Critical | Scheduled optimizer is proposal-only with runtime no-mutation tests | Human versioned approval and RBAC/step-up required |
| T09 | Duplicate workers submit or mutate state twice | Critical | Shared DB limiter, one-worker check, lifespan-held file lock | Prove one replica; future executor split + honest lease/fencing |
| T10 | Broker accepts order but client records ERROR/retries | Critical | `orderRef` correlation | Durable `UNKNOWN`, full reconciliation, never blind retry |
| T11 | Direct submit/cancel path bypasses gateway | Critical | Common helper for some paths | One adapter plus static mutation-site test |
| T12 | Naked/under-protected filled position | Critical | Local ATR exit loop | Broker-native stop coverage for cumulative filled quantity |
| T13 | Stop and discretionary exit race reverses position | Critical | Local position tracking | One intent/protection/OCA graph and broker quantity refresh |
| T14 | Partial exit hides residual broker exposure | Critical | Partial-fill logging | Execution-derived residual quantity; no full local deletion |
| T15 | Stale/wrong-account P&L misses loss breach | Critical | Persisted flag/manual reset | Continuous exact-account risk controller and fail-closed data |
| T16 | DB error returns unlocked defaults | Critical | Safety kernel catches some errors | Guardrail store must propagate unavailable state; lock entries |
| T17 | Stale quote/fallback causes unsafe order | Critical | Freshness monitor/display state | Consumer-specific source/age gate in execution gateway |
| T18 | WS flood exhausts CPU/memory/vendor entitlement | High | Origin and token checks | Frame/rate/connection/subscription/global budgets/backpressure |
| T19 | Spoofed forwarding header defeats IP/rate policy | High | Direct `request.client.host` | Explicit trusted proxies and stripping policy |
| T20 | Sensitive token/account/order data appears in logs | High | Some redaction | Structured schema, allowlisted fields, synthetic secret tests |
| T21 | Health says ready during half-open broker/task death | Critical | Liveness/DB checks | Separate trading readiness and supervised tasks/real heartbeat |
| T22 | Restore resurrects LIVE/old intents/cleared locks | Critical | SQLite integrity check | External restore generation, disarm, reconcile, fresh approval |
| T23 | Backup missing/stale/readable on host | High | Manual backup script | Scheduled encrypted off-host backup, age alert, restore drill |
| T24 | Compromised/mutable release changes behavior | Critical | CI unit/build tests | Locked inputs, SBOM/scans, immutable signed image/evidence |
| T25 | Operator cannot de-risk during auth/control outage | Critical | Emergency functions | Explicit out-of-band procedure; reduce/protect path availability |

## Security Invariants

1. No network caller receives broker mutation authority from authentication
   alone.
2. Authority widening and safety reset require exact-action fresh approval.
3. De-risking does not depend on AI and is not blocked by an entry lock.
4. No secret is bundled into browser/static artifacts or logged.
5. Broker mutation occurs only through the singleton adapter from a durable
   intent.
6. Unknown external outcomes are reconciled, never guessed or blindly retried.
7. Required stale/invalid data locks entries.
8. Restore always starts disarmed and cannot replay consumed intent.
9. Liveness never implies trading readiness.
10. Public control-plane exposure is unsupported.

## Immediate Containment

- Keep the code-owned Stage 9A LIVE/real-money broker fence in place and the
  system isolated. Known ports are containment; actual account identity is
  still unproven.
- Enforce one worker/replica; retain the same-host execution process lock.
- Keep `SIM_MODE` broker-disconnected, including the manual connect route.
- Keep `/metrics` unmounted unless an isolated monitoring profile is deployed.
- Do not expose nginx/backend beyond loopback until the deployment ADR is
  approved and verified.
- Do not place bootstrap credentials in browser build variables.
- Treat existing LIVE-flip instructions as blocked/historical.
- Keep webhook disabled unless isolated to its one route.
- Use out-of-band broker access as the final emergency control.

## Verification Backlog

- Route/method/auth/role/step-up inventory generated in CI
- Browser/image/history/source-map secret canary scan
- Host/network reachability scan for local and VPN profiles
- Trusted-proxy and webhook spoof/replay tests
- HTTP/WS session revocation and resource-limit load tests
- Broker mutation-site architecture test
- Intent/lease/reconciliation/protection/risk fault matrix
- Structured-log redaction and real metric-callsite tests
- Backup/restore replay-fence drill
- Dependency/SAST/container scan with scanner-failure test
- Independent security review

## Residual Risk and Approval

Every threat above remains open until linked evidence proves its control and a
named owner accepts any residual risk. Critical/high exceptions must be
time-limited and cannot authorize LIVE if they affect execution idempotency,
protection, account risk, reconciliation, authentication, or restore replay.

