**VERDICT: NO-GO for 27a9d3432ee65ef8fb7e0f0b616ca57fb302944c** (ops-p3-sizes: P3-C + P3-E + P3-F merged, migration 18). Two MEDIUM findings, no HIGH:
- **MEDIUM-1:** the sidecar v2 "size as % of bar volume" divides shares by round lots. Every value is 100× too high, and the flag fires on almost every trade.
- **MEDIUM-2:** a failed night batch is never retried in production, so the notice of an automatic step-down is lost without any alert.

Both fixes are small. Make them before any v2 sidecar line or any size event exists in production.

On everything else in my scope this layer is ready from an operations view:
- Delivery: one implementation; the silent flag is set on every part; buttons only while phone approval is live.
- The state file is 0600 and written atomically.
- Texts are honest while `PHONE_APPROVAL_LIVE` is False.
- Schedules are anchored to ET; Israel time comes from ZoneInfo.
- Failures are isolated at the identity level.
- Behaviour with v18 pending is correct.
- Bridge and imports pins hold.

# N19b-OPS: operations review of the size layer (b5f2339..27a9d34)

- **Reviewer:** N19b-OPS, independent. I did not write this code.
- **Scope:** messages, schedules, failure isolation, docs, deploy. The ES reviewer covers the size-read math, the triggers and the writer's step logic.
- **Run:** 2026-10-23, 04:49 to 05:10 Israel. Every heavy run fell inside 04:56–05:08, outside 13:05–13:50 and 14:50–23:15.
- **How it ran:** `git archive` extracts of 27a9d34 (and b5f2339 for comparison) under `~/autotrader-work/review-n19/n19b-ops/`, all under `sandbox-exec -f ~/autotrader-work/sandbox-n19.sb`, with a fake HOME.
- **Never touched:** no port, launchctl, live DB, logs, Keychain, worktree, ledger or AGENTS.md. No real Telegram message was sent: every send went to a mock transport.

## 1. Findings

