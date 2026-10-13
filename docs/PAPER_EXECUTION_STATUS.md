# PAPER execution status

Working engineering note (updated 2026-10-13). It records how PAPER orders are
sent today and what still stands between this and the execution engine that is
to replace the bridge. It is not evidence for real-money authority; LIVE has no
V1 path.

## Who sends orders

`backend/scripts_local/paper_bridge.py` is the only process that places,
modifies or cancels orders (IBKR TWS client 18, PAPER account only). The
supervisor (`autotraderctl run`) starts it at 09:25 ET and restarts it on exit
code 75; exit 78 is a permanent refusal and is not restarted.

The supervisor launches only the bridge whose sha256 is pinned in
`REVIEWED_BRIDGE_SHA256` (`backend/trading/operations/supervisor.py`). Any other
content is refused before exec (exit 78, and the phone is told no orders will be
sent). A bridge change therefore needs a new independent execution-safety
review, and the commit that records that review below also moves the pin; a
test ties the pin to the file.

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
- Manual orders never short. Every working sell on the contract reserves
  shares, whichever client placed it (bot legs from earlier sessions and stops
  placed by hand in TWS included), and a filled sell stays reserved until TWS's
  position update shows it. They never sell shares held by a bot bracket (that
  is done by `close`, which flattens the bracket); a symbol with live bot orders
  the bridge does not manage today is left to the owner in TWS.
- Manual orders are refused outside regular hours, when stale, or when the
  bridge heartbeat is older than 20 s; no owner row is claimed in the 20 s
  before the flatten.
- `/closeall` reports `filled` only when everything held was closed; anything
  refused or kept back ends `cancelled`, with the reasons on the phone.
- A TWS error never finalizes an owner order by itself: the bridge asks TWS for
  open and completed orders first. Rows from earlier sessions that IBKR no
  longer works are settled at startup; nothing is ever resent.

## Review history

- 2026-10-12: first independent execution-safety review — NO-GO (H1, H2,
  M1–M4, L1–L8). All fixed with regression tests.
- 2026-10-12: re-review of v4 — all prior findings confirmed fixed; NO-GO on
  new findings N1–N3 (owner holdings vs bot exits, silent rejected modifies,
  untested modify encoding). Fixed in v5 with tests.
- 2026-10-13: final review of v5 with the manual-order intake — NO-GO on F1
  (HIGH: owner sells ignored bot exit legs the bridge did not manage that day,
  which could leave the account short), F2–F4 (MEDIUM) and five LOW items.
  Re-review — NO-GO on R1–R3 (MEDIUM: a fill subtracted twice, bot exit fills
  not reserved until the position updated, a partly refused `/closeall`
  reported as filled). All fixed; every reviewer probe is a repo regression
  test.
- 2026-10-13: final re-review — GO on `paper_bridge.py` sha256
  7ad34b5824602f725fd04ca8631122b95ad2c86dadf8e2b224cd1942d171022a, pinned in
  the supervisor. Remaining LOW items: shares can stay reserved for the day if
  TWS reports a position before the fill (a CRITICAL says so), a narrow
  early-release window on two-execution sells, slow L-a queries can delay a
  loop by up to 20 s, a few-second startup window, and two untested guards
  (startup replay exclusion, the SLD path).

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

- Supervised first live runs: the first owner order through the bridge (the
  09:35 one-share test) and the first 15:45 flatten modify against a real TWS.
- Execution engine: an independent execution-safety re-review of
  `trading/execution/live_executor.py`, `tws_state.py`, `promotion.py`,
  `trading/storage/execution_ledger*.py` and `trading/risk/book.py` after the
  fixes for its first review.
- Execution engine: a supervised drill on the paper account that captures the
  recorded callback sequences from a real TWS (bracket fill, partial fill,
  modify echo, rejected modify, restart mid-send) and checks how TWS reports
  untransmitted legs after a client crash. The bridge must not run during the
  drill.
- Execution engine: a runner (connection, reconnect loop, supervisor child) and
  a decision on how owner manual orders are sent once the bridge is retired;
  both need their own review.
- Replace the bridge with the execution engine (ledger, reconciliation every 2
  minutes, shared risk book) only after the review, the drill and a clean paper
  week; the old `trading/execution/` transport stack is deleted at the same
  time.
