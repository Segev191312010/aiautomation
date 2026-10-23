# Bridge v7c rehearsal V1-V9 (layer L-v7c)

Candidate `bridge-v7c` b5f2339 (code = 64a7d71, GO through N18h; ledger line 105 of
`docs/reviews/2026-10-15-ledger.md`), bridge pin `b40fa40b4acd...`, imports_sha `7b1df1d4824e`,
migration 17 (manual-entry-stop). Live: 8675055 (P1-B.4), schema v16, bridge pin `7d313fd075d2`.
Plan: `start-plannng-on-p2-lazy-candle.md`, P3-B "Rehearsal V1-V9", owner decisions D1 and D5.

## How to run

Official run (one command; about 2 minutes plus the live-DB copy):

```
zsh -f /Users/salomon/autotrader-work/rehearsal-v7c/run_rehearsal.sh all
```

- It refuses to start outside **01:40-13:05 Israel** and outside **18:40-06:15 ET** (V7 and V8 run
  `deploy apply`, whose start window is 18:40 -> 06:50 ET). It never runs 13:05-13:50 or
  14:50-23:15 Israel. Until 24 Oct (Israel = ET+7) the window is 01:40-13:05 Israel; from 25 Oct
  to 1 Nov (ET+6) it is **01:40-12:15 Israel**.
- Order: one live-DB copy (SQLite backup API from a `mode=ro` connection), V1-V6, V9, V7, V8; V7
  and V8 are not started after 06:30 ET. Then every database copy and migration backup is deleted,
  the scratch git trees are removed, everything kept is scanned for account ids, and this report is
  regenerated.
- A later docs-only head of `bridge-v7c` can be rehearsed with `V7C_CANDIDATE=<sha>` in front of
  the command. A changed bridge, imports map or live HEAD (8675055) needs the harness constants
  updated (`harness/driver.py`, `harness/bridge_driver.py`, `harness/setup_seed.py`).
- V1-V6 replay the real 2026-10-21 decisions (SPCX, CPRT, KMI, CTSH) of the fresh live-DB copy;
  V1's first check fails loudly if they are no longer in the database.
- `run_rehearsal.sh PRE [V..]` is the shake-out (window predicates shifted; never official),
  `report` regenerates this file, `clean-db` deletes every database copy and rescans.

## What is real and what is fake

- **Real:** the candidate's `paper_bridge.py` (sha256 checked against the pin), its manual desk,
  `trading.execution.manual_orders` (parse, validate, queue) and migration 17, run on a copy of the
  live database; the 2026-10-21 shadow_accepted decisions of that copy, read by the bridge's own
  `accepted_decisions`; `notify_service.bridge_event_text` for the phone text; the candidate's
  `DeployOperator`, imported from the candidate worktree of a fresh clone of the live repository at
  8675055; the clone's own `autotraderctl` (`shadow agent disable|enable`, `db migrate`,
  `db status`, `run --dry-run`); a real `run_supervisor` of the clone with real telegram and
  heartbeat children; `session_check.check_session`.
- **Fake:** IBKR (an in-process `BrokerState` subclass that answers every request through the
  bridge's real callbacks; it opens no socket), launchd (the P2 rehearsal's fake launchd behind a
  launchctl stub), the Keychain (heartbeat URL = a loopback ping server), sntp, the telegram child's
  body (parks; V8 makes the candidate's exit 78), HOME, the account (`DU1234567`), net liquidation
  (665,000.00, about the paper account's per N18), and the owner positions that place open risk.
- V1-V6 drive `Bridge.tick()` on a stepped clock (no 4 s quote waits or 30 s book cadences in real
  time). The startup lines a connect prints (`bridge sha256=`, `PAPER bridge v7c connected`, caps,
  fences, the budget line) are logged through the bridge's own functions, after the supervisor's
  banner, so `bridge.log` has the live shape; `Bridge.startup()` itself is real.

## Safety

- Sandbox: every run under `sandbox-exec -f sandbox-rehearsal-v7c-run.sb`. Diff against the P2
  profile (`out/sandbox-profile.diff`): writes are refused everywhere except this folder and `/dev`
  (the P2 profile allowed writes anywhere but the live paths); the live paths, 7496/7497/4000-4002,
  every non-loopback address, `/bin/launchctl`, `/usr/bin/security`, `/usr/bin/sntp` and signals
  outside the sandbox stay denied. Probed: writes to `~/autotrader-work`, `/private/tmp` and
  `~/aiautomation` refused; the rehearsal folder writable.
- In-process tripwires (rehearsal venv `sitecustomize`, active only with `AUTOTRADER_REHEARSAL=1`
  and HOME under `fakehome/`): socket connects to the IBKR ports or off loopback, name lookups,
  launchctl/security/sntp, session children and a bridge launch without `--preflight` are refused
  and logged; every scenario checks the tripwire log is empty.
- Live checkout: read only with `git --no-optional-locks rev-parse HEAD` (checked unchanged before
  and after every scenario) and `git clone --shared` into `seed/repo`; nothing written there.
- Live DB: read only by `harness/setup_seed.py` (SQLite backup API from `mode=ro`, into
  `seed/autotrader.db`), once per run; size and mtime logged before and after (`out/livedb.txt`).
  Every copy, migration backup and moved-aside v17 file is deleted at the end of a full run.
- Account ids: captured output is masked (`DU*****`); everything kept is scanned (`out/scan.txt`).

## Findings

**Candidate findings: none.** Every check passed against the unmodified candidate (PRE). Notes:

1. A restarted bridge counts an owner buy that the ledger shows `sent` as open risk even when the
   new connection's IBKR fake does not report the order (V6c: AAPL 10 x (190.01 - 185) = 50.10
   counted). Fail closed, as designed; V6c's expectation includes it.
2. The printed ledger line omits `schema=v17` while the deploys.log PASS line carries it: the
   10-18 ledger form, unchanged by v7c.
3. Harness artifact, not a finding: `session_check` rules 1-4 FAIL on the V9 inputs because the log
   is the concatenation of several synthetic bridge processes (states, ledger decisions and caps do
   not line up across scenarios). Only rule 7 is asserted; its control (one budget line pushed to
   3.01 %) FAILs and V4's log (widened-stop and over-budget pages) WARNs, so the PASS is not vacuous.

**V5: what the owner's phone shows** if IP 2,941 sh and VEEV 100 sh are still held with no
covering working stop at v7c's first session (one message each, about 15 s after the bridge's first
book read, ~09:25 ET; the clock is the bridge line's; text through the candidate's
`notify_service.bridge_event_text`):

```
⚠️ CRITICAL v7c_unprotected_position IP: 2941 sh held, working stops cover 0 — every risk-increasing order (bot and manual) is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) · HH:MM:SS ET
⚠️ CRITICAL v7c_unprotected_position VEEV: 100 sh held, working stops cover 0 — every risk-increasing order (bot and manual) is refused; close it with /close VEEV or in TWS, or protect it with a stop that stays working (GTC) · HH:MM:SS ET
```