| ID | Sev | file:line (27a9d34) | What | Failure scenario | Fix |
|---|---|---|---|---|---|
| MEDIUM-1 | MEDIUM (OPS / evidence, D3, O4) | backend/trading/storage/decision_sizing.py:335 (`bar_volume=newest.volume`), :197 (`quantity * 100 / bar_volume`), :200-206 (flag); tests/test_decision_sizing.py:684-685 | `CompletedBar.volume` is the raw TWS unit, round lots of 100 shares. Evidence:<br>• the golden fixture says so (tests/fixtures/momo/bf_b_2026-10-12.json:25, `"volume_unit": "raw TWS API units (round lots of 100 shares)…"`);<br>• the verified constant `IBKR_API_VOLUME_MULTIPLIER = 100` (shadow_service.py:112-115, "AAPL ~500k against ~50M shares");<br>• MOMO's own evidence records `signal.volume * volume_multiplier` (rules/momo.py:539).<br>The sidecar divides the intent's **shares** by the bar's **lots**, so `size_pct_of_bar_volume` is 100× too high. The test pins the wrong number: "1863 shares on a 3098-share bar", 60.1356 %. The true figure is 1863 / 309,800 = **0.60 %**. A normal ×1 MOMO trade reads 120 % where the truth is 1.2 %, and `size_above_bar_volume_pct` (> 1 %) fires on any order larger than bar-lots / 100 shares, which is nearly every trade. The flag means nothing as built. The 1 % threshold was never the problem; the denominator's unit is.<br>Gate check: no reader outside decision_sizing.py reads `size_above_bar_volume_pct`, `spread_above_bps`, `size_pct_of_bar_volume` or their constants (grep over trading/, scripts_local/, routers/: 0 hits). The flags never block. | From the install on, every decision's v2 line records a share of volume 100× too high, with the flag True. These are D3's realism evidence for the LIVE decision.<br>**The format freezes once written:** `from_payload` re-derives the share and both flags and refuses a line that disagrees with itself (decision_sizing.py:246ff, "a line that disagrees with itself is not evidence"). A later unit fix must ship as `SIZING_VERSION = 3` and keep the wrong v2 formula for every v2 line already written. Otherwise all those lines turn unreadable, and with them their recorded identity factor. | Before L-sizes:<br>• record the bar volume in shares: `newest.volume * IBKR_API_VOLUME_MULTIPLIER` (import the verified constant, or move it to a shared module); or record the raw value plus a `bar_volume_unit` and derive with the multiplier;<br>• re-pin `test_sidecar_v2_fields_and_flags`: BF B ×0.5 gives 0.6014 %, flag False; at ×1, 1.2027 %, flag True;<br>• add a meta-test that ties the sidecar unit to the fixtures' `volume_unit`, as LEARNED says: "fixtures copy the real log format".<br>Keep 1 % of the newest **5-minute** bar: with shares it is meaningful (it flags a ×1 BF B trade and passes ×0.5). Name the bar size in the docs (8.2) so a later 1-minute strategy rescales. |
| MEDIUM-2 | MEDIUM (OPS, D2 "step-downs … notify", D7) | backend/trading/operations/size_notify.py:225 (`WHERE p.as_of = ?`), :363-366 (`unsent_items`); backend/trading/research/report_notify.py:307-309 (docstring: "so a replay restarted after a failed send still delivers"), :340-342; backend/trading/research/nightly.py:509-516; backend/trading/research/replay.py:812-830 (exit 0) | The batch reads **only tonight's** `as_of`. On FAILED (Telegram 5xx ×3, a network drop, a multi-part message whose later part fails), `notify_size_batch` returns FAILED and nothing is recorded. That is correct, but nothing ever sends again:<br>• the nightly catches the outcome;<br>• `run_replay_cli` exits 0;<br>• the replay child is run-once, and a clean exit means done for the day (supervisor.py:580-590);<br>• the next night reads only its own night.<br>The only trace is one WARNING in replay.log (not a size token, so it is not forwarded) and `sizes.delivery = "failed"` in evidence.json. The test `test_a_failed_batch_is_sent_by_the_next_run…` proves the retry only through a second run of the **same** night, which production never makes after a clean exit. | **Probe A** (reproduced):<br>• Monday's VWAP_PB_V2 step-down ×0.5 → ×0.25: the batch fails;<br>• Tuesday night's batch is SENT and carries only GAP's proposal;<br>• Monday's step-down is never delivered (`monday step_down delivered? False`).<br>A size the robot lowered by itself is never told to the owner. There is no `/sizes` before P3-D, the weekly text has no size section, and the dry run prints no sizes. A demotion fence still reaches the owner through the notifier's 07:55 "fenced at start" line; a rung step-down does not. The same loss follows a SIGTERM at 18:30 ET between the writer and the send. | `unsent_items` should read every undelivered item of recent nights, not only tonight: e.g. `p.as_of BETWEEN night − 7 days AND night`. The record already makes each id go out once, and step ups decided since then drop out by themselves (`decision is None`). Show a late item with its own night, e.g. "(from the Mon 2026-10-19 night)".<br>Add a test: Monday FAILED, then Tuesday's batch carries Monday's step-down exactly once, and a third night sends nothing again.<br>Correct the docstring at report_notify.py:307. |
| LOW-1 | LOW (OPS / acceptance) | backend/trading/operations/session_check.py:1410 (`if not entries: return PASS "no SENT: nothing was sized"` comes before the `unknown` branch) | The "unreadable store is never a PASS" rule (session_check docstring; OPERATOR_PROMPT.md:281) only holds while there is a SENT. **Probe F:** with a checksum mismatch or a dropped events table and no SENT, all 7 rules PASS and the exit is 0. The only hint is the inputs line, "sizes in force: … unknown (identity_size_store_unreadable: …)". | An unreadable store blocks every sendable setup (`identity_sizing_missing`), so that day has no SENT. `session check` then reports a clean day: the first-session acceptance after L-sizes would pass a robot that traded nothing. The phone did get the 09:23 ET size tripwire, so this is not the only signal. | When any sendable identity's size is unknown, rule 4 is WARN `size_in_force_unknown` (naming the identities) even with no SENT. Add the probe as a test. |
| LOW-2 | LOW (OPS / alerts) | backend/trading/operations/notify_service.py:2844-2850 (`size_tripwire:{token}`, once per date); backend/trading/operations/overnight_watch.py:360-372 (`replay_size_tripwire:{token}`, once per ET date) | Each token pages once per day **whatever the identity**. **Probe C:** ORB_V2 and GAP_GO_V2 both skipped `size_event_chain_broken` at night gave ONE page naming only ORB_V2. At session start, an unreadable store gives one `identity_size_store_unreadable` page for the first child at 09:23 and none for the others, including MOMO at 09:50. | The owner thinks one identity is affected while all four are blocked. | Key by (token, identity); the identity is the word after `<token>: ` in every size CRITICAL. Or add "(+N more identities today: see <log>)". Still no flood: at most 9 tokens × 4 identities. |
| LOW-3 | LOW (OPS / isolation) | backend/trading/research/nightly.py:506-508; backend/trading/research/size_writer.py:705-721 (the first, global transaction and `_connect` sit outside the per-identity guard) | A failure of the whole `write_night` is logged as ERROR "size writer failed" with no `SIZE_TRIPWIRES` token, so nothing pages. Causes: `BEGIN IMMEDIATE` busy for more than 30 s, `_connect` permissions or I/O, an unexpected raise in `sizes_in_force`. Mutant M06 (the nightly guard narrowed to `ValueError`) survives: the guard is untested. | A night with no step-downs and no proposals, and no page. The coordinator's rule was "a CRITICAL size token forwarded to the phone" for writer failures. It is fail-closed: sizes stay. | Log one CRITICAL with a size token (e.g. `size_writer_night_failed: …`) and add it to `SIZE_TRIPWIRES`. Add a test: a raising writer leaves the replay at exit 0, with `sizes.status=not_written` and one CRITICAL. |
| LOW-4 | LOW (docs) | docs/OPERATOR_PROMPT.md, see §3 | 13 mismatches with the code. The material ones: 8.2 says the buttons, the 05:55 reminder and the 07:55 expiry note are `[NOW]` (none exists while `PHONE_APPROVAL_LIVE` is False); 4.9 says "nothing is sent silently"; Part 5 says the runtime size decrease is `[PLANNED]`; there is no playbook for the size tripwires. | The operator agent and the owner expect a 12:55/13:55 Israel reminder and buttons that never come, and have no instruction for a size page. | Fix the lines in §3. |
| LOW-5 | LOW (tests) | tests/test_size_notify.py, tests/test_overnight_watch.py | 8 of my 14 mutants survive, all in isolation, de-dup and restart (§4):<br>• the record written non-atomically;<br>• a raising sender or writer aborting the nightly;<br>• replay-tripwire de-dup kept in process memory only;<br>• the replay log restarting at its end (lines written while the child was down are missed);<br>• a corrupt record raising (every night FAILED for ever);<br>• ProposalWatch unguarded;<br>• only the first line of each poll examined (the fixture puts the CRITICAL first, against LEARNED "Test fixtures copy the real log format"). | A later regression in any of these paths passes CI. | Add one test per survivor (listed in §4). |
| LOW-6 | LOW (texts) | backend/trading/operations/size_notify.py:149; backend/trading/research/promotion_proposals.py:442, :462 | **(a)** "sizes stay unchanged: phone approval arrives later" can read as "a message to approve will arrive later tonight".<br>**(b)** The weekly header ("a step up waits for the owner's approval; a step down applies by itself at the next session") and the payload's `grants` imply an approval path that does not exist before P3-D.<br>**(c)** The weekly text lists `DEMOTION PROPOSAL X` while the writer may hold it (`demotion_waiting_new_evidence`, `owner_resume_holds`), so the Friday text says a step-down applies when the store did not change. | The owner is misled about what changed and what can be done. | **(a)** "no size change: phone approval is not built yet (P3-D); this proposal lapses at Tue 07:55 ET (14:55 Israel)".<br>**(b)** Gate the header on a flag passed in. Promoter purity forbids importing `size_notify`, so nightly hands it in.<br>**(c)** Mark held demotions in the weekly text, or point to evidence.json `sizes`. |
| LOW-7 | LOW (OPS / owner call) | overnight_watch.py:360-372 | Replay-log size pages use normal sound at 00:35–01:30 Israel (23:35–00:30 in the 25 Oct–1 Nov week). That is inside the Gateway watch's quiet hours (17:30 → 05:55 ET), and the alert asks nothing of the owner at night ("its sizes stay as they are, retried next night"). | Woken at night for something that cannot be acted on before morning. | Owner call: silent at night, or fold it into the 05:55 digest line. Also add an OPERATOR_PROMPT 4.6/4.9 row per token with what to do: at night, nothing; at session start, `session check`, then `db status`, report, never edit the DB. |

