# C9 broker-call bypass audit (v1)

Status: **NON-AUTHORIZING / FAIL-CLOSED**  
Audit basis: `broker-call-inventory-v1.json` on the current implementation
branch.  This artifact records the migration surface for C9; it does not
approve any broker call or grant live authority.

## Finding

The current backend still contains direct calls to the `ib_insync` object.
The inventory verifier reports **98 direct calls**: 5 broker-order side
effects, 37 reads, 40 connection operations/checks, and 16 administrative
client/subscription operations.  The inventory is an exhaustive
source-location list, not evidence that the calls satisfy the C9 OperationGate
contract.

The following calls can create or mutate broker orders and therefore are C9
**side-effect bypasses** until they are routed through the reviewed control
plane:

| Location | Call | Current path | Required C9 disposition |
|---|---|---|---|
| `backend/order_executor.py:246` | `ibkr.ib.placeOrder` | `place_order` entry path | Block until PREPARE → external CAS (sole A consume) → mirror → OperationGate agreement → adapter submit |
| `backend/order_executor.py:363` | `ibkr.ib.cancelOrder` | `cancel_order` | Route through account-scoped cancel/reconcile adapter and uniform stop sequence |
| `backend/order_executor.py:467` | `ibkr.ib.cancelOrder` | startup MKT conversion | No autonomous cancel/resubmit; use reviewed reconciliation state machine |
| `backend/order_executor.py:497` | `ibkr.ib.placeOrder` | startup MKT→LIMIT resubmit | Must not create a second intent; only reviewed adapter may submit a bound replacement |
| `backend/safety_kernel.py:139` | `ibkr.ib.placeOrder` | emergency close | Must use the uniform close-gate → persist → target-cancel/reconcile sequence |

The `openTrades` calls at `order_executor.py:345,361,389,451,547` and
`routers/positions.py:65` are broker-state reads.  They are not order
submission by themselves, but they must eventually be account-scoped and
covered by the C9 reconciliation evidence (including UNKNOWN and restore
replay cases).  Account/position reads, contract qualification, market-data
requests/subscriptions, connection lifecycle operations, client construction,
and local contract construction are now classified individually.  None
satisfies the C9 submit gate.

## Required evidence before C9 PASS

1. Independent C9 fence-design review and owner acceptance, bound to the
   exact fence-design hash.
2. A single reviewed `OperationGate`/adapter boundary with no direct
   side-effect call sites outside that boundary.
3. Durable intent state and an external CAS proving that A is consumed at
   exactly one point; the gate must only validate agreement and never consume
   A a second time.
4. Account-scoped idempotency and broker-state reconciliation for every
   accepted-nonterminal order, including `UNKNOWN`, partial fills, cancel
   races, disconnects, and the crash-after-CAS-before-mirror case.
5. Restore-generation fencing outside the restorable trading database and an
   adversarial test proving that restoring a DB cannot replay a consumed A.
6. The uniform stop sequence for emergency, daily-loss, disconnect, halt,
   latency, timeout, and no-fill paths.
7. A fresh static inventory run after each runtime-bearing change; an
   unlisted direct side-effect call is a fail-closed failure.

## Reproduction

From the repository root:

```text
backend/.venv/bin/python scripts/verify_broker_call_inventory.py
```

Expected current result:

```text
PASS: 98 direct broker calls inventoried (admin=16, connection=40, read=37, side-effect=5)
```

This PASS means only that the locations are listed.  It must not be reported
as C9 conformance or authorization.