Every bot entry's SKIP (not sent to the phone) reads `v7c_unprotected_position: IP 2941 sh held,
working stops cover 0 (and 1 more) — every risk-increasing order is refused; close it with /close IP
or in TWS, or protect it with a stop that stays working (GTC) (source=bot) — would have sent ...`;
an owner `/buy` is rejected with the same text and `(source=manual). Nothing sent.`

**Incident (rehearsal builder, 2026-10-22 ~11:32 Israel):** while probing the new sandbox profile
the builder ran `nc -z -G 1 127.0.0.1 7497` inside `sandbox-exec`. The profile refused the connect
(exit 1, no connection made; same refusal as the v7b incident). It breaks the "never probe 7497"
rule and is recorded here; the harness itself never connects anywhere (FakeIB has no socket).

## Results: official run

<!-- official:start -->
| Scenario | What it checks | Official result | Result |
|---|---|---|---|
| V1 | Four budget cases on the real candidate bridge (real 2026-10-21 decisions from the live-DB copy, migrated to v17): within budget -> SENT with its `v7c_budget_ok` line (exact numbers) and the three legs; over budget -> SKIP with token, realized + open + new = total (pct) > 3% of NLV and what it would have sent; +5,000 realized (IBKR reqPnL and bot brackets) counts as 0 loss; an unfilled entry counts qty x (limit - stop), and leaves the budget when the entry dies. | 29/29 checks (2026-10-23T04:14:42+03:00 -> 04:14:43+03:00) | PASS |
| V2 | Two decisions visible in ONE tick (KMI re-timed to CTSH's second): KMI sent and reserved, CTSH skipped with open = owner + KMI's reservation; only KMI's three legs placed; no invariant page. | 13/13 checks (2026-10-23T04:14:43+03:00 -> 04:14:44+03:00) | PASS |
| V3 | KMI held 2,925 @ 34.06; an openOrder echo on the (fake) ibapi reader thread raises its stop to break-even: current_stop = IBKR's price, KMI's risk 0, the move logged old -> new, CTSH (refused before the raise) is then sent with the freed budget; nothing of KMI modified or cancelled. | 17/17 checks (2026-10-23T04:14:44+03:00 -> 04:14:44+03:00) | PASS |
| V4 | The owner LOWERS KMI's stop in TWS (33.85 -> 33.50): counted at 33.50, CRITICAL v7c_stop_widened once and v7c_budget_over once in 25 ticks; no order placed or cancelled, the bracket stays open (never flattened). | 17/17 checks (2026-10-23T04:14:44+03:00 -> 04:14:45+03:00) | PASS |
| V5 | Owner holds IP 2,941 sh and VEEV 100 sh (quantities from the N18 review, not the DB) with no covering stop: the bot entry and an owner /buy are refused with v7c_unprotected_position; one CRITICAL page per symbol after the 15 s grace, never repeated; the exact phone text through notify_service's own formatter; GTC stops placed in TWS unblock at the next book read. | 17/17 checks (2026-10-23T04:14:45+03:00 -> 04:14:46+03:00) | PASS |
| V6 | /buy without a stop refused by the parser (missing_entry_stop) and by migration 17's trigger on the live-DB copy (manual_buy_needs_stop); /buy 10 AAPL stop 185 sent as BUY MKT + attached SELL STP GTC (parentId, orderRef manual:<id>) with its budget line; over budget refused with token and numbers, final after the budget frees; /closeall on a 1.06 % book keeps the attached stop (N18 MEDIUM-1 fix). | 29/29 checks (2026-10-23T04:14:46+03:00 -> 04:14:47+03:00) | PASS |
| V7 | `deploy apply --expect-migration 17` (the real DeployOperator from the candidate worktree) from a rehearsal clone of live 8675055 to b5f2339, fake HOME, fake launchd running a real supervisor: PREV line (schema=v16), stop, ff, db migrate (backup line, `migrated  v16 -> v17`), db status current, dry run (new pin, `imports reviewed (3 files)`, budget line once), start + 20 s settle, PASS line `schema=v17`; undo pre-checks then refuse a code-only undo (restore first). | 54/54 checks (2026-10-23T04:14:50+03:00 -> 04:15:21+03:00) | PASS |
| V8 | Post-migration failure (the candidate's telegram child exits 78 3 s after its start) -> FAIL post_start_child_exited, HALTED reason=post_start_child_exited backup=<v16 backup>, robot disabled, RESTORE printed and NOT run by the tool; the printed RESTORE then EXECUTED line by line on the copy -> HEAD 8675055, schema v16, dry run shows the old pin 7d313fd075d2 (no v7c lines), the previous code's supervisor runs. | 41/41 checks (2026-10-23T04:15:21+03:00 -> 04:15:38+03:00) | PASS |
| V9 | Dry run of the candidate checkout on a v17 copy: REVIEWED_BRIDGE_SHA256 b40fa40b..., imports map of 3 files with imports_sha 7b1df1d4824e matching the files on disk, `bridge       reviewed (sha256 b40fa40b4acd)`, `imports reviewed (3 files)`, exactly one budget line, v17 current, no !!!; session_check rule 7 on the V1+V2+V3+V6 bridge.log -> PASS (5 bot + 1 owner entry); tampered control -> FAIL; V4's log -> WARN. | 23/23 checks (2026-10-23T04:14:47+03:00 -> 04:14:50+03:00) | PASS |
| all | | 240/240 checks | PASS |
<!-- official:end -->

**Official run history (2026-10-23).** Three starts; the third is the official result above.

| Start (Israel) | Result | Cause | Change |
|---|---|---|---|
| 02:00:30 | 9/9 FAIL at setup | `setup_seed.copy_db` opened the live DB `mode=ro`; with no process holding it (no `-wal`), SQLite must create `-wal`/`-shm`, which the sandbox forbids in the live data dir -> `unable to open database file`; every scenario ran on an empty v0 copy | the copy uses `mode=ro&immutable=1` when `lsof` shows no holder and no `-wal` exists, and refuses the copy if the file's size or mtime changed during it |
| 04:12:45 | 3 PASS, 6 FAIL (few checks each) | the copy carried the live Sentinel fence `paper:MOMO_HOD_V2` (2026-10-22 18:35 Israel, losing streak 5): every MOMO decision became `identity_stopped`, starving the budget checks (V2-V4, V9), adding `fenced identities = MOMO_HOD_V2` (V5 CRITICAL, V7 dry-run line) | the seed copy resumes standing identity fences IN THE COPY ONLY (reason `rehearsal seed copy: resumed before the run (live row: ...)`), printed in the console; the live DB is never written |
| 04:14:41 | **9/9 PASS, 240/240** | | |

Live DB before/after every run: size 389799936, mtime 2026-10-23T00:37:58, no `-wal` (out/livedb.txt). Every copy deleted at the end; account-id scan of everything kept: 0 hits outside the documented fake. A fence still standing at install time is the owner's `deploy apply --expect-fenced <ID>` (P2 `dry_run_fenced`, tested in P2).

## Results: PRE shake-out (harness check, never official)

The V7/V8 window predicates read real time shifted to 02:00 ET; every other clock reading, the
supervisor and its logs are real. Verbatim output: `out/pre/<scenario>.txt`.

<!-- pre:start -->
| Scenario | What it checks | PRE shake-out | Result |
|---|---|---|---|
| V1 | Four budget cases on the real candidate bridge (real 2026-10-21 decisions from the live-DB copy, migrated to v17): within budget -> SENT with its `v7c_budget_ok` line (exact numbers) and the three legs; over budget -> SKIP with token, realized + open + new = total (pct) > 3% of NLV and what it would have sent; +5,000 realized (IBKR reqPnL and bot brackets) counts as 0 loss; an unfilled entry counts qty x (limit - stop), and leaves the budget when the entry dies. | 29/29 checks (2026-10-22T11:50:07+03:00 -> 11:50:08+03:00) | PASS |
| V2 | Two decisions visible in ONE tick (KMI re-timed to CTSH's second): KMI sent and reserved, CTSH skipped with open = owner + KMI's reservation; only KMI's three legs placed; no invariant page. | 13/13 checks (2026-10-22T11:50:08+03:00 -> 11:50:09+03:00) | PASS |
| V3 | KMI held 2,925 @ 34.06; an openOrder echo on the (fake) ibapi reader thread raises its stop to break-even: current_stop = IBKR's price, KMI's risk 0, the move logged old -> new, CTSH (refused before the raise) is then sent with the freed budget; nothing of KMI modified or cancelled. | 17/17 checks (2026-10-22T11:50:09+03:00 -> 11:50:09+03:00) | PASS |
| V4 | The owner LOWERS KMI's stop in TWS (33.85 -> 33.50): counted at 33.50, CRITICAL v7c_stop_widened once and v7c_budget_over once in 25 ticks; no order placed or cancelled, the bracket stays open (never flattened). | 17/17 checks (2026-10-22T11:50:09+03:00 -> 11:50:10+03:00) | PASS |
| V5 | Owner holds IP 2,941 sh and VEEV 100 sh (quantities from the N18 review, not the DB) with no covering stop: the bot entry and an owner /buy are refused with v7c_unprotected_position; one CRITICAL page per symbol after the 15 s grace, never repeated; the exact phone text through notify_service's own formatter; GTC stops placed in TWS unblock at the next book read. | 17/17 checks (2026-10-22T11:50:10+03:00 -> 11:50:11+03:00) | PASS |
| V6 | /buy without a stop refused by the parser (missing_entry_stop) and by migration 17's trigger on the live-DB copy (manual_buy_needs_stop); /buy 10 AAPL stop 185 sent as BUY MKT + attached SELL STP GTC (parentId, orderRef manual:<id>) with its budget line; over budget refused with token and numbers, final after the budget frees; /closeall on a 1.06 % book keeps the attached stop (N18 MEDIUM-1 fix). | 29/29 checks (2026-10-22T11:50:11+03:00 -> 11:50:12+03:00) | PASS |
| V7 | `deploy apply --expect-migration 17` (the real DeployOperator from the candidate worktree) from a rehearsal clone of live 8675055 to b5f2339, fake HOME, fake launchd running a real supervisor: PREV line (schema=v16), stop, ff, db migrate (backup line, `migrated  v16 -> v17`), db status current, dry run (new pin, `imports reviewed (3 files)`, budget line once), start + 20 s settle, PASS line `schema=v17`; undo pre-checks then refuse a code-only undo (restore first). | 54/54 checks (2026-10-22T11:50:14+03:00 -> 11:50:46+03:00) | PASS |
| V8 | Post-migration failure (the candidate's telegram child exits 78 3 s after its start) -> FAIL post_start_child_exited, HALTED reason=post_start_child_exited backup=<v16 backup>, robot disabled, RESTORE printed and NOT run by the tool; the printed RESTORE then EXECUTED line by line on the copy -> HEAD 8675055, schema v16, dry run shows the old pin 7d313fd075d2 (no v7c lines), the previous code's supervisor runs. | 41/41 checks (2026-10-22T11:50:46+03:00 -> 11:51:02+03:00) | PASS |
| V9 | Dry run of the candidate checkout on a v17 copy: REVIEWED_BRIDGE_SHA256 b40fa40b..., imports map of 3 files with imports_sha 7b1df1d4824e matching the files on disk, `bridge       reviewed (sha256 b40fa40b4acd)`, `imports reviewed (3 files)`, exactly one budget line, v17 current, no !!!; session_check rule 7 on the V1+V2+V3+V6 bridge.log -> PASS (5 bot + 1 owner entry); tampered control -> FAIL; V4's log -> WARN. | 23/23 checks (2026-10-22T11:50:12+03:00 -> 11:50:14+03:00) | PASS |
| all | | 240/240 checks | PASS |
<!-- pre:end -->

## Every check: official run

<!-- checks-official:start -->

#### V1 (PASS, 29 checks): the four budget cases: within budget sent with v7c_budget_ok; over budget skipped with token and numbers; realized gains never fund risk; an unfilled entry counts limit - stop

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V1/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V1/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V1/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V1/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | real 2026-10-21 decisions in the database copy | SPCX ORB_V2, CPRT VWAP_PB_V2, KMI MOMO_HOD_V2 present | ['CPRT', 'CTSH', 'DHR', 'IRON', 'KMI', 'PEGA', 'SPCX'] | ok |
| 6 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 7 | V1a open risk before: the owner's 1,000 sh to their TWS stop | 18500.00 | 18500.000 | ok |
| 8 | V1a v7c_budget_ok line with the numbers | present: v7c_budget_ok SPCX (ORB_V2): realized 0.00 + open 18500.00 + new 293.36 = 18793.36 (2.83%) <= 3% of 665000.00 (source=bot) | present | ok |
| 9 | V1a SENT line after its budget line | SENT SPCX (ORB_V2): BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | ['SPCX (ORB_V2): BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 (orders 1000-1002)'] | ok |
| 10 | V1a orders placed: parent LMT DAY, target LMT GTC, stop STP GTC, one orderRef | [('BUY', 'LMT', 152, 'DAY', 'autotrader:ORB_V2:7929276e-3ec'), ('SELL', 'LMT', 152, 'GTC', 'autotrader:ORB_V2:7929276e-3ec'), ('SELL', 'STP', 152, 'GTC', 'autotrader:ORB_V2:7929276e-3ec')] | [('BUY', 'LMT', 152, 'DAY', 'autotrader:ORB_V2:7929276e-3ec'), ('SELL', 'LMT', 152, 'GTC', 'autotrader:ORB_V2:7929276e-3ec'), ('SELL', 'STP', 152, 'GTC', 'autotrader:ORB_V2:7929276e-3ec')] | ok |
| 11 | V1a stop leg price is the intent stop | 161.58 | 161.58 | ok |
| 12 | V1d open risk with SPCX unfilled: 18,500 + 152 x (163.51 - 161.58) | 18793.36 | 18793.360 | ok |
| 13 | V1d open-risk line of the unfilled entry | present: SPCX bot 0 held @ 163.51 + 152 unfilled @ 163.51, stop 161.58: 293.36 | present | ok |
| 14 | V1d the next entry's budget line counts SPCX's unfilled limit - stop as open | present: v7c_budget_ok CPRT (VWAP_PB_V2): realized 0.00 + open 18793.36 + new 476.28 = 19269.64 (2.90%) <= 3% of 665000.00 (source=bot) | present | ok |
| 15 | V1d after the SPCX entry is cancelled unfilled its risk leaves the budget | 18976.28 | 18976.280 | ok |
| 16 | process 2: manual desk attached to the scenario ledger | attached | attached | ok |
| 17 | V1b skip_reason: token, realized + open + new = total (pct) > 3% of NLV, what it would have sent | v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | ok |
| 18 | V1b SKIP line in bridge.log | present: SPCX (ORB_V2): v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | present | ok |
| 19 | V1b nothing placed | [] | [] | ok |
| 20 | V1b no v7c_budget_ok line | none | [] | ok |
| 21 | process 3: manual desk attached to the scenario ledger | attached | attached | ok |
| 22 | V1c the bot's realized P&L today | 5000.00 | 5000.00 | ok |
| 23 | V1c account_realized_loss() with +5,000 realized (IBKR and computed) | 0 | 0 | ok |
| 24 | V1c skipped at realized 0.00 (gains never fund risk); with -5,000 counted it would have fit | v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | ok |
| 25 | V1c the same entry would fit if gains funded risk (19,993.36 - 5,000 <= 19,950) | fits | 14993.36 | ok |
| 26 | V1c nothing placed | [] | [] | ok |
| 27 | V1c realized source logged | present: v7c_pnl_source=reqpnl | present | ok |
| 28 | tripwires (socket / spawn) | none | none | ok |
| 29 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V2 (PASS, 13 checks): two decisions in one tick: the first is sent and reserved, the second is skipped

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V2/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V2/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V2/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V2/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 6 | one tick: KMI sent | sent | sent | ok |
| 7 | KMI budget line | present: v7c_budget_ok KMI (MOMO_HOD_V2): realized 0.00 + open 19200.00 + new 614.25 = 19814.25 (2.98%) <= 3% of 665000.00 (source=bot) | present | ok |
| 8 | same tick: CTSH skipped, its open risk includes KMI's reservation | v7c_day_risk_budget: realized 0.00 + open 19814.25 + new 294.38 = 20108.63 (3.02%) > 3% of 665000.00 (source=bot) — would have sent BUY 718 @ 69.37 \| stop 68.96 \| target 70.19 | v7c_day_risk_budget: realized 0.00 + open 19814.25 + new 294.38 = 20108.63 (3.02%) > 3% of 665000.00 (source=bot) — would have sent BUY 718 @ 69.37 \| stop 68.96 \| target 70.19 | ok |
| 9 | orders placed in that tick: KMI's three legs only | ['autotrader:MOMO_HOD_V2:e76f9cd8-ca6', 'autotrader:MOMO_HOD_V2:e76f9cd8-ca6', 'autotrader:MOMO_HOD_V2:e76f9cd8-ca6'] | ['autotrader:MOMO_HOD_V2:e76f9cd8-ca6', 'autotrader:MOMO_HOD_V2:e76f9cd8-ca6', 'autotrader:MOMO_HOD_V2:e76f9cd8-ca6'] | ok |
| 10 | KMI alone fits, KMI + CTSH does not | 19,814.25 <= 19,950 < 20,108.63 | (Decimal('19814.25'), Decimal('20108.63')) | ok |
| 11 | no v7c_budget_invariant_broken | none | [] | ok |
| 12 | tripwires (socket / spawn) | none | none | ok |
| 13 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V3 (PASS, 17 checks): an openOrder echo raises a held bracket's stop to break-even: budget freed, the next entry is sent

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V3/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V3/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V3/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V3/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 6 | V3: KMI sent | sent | sent | ok |
| 7 | V3: KMI held 2,925 (bracket open) | ('open', 2925) | ('open', 2925) | ok |
| 8 | open risk with KMI held at its intent stop: 19,200 + 2,925 x (34.06 - 33.85) | 19814.25 | 19814.25000 | ok |
| 9 | before the raise CTSH's risk would not fit | v7c_day_risk_budget refusal | v7c_day_risk_budget: realized 0.00 + open 19814.25 + new 294.38 = 20108.63 (3.02%) > 3% of 665000.00 (source=bot) | ok |
| 10 | current_stop after the echo = break-even (IBKR's price, not the intent) | 34.06 | 34.06 | ok |
| 11 | open risk after the raise: KMI risks 0, only the owner's 19,200 remains | 19200.00 | 19200.000 | ok |
| 12 | the stop move is logged old -> new | present: v7c stop KMI order 1002: 33.85 -> 34.06 | present | ok |
| 13 | the freed budget sends CTSH (budget line) | present: v7c_budget_ok CTSH (VWAP_PB_V2): realized 0.00 + open 19200.00 + new 294.38 = 19494.38 (2.93%) <= 3% of 665000.00 (source=bot) | present | ok |
| 14 | CTSH sent | sent | sent | ok |
| 15 | nothing about KMI was modified or cancelled | no KMI order re-sent, no cancel | ([], []) | ok |
| 16 | tripwires (socket / spawn) | none | none | ok |
| 17 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V4 (PASS, 17 checks): a widened stop: CRITICAL v7c_stop_widened once (and v7c_budget_over), counted at the new price, never flattened or modified

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V4/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V4/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V4/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V4/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 6 | V4: KMI sent | sent | sent | ok |
| 7 | V4: KMI held 2,925 (bracket open) | ('open', 2925) | ('open', 2925) | ok |
| 8 | the widened stop counts at its new price: 18,500 + 2,925 x (34.06 - 33.50) | 20138.00 | 20138.00000 | ok |
| 9 | CRITICAL v7c_stop_widened exactly once in 20 ticks | 1 | 1 | ok |
| 10 | CRITICAL v7c_budget_over exactly once (one 0.5 % step) | 1 | 1 | ok |
| 11 | never flattened or modified: no order placed after the widening | 3 | 3 | ok |
| 12 | never flattened or modified: no cancel after the widening | [] | [] | ok |
| 13 | the bracket is still open with all its shares | ('open', 2925) | ('open', 2925) | ok |
| 14 | no FLATTEN line | none | [] | ok |
| 15 | no repeat page after 25 ticks | (1, 1) | (1, 1) | ok |
| 16 | tripwires (socket / spawn) | none | none | ok |
| 17 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V5 (PASS, 17 checks): an unprotected foreign position (IP 2941 sh, VEEV 100 sh, no covering stop) blocks bot and manual entries and pages once each; the exact phone text

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V5/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V5/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V5/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V5/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 6 | owner /buy rejected: reject_code | v7c_unprotected_position | v7c_unprotected_position | ok |
| 7 | owner /buy rejected: status and error | ('rejected', 'v7c_unprotected_position: IP 2941 sh held, working stops cover 0 (and 1 more) — every risk-increasing order is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) (source=manual). Nothing sent.') | ('rejected', 'v7c_unprotected_position: IP 2941 sh held, working stops cover 0 (and 1 more) — every risk-increasing order is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) (source=manual). Nothing sent.') | ok |
| 8 | bot entry SPCX skipped: skip_reason | v7c_unprotected_position: IP 2941 sh held, working stops cover 0 (and 1 more) — every risk-increasing order is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | v7c_unprotected_position: IP 2941 sh held, working stops cover 0 (and 1 more) — every risk-increasing order is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | ok |
| 9 | nothing placed (bot or manual) | [] | [] | ok |
| 10 | IP paged exactly once in 30+ s | 1 | 1 | ok |
| 11 | VEEV paged exactly once in 30+ s | 1 | 1 | ok |
| 12 | no other CRITICAL | [] | [] | ok |
| 13 | the phone text the owner sees (notifier's own formatter), IP then VEEV | ⚠️ <page> · HH:MM:SS ET for each | ['⚠️ CRITICAL v7c_unprotected_position IP: 2941 sh held, working stops cover 0 — every risk-increasing order (bot and manual) is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) · 21:14:46 ET', '⚠️ CRITICAL v7c_unprotected_position VEEV: 100 sh held, working stops cover 0 — every risk-increasing order (bot and manual) is refused; close it with /close VEEV or in TWS, or protect it with a stop that stays working (GTC) · 21:14:46 ET'] | ok |
| 14 | after the owner's GTC stops: nothing unprotected | () | () | ok |
| 15 | open risk then = 2,941 x (33.94 - 32.50) + 100 x (311.15 - 300.00) | 5350.04 | 5350.0400 | ok |
| 16 | tripwires (socket / spawn) | none | none | ok |
| 17 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V6 (PASS, 29 checks): manual /buy without a stop (parser and migration-17 trigger), under budget (attached GTC stop sent), over budget (refused, final); /closeall keeps the stop of a holding it cannot sell (N18 MEDIUM-1 fix)

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V6/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V6/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V6/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V6/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | /buy 10 AAPL (no stop): parser refusal | ParseRefusal missing_entry_stop | ParseRefusal(code='missing_entry_stop', reason='A buy needs its protective stop, written last: /buy 100 AAPL at 180 stop 175 or /buy $5k NVDA stop 170. Nothing sent.', is_order_attempt=True) | ok |
| 6 | /buy $5k NVDA (no stop): parser refusal | ParseRefusal missing_entry_stop | ParseRefusal(code='missing_entry_stop', reason='A buy needs its protective stop, written last: /buy 100 AAPL at 180 stop 175 or /buy $5k NVDA stop 170. Nothing sent.', is_order_attempt=True) | ok |
| 7 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 8 | a BUY row without entry_stop_price on the migrated live copy: migration 17 trigger | manual_buy_needs_stop | manual_buy_needs_stop | ok |
| 9 | /buy 10 AAPL stop 185 sent as BUY MKT DAY (transmit False) + attached SELL STP GTC (parentId, transmit True) | 2 orders | [(1000, 'BUY', 'MKT', 10, 'DAY', 0, False, 'manual:63e6837b654d41228ad46c2c8c1ff74e'), (1001, 'SELL', 'STP', 10, 'GTC', 1000, True, 'manual:63e6837b654d41228ad46c2c8c1ff74e')] | ok |
| 10 | attached stop price | 185.0 | 185.0 | ok |
| 11 | manual budget line (sized at the ask 190.01) | present: v7c_budget_ok AAPL (manual #63e683): realized 0.00 + open 0.00 + new 50.10 = 50.10 (0.01%) <= 3% of 665000.00 (source=manual) | present | ok |
| 12 | row sent with the parent order id | ('sent', 1000) | ('sent', 1000) | ok |
| 13 | MANUAL SENT line names its GTC stop | present: SENT BUY 10 AAPL MKT #63e683 (order 1000) with its stop 185 GTC (order 1001) | present | ok |
| 14 | process 2: manual desk attached to the scenario ledger | attached | attached | ok |
| 15 | a restarted bridge counts the owner's still-working AAPL buy (10 x (190.01 - 185)) | present: AAPL owner buy 10 not in the position yet @ 190.01, stop 185: 50.10 | present | ok |
| 16 | open risk before the TSLA buy: 19,850 owner + 50.10 working buy | 19900.10 | 19900.1000 | ok |
| 17 | over budget: row rejected with token and numbers | ('rejected', 'v7c_day_risk_budget', 'v7c_day_risk_budget: realized 0.00 + open 19900.10 + new 50.10 = 19950.20 (3.00%) > 3% of 665000.00 (source=manual). Nothing sent.') | ('rejected', 'v7c_day_risk_budget', 'v7c_day_risk_budget: realized 0.00 + open 19900.10 + new 50.10 = 19950.20 (3.00%) > 3% of 665000.00 (source=manual). Nothing sent.') | ok |
| 18 | over budget: MANUAL REJECTED line | present: REJECTED BUY 10 TSLA MKT #eba016: v7c_day_risk_budget: realized 0.00 + open 19900.10 + new 50.10 = 19950.20 (3.00%) > 3% of 665000.00 (source=manual). Nothing sent. (v7c_day_risk_budget) | present | ok |
| 19 | over budget: nothing placed | [] | [] | ok |
| 20 | the refusal is final (no override, no resend once budget frees) | ('rejected', []) | ('rejected', []) | ok |
| 21 | the /buy filled | filled | filled | ok |
| 22 | AAPL protected by its attached stop (nothing unprotected) | () | () | ok |
| 23 | /closeall: AAPL not sold, its stop kept | ('rejected', 'nothing_closed', 'AAPL: spread 1.06% over 1% — not sold; its stop is kept') | ('rejected', 'nothing_closed', 'AAPL: spread 1.06% over 1% — not sold; its stop is kept') | ok |
| 24 | /closeall: the attached stop was NOT cancelled, nothing sent | ([], []) | ([], []) | ok |
| 25 | /closeall: AAPL still protected | () | () | ok |
| 26 | /closeall: no CRITICAL | none | [] | ok |
| 27 | the stop order is still live at IBKR | PreSubmitted | PreSubmitted | ok |
| 28 | tripwires (socket / spawn) | none | none | ok |
| 29 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V7 (PASS, 54 checks): deploy apply --expect-migration 17 from a rehearsal clone of live 8675055 to the candidate: PREV line, stop, ff, db migrate (backup, migrated v16 -> v17), db status current, dry run (pins, imports, budget line), start, PASS line

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | live-DB copy before | v16 current for 8675055; pending (17,) for the candidate code | applied v16 code v17 pending (17,) | ok |
| 2 | exit code | 0 | 0 | ok |
| 3 | deploys.log keeps the 14 earlier lines byte for byte | unchanged prefix | True | ok |
| 4 | deploys.log new lines | 2 | 2 | ok |
| 5 | deploys.log mode | 0o600 | 0o600 | ok |
| 6 | the tool's _DEPLOY_PASS reads every earlier DEPLOY line (10-16 .. 10-22) | 6/6 | 6/6 | ok |
| 7 | PREV line `<iso> v7c PREV_SHA=8675055c0b57.. schema=v16` | parsed by _PREV_LINE | 2026-10-23T04:14:55+03:00 v7c PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 schema=v16 | ok |
| 8 | DEPLOY line parsed by _DEPLOY_PASS | short b5f2339 prev 8675055c0b57 pin b40fa40b4acd schema 17 | 2026-10-23T04:15:18+03:00 DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS schema=v17 | ok |
| 9 | PASS line | <iso> DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS schema=v17 | 2026-10-23T04:15:18+03:00 DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS schema=v17 | ok |
| 10 | _deploy_line_for(HEAD) and _prev_schema read the pair back | the new DEPLOY line, PREV schema 16 | DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS schema=v17; prev schema 16 | ok |
| 11 | printed ledger line | `<iso> \| DEPLOY v7c b5f2339 PREV_SHA=<40> pin=b40fa40b4acd result=PASS ...` | 2026-10-23T04:15:18+03:00 \| DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS \| owner-run via autotraderctl deploy apply (b5f2339); 11 dry-run lines ok; post-checks ok \| ~/Library/Logs/AutotraderV1/deploys.log | ok |
| 12 | steps in order: PREV, stop, window, ff, db migrate (backup, migrated), db status, dry run, window, start, 20 s settle, PASS | all present in order | [5533, 5640, 5838, 5938, 5972, 6099, 6208, 6650, 6840, 6865, 7037, 8144, 8195, 8222, 8695, 8772, 9004, 9237] | ok |
| 13 | step printed: OK deploys.log: | present | True | ok |
| 14 | step printed: STEP stop: | present | True | ok |
| 15 | step printed: OK stopped: | present | True | ok |
| 16 | step printed: OK window before the fast-forward | present | True | ok |
| 17 | step printed: STEP git -C $R/scen/V7/live merge --ff-only b5f2339e19f3c357d4ecf0a3fbce7d6c06ed | present | True | ok |
| 18 | step printed: OK fast-forward: | present | True | ok |
| 19 | step printed: STEP $R/scen/V7/live/backend/.venv/bin/python $R/scen/V7/live/backend/autotrader | present | True | ok |
| 20 | step printed:   \| backup     | present | True | ok |
| 21 | step printed:   \| migrated  v16 -> v17 | present | True | ok |
| 22 | step printed: OK migrated v16 -> v17; backup  | present | True | ok |
| 23 | step printed: STEP $R/scen/V7/live/backend/.venv/bin/python $R/scen/V7/live/backend/autotrader | present | True | ok |
| 24 | step printed: OK dry run: | present | True | ok |
| 25 | step printed: OK window before the start | present | True | ok |
| 26 | step printed: STEP $R/scen/V7/live/backend/.venv/bin/python $R/scen/V7/live/backend/autotrader | present | True | ok |
| 27 | step printed: OK settled: | present | True | ok |
| 28 | step printed: OK started: | present | True | ok |
| 29 | step printed:  \| DEPLOY v7c  | present | True | ok |
| 30 | step printed: deploy apply: PASS v7c b5f2339 (exit 0) | present | True | ok |
| 31 | the plan names the migration | `--expect-migration 17` plan step with db migrate then db status | True | ok |
| 32 | dry-run evidence: bridge       reviewed (sha256 b40fa40b4acd) | present | True | ok |
| 33 | dry-run evidence: bridge       imports reviewed (3 files) | present | True | ok |
| 34 | dry-run evidence: bridge       INFO bridge sha256=b40fa40b4acda4d75115c59a1c391e7b639b4dd1 | present | True | ok |
| 35 | dry-run evidence: bridge       INFO day risk budget 3% of net liquidation, account level ( | present | True | ok |
| 36 | dry-run evidence: bridge       INFO fenced identities = none | present | True | ok |
| 37 | dry-run evidence: bridge       INFO sendable identities = GAP_GO_V2, MOMO_HOD_V2, ORB_V2,  | present | True | ok |
| 38 | dry-run evidence: registry     sendable identities, size table and strategy registry agree | present | True | ok |
| 39 | dry-run evidence: database     schema v17 current (/Users/salomon/autotrader-work/rehearsa | present | True | ok |
| 40 | exactly one `day risk budget 3%` line in the dry run | 1 | 1 | ok |
| 41 | SESSION_WAIT in the dry run | present | True | ok |
| 42 | db migrate's backup is the v16 database in <data>/backups/ | v16 | $R/fakehome/V7/.local/share/autotrader-v1/backups/autotrader.20261023T011455.746933Z.v16-to-v17.db v16 | ok |
| 43 | database after | v17 current | v17 current=True | ok |
| 44 | `db status` of the live clone (now the candidate): current | state     schema v17 is current | ['database  /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V7/.local/share/autotrader-v1/autotrader.db', 'state     schema v17 is current', 'migrate   allowed now (outside every supervised trading day)'] | ok |
| 45 | live clone HEAD after (fast-forward) | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | ok |
| 46 | live clone tree | clean | clean | ok |
| 47 | bridge on disk is the new pin | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 48 | one supervisor running, the clone's, a new pid | 1, not 50642 | [(51039, '$R/scen/V7/live/backend/.venv/bin/python $R/scen/V7/live/backend/autotraderctl.py run')] | ok |
| 49 | plist WorkingDirectory | /Users/salomon/autotrader-work/rehearsal-v7c/scen/V7/live/backend | /Users/salomon/autotrader-work/rehearsal-v7c/scen/V7/live/backend | ok |
| 50 | deploy undo's pre-checks read the migration deploy back (read-only): a code-only undo is refused | refusal `the deploy migrated v16 -> v17: the database must be restored first`, old pin 7d313fd075d2 | refusals=['undo_needs_db_restore'] ['the deploy migrated v16 -> v17: the database must be restored first'] old_pin=7d313fd075d2 | ok |
| 51 | tripwires | no tripwire line | none | ok |
| 52 | live subprocesses use the live checkout's python and cwd | all | 5/5 | ok |
| 53 | no rehearsal process left | none | none | ok |
| 54 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V8 (PASS, 41 checks): DB-restore undo drill: a post-migration failure (the candidate's telegram child exits 78 after its start) -> HALTED with the printed RESTORE; the RESTORE executed by hand on the copy -> v16 back, the old pin shown, the previous code runs

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | copy schema before | v16 current, pending (17,) for the candidate code | applied v16 code v17 pending (17,) | ok |
| 2 | exit code | 4 | 4 | ok |
| 3 | deploys.log keeps the 14 earlier lines byte for byte | unchanged prefix | True | ok |
| 4 | deploys.log new lines | 2 | 2 | ok |
| 5 | deploys.log mode | 0o600 | 0o600 | ok |
| 6 | the tool's _DEPLOY_PASS reads every earlier DEPLOY line (10-16 .. 10-22) | 6/6 | 6/6 | ok |
| 7 | HALTED line | DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57.. pin=b40fa40b4acd result=HALTED reason=post_start_child_exited backup=<path> | 2026-10-23T04:15:33+03:00 DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=HALTED reason=post_start_child_exited backup=/Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V8/.local/share/autotrader-v1/backups/autotrader.20261023T011527.127732Z.v16-to-v17.db | ok |
| 8 | PREV line carries schema=v16 | schema=v16 | 2026-10-23T04:15:26+03:00 v7c PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 schema=v16 | ok |
| 9 | printed: STEP $R/scen/V8/live/backend/.venv/bin | present | True | ok |
| 10 | printed:   \| migrated  v16 -> v17 | present | True | ok |
| 11 | printed: OK migrated v16 -> v17; backup  | present | True | ok |
| 12 | printed: OK dry run: | present | True | ok |
| 13 | printed: FAIL post_start_child_exited: | present | True | ok |
| 14 | printed: The database was migrated (or may have been): HALT, never an automatic undo. | present | True | ok |
| 15 | printed: RESTORE (never run by this command) | present | True | ok |
| 16 | printed: git -C $R/scen/V8/live reset --keep 86 | present | True | ok |
| 17 | printed:   1. pgrep -fl $R/scen/V8/live/backend | present | True | ok |
| 18 | printed: Expect: `bridge       reviewed (sha256 7d313fd075d2)` and `database     schema v | present | True | ok |
| 19 | printed: deploy apply: HALTED reason=post_start_child_exited (exit 4; the robot is disabl | present | True | ok |
| 20 | the failure is the telegram exit after the start | FAIL post_start_child_exited: ... telegram code=78 | ['FAIL post_start_child_exited: 2026-10-23T01:15:32+00:00 CHILD_EXIT telegram code=78 restart_in=15 after the start'] | ok |
| 21 | no automatic reset after the migration | no `reset --keep` before the printed RESTORE | False | ok |
| 22 | HEAD left at the candidate (never reset by the tool) | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | ok |
| 23 | robot left disabled: no supervisor | 0 supervisors | 0 | ok |
| 24 | LaunchAgent disabled and unloaded | enabled False, loaded False | {'enabled': False, 'loaded': False, 'pid': None, 'plist': None, 'spawns': 0} | ok |
| 25 | database migrated by the live clone's db migrate | v17 current | v17 current=True | ok |
| 26 | backup is the v16 database | v16, in <data>/backups/ | $R/fakehome/V8/.local/share/autotrader-v1/backups/autotrader.20261023T011527.127732Z.v16-to-v17.db v16 | ok |
| 27 | restore script ran | exit 0 | 0 | ok |
| 28 | after the restore: HEAD | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |
| 29 | after the restore: database | schema v16 (current for 8675055 code) | v16 current=False | ok |
| 30 | restore dry run shows the old pin / v16: bridge       reviewed (sha256 7d313fd075d2) | present | True | ok |
| 31 | restore dry run shows the old pin / v16: database     schema v16 current (/Users/salomon/autotrader-w | present | True | ok |
| 32 | restore dry run shows the old pin / v16: bridge       INFO bridge sha256=7d313fd075d29e1e62c11eab52fd | present | True | ok |
| 33 | restore dry run shows no v7c line (no imports pin, no budget line) | absent | (False, False) | ok |
| 34 | restore dry run has no !!! | none | False | ok |
| 35 | the failed v17 files were moved aside, not deleted | >= 1 file | ['autotrader.db-shm.v17-failed-20261023T011533Z', 'autotrader.db-wal.v17-failed-20261023T011533Z', 'autotrader.db.v17-failed-20261023T011533Z'] | ok |
| 36 | after the printed enable: the previous code's supervisor runs | 1 supervisor | [(53807, '$R/scen/V8/live/backend/.venv/bin/python $R/scen/V8/live/backend/autotraderctl.py run')] | ok |
| 37 | the restarted supervisor is a new pid | not 52960 | 53807 | ok |
| 38 | tripwires | no tripwire line | none | ok |
| 39 | live subprocesses use the live checkout's python and cwd | all | 6/6 | ok |
| 40 | no rehearsal process left | none | none | ok |
| 41 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V9 (PASS, 23 checks): the dry run on the candidate shows the new pins, the imports line and the budget line; session_check rule 7 reads the rehearsal's bridge.log as PASS

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | V9 clone at the candidate | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | ok |
| 2 | candidate supervisor REVIEWED_BRIDGE_SHA256 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 3 | REVIEWED_BRIDGE_IMPORTS_SHA256 names the bridge's three lazy imports | 3 files | ['config.py', 'trading/execution/manual_orders.py', 'trading/scheduler/sessions.py'] | ok |
| 4 | imports_sha of the pinned map (the verdict line's imports_sha=) | 7b1df1d4824e | 7b1df1d4824e | ok |
| 5 | every pinned import file on disk matches its pin | none mismatched | [] | ok |
| 6 | bridge file on disk | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 7 | db migrate on the copy | backup line and `migrated  v16 -> v17` | ['applying  17 manual-entry-stop', 'migrated  v16 -> v17'] | ok |
| 8 | db status | state     schema v17 is current | ['state     schema v17 is current', 'migrate   allowed now (outside every supervised trading day)'] | ok |
| 9 | dry run exit | 0 | 0 | ok |
| 10 | dry run shows: bridge       reviewed (sha256 b40fa40b4acd) | present | True | ok |
| 11 | dry run shows: bridge       imports reviewed (3 files) | present | True | ok |
| 12 | dry run shows: bridge       INFO bridge sha256=b40fa40b4acda4d75115c59a1c391e7b639b4dd1 | present | True | ok |
| 13 | dry run shows: bridge       INFO day risk budget 3% of net liquidation, account level ( | present | True | ok |
| 14 | dry run shows: database     schema v17 current (/Users/salomon/autotrader-work/rehearsa | present | True | ok |
| 15 | exactly one budget line | 1 | 1 | ok |
| 16 | no !!! and no WARNING: line | none | [] | ok |
| 17 | the V1, V2, V3 and V6 bridge.log files exist (run V1-V6 first) | all | all | ok |
| 18 | session_check inputs read (no INPUTS MISSING) | none missing | [] | ok |
| 19 | rule 7 day risk budget on the rehearsal's bridge.log (V1, V2, V3, V6: 5 bot SENT + 1 owner buy) | PASS | PASS: 6 accepted entries (5 bot, 1 manual) within 3% of net liquidation, highest 2.98% (MOMO_HOD_V2 KMI (orders 1000-1002)); refused: bot v7c_day_risk_budget x3, manual v7c_day_risk_budget x1 | ok |
| 20 | control: the same log with one budget line over 3 % -> rule 7 FAIL | FAIL | FAIL: 1 problem(s); first: accepted at a day risk of 19993.36 = 3.01% of net liquidation 665000.00, above 3% (19950.00): bridge.log:15 21:14:43 ET INFO v7c_budget_ok SPCX (ORB_V2): realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) <= 3% of 665000.00 (source=bot) | ok |
| 21 | V4's log (widened-stop and over-budget pages) -> rule 7 WARN, never PASS | WARN | WARN: 2 flag(s); first: v7c_stop_widened page: bridge.log:23 21:14:45 ET CRITICAL v7c_stop_widened KMI intent 33.85 now 33.5 — counted at 33.5 in the day risk budget; never flattened | ok |
| 22 | tripwires | no tripwire line | none | ok |
| 23 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |
<!-- checks-official:end -->

## Every check: PRE shake-out

<!-- checks-pre:start -->

#### V1 (PASS, 29 checks): the four budget cases: within budget sent with v7c_budget_ok; over budget skipped with token and numbers; realized gains never fund risk; an unfilled entry counts limit - stop

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V1/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V1/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V1/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V1/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | real 2026-10-21 decisions in the database copy | SPCX ORB_V2, CPRT VWAP_PB_V2, KMI MOMO_HOD_V2 present | ['CPRT', 'CTSH', 'DHR', 'IRON', 'KMI', 'PEGA', 'SPCX'] | ok |
| 6 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 7 | V1a open risk before: the owner's 1,000 sh to their TWS stop | 18500.00 | 18500.000 | ok |
| 8 | V1a v7c_budget_ok line with the numbers | present: v7c_budget_ok SPCX (ORB_V2): realized 0.00 + open 18500.00 + new 293.36 = 18793.36 (2.83%) <= 3% of 665000.00 (source=bot) | present | ok |
| 9 | V1a SENT line after its budget line | SENT SPCX (ORB_V2): BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | ['SPCX (ORB_V2): BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 (orders 1000-1002)'] | ok |
| 10 | V1a orders placed: parent LMT DAY, target LMT GTC, stop STP GTC, one orderRef | [('BUY', 'LMT', 152, 'DAY', 'autotrader:ORB_V2:7929276e-3ec'), ('SELL', 'LMT', 152, 'GTC', 'autotrader:ORB_V2:7929276e-3ec'), ('SELL', 'STP', 152, 'GTC', 'autotrader:ORB_V2:7929276e-3ec')] | [('BUY', 'LMT', 152, 'DAY', 'autotrader:ORB_V2:7929276e-3ec'), ('SELL', 'LMT', 152, 'GTC', 'autotrader:ORB_V2:7929276e-3ec'), ('SELL', 'STP', 152, 'GTC', 'autotrader:ORB_V2:7929276e-3ec')] | ok |
| 11 | V1a stop leg price is the intent stop | 161.58 | 161.58 | ok |
| 12 | V1d open risk with SPCX unfilled: 18,500 + 152 x (163.51 - 161.58) | 18793.36 | 18793.360 | ok |
| 13 | V1d open-risk line of the unfilled entry | present: SPCX bot 0 held @ 163.51 + 152 unfilled @ 163.51, stop 161.58: 293.36 | present | ok |
| 14 | V1d the next entry's budget line counts SPCX's unfilled limit - stop as open | present: v7c_budget_ok CPRT (VWAP_PB_V2): realized 0.00 + open 18793.36 + new 476.28 = 19269.64 (2.90%) <= 3% of 665000.00 (source=bot) | present | ok |
| 15 | V1d after the SPCX entry is cancelled unfilled its risk leaves the budget | 18976.28 | 18976.280 | ok |
| 16 | process 2: manual desk attached to the scenario ledger | attached | attached | ok |
| 17 | V1b skip_reason: token, realized + open + new = total (pct) > 3% of NLV, what it would have sent | v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | ok |
| 18 | V1b SKIP line in bridge.log | present: SPCX (ORB_V2): v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | present | ok |
| 19 | V1b nothing placed | [] | [] | ok |
| 20 | V1b no v7c_budget_ok line | none | [] | ok |
| 21 | process 3: manual desk attached to the scenario ledger | attached | attached | ok |
| 22 | V1c the bot's realized P&L today | 5000.00 | 5000.00 | ok |
| 23 | V1c account_realized_loss() with +5,000 realized (IBKR and computed) | 0 | 0 | ok |
| 24 | V1c skipped at realized 0.00 (gains never fund risk); with -5,000 counted it would have fit | v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | v7c_day_risk_budget: realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) > 3% of 665000.00 (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | ok |
| 25 | V1c the same entry would fit if gains funded risk (19,993.36 - 5,000 <= 19,950) | fits | 14993.36 | ok |
| 26 | V1c nothing placed | [] | [] | ok |
| 27 | V1c realized source logged | present: v7c_pnl_source=reqpnl | present | ok |
| 28 | tripwires (socket / spawn) | none | none | ok |
| 29 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V2 (PASS, 13 checks): two decisions in one tick: the first is sent and reserved, the second is skipped

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V2/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V2/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V2/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V2/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 6 | one tick: KMI sent | sent | sent | ok |
| 7 | KMI budget line | present: v7c_budget_ok KMI (MOMO_HOD_V2): realized 0.00 + open 19200.00 + new 614.25 = 19814.25 (2.98%) <= 3% of 665000.00 (source=bot) | present | ok |
| 8 | same tick: CTSH skipped, its open risk includes KMI's reservation | v7c_day_risk_budget: realized 0.00 + open 19814.25 + new 294.38 = 20108.63 (3.02%) > 3% of 665000.00 (source=bot) — would have sent BUY 718 @ 69.37 \| stop 68.96 \| target 70.19 | v7c_day_risk_budget: realized 0.00 + open 19814.25 + new 294.38 = 20108.63 (3.02%) > 3% of 665000.00 (source=bot) — would have sent BUY 718 @ 69.37 \| stop 68.96 \| target 70.19 | ok |
| 9 | orders placed in that tick: KMI's three legs only | ['autotrader:MOMO_HOD_V2:e76f9cd8-ca6', 'autotrader:MOMO_HOD_V2:e76f9cd8-ca6', 'autotrader:MOMO_HOD_V2:e76f9cd8-ca6'] | ['autotrader:MOMO_HOD_V2:e76f9cd8-ca6', 'autotrader:MOMO_HOD_V2:e76f9cd8-ca6', 'autotrader:MOMO_HOD_V2:e76f9cd8-ca6'] | ok |
| 10 | KMI alone fits, KMI + CTSH does not | 19,814.25 <= 19,950 < 20,108.63 | (Decimal('19814.25'), Decimal('20108.63')) | ok |
| 11 | no v7c_budget_invariant_broken | none | [] | ok |
| 12 | tripwires (socket / spawn) | none | none | ok |
| 13 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V3 (PASS, 17 checks): an openOrder echo raises a held bracket's stop to break-even: budget freed, the next entry is sent

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V3/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V3/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V3/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V3/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 6 | V3: KMI sent | sent | sent | ok |
| 7 | V3: KMI held 2,925 (bracket open) | ('open', 2925) | ('open', 2925) | ok |
| 8 | open risk with KMI held at its intent stop: 19,200 + 2,925 x (34.06 - 33.85) | 19814.25 | 19814.25000 | ok |
| 9 | before the raise CTSH's risk would not fit | v7c_day_risk_budget refusal | v7c_day_risk_budget: realized 0.00 + open 19814.25 + new 294.38 = 20108.63 (3.02%) > 3% of 665000.00 (source=bot) | ok |
| 10 | current_stop after the echo = break-even (IBKR's price, not the intent) | 34.06 | 34.06 | ok |
| 11 | open risk after the raise: KMI risks 0, only the owner's 19,200 remains | 19200.00 | 19200.000 | ok |
| 12 | the stop move is logged old -> new | present: v7c stop KMI order 1002: 33.85 -> 34.06 | present | ok |
| 13 | the freed budget sends CTSH (budget line) | present: v7c_budget_ok CTSH (VWAP_PB_V2): realized 0.00 + open 19200.00 + new 294.38 = 19494.38 (2.93%) <= 3% of 665000.00 (source=bot) | present | ok |
| 14 | CTSH sent | sent | sent | ok |
| 15 | nothing about KMI was modified or cancelled | no KMI order re-sent, no cancel | ([], []) | ok |
| 16 | tripwires (socket / spawn) | none | none | ok |
| 17 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V4 (PASS, 17 checks): a widened stop: CRITICAL v7c_stop_widened once (and v7c_budget_over), counted at the new price, never flattened or modified

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V4/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V4/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V4/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V4/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 6 | V4: KMI sent | sent | sent | ok |
| 7 | V4: KMI held 2,925 (bracket open) | ('open', 2925) | ('open', 2925) | ok |
| 8 | the widened stop counts at its new price: 18,500 + 2,925 x (34.06 - 33.50) | 20138.00 | 20138.00000 | ok |
| 9 | CRITICAL v7c_stop_widened exactly once in 20 ticks | 1 | 1 | ok |
| 10 | CRITICAL v7c_budget_over exactly once (one 0.5 % step) | 1 | 1 | ok |
| 11 | never flattened or modified: no order placed after the widening | 3 | 3 | ok |
| 12 | never flattened or modified: no cancel after the widening | [] | [] | ok |
| 13 | the bracket is still open with all its shares | ('open', 2925) | ('open', 2925) | ok |
| 14 | no FLATTEN line | none | [] | ok |
| 15 | no repeat page after 25 ticks | (1, 1) | (1, 1) | ok |
| 16 | tripwires (socket / spawn) | none | none | ok |
| 17 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V5 (PASS, 17 checks): an unprotected foreign position (IP 2941 sh, VEEV 100 sh, no covering stop) blocks bot and manual entries and pages once each; the exact phone text

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V5/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V5/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V5/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V5/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 6 | owner /buy rejected: reject_code | v7c_unprotected_position | v7c_unprotected_position | ok |
| 7 | owner /buy rejected: status and error | ('rejected', 'v7c_unprotected_position: IP 2941 sh held, working stops cover 0 (and 1 more) — every risk-increasing order is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) (source=manual). Nothing sent.') | ('rejected', 'v7c_unprotected_position: IP 2941 sh held, working stops cover 0 (and 1 more) — every risk-increasing order is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) (source=manual). Nothing sent.') | ok |
| 8 | bot entry SPCX skipped: skip_reason | v7c_unprotected_position: IP 2941 sh held, working stops cover 0 (and 1 more) — every risk-increasing order is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | v7c_unprotected_position: IP 2941 sh held, working stops cover 0 (and 1 more) — every risk-increasing order is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) (source=bot) — would have sent BUY 152 @ 163.51 \| stop 161.58 \| target 167.37 | ok |
| 9 | nothing placed (bot or manual) | [] | [] | ok |
| 10 | IP paged exactly once in 30+ s | 1 | 1 | ok |
| 11 | VEEV paged exactly once in 30+ s | 1 | 1 | ok |
| 12 | no other CRITICAL | [] | [] | ok |
| 13 | the phone text the owner sees (notifier's own formatter), IP then VEEV | ⚠️ <page> · HH:MM:SS ET for each | ['⚠️ CRITICAL v7c_unprotected_position IP: 2941 sh held, working stops cover 0 — every risk-increasing order (bot and manual) is refused; close it with /close IP or in TWS, or protect it with a stop that stays working (GTC) · 04:50:11 ET', '⚠️ CRITICAL v7c_unprotected_position VEEV: 100 sh held, working stops cover 0 — every risk-increasing order (bot and manual) is refused; close it with /close VEEV or in TWS, or protect it with a stop that stays working (GTC) · 04:50:11 ET'] | ok |
| 14 | after the owner's GTC stops: nothing unprotected | () | () | ok |
| 15 | open risk then = 2,941 x (33.94 - 32.50) + 100 x (311.15 - 300.00) | 5350.04 | 5350.0400 | ok |
| 16 | tripwires (socket / spawn) | none | none | ok |
| 17 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V6 (PASS, 29 checks): manual /buy without a stop (parser and migration-17 trigger), under budget (attached GTC stop sent), over budget (refused, final); /closeall keeps the stop of a holding it cannot sell (N18 MEDIUM-1 fix)

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | ledger path (config, fake HOME) is the scenario's database copy | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V6/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V6/.local/share/autotrader-v1/autotrader.db | ok |
| 2 | database copy migrated by the candidate's code | v16 -> v17 current | v16 -> v17 current=True | ok |
| 3 | bridge under test: sha256 of the archived candidate file (the reviewed pin) | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 4 | bridge reads the scenario's database | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V6/.local/share/autotrader-v1/autotrader.db | /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V6/.local/share/autotrader-v1/autotrader.db | ok |
| 5 | /buy 10 AAPL (no stop): parser refusal | ParseRefusal missing_entry_stop | ParseRefusal(code='missing_entry_stop', reason='A buy needs its protective stop, written last: /buy 100 AAPL at 180 stop 175 or /buy $5k NVDA stop 170. Nothing sent.', is_order_attempt=True) | ok |
| 6 | /buy $5k NVDA (no stop): parser refusal | ParseRefusal missing_entry_stop | ParseRefusal(code='missing_entry_stop', reason='A buy needs its protective stop, written last: /buy 100 AAPL at 180 stop 175 or /buy $5k NVDA stop 170. Nothing sent.', is_order_attempt=True) | ok |
| 7 | process 1: manual desk attached to the scenario ledger | attached | attached | ok |
| 8 | a BUY row without entry_stop_price on the migrated live copy: migration 17 trigger | manual_buy_needs_stop | manual_buy_needs_stop | ok |
| 9 | /buy 10 AAPL stop 185 sent as BUY MKT DAY (transmit False) + attached SELL STP GTC (parentId, transmit True) | 2 orders | [(1000, 'BUY', 'MKT', 10, 'DAY', 0, False, 'manual:cdd1f71aaecb4f3c9e932142c8a732fd'), (1001, 'SELL', 'STP', 10, 'GTC', 1000, True, 'manual:cdd1f71aaecb4f3c9e932142c8a732fd')] | ok |
| 10 | attached stop price | 185.0 | 185.0 | ok |
| 11 | manual budget line (sized at the ask 190.01) | present: v7c_budget_ok AAPL (manual #cdd1f7): realized 0.00 + open 0.00 + new 50.10 = 50.10 (0.01%) <= 3% of 665000.00 (source=manual) | present | ok |
| 12 | row sent with the parent order id | ('sent', 1000) | ('sent', 1000) | ok |
| 13 | MANUAL SENT line names its GTC stop | present: SENT BUY 10 AAPL MKT #cdd1f7 (order 1000) with its stop 185 GTC (order 1001) | present | ok |
| 14 | process 2: manual desk attached to the scenario ledger | attached | attached | ok |
| 15 | a restarted bridge counts the owner's still-working AAPL buy (10 x (190.01 - 185)) | present: AAPL owner buy 10 not in the position yet @ 190.01, stop 185: 50.10 | present | ok |
| 16 | open risk before the TSLA buy: 19,850 owner + 50.10 working buy | 19900.10 | 19900.1000 | ok |
| 17 | over budget: row rejected with token and numbers | ('rejected', 'v7c_day_risk_budget', 'v7c_day_risk_budget: realized 0.00 + open 19900.10 + new 50.10 = 19950.20 (3.00%) > 3% of 665000.00 (source=manual). Nothing sent.') | ('rejected', 'v7c_day_risk_budget', 'v7c_day_risk_budget: realized 0.00 + open 19900.10 + new 50.10 = 19950.20 (3.00%) > 3% of 665000.00 (source=manual). Nothing sent.') | ok |
| 18 | over budget: MANUAL REJECTED line | present: REJECTED BUY 10 TSLA MKT #0f73dc: v7c_day_risk_budget: realized 0.00 + open 19900.10 + new 50.10 = 19950.20 (3.00%) > 3% of 665000.00 (source=manual). Nothing sent. (v7c_day_risk_budget) | present | ok |
| 19 | over budget: nothing placed | [] | [] | ok |
| 20 | the refusal is final (no override, no resend once budget frees) | ('rejected', []) | ('rejected', []) | ok |
| 21 | the /buy filled | filled | filled | ok |
| 22 | AAPL protected by its attached stop (nothing unprotected) | () | () | ok |
| 23 | /closeall: AAPL not sold, its stop kept | ('rejected', 'nothing_closed', 'AAPL: spread 1.06% over 1% — not sold; its stop is kept') | ('rejected', 'nothing_closed', 'AAPL: spread 1.06% over 1% — not sold; its stop is kept') | ok |
| 24 | /closeall: the attached stop was NOT cancelled, nothing sent | ([], []) | ([], []) | ok |
| 25 | /closeall: AAPL still protected | () | () | ok |
| 26 | /closeall: no CRITICAL | none | [] | ok |
| 27 | the stop order is still live at IBKR | PreSubmitted | PreSubmitted | ok |
| 28 | tripwires (socket / spawn) | none | none | ok |
| 29 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V7 (PASS, 54 checks): deploy apply --expect-migration 17 from a rehearsal clone of live 8675055 to the candidate: PREV line, stop, ff, db migrate (backup, migrated v16 -> v17), db status current, dry run (pins, imports, budget line), start, PASS line

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | live-DB copy before | v16 current for 8675055; pending (17,) for the candidate code | applied v16 code v17 pending (17,) | ok |
| 2 | exit code | 0 | 0 | ok |
| 3 | deploys.log keeps the 14 earlier lines byte for byte | unchanged prefix | True | ok |
| 4 | deploys.log new lines | 2 | 2 | ok |
| 5 | deploys.log mode | 0o600 | 0o600 | ok |
| 6 | the tool's _DEPLOY_PASS reads every earlier DEPLOY line (10-16 .. 10-22) | 6/6 | 6/6 | ok |
| 7 | PREV line `<iso> v7c PREV_SHA=8675055c0b57.. schema=v16` | parsed by _PREV_LINE | 2026-10-22T11:50:19+03:00 v7c PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 schema=v16 | ok |
| 8 | DEPLOY line parsed by _DEPLOY_PASS | short b5f2339 prev 8675055c0b57 pin b40fa40b4acd schema 17 | 2026-10-22T11:50:43+03:00 DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS schema=v17 | ok |
| 9 | PASS line | <iso> DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS schema=v17 | 2026-10-22T11:50:43+03:00 DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS schema=v17 | ok |
| 10 | _deploy_line_for(HEAD) and _prev_schema read the pair back | the new DEPLOY line, PREV schema 16 | DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS schema=v17; prev schema 16 | ok |
| 11 | printed ledger line | `<iso> \| DEPLOY v7c b5f2339 PREV_SHA=<40> pin=b40fa40b4acd result=PASS ...` | 2026-10-22T11:50:43+03:00 \| DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=PASS \| owner-run via autotraderctl deploy apply (b5f2339); 11 dry-run lines ok; post-checks ok \| ~/Library/Logs/AutotraderV1/deploys.log | ok |
| 12 | steps in order: PREV, stop, window, ff, db migrate (backup, migrated), db status, dry run, window, start, 20 s settle, PASS | all present in order | [5533, 5640, 5838, 5938, 5972, 6099, 6208, 6650, 6840, 6865, 7037, 8144, 8195, 8222, 8695, 8772, 9004, 9237] | ok |
| 13 | step printed: OK deploys.log: | present | True | ok |
| 14 | step printed: STEP stop: | present | True | ok |
| 15 | step printed: OK stopped: | present | True | ok |
| 16 | step printed: OK window before the fast-forward | present | True | ok |
| 17 | step printed: STEP git -C $R/scen/V7/live merge --ff-only b5f2339e19f3c357d4ecf0a3fbce7d6c06ed | present | True | ok |
| 18 | step printed: OK fast-forward: | present | True | ok |
| 19 | step printed: STEP $R/scen/V7/live/backend/.venv/bin/python $R/scen/V7/live/backend/autotrader | present | True | ok |
| 20 | step printed:   \| backup     | present | True | ok |
| 21 | step printed:   \| migrated  v16 -> v17 | present | True | ok |
| 22 | step printed: OK migrated v16 -> v17; backup  | present | True | ok |
| 23 | step printed: STEP $R/scen/V7/live/backend/.venv/bin/python $R/scen/V7/live/backend/autotrader | present | True | ok |
| 24 | step printed: OK dry run: | present | True | ok |
| 25 | step printed: OK window before the start | present | True | ok |
| 26 | step printed: STEP $R/scen/V7/live/backend/.venv/bin/python $R/scen/V7/live/backend/autotrader | present | True | ok |
| 27 | step printed: OK settled: | present | True | ok |
| 28 | step printed: OK started: | present | True | ok |
| 29 | step printed:  \| DEPLOY v7c  | present | True | ok |
| 30 | step printed: deploy apply: PASS v7c b5f2339 (exit 0) | present | True | ok |
| 31 | the plan names the migration | `--expect-migration 17` plan step with db migrate then db status | True | ok |
| 32 | dry-run evidence: bridge       reviewed (sha256 b40fa40b4acd) | present | True | ok |
| 33 | dry-run evidence: bridge       imports reviewed (3 files) | present | True | ok |
| 34 | dry-run evidence: bridge       INFO bridge sha256=b40fa40b4acda4d75115c59a1c391e7b639b4dd1 | present | True | ok |
| 35 | dry-run evidence: bridge       INFO day risk budget 3% of net liquidation, account level ( | present | True | ok |
| 36 | dry-run evidence: bridge       INFO fenced identities = none | present | True | ok |
| 37 | dry-run evidence: bridge       INFO sendable identities = GAP_GO_V2, MOMO_HOD_V2, ORB_V2,  | present | True | ok |
| 38 | dry-run evidence: registry     sendable identities, size table and strategy registry agree | present | True | ok |
| 39 | dry-run evidence: database     schema v17 current (/Users/salomon/autotrader-work/rehearsa | present | True | ok |
| 40 | exactly one `day risk budget 3%` line in the dry run | 1 | 1 | ok |
| 41 | SESSION_WAIT in the dry run | present | True | ok |
| 42 | db migrate's backup is the v16 database in <data>/backups/ | v16 | $R/fakehome/V7/.local/share/autotrader-v1/backups/autotrader.20261022T085020.027861Z.v16-to-v17.db v16 | ok |
| 43 | database after | v17 current | v17 current=True | ok |
| 44 | `db status` of the live clone (now the candidate): current | state     schema v17 is current | ['database  /Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V7/.local/share/autotrader-v1/autotrader.db', 'state     schema v17 is current', 'migrate   allowed now (outside every supervised trading day)'] | ok |
| 45 | live clone HEAD after (fast-forward) | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | ok |
| 46 | live clone tree | clean | clean | ok |
| 47 | bridge on disk is the new pin | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 48 | one supervisor running, the clone's, a new pid | 1, not 28198 | [(28273, '$R/scen/V7/live/backend/.venv/bin/python $R/scen/V7/live/backend/autotraderctl.py run')] | ok |
| 49 | plist WorkingDirectory | /Users/salomon/autotrader-work/rehearsal-v7c/scen/V7/live/backend | /Users/salomon/autotrader-work/rehearsal-v7c/scen/V7/live/backend | ok |
| 50 | deploy undo's pre-checks read the migration deploy back (read-only): a code-only undo is refused | refusal `the deploy migrated v16 -> v17: the database must be restored first`, old pin 7d313fd075d2 | refusals=['undo_needs_db_restore'] ['the deploy migrated v16 -> v17: the database must be restored first'] old_pin=7d313fd075d2 | ok |
| 51 | tripwires | no tripwire line | none | ok |
| 52 | live subprocesses use the live checkout's python and cwd | all | 5/5 | ok |
| 53 | no rehearsal process left | none | none | ok |
| 54 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V8 (PASS, 41 checks): DB-restore undo drill: a post-migration failure (the candidate's telegram child exits 78 after its start) -> HALTED with the printed RESTORE; the RESTORE executed by hand on the copy -> v16 back, the old pin shown, the previous code runs

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | copy schema before | v16 current, pending (17,) for the candidate code | applied v16 code v17 pending (17,) | ok |
| 2 | exit code | 4 | 4 | ok |
| 3 | deploys.log keeps the 14 earlier lines byte for byte | unchanged prefix | True | ok |
| 4 | deploys.log new lines | 2 | 2 | ok |
| 5 | deploys.log mode | 0o600 | 0o600 | ok |
| 6 | the tool's _DEPLOY_PASS reads every earlier DEPLOY line (10-16 .. 10-22) | 6/6 | 6/6 | ok |
| 7 | HALTED line | DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57.. pin=b40fa40b4acd result=HALTED reason=post_start_child_exited backup=<path> | 2026-10-22T11:50:58+03:00 DEPLOY v7c b5f2339 PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 pin=b40fa40b4acd result=HALTED reason=post_start_child_exited backup=/Users/salomon/autotrader-work/rehearsal-v7c/fakehome/V8/.local/share/autotrader-v1/backups/autotrader.20261022T085051.584125Z.v16-to-v17.db | ok |
| 8 | PREV line carries schema=v16 | schema=v16 | 2026-10-22T11:50:50+03:00 v7c PREV_SHA=8675055c0b57200d395534568af8eae031f293e2 schema=v16 | ok |
| 9 | printed: STEP $R/scen/V8/live/backend/.venv/bin | present | True | ok |
| 10 | printed:   \| migrated  v16 -> v17 | present | True | ok |
| 11 | printed: OK migrated v16 -> v17; backup  | present | True | ok |
| 12 | printed: OK dry run: | present | True | ok |
| 13 | printed: FAIL post_start_child_exited: | present | True | ok |
| 14 | printed: The database was migrated (or may have been): HALT, never an automatic undo. | present | True | ok |
| 15 | printed: RESTORE (never run by this command) | present | True | ok |
| 16 | printed: git -C $R/scen/V8/live reset --keep 86 | present | True | ok |
| 17 | printed:   1. pgrep -fl $R/scen/V8/live/backend | present | True | ok |
| 18 | printed: Expect: `bridge       reviewed (sha256 7d313fd075d2)` and `database     schema v | present | True | ok |
| 19 | printed: deploy apply: HALTED reason=post_start_child_exited (exit 4; the robot is disabl | present | True | ok |
| 20 | the failure is the telegram exit after the start | FAIL post_start_child_exited: ... telegram code=78 | ['FAIL post_start_child_exited: 2026-10-22T08:50:56+00:00 CHILD_EXIT telegram code=78 restart_in=15 after the start'] | ok |
| 21 | no automatic reset after the migration | no `reset --keep` before the printed RESTORE | False | ok |
| 22 | HEAD left at the candidate (never reset by the tool) | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | ok |
| 23 | robot left disabled: no supervisor | 0 supervisors | 0 | ok |
| 24 | LaunchAgent disabled and unloaded | enabled False, loaded False | {'enabled': False, 'loaded': False, 'pid': None, 'plist': None, 'spawns': 0} | ok |
| 25 | database migrated by the live clone's db migrate | v17 current | v17 current=True | ok |
| 26 | backup is the v16 database | v16, in <data>/backups/ | $R/fakehome/V8/.local/share/autotrader-v1/backups/autotrader.20261022T085051.584125Z.v16-to-v17.db v16 | ok |
| 27 | restore script ran | exit 0 | 0 | ok |
| 28 | after the restore: HEAD | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |
| 29 | after the restore: database | schema v16 (current for 8675055 code) | v16 current=False | ok |
| 30 | restore dry run shows the old pin / v16: bridge       reviewed (sha256 7d313fd075d2) | present | True | ok |
| 31 | restore dry run shows the old pin / v16: database     schema v16 current (/Users/salomon/autotrader-w | present | True | ok |
| 32 | restore dry run shows the old pin / v16: bridge       INFO bridge sha256=7d313fd075d29e1e62c11eab52fd | present | True | ok |
| 33 | restore dry run shows no v7c line (no imports pin, no budget line) | absent | (False, False) | ok |
| 34 | restore dry run has no !!! | none | False | ok |
| 35 | the failed v17 files were moved aside, not deleted | >= 1 file | ['autotrader.db-shm.v17-failed-20261022T085058Z', 'autotrader.db-wal.v17-failed-20261022T085058Z', 'autotrader.db.v17-failed-20261022T085058Z'] | ok |
| 36 | after the printed enable: the previous code's supervisor runs | 1 supervisor | [(28484, '$R/scen/V8/live/backend/.venv/bin/python $R/scen/V8/live/backend/autotraderctl.py run')] | ok |
| 37 | the restarted supervisor is a new pid | not 28380 | 28484 | ok |
| 38 | tripwires | no tripwire line | none | ok |
| 39 | live subprocesses use the live checkout's python and cwd | all | 6/6 | ok |
| 40 | no rehearsal process left | none | none | ok |
| 41 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### V9 (PASS, 23 checks): the dry run on the candidate shows the new pins, the imports line and the budget line; session_check rule 7 reads the rehearsal's bridge.log as PASS

| # | Check | Expected | Observed | |
|---|---|---|---|---|
| 1 | V9 clone at the candidate | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | b5f2339e19f3c357d4ecf0a3fbce7d6c06ed2eda | ok |
| 2 | candidate supervisor REVIEWED_BRIDGE_SHA256 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 3 | REVIEWED_BRIDGE_IMPORTS_SHA256 names the bridge's three lazy imports | 3 files | ['config.py', 'trading/execution/manual_orders.py', 'trading/scheduler/sessions.py'] | ok |
| 4 | imports_sha of the pinned map (the verdict line's imports_sha=) | 7b1df1d4824e | 7b1df1d4824e | ok |
| 5 | every pinned import file on disk matches its pin | none mismatched | [] | ok |
| 6 | bridge file on disk | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 | ok |
| 7 | db migrate on the copy | backup line and `migrated  v16 -> v17` | ['applying  17 manual-entry-stop', 'migrated  v16 -> v17'] | ok |
| 8 | db status | state     schema v17 is current | ['state     schema v17 is current', 'migrate   allowed now (outside every supervised trading day)'] | ok |
| 9 | dry run exit | 0 | 0 | ok |
| 10 | dry run shows: bridge       reviewed (sha256 b40fa40b4acd) | present | True | ok |
| 11 | dry run shows: bridge       imports reviewed (3 files) | present | True | ok |
| 12 | dry run shows: bridge       INFO bridge sha256=b40fa40b4acda4d75115c59a1c391e7b639b4dd1 | present | True | ok |
| 13 | dry run shows: bridge       INFO day risk budget 3% of net liquidation, account level ( | present | True | ok |
| 14 | dry run shows: database     schema v17 current (/Users/salomon/autotrader-work/rehearsa | present | True | ok |
| 15 | exactly one budget line | 1 | 1 | ok |
| 16 | no !!! and no WARNING: line | none | [] | ok |
| 17 | the V1, V2, V3 and V6 bridge.log files exist (run V1-V6 first) | all | all | ok |
| 18 | session_check inputs read (no INPUTS MISSING) | none missing | [] | ok |
| 19 | rule 7 day risk budget on the rehearsal's bridge.log (V1, V2, V3, V6: 5 bot SENT + 1 owner buy) | PASS | PASS: 6 accepted entries (5 bot, 1 manual) within 3% of net liquidation, highest 2.98% (MOMO_HOD_V2 KMI (orders 1000-1002)); refused: bot v7c_day_risk_budget x3, manual v7c_day_risk_budget x1 | ok |
| 20 | control: the same log with one budget line over 3 % -> rule 7 FAIL | FAIL | FAIL: 1 problem(s); first: accepted at a day risk of 19993.36 = 3.01% of net liquidation 665000.00, above 3% (19950.00): bridge.log:14 04:50:08 ET INFO v7c_budget_ok SPCX (ORB_V2): realized 0.00 + open 19700.00 + new 293.36 = 19993.36 (3.01%) <= 3% of 665000.00 (source=bot) | ok |
| 21 | V4's log (widened-stop and over-budget pages) -> rule 7 WARN, never PASS | WARN | WARN: 2 flag(s); first: v7c_stop_widened page: bridge.log:22 04:50:10 ET CRITICAL v7c_stop_widened KMI intent 33.85 now 33.5 — counted at 33.5 in the day risk budget; never flattened | ok |
| 22 | tripwires | no tripwire line | none | ok |
| 23 | live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |
<!-- checks-pre:end -->