Informational (no action needed for GO):
- **I-1.** A `telegram` child that is down across the 18:35 ET rename misses the unread tail of `replay.log` (probe D: 0 pages after the restart). The tailer by design never reads an archive (log_tailer.py:54). It needs the child down during 17:35–18:35; fail-closed.
- **I-2.** `autotraderctl research replay` (a full run) is the writer's entry point (replay.py:791-797 → nightly → size_writer). Any manual full replay against the live DB writes that night's sizes and sends the batch. It is idempotent per (identity, night), but a run between 16:00 and 17:35 ET would evaluate the night first. OPERATOR_PROMPT.md:278 says "No CLI verb … writes a size". Part 5 should make a full `research replay` against the live DB OWNER-only, or the doc should name the replay child as the writer.
- **I-3.** On an undo (a DB restore to v17), `telegram-proposals.json` stays in the data directory. After a later re-install, the same night with the same evidence produces the same 10-hex id, so that item would be treated as already sent. Move the file aside in the printed undo.
- **I-4.** After an undo to v7c, the v7c reader lists v2 lines as unreadable (`size_steps.sidecar_unreadable`, b5f2339 decision_sizing.py:343-346) and sizes those decisions by commit. Harmless.
- **I-5.** Two concurrent full replays could both read the same unsent items before either records them: one duplicate silent message. The writer itself is serialised (`BEGIN IMMEDIATE` + evaluation row).

