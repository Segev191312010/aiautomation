# PAPER execution status

Working engineering note (updated 2026-10-13). It records how PAPER orders are
sent today and what still stands between this and the reviewed execution
engine. It is not evidence for real-money authority; LIVE has no V1 path.

## Who sends orders

`backend/scripts_local/paper_bridge.py` is the only process that places,
modifies or cancels orders (IBKR TWS client 18, PAPER account only). The
supervisor (`autotraderctl run`) starts it at 09:25 ET and restarts it on exit
code 75; exit 78 is a permanent refusal and is not restarted.

Order sources:
- **Bot decisions** — accepted decisions of strategies whose catalogue name
  starts with `MOMO_HOD`. Other strategies are recorded but never sent.
- **Owner manual orders** — rows queued in `autotrader_manual_orders`
  (migration 12) by the Telegram assistant after the deterministic parser and
  validator accept them. The bridge claims a row atomically, re-validates it
  against live positions and working orders, and only then sends it.

## Containment rules (enforced and tested)

- Every managed account must be a paper (`DU…`) account, checked at connect
  and on every restart; orders carry the account explicitly.
- Bot entries are native brackets (parent DAY limit, GTC target, GTC stop).
  State is persisted before any order is sent; a crash never resends.
- At most three bot positions across strategies, one per symbol, only in
  symbols the account does not already hold; per-order caps of 25% notional
  and 1% risk; a 2% daily-loss stop; the kill switch blocks new entries.
- Exit quantities come from one locked fill snapshot and are capped by the
  account position; exits never exceed fills.
- The session-end flatten reprices the existing target order once (15 minutes
  before the close, half-day aware). Later checks only alert; the bridge never
  sends a second sell.
- Every modify is rebuilt with the placement encoding TWS already accepted and
  counts as done only when TWS echoes it; unconfirmed modifies alert.
- Any error on an own order id is CRITICAL unless informational.
- Manual orders never short, never sell shares held by a bot bracket (that is
  done by `close`, which flattens the bracket), and are refused outside regular
  hours, when stale, or when the bridge heartbeat is older than 20 s.

## Review history

- 2026-10-12: first independent execution-safety review — NO-GO (H1, H2,
  M1–M4, L1–L8). All fixed with regression tests.
- 2026-10-12: re-review of v4 — all prior findings confirmed fixed; NO-GO on
  new findings N1–N3 (owner holdings vs bot exits, silent rejected modifies,
  untested modify encoding). Fixed in v5 with tests.
- Pending: final review of v5 with manual-order intake before unattended use.

## The execution engine (C2): built, not wired, review pending

`backend/trading/execution/live_executor.py` is built and tested against a fake
TWS only. Nothing runs it and it cannot send an order today: no supervisor
child, LaunchAgent or CLI command starts it, and `start()` refuses without an
explicit promotion record for its exact code (`autotraderctl execution promote
--confirm`, MOMO_HOD strategies and DU accounts only). What it adds over the
bridge:

- a SQLite ledger (migration 13) instead of a JSON state file: an outbox keyed
  by decision id written before any order, brackets, legs tagged
  `at1:<bracket>:P|T|S`, fills keyed by execId with commission reports, and
  append-only order events; a bracket still being sent at a restart becomes
  `sent` when IBKR reports its legs and `unknown` (never resent) otherwise;
- order ids `max(nextValidId, ledger max + 1)` and a paper-only check with
  `reqIds` on every reconnect;
- reconciliation at start, on reconnect and every two minutes, scoped to
  `at1:`: a mismatch pauses new entries (CRITICAL), exits never pause;
- a shared risk book across strategies: 0.5% per new trade, 1.5% open risk,
  2% realized loss plus open plus new risk, three positions, 25% notional;
- a flatten fallback when the target cannot be repriced or the reprice is not
  confirmed: cancel target and stop, confirm both, then one market sell of the
  ledger quantity capped by the position less every other working sell (never
  a second sell next to the owner's manual close).

The bridge's review lessons (H1, H2, M1-M4, L1-L8, N1-N7, D3) are ported with a
regression test each. The recorded TWS callback sequences in
`backend/tests/fakes/tws_recordings.py` were written from the ibapi 9.81
signatures, not captured from a live TWS.

## Still to do

- Supervised first live modify (the 15:45 flatten path has not yet run against
  a real TWS).
- Execution engine: an independent execution-safety review of
  `trading/execution/live_executor.py`, `tws_state.py`, `promotion.py`,
  `trading/storage/execution_ledger*.py` and `trading/risk/book.py`.
- Execution engine: a supervised drill on the paper account that captures the
  recorded callback sequences from a real TWS (bracket fill, partial fill,
  modify echo, rejected modify, restart mid-send) and checks how TWS reports
  untransmitted legs after a client crash.
- Execution engine: a runner (connection, reconnect loop, supervisor child) and
  a decision on how owner manual orders are sent once the bridge is retired;
  both need their own review.
- Replace the bridge with the engine only after the review, the drill and a
  clean paper week; delete the old `trading/execution/` transport stack at the
  same time.
