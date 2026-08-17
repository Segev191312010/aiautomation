# ADR 0008: Security, Data, and Release Boundary

**Status:** Proposed — human security/risk approval required  
**Date:** 2026-07-27  
**Decision owners:** Unassigned  
**Security approver:** Unassigned  
**Risk approver:** Unassigned  
**Release authority:** Unassigned

## Context

The current dashboard uses a development bootstrap-token flow rather than a
production identity system. Browser code can receive a build-time bootstrap
secret, tokens are stored in `localStorage`, the login form does not validate
the entered credentials, and there is no RBAC, step-up authorization,
revocation, or production session model.

An authenticated development user can perform high-risk actions including
order placement/cancellation, risk changes, LIVE arming, safety-lock resets,
rule promotion, IBKR control, retention, and backup deletion. Several
read-heavy routes are unauthenticated. WebSocket authentication exists but
does not enforce user revocation/roles or resource limits.

The default compose ingress publishes the dashboard on host interfaces using
cleartext HTTP. Metrics and structured-logging modules exist but are
incompletely wired. Readiness does not cover broker/account/reconciliation/
risk/data state, backups are not demonstrated as scheduled encrypted off-host
artifacts, and release evidence is not tied to an immutable image.

## Decision

### 1. Supported deployment profiles

Only two control-plane profiles are supported:

#### `local`

- dashboard binds to `127.0.0.1`;
- backend is reachable only through the local reverse proxy or loopback;
- broker/API/database ports are not published;
- host firewall denies LAN/WAN access; and
- one application replica and `WORKERS=1`.

#### `vpn`

- dashboard binds only to an approved private VPN interface;
- TLS is mandatory;
- exact Host and Origin allowlists;
- backend is reachable only from the trusted reverse proxy;
- host firewall denies non-VPN clients; and
- one execution owner; scalable read/API services require later separation.

Public Internet exposure of the control plane is unsupported. If TradingView
ingress is required, it uses a separate TLS hostname/service exposing only the
webhook route. Auth, UI, trading, docs, metrics, and health detail are not
routable on that ingress.

An unset or unknown profile starts disarmed and refuses broker-capable
operation.

### 2. Production identity and session boundary

Production does not use the demo JWT/bootstrap flow or a home-grown password
system. Select an established OIDC/passkey/MFA-capable provider after a
deployment-owner decision.

The target browser session is server-managed and uses a `Secure`, `HttpOnly`,
`SameSite` cookie, CSRF protection, short idle and absolute lifetimes, logout,
revocation, and WebSocket binding. Tokens/secrets never appear in URLs,
`localStorage`, source maps, static assets, or logs.

Bootstrap endpoints and browser build-time bootstrap secrets are prohibited in
release profiles. Initial provisioning is local/out-of-band and auditable.

### 3. Roles and step-up authorization

Minimum roles:

| Role | Permitted scope |
|---|---|
| `viewer` | approved read-only views |
| `operator` | ordinary bounded operations and immediate de-risk actions |
| `risk_admin` | risk increases, lock reset, protection policy, LIVE arming |
| `system_admin` | identity, release, retention, and backup administration |

All route, WebSocket, background-workload, and gateway permissions are explicit
deny-by-default mappings.

Step-up authorization is one-use, short-lived, and bound to the exact actor,
session, account, action, before/after values, policy version, and
request/intent ID. It is required for:

- LIVE arming or authority widening;
- increasing risk limits/notional/universe/session scope;
- weakening or cancelling required protection;
- loss/integrity/reconciliation/circuit reset;
- rule promotion into a broker-active state; and
- destructive administration/restore.

Kill, cancel known working entries, verified reduce-only exit, protection
repair, and approved flatten must remain quickly accessible to authorized
operators and must not require a control that can be unavailable during an
incident.

### 4. Proxy, webhook, and WebSocket trust

Forwarding headers are accepted only from explicit trusted proxy peers;
untrusted copies are stripped. Rate limiting, audit identity, and webhook IP
policy use the resulting trusted client identity.

WebSockets require:

- current non-revoked session and role;
- exact allowed Origin;
- maximum frame/message bytes;
- strict message schema and symbol validation;
- connections per user/IP;
- messages per interval;
- subscriptions per connection and user;
- global broker entitlement/subscription budget;
- bounded queues and slow-consumer disconnect; and
- prompt resource release on disconnect/revocation.

Both `/ws` and `/ws/market-data` must work through the actual TLS ingress.

### 5. Typed configuration authority

Replace scattered environment parsing with one typed, range-validated
configuration schema and explicit `dev`, `local`, and `vpn` profiles.
Production-like fatal checks cannot be disabled.

Configuration validates:

- exact broker account/environment/client identity;
- execution authority versus paper/live/simulation state;
- single execution topology;
- bind, proxy, origin, TLS, and webhook exposure;
- risk limits and source freshness thresholds;
- authentication/session provider settings;
- database/disk/backup requirements; and
- model/prompt/fallback allowlists.