## 2. What holds (verified in code, tests and probes)

**Delivery (D7)**
- **One sender.** `TelegramNotifier.send(text, *, silent, reply_markup)` (notifier.py:482-509) is the only send in both children:
  - replay child: `report_notify.deliver_size_batch` → `notifier.send(text, silent=True, reply_markup=markup)` (report_notify.py:334);
  - telegram child: `ProposalWatch(deliver=lambda text, silent: notifier.send(text, silent=silent))` (telegram_bot.py:1949-1953).
  `_deliver` sets `disable_notification` on **every** part and `reply_markup` on the last part only. `notify()` is a plain send, so existing payloads are byte-identical (test).
- **Once per proposal id.** The record `telegram-proposals.json` lives in the data directory. It is written with `atomic_private_write` (paths.py:39-59: an O_EXCL 0600 temp file, fsync, `os.replace`, chmod 0600) and only after Telegram accepted the whole message (report_notify.py:340-344; M10 and M12 killed).
- **Bad record.** A corrupt, missing or oversized record, or a symlink, reads as empty and is logged. **Probe B:** a corrupt record gives one send; the record is rewritten at 0600; the second run is NOTHING_NEW. At worst one duplicate, never a crash.
- **Record write fails.** If the record cannot be written after a send: a WARNING, then a duplicate on a rerun.
- **Mid-way send failure.** Nothing is recorded: a duplicate if the same night reruns, otherwise lost (MEDIUM-2).
- **Texts while `PHONE_APPROVAL_LIVE = False`** (size_notify.py:71; test pinned; M07 killed):
  - no buttons (`approval_markup` returns None);
  - no "approve" wording in the batch;
  - no 05:55 reminder and no 07:55 expiry note: `ProposalWatch.step` returns before any DB read and writes no memory key;
  - `ALLOWED_UPDATES` stays `("message", "edited_message")`.
- **Time.** Israel time is computed with `ZoneInfo("Asia/Jerusalem")` at the deadline instant (size_notify.py:108-115). Every schedule is ET:
  - `plan_night` uses `db_admin.trading_day().starts_at`;
  - the reminder uses `pre_session_leads()[0]`;
  - the deadline uses `gateway_watch.session_start`, the same instant (test).
  The six DST cases pass: 2026-10-26 and 2027-03-15 show 13:55 Israel; 10-20, 11-03, 2027-03-08 and 03-29 show 14:55.
- **No secrets in logs or texts.** The new logs carry exception type names and counts only. Texts carry identities and proposal ids, never the chat id or the token. A night without news reads no secret (test).

**Failure isolation**
- **Nightly.** The writer and the send each sit in `except Exception` (nightly.py:499-516). `notify_size_batch` never raises (report_notify.py:353-359). Each identity is written alone (size_writer.py:731-740), with one CRITICAL per skipped identity. SIGTERM is `_Terminated(BaseException)` (cli.py:62): it is never swallowed, and the open transaction rolls back.
- **Telegram child.**
  - `ProposalWatch` is guarded (overnight_watch.py:408-415) and reads nothing while approval is not live.
  - The replay-log tailer opens lazily, and `_open` catches OSError.
  - Config was already imported through `schema_gate`.
  - The composition-root test drives `run_telegram_assistant`.
  - I found no new crash-loop path.
- **Next session's start.** Only events reach it, and they are written ≥ 30 min before the start (`WRITE_MARGIN`). A demotion fence goes through `sentinel.fence_identity` and is announced by the notifier's 07:55 "fenced at start" (notify_service.py:2753-2776).

**Phone alert path**
- **By day.** The notifier forwards `SIZE_TRIPWIRES` from the strategy children's logs, once per token per session date (per-date state, retried delivery rounds). The regex needs a `CRITICAL … <token>:` line: decision lines and stack lines are ignored (test).
- **By night.** The `telegram` child tails `replay.log`. Its checkpoint and memory live in `state.json` (`tails`, `watch`).
  - **First start:** it follows the log from its end, so there is no flood on the install night (probe E: 0 pages).
  - **18:35 rotation:** `replay.log` is renamed. The replay child ended at 18:30 + 20 s, so the file is not held open. The tailer drains the old handle's tail first. Only supervisor.log carries lines, so no replay line is re-sent.
  - **Same night:** de-dup holds per token per ET date (the replay runs 17:35–18:30 ET, one ET date).

