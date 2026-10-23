# PAPER execution status

Working engineering note (updated 2026-10-15, bridge v7a). It records how PAPER orders are
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
- **Bot decisions** — accepted decisions of strategies whose catalogue name is
  exactly one of the sendable identities (`SENDABLE_STRATEGY_NAMES` in the
  bridge, pinned by a test to `trading.sendable_identities.SENDABLE_IDENTITIES`;
  v7a: `MOMO_HOD_V2` only; v7b: `MOMO_HOD_V2`, `ORB_V2`, `VWAP_PB_V2`,
  `GAP_GO_V2`). Other strategies are recorded but never sent. Every
  sendable identity has a caps row (`IDENTITY_CAPS`: max positions, max entries
  per day, last entry as an offset from the session close) and a fixed size
  multiplier (`trading.risk.identity_sizing`, applied upstream in the shadow
  pipeline). A sendable name without a row is skipped, never sent at defaults.
- **Owner manual orders** — rows queued in `autotrader_manual_orders`
  (migration 12) by the Telegram assistant after the deterministic parser and
  validator accept them. The bridge claims a row atomically, re-validates it
  against live positions and working orders, and only then sends it.

## Containment rules (enforced and tested)

- Every managed account must be a paper (`DU…`) account, checked at connect
  and on every restart; orders carry the account explicitly.
- Bot entries are native brackets (parent DAY limit, GTC target, GTC stop).
  State is persisted before any order is sent; a crash never resends.
- Per identity at most `max_positions` bot positions and `max_entries_per_day`
  entries (every attempt counts); across all identities at most the sum of
  `max_positions` (v7a: 3), one per symbol, only in symbols the account does
  not already hold; per-order caps of 25% notional and 1% risk; a 2% daily-loss
  stop. The global kill switch (`paper`) and the identity's own fence
  (`paper:<IDENTITY>`, `autotraderctl paper stop|resume --identity`) block new
  entries; an unreadable fence fails closed. Both are read right before every
  entry.
- Every order carries `orderRef autotrader:<IDENTITY>:<decision id prefix>`
  so IBKR reports attribute fills per identity.
- Before any launch the supervisor refuses (exit 78, phone line) if the
  strategy registry, the sendable tuple and the size table disagree; the dry
  run also runs the bridge's own `--preflight` (config, caps rows, fences)
  with the launch's interpreter, environment and cwd.
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
- 2026-10-15: bridge v7a (per-identity authority: exact-match allowlist,
  identity fence rows, caps and last-entry rows, orderRef identity,
  `--preflight`), sha256
  a63982cc01c36ffcb7d1b7877dfdec4b0b2c7371911d5909e5f36893cbbfb440. Values
  reproduce v5's behaviour for `MOMO_HOD_V2` on normal days (one known change:
  on a 13:00 close its last entry is 12:00, not 12:30). Independent
  execution-safety review of commit 3354c3c (2026-10-15): **GO** on the order
  path — every claim verified hunk by hunk, 13/13 plan mutations killed, no
  HIGH. Findings fixed in the follow-up commit without touching the bridge
  file (pin unchanged): F-1 MEDIUM test hermeticity (dry-run tests ran the
  real bridge preflight against the operator's home; every test now stubs it
  and the suite runs under a scratch HOME), F-2 LOW the notifier forwards the
  identity-fence and caps SKIPs, F-4 LOW a registry check that cannot run is
  a refusal token, F-5 LOW a hung preflight keeps its partial output, F-6 LOW
  Telegram status names the identity fences that are on, F-7 LOW a
  tautological test assertion. Deploy only pre-session, never mid-session (a
  bracket placed by v5 would be re-encoded with the v7a orderRef on a modify).
