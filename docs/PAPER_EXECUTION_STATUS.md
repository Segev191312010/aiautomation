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

## The execution engine (C2): built, not wired; drill preparation done, re-review pending

`backend/trading/execution/live_executor.py` is built and tested against a fake
TWS only. Nothing schedules it: no supervisor child, LaunchAgent or schedule
starts it. Only `autotraderctl execution run --confirm [--date D]`
(`trading/execution/runner.py`) starts it, by hand, for the supervised drill.
Before it contacts TWS, the runner refuses (exit 78) without an explicit
promotion record for the exact code it runs (`autotraderctl execution promote
--confirm`, MOMO_HOD strategies and DU accounts only), while the bridge runs (a
`paper_bridge.py` process in any launch form, or a heartbeat under 60 s old in
the bridge's own database), unless the database is at exactly this code's
schema (it never migrates), without the shadow configuration's paper account,
or on a day without a session. `promote` and `revoke` never migrate either:
they refuse (exit 78) anything but exactly this code's schema, read-only.
`start()` repeats every check once connected. Started after the session's
stop time, the engine only reports what its ledger holds and finishes a
fallback sell whose cancels were already sent, before the close. It
connects with the bridge's order client id; a lost TWS exits 75 and is never
reconnected in-process (a restart reconciles from the ledger). What it adds
over the bridge:

- a SQLite ledger (migration 14; see "Migration numbering") instead of a
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
- the code digest binds the whole import graph of the engine and its runner,
  the process entry files (`autotraderctl.py`, `trading/cli.py`) and the
  installed ibapi release, which must be the reviewed 9.81.1.post1. The
  branch merged the deployed main line (88b963d: explicit migrations 13-16,
  the AI day manager), which changed that digest: every grant must be issued
  again, after the re-review, for the code that is deployed.
- `autotraderctl run --drill-date YYYY-MM-DD` (an explicit flag only; no
  config key, no environment variable) plans that one session without the
  bridge, logs `DRILL_MODE date=... bridge=not-planned`, and refuses unless
  the deployed engine code holds an active PAPER grant (what `execution
  status` shows); the dry run prints `DRILL MODE: bridge not planned`. Every
  other session is planned exactly as before.
- `autotraderctl execution drill-capture --out <json>` records, read-only
  and on its own TWS client id (30), what a real TWS reports for the drill
  checklist; it refuses inside a trading day or next to the bridge, and its
  source can never place, modify or cancel an order (a static test).

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
- 2026-10-13: second re-review of 5275373 — NOT-READY on one drill blocker
  (HIGH), plus LOW findings; all reviewer mutants were caught.
  - HIGH: `execution promote` and `revoke` ran `init_db` on the owner's
    database and would have applied this branch's ledger migration 13. The
    main line refuses unknown migrations, so the live `paper stop` kill
    switch, the screener, shadow runs and the Telegram assistant would all
    have refused to start; and the main line has taken migration 13 itself
    (manager setups).
  - LOW: after the stop time the R-4 path acted on the first load's bracket
    copies (A); a fallback sell TWS never acknowledged counted as working
    (D); a recorded but unsent sell was not flagged after the stop time (E);
    the digest left out the process entry files and the ibapi release R-2
    depends on. B: a cancelled sell reporting filledQuantity 0 keeps its
    shares reserved (R-2's conservative rule, kept for now).
  - Probe F (third pass, 2026-10-14): D's fix trusted the F leg's persisted
    "Submitted" when TWS reported nothing after a restart, so a market sell
    gone with a TWS restart counted as working. Fixed: only what IBKR
    reports now counts; such a sell is flagged UNPROTECTED and is a
    `missing_order` finding (`test_rr3_f_*`).
- Fixed: the ledger is migration 14, shipped alone on `c2/ledger-schema-14`
  (see "Migration numbering"); promote, revoke and the runner never migrate
  and refuse anything but exactly this code's schema (exit 78); A, D and E
  and the digest are fixed with regression tests (`test_rr2_*`,
  `test_r6_*`); B's alerts now say the shares "may have sold, not confirmed
  by the position".

### Migration numbering and the drill database (settled)

The engine's ledger is migration 14, after the main line's 13 (manager
setups); checksum
`01f8962c8d82e11d2617b6f0d4886e960e600c22abfb380ac71e8b6ee2fb7f8c`. Its
number is written in one place only, `EXECUTION_LEDGER_MIGRATION` in
`trading/storage/execution_ledger_schema.py`.

The schema ships ahead of the engine: branch `c2/ledger-schema-14` is the
main line plus the ledger schema module registered as migration 14 and its
schema tests (`tests/test_execution_ledger_schema.py`), with no engine,
runner or CLI code; the main line merges it on its own. The owner's database
(at 12) then gets migrations 13 and 14 through the main line's explicit
`autotraderctl db migrate`: an exclusive lock, a verified backup, after the
close. A dry run on a copy of the owner's database applied both cleanly (only
new, empty tables; integrity and foreign keys clean; a second run is a
no-op). Nothing on the engine branch migrates: promote, revoke and the runner
refuse anything but exactly this code's schema, read-only, and this branch
carries the very same migrations 13 and 14 (it merged `c2/ledger-schema-14`).

On the drill day the bridge must not run (the interlock refuses otherwise),
and the kill switch stays the normal `autotraderctl paper stop`: it writes
only the fence row, never migrates and never waits for a migration, and the
engine reads it before every entry.

### Deploy order and the drill checklist

Deploy order, each step after the close:

1. The main line with migrations 13-16 is deployed (2026-10-14 02:35 Israel;
   the owner's database is at v16; those migrations are frozen).
2. Merge the engine branch (`feat/c2-execution-engine`, which carries the
   same 13-16 and no other migration) after its re-review; nothing in it
   runs on a schedule.
3. `autotraderctl execution promote --confirm` for `MOMO_HOD_V2` on the paper
   account, for the deployed digest (`execution status` shows it); promote
   never migrates and backs the database up first.
4. The drill day: `autotraderctl run --drill-date D` (the supervisor plans
   that session without the bridge), then `autotraderctl execution run
   --confirm --date D` by hand, supervised all day. The kill switch stays
   the normal `autotraderctl paper stop`.

Drill checklist (the reviewer's seven points, plus what the two re-reviews
added), to be recorded from the real TWS, the capture script first
(`execution drill-capture`, the evening before, bridge stopped):

- a bracket fill, a partial fill, a modify echo, a rejected modify and a
  restart mid-send, as recorded callback sequences;
- how TWS reports untransmitted legs after a client crash;
- `reqCompletedOrders(apiOnly=False)` for TWS GUI sells: the raw
  filledQuantity of a filled, a partly filled then cancelled and an unfilled
  cancelled sell; `completedTime`'s format and zone; permId; whether
  Inactive orders are listed (R-2, R-3, B);
- whether `reqExecutions` with an empty client filter returns the GUI's
  executions, a positive fill signal that could replace B's conservative
  rule;
- the lag of a `reqPositions` answer after a `Filled` report, and the
  four-part snapshot's latency (R-1's retries are sized for it);
- what TWS sends on 1101 and whether a renewed `reqPositions` answers;
- error 326 on a second connection with the engine's client id (the
  interlock's premise);
- a fallback sell across a TWS restart: does TWS still list it, or must the
  UNPROTECTED path (probe F) carry the day.

## Still to do

- Supervised first live runs: the first owner order through the bridge (the
  09:35 one-share test) and the first 15:45 flatten modify against a real TWS.
- Execution engine: a re-review of the second re-review's fixes, probe F,
  the merge of the deployed main line (88b963d), the drill mode, the capture
  script and the runner.
- Execution engine: the supervised drill on the paper account, per the
  deploy order and checklist above.
- Execution engine: a decision on how owner manual orders are sent once the
  bridge is retired; it needs its own review.
- Replace the bridge with the execution engine (ledger, reconciliation every 2
  minutes, shared risk book) only after the review, the drill and a clean paper
  week; the old `trading/execution/` transport stack is deleted at the same
  time.