**Deploy with v18 pending** (the live DB at v17 and this code)
- **Children that exit 75** (schema gate, retried, `CHILD_EXIT_UNCOUNTED reason=schema_not_ready`): screener, universe, the strategies, manager, recorder.
- **The replay child runs** (`research replay` has no CLI gate; cli.py:896-910):
  - the promoter reports `identity_size_store_unreadable` (schema_migration_pending);
  - the writer returns `not_written` before any CRITICAL;
  - the batch finds no store, so NOTHING_NEW.
  No write and no page.
- **The telegram assistant keeps serving** `/halt`, `/stop` and the manual desk (`TELEGRAM_ASSISTANT_REQUIRES = 12`).
- **The notifier** tolerates the pending migration.
- **The bridge** runs (no schema check) and has nothing new to send.
- **The dry run** prints `!!! DATABASE NOT READY (schema_migration_pending)` and `!!! the screener, the strategies, the manager and the recorder will refuse to start …` (supervisor.py:1984-1996).
- **`deploy check/apply --expect-migration 18`** passes only with pending == (18,) (deploy_check.py:741-770) and a verdict line carrying `migration=18`.
- **Undo** (`deploy undo`) refuses `undo_needs_db_restore`; a DB restore is never automated.
- **Nothing writes sizes at install time.** Migration 18 creates empty tables; the dry run and the start read only.
- **Pins.** `shasum -a 256 backend/scripts_local/paper_bridge.py` = b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4, equal to `REVIEWED_BRIDGE_SHA256` (supervisor.py:241). The three import pins match the files. `deploy_check.imports_digest(map)[:12]` = 7b1df1d4824e. The diff touches no bridge, pinned-import, supervisor or deploy-tool file, so the P2 scope is not needed.

**session_check and sidecar**
- **Rule 4** reads the size in force through `size_history.read_size_store` / `SizeStoreView.size_for`, which shares `identity_sizing.size_from_events`:
  - no store (v17) gives the baseline "no size store …";
  - a session before the install gives the baseline;
  - an unreadable store gives WARN `size_in_force_unknown`, each SENT held to ×1, and a quantity above it still FAILs;
  - a mid-session event gives WARN.
  WARN keeps exit 0, as every WARN does (gap: LOW-1).
- **Sidecar readers.** The only readers are `size_history` and `promotion_proposals`, through `read_decision_sizing` → `from_payload`, which accepts versions 1 and 2. A v1 line re-serialises unchanged. The decision payload is untouched (golden digests, test). The dashboard and routers read no sidecar.
- **A failed sidecar write** is caught in the pipeline (pipeline.py:689-700) and never blocks a decision.

## 3. Docs mismatches (docs/OPERATOR_PROMPT.md at 27a9d34 vs the code)

| # | line | Docs say | Code does |
|---|---|---|---|
| 1 | 1167-1171 | "step up delivery [NOW] P3-E: … with Approve / Reject buttons per proposal id; a normal-sound reminder 2 h before the deadline (05:55 ET) …; at the deadline … a silent 'expired, sizes unchanged' note" | While `PHONE_APPROVAL_LIVE = False` (size_notify.py:71) there are no buttons (:174), no reminder (:407) and no expiry note (:416). Only the silent batch is `[NOW]`. |
| 2 | 1178-1179 | "Until P3-D is live the buttons do nothing" | No buttons are sent at all until P3-D. |
| 3 | 733 | "Every line comes with the phone's normal notification sound … nothing is sent silently (owner, 2026-10-17)" | The night batch is silent (`disable_notification`, report_notify.py:334; D7). |
| 4 | 713-729 (4.9 table) | No row for the size layer | Missing: the silent night batch (17:35–18:30 ET); size tripwires by day (notifier) and by night (telegram child, replay.log), each token once per day; the reminder and expiry `[PLANNED]` P3-D. |
| 5 | 617 (4.6) | `identity_sizing_missing` = "Table row missing" | It also follows an unreadable store, a ceiling problem or a broken chain. No rows for the 9 `SIZE_TRIPWIRES` tokens, `size writer: night …`, `demotion_waiting_new_evidence`, `size changes not sent` / `size delivery record not saved`, or what to do. |
| 6 | 841 (Part 5) | "Decrease size / caps \| RUNTIME `[PLANNED]` within rules" | The nightly step-down is runtime `[NOW]` with this layer (size_writer.py). "AGENT+ implement" a decrease by a baseline commit would now break the event chain (the identity reads None) once events exist. |
| 7 | 278 | "No CLI verb, agent, AI manager or hand edit writes a size" | `autotraderctl research replay` (a full run) is the CLI verb the replay child runs, and it writes sizes (I-2). |
| 8 | 280 | "one CRITICAL names the token" | Two CRITICALs, so two pages: the read's token and the pipeline's `identity_sizing_missing`. A missing ceiling or account-mode row refuses the whole launch (`registry_problems` → LAUNCH_REFUSED), not just that identity's setups. |
| 9 | 281 | "an unreadable store is a WARN `size_in_force_unknown` …, never a PASS" | PASS when nothing was sent (session_check.py:1410; LOW-1). |
| 10 | 1145-1150 | "the size as % of that volume … flagged size_above_bar_volume_pct (> 1 %)" | Shares ÷ round lots, 100× too high (MEDIUM-1). The bar is the newest 5-minute bar; the doc does not say so. |
| 11 | 1322 (10.3) | "size store, step-downs, delivery and realism `[NOW]` with this layer" | The delivery's reminder, expiry and buttons are not live; the realism figure is wrong (MEDIUM-1). |
| 12 | 209 (timeline) | "17:35–18:30 \| 00:35–01:30 \| Replay" | Also the size writer, the silent batch and size pages at night (23:35–00:30 Israel in the 25 Oct–1 Nov week). |
| 13 | 1391 (D-15) | "Nothing can disagree in effect before then: … a step-down only lowers risk" | A step-down is a size that differs from AGENTS.md:7-11's written grant. It is risk-reducing, but it does disagree: the Appendix A approval must come before L-sizes. The required rule "AGENTS.md wins until Appendix A is approved" is stated correctly. |