- 2026-10-15: bridge v7a.1 (small logic follow-up, deployed with v7a), sha256
  9790eb3c1e81a3c74ceaab5245570ea07ee9cbfd407a0fa167d51015456af01f: F-3 — `Caps` rows are validated at import (positions >= 1, entries
  per day None or >= 1, last-entry offset in (0, 6 h]) and `IDENTITY_CAPS` is a
  read-only mapping; the shared read-only opener `_read_only` retries a
  transient "unable to open database file" three times at 250 ms while the
  file exists and raises at once otherwise, so --preflight and the session's
  fence reads keep one fail-closed path (the LEARNED 10-14 artefact did not
  reproduce on SQLite 3.50.4: a `mode=ro` open of a WAL database with its -shm
  absent succeeds in a writable directory and fails only in a read-only one);
  the AI day manager counts only `MOMO_HOD_V2` brackets toward MOMO's slots
  (`trading/manager/context.py`), so other identities' brackets never shrink
  its view from v7b on. Independent execution-safety re-read of the v7a.1
  hunks (2026-10-15): **GO**, nothing on the order path. Two LOW
  availability-only notes, fail-closed as shipped, to fold into the next
  logic re-pin (not v7b, which is data-only): the opener's retry wraps only
  `connect()`, not a CANTOPEN raised at the first statement; with the database
  being replaced at that instant the two fence reads can add about one second
  between the last expiry check and `placeOrder` (vs the 15 s TTL) — re-run
  the expiry check after the fence reads.
