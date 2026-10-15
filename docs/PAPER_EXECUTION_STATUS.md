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
  v7a: `MOMO_HOD_V2` only). Other strategies are recorded but never sent. Every
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
  tautological test assertion. Deferred to the v7b re-pin: F-3 LOW
  (validate `Caps` values and freeze `IDENTITY_CAPS`). Deploy only
  pre-session, never mid-session (a bracket placed by v5 would be re-encoded
  with the v7a orderRef on a modify).

## Still to do

- Supervised first live runs: the first owner order through the bridge (the
  09:35 one-share test) and the first 15:45 flatten modify against a real TWS.
- Replace the bridge with the execution engine (ledger, reconciliation every 2
  minutes, shared risk book). Its groundwork is built and unwired on branch
  `feat/c2-execution-engine`; it needs its own independent review, a
  supervised drill and a clean paper week, after which the old
  `trading/execution/` transport stack is deleted.