Correct as written (checked against the code):
- 1.3: no size goes up before P3-D;
- 3.1: the ladder, the read once after the connect wait at about 09:23 ET, the 07:55 ET mid-session rule, the ceiling ×1 PAPER, fail closed;
- 8.1;
- 8.2 Stage 2 rewritten to D2, with the "auto_within_ceiling" text removed;
- 8.4 "Size step-down";
- 3.3 v7c against bridge-v7c's real behaviour: `budget_problem` / `account_open_risk` / `account_realized_loss` exist (paper_bridge.py:2321, 2431, 2526); 3 % and 2 % (:209, :215); `STOP_SLIPPAGE_FACTOR` 1.0 with a ≥ 1 tripwire (:218, :233); the lowest working SELL STP from `reqAllOpenOrders` every 30 s (:223-226); unprotected shares block (`v7c_unprotected_position`); check before `placeOrder` after the quote wait and the fences; `v7c_budget_ok`; the manual buy grammar `/buy 100 AAPL at 180 stop 175` (manual_orders.py:22, :430); the worst case 3 × 0.5 + 3 × 0.5 = 3.0 %; `OPEN_RISK_TERM_DEPLOYED = True`;
- D-15: AGENTS.md wins until Appendix A is approved.
- Nothing in the docs says phone approval is live: every approval mention is `[PLANNED]` P3-D.

## 4. Tests and mutants

- **Targeted run** (sandboxed, fake HOME, 04:56): test_size_notify, test_size_writer, test_size_store, test_size_history, test_decision_sizing, test_session_check, test_overnight_watch, test_notifier and test_promotion_proposals all passed (exit 0).
- **Mutant set baseline** (8 files): 209 passed in 33.8 s.
- **Broader run** (19 files, adding nightly, shadow_service, sendable, setup_persistence, migration_freeze, live_manager_routes, notify_manager, notify_service, telegram_bot, research_ops; 05:04–05:07 Israel): **824 passed, 1 skipped** in 178 s.

Mutants: 14. Each ran on a fresh `git archive` extract of 27a9d34, sandboxed, with its own HOME and the 8-file set. Runner: `n19b-ops/scripts/mutate.py`; logs in `n19b-ops/logs/`.