- 2026-10-15: v7a.2 (logic outside the bridge; bridge file and pin unchanged
  at 9790eb3c…). Found by the v7b data-only build, which could not even import:
  `trading/rules/all_session.py` refused any all-session name in
  `SENDABLE_IDENTITIES` at import and in the constructor (a pre-v7 tripwire);
  both guards now refuse only MOMO's prefix, and whether a V2 name may send is
  decided by `SENDABLE_IDENTITIES` and the launch registry check. The
  notifier's real-decision filter (`is_real_decision`) matches the sendable
  identities exactly instead of the `MOMO_HOD` prefix, so a granted V2
  identity's real-order lines reach the phone at once and `MOMO_HOD_V1`
  (shadow-only) goes to the digest. Independent re-read (2026-10-15): **GO**,
  no authority widened (the dropped guard legs were a no-op at the MOMO-only
  tuple). Two findings that become MEDIUM at v7b go into v7a.3: the API's
  reserved-name check must refuse every sendable identity by exact name (not
  only MOMO's prefix), and the screener's held-instrument filter must use
  exact identities so held V2 brackets keep a flatten quote source.
- 2026-10-15: v7a.3 (logic outside the bridge; bridge file and pin unchanged
  at 9790eb3c…): `is_api_reserved_name` refuses every sendable identity by
  exact name and any case or whitespace variant (the API can no longer create
  an `ORB_V2`-named draft once ORB_V2 is granted); `require_creatable_name`
  refuses variants of a sendable identity; the screener's `held_instruments`
  uses `is_sendable_identity`, so held brackets of every sender are quoted for
  the bridge's flatten fallback; MOMO-only prose in five modules updated.
  Independent re-read (2026-10-15): **GO**, no HIGH or MEDIUM; its LOW
  test-coverage gap (the held-instruments test now distinguishes the exact
  gate from the old prefix under a widened tuple) and two prose nits fixed in
  the following docs/tests commit.
- 2026-10-15: bridge v7b (data only, on top of v7a.3), sha256 7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4: the
  allowlist becomes MOMO_HOD_V2, ORB_V2, VWAP_PB_V2, GAP_GO_V2; caps rows
  ORB_V2 (1 position, 1 entry/day, last entry close-30m), VWAP_PB_V2 (1, 2,
  close-30m), GAP_GO_V2 (1, 1, close-30m); size multipliers 0.25 / 0.5 / 0.5;
  `SENDABLE_KEYS` orb-v2, vwap-v2, gap-v2. `git diff e5997af..12fc726` (v7a.3 → v7b) on the bridge
  and the trading package is rows, names, the version line, the pin and the
  poll-slot table (the three V2 children keep their reviewed slots; the
  unscheduled V1 slots moved behind them) — no logic. Independent execution-safety review of the data diff (2026-10-15):
  **GO** — data-only confirmed hunk by hunk, every table equals the owner's
  decision, 4/4 mutations killed, no HIGH or MEDIUM. All caps full = 2.125 %
  open risk / 63.75 % notional against the realized-only −2 % stop (worst
  realized day ≈ 4 %); the owner decides awake whether a later logic commit
  adds an open-risk term.
  Deploys only after one observed v7a session (Friday 2026-10-16), planned
  Monday 2026-10-19 (Sunday deploy with the Gateway 2FA login).

- 2026-10-16: v7b.1 (outside the bridge; bridge file and pin unchanged at
  7d313fd0…). Found while building promotion Stage 1: the repository runner
  (`RepositoryBackedShadowRunner.open_session`) rebuilt the shadow pipeline
  field by field and dropped `identity_multiplier`, so every production
  session sized every identity at ×1 whatever its row said. Under v7a this
  changed nothing (MOMO_HOD_V2 is ×1); under v7b as reviewed it would have
  sent ORB_V2 at 4× and VWAP_PB_V2/GAP_GO_V2 at 2× their rows, inside the
  bridge's 1 %/25 % ceiling. The dropped keyword also bypassed the
  fail-closed missing-row block (`identity_sizing_missing`) on v7a and v7b:
  a sendable identity with no row was sized ×1, and only the launch
  preflight `registry_problems` held the line. Fix:
  `ShadowPipeline.with_decision_recorder` copies every constructor argument
  and the runner uses it; a regression test replays the recorded 2026-10-12
  MOMO day through the runner at ×0.25 (3726 → 931 shares). v7b.1's copy
  test checked only the arguments its fixture set; since v7b.2 the test sets
  every constructor argument off its default, so it ties the copy to the
  signature for every argument. v7b.2 (tests and docs only) also checks the
  first hop (`run_shadow_session` passes each sendable identity's row and ×1
  for every shadow key), the missing-row block through the runner, and the
  lease-checked recorder. Independent review of v7b.1: GO (N11,
  docs/reviews/2026-10-16-v7b1-review.md; its MEDIUM-1/2 and LOW-1/2 are the
  v7b.2 items above). v7b deploys only with this fix, v7b.2 and a GO re-read
  naming the deployed head.

- 2026-10-23/24: bridge v7c-t1, the no-limit PAPER test L-T1 (owner plan v2 of
  2026-10-23, items 3-5; decisions D7, D8), sha256
  4096ddcd7ff0a29c9f54373d6dbd41d8033c20ef145cd411abb2c826171a6ba4 (integration head on bridge-t1;
  builder B's a29ba9d was c03f25d6…) — **status: built, awaiting the independent
  execution-safety review N21; not deployable before its GO naming the head.**
  On top of bridge v7c (b40fa40b…). The literal `NO_LIMIT_TEST = True` (never
  configuration; an import tripwire refuses a half-on table) lifts the
  per-identity position and entries/day caps and the account-wide cap (the
  last-entry offsets stay); for bot entries the -2 % daily loss stop and the
  3 % day risk budget are computed on every signal and logged
  (`v7c_budget_ok`, or `t1_over_v7c_limits` with the v7c refusals it would
  have made) right before the order is stored and placed, and never refuse it;
  owner buys keep every v7c refusal (D8). A robot position without a working
  stop still refuses every entry and owner buy; its stop is resubmitted once
  (never looser, joined to the target's OCA group), and if that is rejected or
  the position is still unprotected 45 s after detection it is closed at
  market, flat within 60 s (`t1_stop_repair`; changed from 60 s at the T1
  integration, owner decision). A manual or foreign position without a stop pages
  once per symbol per day and no longer blocks bot entries (D7). Six
  malfunction guards (20 entries per identity per 60 s; 3 consecutive broker
  rejections halt an identity for the day, in memory and recounted from the
  day's state; 3 entries per symbol per day; a duplicate within 5 s; any entry
  outside the XNYS session); an IBKR rejection is never retried and pages once
  per identity per day; account values (request 7004) are logged, never
  gating and never the net liquidation the checks use; one `t1_snapshot` line
  per decision; the 15:45 close-out waits for all bids once. State schema
  unchanged (v5; five optional Bracket fields); lazy imports unchanged (imports
  pin 7b1df1d4824e). `deploy undo` restores the reviewed v7c bridge.

## Still to do

- Supervised first live runs: the first owner order through the bridge (the
  09:35 one-share test) and the first 15:45 flatten modify against a real TWS.
- Replace the bridge with the execution engine (ledger, reconciliation every 2
  minutes, shared risk book). Its groundwork is built and unwired on branch
  `feat/c2-execution-engine`; it needs its own independent review, a
  supervised drill and a clean paper week, after which the old
  `trading/execution/` transport stack is deleted.