Environment-versus-database authority conflicts start disarmed. Startup emits a
non-secret canonical configuration fingerprint that must match release
evidence.

### 6. Data authority and degradation

Every decision consumer has an approved source, maximum age, event-time rule,
calendar/session semantics, and fallback policy.

Initial policy:

- approved broker account/position/order/execution data is mandatory for
  execution, reconciliation, and risk;
- broker quote data is mandatory for broker order pricing/sanity decisions;
- Yahoo/other research data may support display or offline analysis but cannot
  silently replace a required execution source;
- AI output is advisory until separately promoted and never overrides missing
  deterministic safety data; and
- an unapproved model/prompt/fallback cannot take action.

Stale, unavailable, inconsistent, or unknown required data blocks
risk-increasing intents before broker submission. It does not block
authenticated cancel, protection repair, reduce-only exit, or approved
flatten when their effect is established from fresh broker truth.

“Broker heartbeat” means age since the last valid required broker message, not
time since the socket first connected.

### 7. Observability and readiness

Expose separate states:

- **liveness:** process/event loop responds;
- **API readiness:** API dependencies required for read/control functions are
  available; and
- **trading readiness:** exact execution owner/account, reconciliation,
  protection, durable risk controller, required data freshness, clock, DB,
  disk, background-task supervision, and backup age all pass.

Trading readiness returns unavailable and locks new entries for any unknown or
failed component while liveness remains available.

Structured logs are single-line JSON with timestamp, severity, service,
release, request/session/intent/reconciliation IDs, actor/workload, outcome,
and redacted error details. Secrets, bearer/cookie values, webhook payload
secrets, AI credentials, and full account identifiers are prohibited.

Metrics are served only on the monitoring network and are wired to real
order/intent/fill/protection/risk/reconciliation/data/task/backup events with
bounded labels. Alerts have signed severity, deduplication, recovery, on-call,
and delivery-SLA definitions.

### 8. Backup, restore, and replay fence

Define and approve RPO/RTO. Backups are scheduled, integrity/schema/hash
verified, encrypted, access-controlled, stored off-host, retained by policy,
and monitored for age/failure.

Restore:

1. never mutates the only original on failed validation;
2. handles SQLite database/WAL/SHM consistently;
3. validates schema/application compatibility;
4. creates an external monotonic restore/deployment generation;
5. invalidates old leases/sessions and prevents consumed-intent replay;
6. forces execution `DISARMED` and trading readiness false;
7. reconciles full broker truth; and
8. requires fresh step-up approval before rearming.

The exact external generation/fence mechanism remains a required design input;
the restored database cannot be trusted to prove that it is the newest copy.

### 9. Release evidence

Only immutable image digests are deployed. Each evidence bundle identifies:

- clean commit and tree;
- image digest/signature and build provenance;
- DB schema/migration checksums;
- locked dependency hashes, SBOM, and scan results;
- sanitized configuration fingerprint;
- model, prompt, schema, routing, and fallback fingerprints;
- broker account/environment/client and TWS/API versions;
- unit/integration/fault/soak/drill results;
- known residual risks and expiring exceptions; and
- named engineering, risk, security, release, and on-call approvals.

Material changes invalidate the affected evidence. Scanner failure fails CI;
it is not interpreted as “no findings.”

## Acceptance Evidence

- generated unauthenticated-route inventory and deny-by-default RBAC tests;
- session expiry/logout/revocation/user-disable tests across HTTP and WS;
- exact-action one-use step-up and replay/alteration tests;
- static/image/source-map canary-secret scan;
- loopback/VPN reachability and public-port scans;
- trusted/untrusted proxy and isolated webhook ingress tests;
- WS byte/rate/connection/subscription/backpressure limits;
- configuration cross-product and env/DB-conflict tests;
- source-age boundary and stale-data entry-lock/exit-availability tests;
- real callsite metrics and secret-redacted structured log tests;
- task-death, DB/disk/broker/data/backup readiness fault injection;
- scheduled Docker-volume backup and isolated oldest/latest restore drills;
- immutable artifact/evidence fingerprint enforcement; and
- independent security review with no unresolved critical/high finding.

## Implementation Hold

While Proposed:

- the system is isolated development only;
- the code-owned Stage 9A release fence rejects `AUTOPILOT_MODE=LIVE` and
  configured real-money broker access (including known live-port mismatches);
- remote/public deployment is NO-GO;
- no identity-provider/session implementation is selected by the coding agent;
- no trusted-proxy, step-up, restore-generation, or public-ingress behavior is
  invented without approval;
- existing live-flip runbooks are historical/blocked; and
- metrics remain unmounted by default and may be enabled only with the explicit
  isolated-monitoring profile; production exposure still waits for network
  boundary evidence.

