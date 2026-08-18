# Offline PAPER lifecycle drill

`scripts/paper_lifecycle_simulator.py` is a deterministic regression harness
for the minimum execution lifecycle. It covers normal fill, fragmented
partial fill, cancel/replace, disconnect/reconnect, restart/reconciliation,
and a deliberate broker/local mismatch.

```bash
python scripts/paper_lifecycle_simulator.py
python scripts/paper_lifecycle_simulator.py --scenario partial_fill
```

The harness uses an in-process broker double only. It does **not** import an
IBKR client, open a network connection, read credentials, mutate the trading
application, or authorize LIVE. A passing result is therefore a code-level
invariant check, not evidence of an IBKR PAPER session.

The real PAPER gate still requires the operator drill in
`docs/paper_review_protocol.md`: approved account/port identity, market
session, restart, partial fills, cancel/replace, disconnect/reconnect, and
broker/local reconciliation artifacts tied to the audited SHA.
