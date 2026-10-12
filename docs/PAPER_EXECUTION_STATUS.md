# PAPER execution integration status

This is a working-tree engineering note, not SHA-bound release evidence or
permission to submit orders. Current runtime authority remains blocked.

## Containment contract

- The outgoing IBKR order carries the configured account explicitly.
- Every staged leg retains the qualified stock conId and primary exchange.
- Submission uncertainty stops the sequence and requires manual intervention.
- The executor never cancels broker orders as failure cleanup. In particular,
  a timeout after final transmission must not remove potentially active stops.
- A cancellation request is not proof of cancellation. This executor does not
  invent canceled leg states when a broker outcome is unknown.
- Failures and task cancellation during persistence block local authority and
  attempt a durable execution fence independently of the bracket-group write.
- A failed durable fence remains an error; no database failure is considered safe.
- Broker acknowledgements must match account, qualified instrument, order reference,
  prices, quantity, parent linkage and protective order settings. Pending statuses
  and identity-free orderStatus callbacks cannot establish accepted protection.
- Disconnects, rejected/uncertain orders and observed fills currently block the
  transport instance from further submissions or reconnect. Fill containment is
  temporary until a reviewed lifecycle reconciler can establish protection.
- The final pre-send check shares a lock with callback-triggered blocking. This
  prevents a previously recorded block from being ignored at placeOrder.

This transport block is process-local, not durable recovery. Creating another
transport is not proof of reconciliation or permission to resume entries.

## Remaining release blockers

- Instantiate the reviewed transport/store in the application lifecycle, then
  implement account/order/position reconciliation and durable session recovery.
- Verification of acknowledged account, instrument, order reference and leg state.
- Restart/disconnect fencing and independent review of the full execution path.
- Real PAPER account, entitlements and client identity verification.
- Reviewed activation authority and evidence requirements; no environment bypass.
- Human-approved real PAPER bracket and partial-fill protection drill.
- Full green engineering gates and exact-source evidence.

Unit tests use test-only broker substitutes. They are not real IBKR order or
fill evidence. No test in this change sends an order to TWS.

## Observation inbox

Migration 8 adds an immutable inbox independent of bracket foreign keys, so
unknown order and connection events can be retained. Session/sequence retries
are idempotent only for identical owner, account, client and payload. Conflicting
observations are rejected. Raw broker error messages are excluded; numeric codes
and request IDs remain. The transport flush cursor advances only after each
successful write, and persistence failures block further execution.

The transport accepts a scoped observation store and starts its background writer
after handshake. Submission requires a running writer and flushes observations
before sending and before returning an acknowledgement. Writer failure blocks
execution. Disconnect stops and joins the writer. The application composition
still needs to provide the store and verified owner.

Events received between flushes can still be lost on process crash. A durable
session marker and restart reconciliation are required before release; observation
records alone do not establish reconciled state or protection.