| # | Mutant | Result | Killed by / missing test |
|---|---|---|---|
| M01 | the record is ignored, so the batch is sent twice | KILLED | test_batch_sent_once_silent_with_buttons |
| M02 | the batch is not silent | KILLED | test_batch_sent_once_silent_with_buttons |
| M03 | the record is a plain write (0644, not atomic) | KILLED | same (the 0600 assertion) |
| M04 | the record is non-atomic but 0600 | SURVIVED | Atomicity is untested. Add: a crash between truncate and write leaves the old record readable (or a monkeypatched write that raises keeps the old file). |
| M05 | an exception from `deliver_sizes` aborts the nightly | SURVIVED | Add: a raising sender leaves `run_replay_cli` at exit 0 with `sizes.delivery=failed_*`. |
| M06 | the nightly's writer guard is narrowed to `ValueError` | SURVIVED | Add: a raising `write_night` (e.g. `OperationalError`) leaves the replay at exit 0 and logs a CRITICAL size token (LOW-3). |
| M07 | "approve by" wording while not live | KILLED | test_phone_approval_live_is_false_until_p3d |
| M08 | replay-tripwire de-dup in process memory only (not persisted) | SURVIVED | Add: two `OvernightWatch` instances sharing `memory`, the same night (a telegram restart): one page. |
| M09 | the replay log restarts at its end (checkpoint ignored) | SURVIVED | Add: a restart with a checkpoint, and a CRITICAL appended while down: paged once. |
| M10 | record written before the send | KILLED | test_a_failed_batch_is_sent_by_the_next_run… |
| M11 | a corrupt record raises (every night FAILED for ever) | SURVIVED | Add probe B as a test: a corrupt or oversized record or a symlink gives one send and a rewritten 0600 record. |
| M12 | record written after a failed send | KILLED | test_a_failed_batch_is_sent_by_the_next_run… |
| M13 | `ProposalWatch.step` unguarded in the watch | SURVIVED | It only matters once approval is live: P3-D must add "a raising store never stops the port watch". |
| M14 | only the first line of each replay-log poll is examined | SURVIVED | The fixture holds only the writer's own records, so the CRITICAL is always first. Surround it with real replay lines. |

Six killed, eight survived. Every survivor is a missing test in the isolation, de-dup or restart paths (LOW-5). None is a defect in the current code, except M06, which LOW-3 covers.

Probes (scratch tests in `n19b-ops/probe/backend/tests/test_n19b_probe*.py`, sandboxed):
- **A** (MEDIUM-2): a failed Monday batch is never delivered.
- **B:** a corrupt record gives one send, then a 0600 rewrite.
- **C** (LOW-2): two identities with the same token give one page naming one identity.
- **D** (I-1): the telegram child down across the rename gives 0 pages.
- **E:** first start with an old `replay.log`: 0 pages.
- **F** (LOW-1): an unreadable store with no SENT: all 7 rules PASS, exit 0.

## 5. Deploy notes (L-sizes)

**Prerequisites, all required:**
1. Live is the frozen bridge-v7c head (b5f2339 or its docs-only successor), installed by `deploy apply --expect-migration 17`. The live DB is at schema **v17**, so `pending == (18,)`; a v16 DB refuses `migration_unexpected`. At least 2 clean v7c sessions have been observed (plan).
2. A GO verdict line names the exact deploy head, with `bridge_sha=b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 imports_sha=7b1df1d4824e migration=18` and the ES verdict (N19b-ES) for the same head. This head is NO-GO: the fixes for MEDIUM-1 and MEDIUM-2 need a re-read.
3. The owner has approved the Appendix A wording (D6), and the AGENTS.md commit is in the deploy head. AGENTS.md is not under `docs/reviews/**`, so a commit after the verdict needs its own re-read verdict line. Also update AGENTS.md's "Schema" bullet (migrations 17 and 18 frozen once applied), and add v18 to the freeze test's applied-in-production map as a data commit after the install.
4. Rehearsal S1–S5 PASS on a live-DB copy, with S5 (delivery on a mock transport) including a **failed** send followed by the next night. Docker gates PASS. Zero hits in the `DU[A-Z0-9]{5,}` scan.
5. **Pre-mortem read** (owner, or read-only): the newest nightly `proposals.json` (it holds `DEMOTION PROPOSAL` lines). The first night after the install acts on any standing demotion **immediately**: with no earlier size event there is no wait for 5 new trades. That means a rung step-down for MOMO, VWAP or GAP, or a **fence of ORB_V2** if it is at ×0.25 (`SENTINEL_FENCES_LIVE = True`). The owner should know this before installing.
6. **Command,** inside 18:40 → 06:50 ET, owner at a Terminal:

   ```
   autotraderctl deploy apply --candidate <head> --repo /Users/salomon/aiautomation --expect-parent <live HEAD 40-hex> --expect-pin b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 --scope <verdict scope> --expect-migration 18
   ```

   The dry run after `db migrate` must show:
   - `database     schema v18 current (…)`;
   - `bridge       reviewed (sha256 b40fa40b4acd)` and `bridge       imports reviewed (3 files)`;
   - `fenced identities = none` (or exactly `--expect-fenced`);
   - the four sendable identities;
   - `registry … agree`;
   - the `day risk budget 3%` line.
7. **Undo** is the printed DB restore (never automated) plus `reset --keep <PREV_SHA>`. Also move `~/.local/share/autotrader-v1/telegram-proposals.json` aside (I-3). Old v2 sidecar lines stay readable as "unreadable" lines to v7c (I-4).

