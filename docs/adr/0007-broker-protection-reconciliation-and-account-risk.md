# ADR 0007: Broker Protection, Reconciliation, and Account Risk

**Status:** Proposed — human approval and paper-broker evidence required  
**Date:** 2026-07-27  
**Decision owners:** Unassigned  
**Risk approver:** Unassigned  
**Release authority:** Unassigned

## Context

The current executor submits naked market/limit entries. Hard stops are local
calculations evaluated by an application loop, so a process, network, or data
failure can leave broker exposure without a broker-native protective order.

Startup recovery compares some recent local pending rows only with
`openTrades()`. It does not converge account, positions, completed orders,
executions, commission reports, attached orders, protection quantities, or
offline fills. It reaps some ambiguous pending rows before asynchronous
reconciliation.

The daily-loss flag is persisted, but no production controller continuously
derives and latches it from authoritative account data. Account summary
handling does not establish one approved account/currency and assumes P&L
fields that are not supplied by the current request. A database read failure
can also produce unlocked defaults.

ADR 0001’s “database is trade truth” statement conflicts with these facts. If
accepted, this ADR supersedes its broker/local truth model.

## Decision

### 1. Broker and local truth

The broker is authoritative for:

- managed account identity and base currency;
- cash, NetLiquidation, margin, and buying power;
- aggregate positions;
- open, completed, parent, child, OCA, and replacement orders;
- executions/fill fragments and cumulative filled quantity; and
- commissions and broker-reported realized P&L.

The local database is the durable, auditable interpretation of broker truth,
execution intent, strategy ownership, policy, reconciliation, and approvals.
In-memory values are caches only.

Disagreement never causes silent adoption, deletion, or retry. It creates a
durable discrepancy, makes trading readiness false, and locks new
risk-increasing intents. Authenticated reduce-only/protection actions remain
available through the shared gateway when their effect can be proven from a
fresh broker snapshot.

### 2. Fixed account and broker identity

Before readiness, the platform verifies:

- exact approved account identifier;
- paper/live environment through account evidence, not port naming alone;
- base currency and supported currency conversions;
- one fixed broker client ID;
- exact TWS/IB Gateway/API versions; and
- reconciliation capability/retention settings.

Client-ID fallback and degraded account synchronization are not trading-ready
states. Multiple accounts or currencies are unsupported until separately
designed and tested.

### 3. Supported initial scope

The first paper and live-canary scope is:

- long common stock only;
- one approved account and base currency;
- regular trading hours only;
- no shorting, options, futures, fractional shares, extended hours, or
  automatic failover;
- one entry intent at a time during canary; and
- a mandatory broker-native hard stop for every filled share.

Exact symbols, quantity/notional ceilings, limit/market entry policy, stop
type, time-in-force, price bands, session calendar, and gap/containment policy
remain TBD until signed by the risk owner. An unset field denies the canary.

### 4. Protection state and invariant

Entry and protection are one persisted graph before the first broker call.

```text
Protection:
PLANNED -> STAGING -> AWAITING_ACK -> PROTECTED
                         |               |
                         v               v
                  UNDER_PROTECTED   REPLACING
                         \               /
                          -> CONTAINMENT
```

For every managed position:

```text
acknowledged protective quantity >= cumulative filled entry quantity
```

“Submitted” is not “acknowledged.” Coverage uses broker-observed child
identity, status, action, account, contract, stop price, and quantity.

The persisted graph includes parent and child roles, account, `conId`, local
intent/order IDs, broker client/order/perm IDs, `orderRef`, parent identity,
OCA group, transmit sequence, requested/filled/remaining quantities, stop
policy version, status history, and acknowledgment timestamps.

AI cannot cancel, loosen, or replace the hard stop. Any future tightening must
pass deterministic bounds and a separate approval/evidence gate.

### 5. Partial fills and containment

The repository does not assume how attached-child activation behaves on a
partial parent fill. Published broker materials describe this differently
across interfaces, and the exact deployed combination has not been tested.

Paper-broker contract tests must prove, after each partial execution, that
active acknowledged stop quantity equals cumulative fill. If they cannot,
the signed policy must use a tested cancel-residual plus standalone-protection
containment flow.

If required protection is not acknowledged within the approved SLA:

1. durably latch `ENTRY_LOCKED`;
2. stop claiming new entries;
3. cancel only the known unfilled entry remainder;
4. reconcile late fills and existing protection;
5. protect or flatten filled managed exposure according to the signed
   containment policy; and
6. alert the named operator.

Global cancel is forbidden. Stops reduce risk but do not eliminate gap, halt,
liquidity, or slippage loss.

### 6. Reconciliation and readiness

On startup, reconnect, restore, periodically, before resolving `UNKNOWN`, and
before any live arming:

1. persist trading readiness false and lock entries;
2. connect with the fixed client/account identity;
3. install idempotent order/execution/commission handlers before consuming
   snapshots;
4. fetch account values, positions, portfolio, same-client open orders,
   all-client order snapshots, completed orders, executions, fills, and
   commissions;
