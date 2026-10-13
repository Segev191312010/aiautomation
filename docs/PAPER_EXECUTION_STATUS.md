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

## The execution engine (C2): built, not wired; re-review READY-FOR-DRILL

`backend/trading/execution/live_executor.py` is built and tested against a fake
TWS only. Nothing schedules it: no supervisor child, LaunchAgent or schedule
starts it. Only `autotraderctl execution run --confirm [--date D]`
(`trading/execution/runner.py`) starts it, by hand, for the supervised drill.
Before it contacts TWS, the runner refuses (exit 78) without an explicit
promotion record for the exact code it runs (`autotraderctl execution promote
--confirm`, MOMO_HOD strategies and DU accounts only), while the bridge runs (a
`paper_bridge.py` process in any launch form, or a heartbeat under 60 s old in
the bridge's own database), with pending or unknown migrations (it never
migrates), without the shadow configuration's paper account, or on a day
without a session. `start()` repeats every check once connected. Started
after the session's stop time, the engine only reports what its ledger holds
and finishes a fallback sell whose cancels were already sent, before the
close. It connects with the bridge's order client id; a lost TWS exits 75 and
is never reconnected in-process (a restart reconciles from the ledger). What
it adds over the bridge:

- a SQLite ledger (its own migration; see "Migration numbering") instead of a
  JSON state file: an outbox keyed by decision id written before any order,
  brackets, legs tagged `at1:<bracket>:P|T|S`, fills keyed by execId with
  commission reports, and append-only order events; a bracket still being
  sent at a restart becomes `sent` when IBKR reports its legs and `unknown`
  (never resent) otherwise;
- order ids above TWS's next valid id, the ledger's highest id and every id
  TWS reports for this client, and a paper-only check with `reqIds` on every
  reconnect;
- reconciliation at start, on reconnect, after TWS error 1101 (which also
  renews the position and account subscriptions) and every two minutes,
  scoped to `at1:`: a mismatch pauses new entries (CRITICAL), exits never
  pause. A short position in a symbol the engine traded today, an entry whose
  stop died and a running bridge are mismatches too, and the engine never
  reports FLAT while such a short exists;
- a shared risk book across strategies: 0.5% per new trade, 1.5% open risk,
  2% realized loss plus open plus new risk, three positions, 25% notional;
- the 15:45 target reprice and its fallback (when the target cannot be
  repriced or the reprice is not confirmed: cancel target and stop, an
  Inactive one too, confirm both Cancelled or Filled, then one market sell)
  sell at most the ledger quantity capped by a fresh account position less
  every other working sell and less what other orders, the TWS GUI's
  included, sold since the entry that the position does not show yet (never
  a second sell next to the owner's manual close). Without a fresh snapshot
  nothing is sold and the decision is taken again every tick: the start
  until 10 minutes before the close, the fallback's market sell until the
  close; shares left without stop, target or working sell are flagged
  UNPROTECTED at once;
- `autotraderctl execution promote` backs the database up first
  (`<database dir>/backups/<name>.before-execution-promote-<UTC time>.db`,
  printed) and refuses when there is no database or the backup fails.

The bridge's review lessons (H1, H2, M1-M4, L1-L8, N1-N7, D3) are ported with a
regression test each. The recorded TWS callback sequences in
`backend/tests/fakes/tws_recordings.py` were written from the ibapi 9.81
signatures, not captured from a live TWS.

### Engine review history

- 2026-10-13: first independent execution-safety review (at 4c755f6) —
  NOT-READY.
  - C2-1 (HIGH): the fallback market sell ignored another client's sell (the
    TWS GUI) that had filled but was not in the position yet, sold the same
    shares again and then reported FLAT while the account was short.
  - C2-2 (MEDIUM): new order ids skipped only the ledger's, not this client's
    live orders, executions and unmanaged orders.
  - C2-3 (MEDIUM): a slow TWS (no accounts or order id in time) exited 78, so a
    supervisor would never retry.
  - C2-4 (MEDIUM): the target reprice was capped by the position only, not by
    other working sells.
  - C2-5 to C2-11 (LOW): the fallback sold next to an Inactive leg it never
    cancelled; a working entry whose stop died was neither flagged nor
    cancelled; a start after the stop time still flattened, at any hour; the
    cancel and execution orderRef guards were untested; a missing order id
    after the fallback's cancels was uncaught; TWS error 1101 was ignored; the
    code digest left out the kill switch, manual-order and calendar modules,
    and the engine never checked that its authority named the code it runs.
  - Also requested: a test for a commission report before its execution, a
    test that kills the surviving "saved fill rewritten every tick" mutant, a
    database backup before promote and a hard interlock with the bridge.
