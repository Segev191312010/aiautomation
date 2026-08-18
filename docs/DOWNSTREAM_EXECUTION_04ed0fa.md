# Downstream Execution — `73d9491`

**Branch:** `integration/post-reconciliation`  
**Current SHA:** `73d9491ac4cc0bb57d5391cedef72f0f24a70e39`  
**Status:** automated gates green; PAPER evidence pending; LIVE **NO-GO**

## Included in this SHA

- Screener contract and UI support for the `us_all` universe.
- Single-symbol TradingView hosted chart embed with explicit feed/entitlement caveat.
- Persisted AI walk-forward run and fold evidence with non-overlapping boundaries.
- Canonical database-path guard and secure-consolidation controls.

## Minimum AI evidence bar

No AI candidate may influence execution authority until a SHA-pinned evidence
bundle contains at least seven consecutive calendar days, 100 evaluated test
signals (or the documented reason fewer were available), a named baseline,
realistic transaction costs, abstention and calibration metrics, regime slices,
and reproducible train/test dataset hashes. Any unresolved leakage, fold overlap,
cost omission, or failed integrity check is a failure, not a waiver.

## PAPER pass/fail drills

The operator must capture the exact SHA, configuration fingerprint, account/port,
startup health, IBKR heartbeat, database integrity, WebSocket state, and mode
fence before the session. During PAPER only, run normal order lifecycle,
partial-fill handling, cancel/replace, disconnect/reconnect, gateway restart,
duplicate retry, and reduce-only recovery drills. Compare broker and local
positions, orders, fills, commissions, and candidate status before and after the
restart. Any unresolved reconciliation discrepancy fails the gate and keeps the
system OFF.

Passing PAPER evidence does not authorize LIVE. LIVE remains blocked until the
execution ledger, broker reconciliation, account risk, protection, restore,
identity, and security gates are independently accepted.