5. match by account and `conId`, then correlation/identity fields such as
   `orderRef`, `permId`, `execId`, and client-scoped `orderId`;
6. backfill executions with an overlap window and deduplicate by account plus
   `execId`;
7. reconstruct managed positions from durable executions and compare them with
   broker positions;
8. verify working entry quantity and broker-acknowledged protection coverage;
9. classify broker-only/manual/external activity without silently adopting it;
10. persist discrepancies and the synchronization cursor/high-water mark; and
11. set readiness true only after the complete snapshot and required streams
    are fresh with no blocking discrepancy.

Execution history beyond broker retention requires approved statement/Flex or
equivalent evidence. If the outage exceeds available evidence, readiness
remains false.

Liveness and API readiness stay available while trading readiness is false.

### 7. Durable account-risk controller

The controller operates independently from bot/AI cycles and is supervised.
It consumes only the approved account and finite, fresh, currency-consistent
broker values.

The proposed primary calculation is:

```text
adjusted_equity = NetLiquidation - approved_external_cash_flows
session_pnl     = adjusted_equity - persisted_session_baseline
drawdown        = adjusted_equity - persisted_session_peak
```

Broker P&L subscriptions are a secondary freshness/integrity signal; they are
not blindly added to NetLiquidation change.

Persist:

- signed risk-policy version and thresholds;
- session calendar/timezone/boundary;
- baseline and peak;
- approved cash-flow adjustments;
- every input sample with source age/currency;
- warning/lock/flatten state;
- trigger measure, threshold, cause, and time; and
- reset actor, step-up approval, cause note, cooldown, and reconciliation ID.

On a signed entry-lock threshold breach, one database transaction latches the
risk event and moves execution authority to `ENTRY_LOCKED` before cancellation
work begins. It then cancels only known risk-increasing working entries,
preserving broker-native protection and verified reduce-only exits.

Stale, missing, non-finite, unset-max-double, wrong-account, wrong-currency,
inconsistent, or unavailable risk data locks new entries. Database failure is
also fail-closed. Restart, midnight, DST, holiday boundaries, or restore never
clear a latch.

Flatten thresholds and policy require separate explicit approval because an
automatic flatten can itself cause loss or reverse exposure when truth is
incomplete.

### 8. Proposed persistence

Normalized tables are required for:

- managed broker orders, fill fragments, and commissions;
- protection groups and quantity coverage;
- managed-position ownership separate from aggregate broker positions;
- reconciliation runs, discrepancies, and sync cursors;
- trading-readiness state and events;
- versioned risk policies and sessions;
- append-only risk samples/events; and
- authorized reset/containment records.

Partial fills remain partial. A cancelled partially-filled exit must never
delete the entire local position; residual quantity is derived from
authoritative executions and broker position.

## Required Approval Inputs

The approvers must fill and sign:

- account ID and environment;
- fixed broker client ID and exact software versions;
- supported instrument/order/session matrix;
- stop type/TIF/outside-RTH and transmission policy;
- partial-fill and child-rejection containment;
- execution overlap/retention source and reconciliation SLA;
- account/base currency and cash-flow treatment;
- session timezone/boundary;
- warning, entry-lock, and optional flatten thresholds;
- reset roles, step-up, cooldown, and evidence;
- paper/live canary symbol, quantity/notional, and loss ceiling; and
- on-call/rollback ownership.

## Acceptance Evidence

- paper tests for parent/child transmission, partial fills, child rejection,
  cancel/replace, late fill, reconnect, restart, and TWS restart;
- duplicate/missing/out-of-order order status plus execution deduplication;
- broker-only order/position and account mismatch quarantine;
- outage within and beyond execution-retention windows;
- protection coverage checked after every fill;
- stop/discretionary-exit race without position reversal;
- continuous risk samples through process restart and session boundaries;
- loss latch committed before selective cancellation;
- stale/invalid/DB-failure tests proving entry lock and exit availability;
- reset replay/wrong-role/wrong-account/cooldown rejection;
- backup restore starts disarmed and reconciles before rearm; and
- measured readiness, containment, alert, RPO, and RTO SLAs.

## Implementation Hold

While Proposed, no bracket, stop replacement, automatic flatten, orphan
resubmission, fixed-client behavior change, or loss threshold is deployed.
Implementation may add normalized schemas, pure transition tests, passive
paper event ingestion, and readiness-false scaffolding without broker
mutation.

## Reference Material

- IBKR bracket order transmission:
  <https://interactivebrokers.github.io/tws-api/bracket_order.html>
- IBKR order callbacks:
  <https://interactivebrokers.github.io/tws-api/order_submission.html>
- IBKR open-order behavior:
  <https://interactivebrokers.github.io/tws-api/open_orders.html>
- IBKR executions and commissions:
  <https://interactivebrokers.github.io/tws-api/executions_commissions.html>
- IBKR P&L behavior:
  <https://interactivebrokers.github.io/tws-api/pnl.html>