- Fixed on `feat/c2-execution-engine`: every finding and request, each with
  a regression test named after it in
  `backend/tests/test_live_executor_review.py` (CLI backup tests in
  `test_autotraderctl_execution.py`). Every reviewer probe fails on the fixed
  code, and removing any fix in a scratch copy fails its test. Filled sells of
  other clients come from `reqCompletedOrders(apiOnly=False)` with their
  filled quantity and completion time; one without a readable time zone
  counts as after the entry (its shares stay reserved), and a CRITICAL
  follows when the position still does not show such a sell after 15 s (the
  bridge's L-e).
- 2026-10-13: independent re-review of 3579e92 — READY-FOR-DRILL: a
  supervised PAPER drill only, conditional on the reviewer's drill checklist.
  All eight first-review probes no longer reproduce. New findings (R-1 and
  R-2 must be fixed before any unattended use):
  - R-1 (MEDIUM): a snapshot TWS does not answer ended the flatten for good:
    at its start (stop left live) and in the fallback after both cancels were
    confirmed (no stop, no target, no sell).
  - R-2 (MEDIUM): a completed sell whose filledQuantity is empty counted as
    0 sold (ibapi 9.81 decodes an empty field as 0.0), which reopens the
    C2-1 double sell.
  - R-3 (MEDIUM-LOW): the engine's own earlier bracket in the same symbol, or
    any same-day sell without a readable time zone, counted as another
    order's unshown sale: no flatten sell, a false CRITICAL.
  - R-4 (LOW): a restart after the stop time neither finished nor flagged a
    fallback that had already cancelled target and stop.
  - R-5 (LOW): the bridge interlock missed
    `python -m scripts_local.paper_bridge`, read the heartbeat from the
    engine's own database rather than the bridge's, and was tested only with
    a mocked pgrep.
  - R-6 (LOW): the code digest left out the package `__init__` modules the
    engine imports for their side effects and `db/core.py`; its completeness
    test followed explicit imports only.
  - R-7 (LOW): only promote backs the database up; revoke, the Telegram
    assistant and supervisor children would apply the ledger migration
    without one. Covered by the explicit-migration work on the main line (see
    "Migration numbering" below), not on this branch.
- Fixed on `feat/c2-execution-engine`, each with a regression test named
  after it in `backend/tests/test_live_executor_rereview.py`; removing any
  fix in a scratch copy fails its test:
  - R-1: no flatten ends on a missing snapshot. Nothing is sent and the same
    decision is retried every tick on a fresh snapshot: the start (and a
    fallback's start) until 10 minutes before the close, the market sell
    after both cancels until the close, then a CRITICAL; a recorded attempt
    is still never restarted after a restart [N5]. Shares without stop,
    target or a working market sell are flagged UNPROTECTED at once.
  - R-2: a completed sell counts its whole size when it is Filled or reports
    a filledQuantity of 0. A sell cancelled unfilled after the entry
    therefore also keeps its shares reserved (no flatten sell for them, a
    CRITICAL) until the drill shows how TWS reports the field.
  - R-3: the engine's own legs never count as another order's sale. Each
    bracket keeps, with its ledger claim, the orders TWS reported completed
    before its entry (the entry gate reads completed orders too), so an
    earlier sale never counts whatever its completedTime says; only brackets
    without that record use the completion time.
  - R-4: a restart after the stop time flags a fallback that cancelled
    target and stop as UNPROTECTED and, before the close and after the full
    start checks, finishes its one market sell.
  - R-5: the interlock matches the supervisor's launch line, a hand start and
    the module form; it reads the heartbeat, read-only, from the bridge's
    own database; a darwin test runs the real pgrep.
  - R-6: the digest binds the whole import graph of the engine and its
    runner (package `__init__` modules, `db/core.py` and what they import:
    37 files); its test walks that graph.
  - R-7: left to the main line's explicit migrations (below); the runner
    never migrates.

### Migration numbering

The engine's ledger is migration 13 on this branch and has never been
applied to the owner's database (it is at 12). The main line is moving to
explicit migrations: children refuse to start while a migration is pending,
and `autotraderctl db migrate` takes a lock and a unique backup, which also
closes R-7. The main line takes migration 13 for its own manager tables, so
the ledger migration is renumbered to the next free number when the engine
merges, not before. Its number is written in one place only,
`EXECUTION_LEDGER_MIGRATION` in `trading/storage/execution_ledger_schema.py`;
the migration list, the tests and the CLI derive from it. Its pinned
checksum covers the number, so it is pinned again at the renumbering. Until
then, on this branch, any `init_db` still applies pending migrations (R-7).

## Still to do

- Supervised first live runs: the first owner order through the bridge (the
  09:35 one-share test) and the first 15:45 flatten modify against a real TWS.
- Execution engine: a re-review of the R-1 to R-6 fixes and of the runner
  (`trading/execution/runner.py` and `execution run` in `trading/cli.py`).
- Execution engine: the supervised drill on the paper account (the
  reviewer's checklist), started with `execution run --confirm` on a day the
  bridge does not run (a supervisor drill mode is still to be built). It
  captures the recorded callback sequences from a real TWS (bracket fill,
  partial fill, modify echo, rejected modify, restart mid-send), checks how
  TWS reports untransmitted legs after a client crash, and records exactly
  how `reqCompletedOrders(apiOnly=False)` reports TWS GUI sells: the raw
  filledQuantity of a filled, a partly filled then cancelled and an unfilled
  cancelled sell, and the `completedTime` format and time zone.
- Execution engine: a decision on how owner manual orders are sent once the
  bridge is retired; it needs its own review.
- Replace the bridge with the execution engine (ledger, reconciliation every 2
  minutes, shared risk book) only after the review, the drill and a clean paper
  week; the old `trading/execution/` transport stack is deleted at the same
  time.