**What runs and what the owner sees after the install** (example: install Fri night, first session Mon):
- **Install night.** Nothing writes sizes; migration 18 adds four empty tables. The `telegram` child restarts and follows `replay.log` from its end, so no old page. No new message.
- **Monday session.** The strategy children read the store at about 09:23 ET (orb-v2, gap-v2, vwap-v2) and 09:50 ET (momo). With no events, every identity gets its baseline: ×1, ×0.25, ×0.5, ×0.5. No CRITICAL and no page.
  - If the store were unreadable: one `🚨 Size tripwire: identity_size_store_unreadable` (normal sound, about 16:23 Israel) plus `identity_sizing_missing`, and no entries.
- **Monday 17:35–18:30 ET** (Tue 00:35–01:30 Israel): the replay, then the promoter (`current` from the store), then the writer: 4 evaluation rows for night Mon, session Tue. Then **at most one silent message**: "🌙 Sizes after the Mon … session (silent; step-downs apply by themselves)", with these lines:
  - `PROPOSAL … sizes stay unchanged: phone approval arrives later (this proposal expires Tue 07:55 ET (14:55 Israel))`;
  - `STEP DOWN …`;
  - `SHADOW-ONLY … fenced … (resume is yours: autotraderctl paper resume --identity …)`.
  
  No news means no message at all. A skipped identity pages `🚨 Size tripwire: <token>` with **sound** at night (LOW-7).
- **Tuesday.** No 05:55 reminder and no 07:55 expiry note (approval is not live). A demotion fence, if one was written, is in the notifier's 07:55 "fenced at start" line (normal sound). A step-down is applied silently at the Tuesday start.
- **After the first session,** from the candidate worktree against copies: `autotraderctl session check --date <D>`. The inputs must show `sizes in force (size store, read-only): GAP_GO_V2 x0.5 (baseline: no size event in force); …`. Because of LOW-1, also grep each strategy log for `identity_size_store_unreadable` and `identity_sizing_missing`: a rule 4 PASS alone does not prove the store was readable.

## 6. Suggested ledger line (grammar of docs/reviews/2026-10-15-ledger.md's last lines)

```
2026-10-23T05:08:45+03:00 | P3-sizes 27a9d3432ee65ef8fb7e0f0b616ca57fb302944c verdict=NO-GO scope=P3-C,P3-E,P3-F,N19b-ops bridge_sha=b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 imports_sha=7b1df1d4824e migration=18 | reviewer N19b-ops: OPS review of b5f2339..27a9d34 (P3-C+fix 1+fix 2, P3-E+fix 1, P3-F merged); MEDIUM-1 sidecar v2 size_pct_of_bar_volume divides shares by TWS round lots (bar_volume raw, fixture volume_unit lots, IBKR_API_VOLUME_MULTIPLIER 100): 100x too high, flag fires on ~every trade, v2 format freezes once written (from_payload re-derives) - fix to shares before any v2 line ships; MEDIUM-2 a failed night batch is never retried in production (unsent_items reads only tonight's as_of, replay exits 0, run-once) - a step-down notice is lost silently (probe A) - fix: read undelivered items of recent nights; LOW-1 rule 4 PASS with an unreadable store and no SENT (all 7 PASS, probe F); LOW-2 per-token de-dup names one identity (probe C); LOW-3 whole-night writer failure has no CRITICAL token; LOW-4 13 OPERATOR_PROMPT mismatches (8.2 buttons/05:55 reminder/07:55 expiry claimed [NOW], 4.9 "nothing is sent silently", Part 5 runtime decrease [PLANNED]); LOW-5 test gaps; LOW-6 texts; LOW-7 night pages with sound; verified: one sender (silent on every part, keyboard last part), record 0600 atomic, written only after acceptance, corrupt record = one duplicate; PHONE_APPROVAL_LIVE False: no buttons, no approve wording, no reminder or expiry note; ZoneInfo Israel time, ET anchors, 6 DST cases; v18 pending: screener/strategies/manager/recorder exit 75, replay writes nothing, telegram /halt and manual desk serve, dry run !!! DATABASE NOT READY; nothing writes sizes at install; v7c 3.3 text matches the bridge; bridge b40fa40b..., imports_sha 7b1df1d4824e unchanged; my mutants 6/14 killed (8 survivors = isolation/dedup/restart test gaps); 19 related test files 824 passed 1 skipped | docs/reviews/2026-10-23-p3-sizes-n19b-ops-review.md
```

The label `P3-sizes` is a suggestion: the coordinator picks the layer label used in the verdict and DEPLOY lines.
