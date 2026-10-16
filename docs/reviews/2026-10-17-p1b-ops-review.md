P1-B 134bde4 verdict=GO scope=OPS:T-3,T-4,T-4b,T-6 bridge_sha=7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4

# N15b — independent OPERATIONS review of P1-B (ops-p1b 134bde4, base a070848)

Reviewer: independent, read-only, did not write these changes. Scope: T-3 (heartbeat, 18:35 ET
rotation), T-4 (overnight Gateway watch, F-2 forwarding, competing login), T-4b (pre-session
real-data check), T-6 (OPS section), 087e9f0 (shutdown fix), docs. Order safety (T-8, Sentinel
fence writes, tws_actor listener, client-21 sharing) belongs to the other reviewer; one Sentinel
observation is passed on (L-11). Bridge `backend/scripts_local/paper_bridge.py` at 134bde4 is
sha256 `7d313fd0…62af4`, byte-identical to a070848 and equal to `supervisor.REVIEWED_BRIDGE_SHA256`
(supervisor.py:232); `git diff --stat a070848 134bde4 -- backend/scripts_local backend/trading/risk
backend/trading/execution` is empty.

Method: clones in /Users/salomon/autotrader-work/review-n15b (`clone`, `clone-ro` at 134bde4,
`clone-eededf9`, `clone-live` at 664c93b). Every python/pytest under `sandbox.sb` (a copy of
sandbox-gates.sb, writes allowed only under review-n15b, every deny kept), HOME=fakehome,
PYTHONDONTWRITEBYTECODE=1, backend/.venv python 3.11.15. No connection to 7496/7497/4000-4002, no
launchctl, no Keychain, no ~/Jts. Real logs copied read-only from ~/Library/Logs/AutotraderV1 and the
real bridge state files (10-13..10-16) from ~/.local/share/autotrader-v1/bridge; one `mode=ro` query
of the live universe table (AAPL conId). `pmset -g log` read for power/sleep evidence. Account ids
masked as DU1234567 everywhere below.

## Summary
- **GO: zero HIGH.** T-4/T-4b, replayed through the production loop (`OvernightWatch.run` + the real
  `PreSessionCheck`) over the real 2026-10-13..10-17 logs, page every real Gateway outage at the right
  time and send nothing on the healthy nights/mornings (10-13, 10-15, 10-16 evening, Saturday).
- **10-16 (GW-HMDS-1016):** T-4b would page "not returning prices" at 05:55:20 and 07:40:20 ET *if* the
  link was already broken (consistent with, but not proven by, the evidence: earliest direct evidence
  is 08:00:20 ET). In every case P1-A.3's notifier page still comes at 08:02:20 ET (replayed with the
  real 134bde4 notifier over the real day).
- **087e9f0 is a real fix:** my own deterministic reproduction hangs `stop()` on eededf9 and not on
  134bde4; all six of the builder's stop tests fail on eededf9; composition-root tests 20/20. The live
  664c93b `stop()` hangs the same way (reproduced) — residual risk LOW.
- **Two MEDIUM docs/runbook items to fix before the P1-B install:** the first P1-B start (and any start
  that missed 18:35) rotates at once, so the deploy STEP 4 `tail -n 3 supervisor.log` expectation fails;
  OPERATOR_PROMPT documents T-4/T-4b but not T-3/T-6 (rotation, heartbeat, OPS).
- Mutations: 15/15 KILLED (7 required + 8 own). Full backend suite: 3052 passed, 2 skipped (env).

## Findings

### HIGH
None.

### MEDIUM

**M-1 The first P1-B start rotates immediately; deploy STEP 4's `tail -n 3 supervisor.log` check fails.**
Evidence: `Supervisor.run` sets `_rotation_due = rotation.due_at(started)` (supervisor.py:784);
`LogRotation.due_at` returns the newest 18:35 boundary while `supervisor.log.<that date>` does not exist
(supervisor.py:1531-1538); `_wait_until` rotates on its first pass (supervisor.py:1221-1229, 1240).
The first P1-B start never has that archive (P1-A does not rotate), wherever it falls in the window; so
does any start after a supervisor that was stopped across 18:35 (STEP 1 at 18:30-18:35, STEP 4 later).
Simulated with the real Supervisor/SupervisorLog/LogRotation (reviewer test, deleted after the run):
new supervisor.log = `SUPERVISOR_START, CHILD_START telegram, SESSION_WAIT, LOG_ROTATED …`, so
`tail -n 3` = `CHILD_START telegram | SESSION_WAIT … | LOG_ROTATED date=… carried=3`; with the
heartbeat scheduled `CHILD_START heartbeat | SESSION_WAIT | LOG_ROTATED …`. OPERATOR_PROMPT 6.2
STEP 4 (docs/OPERATOR_PROMPT.md:759-761) expects `SUPERVISOR_START, CHILD_START telegram,
SESSION_WAIT`. The first rotation also moves the whole multi-day supervisor.log (since 10-13) and every
child log into `<name>.log.<date>`. STEP 1's `tail -n 1 → SUPERVISOR_STOP` survives (STOP is written
last). Failure scenario: at the P1-B install the owner/agent sees an unexpected last line at STEP 4,
aborts or rolls back a good deploy at 01:45 Israel — or learns to ignore the check. Rev-2 item 17
("installs start at 18:40 ET or later") does not cover the first start. Fix: in the P1-B runbook,
check `grep -E ' (SUPERVISOR_START|CHILD_START telegram|SESSION_WAIT) ' supervisor.log | tail -3` and
accept `LOG_ROTATED` (and `CHILD_START heartbeat`) as later lines; say that the first start rotates.

**M-2 OPERATOR_PROMPT on ops-p1b omits T-3 and T-6, which the same branch ships.** No 18:35 ET rotation
in the 2.3 timeline although it sits inside the 18:30→07:20 deploy window; no `LOG_ROTATED` /
`LOG_ROTATION_FAILED` rows in 4.6; the heartbeat is still `[PLANNED]` (docs/OPERATOR_PROMPT.md:152,
:619) with no monitor setup guidance (period/grace, `/fail?reason=battery`, what a deploy or a dark wake
looks like); the OPS section is still `[PLANNED] P1` (:805). Agent commands that read supervisor.log
silently change meaning after 18:35: `grep -c 'CHILD_START' $L/supervisor.log # restarts = starts − 1`
(:557) counts the carried copies and misses the archived day; the post-session
`grep -E 'CHILD_(SUSPENDED|RESTART_STORM)' $L/supervisor.log` (:158) misses lines now in
`supervisor.log.<date>`. Failure scenario: the operator agent's post-session report after 01:35 Israel
says "no suspensions, 1 restart" for a day that had a replay suspension and nine bridge restarts.
Fix before install: document rotation (incl. `supervisor.log.<date>` for same-day greps), heartbeat
(states, battery rule, monitor grace — see L-13), the OPS lines.

### LOW

**L-1 Outage start times after a handover/restart are the watch's own, not the outage's.** The watch
starts a fresh streak at 16:15 and pages an outage that BrokerWatch already announced as "closed since
16:15 ET" (pinned by test_inside_the_notifier_window_…, test_overnight_watch.py:244-257); after a
telegram restart its in-memory state is gone. Replay 10-14: the deploy restart at 19:28 ET 10-13 made the
05:55 page say "closed since Tue 19:29 ET" (real: 16:45 ET), and the digest say "quiet hours, not paged"
for an outage paged at 16:50. Text only; persist `outage_since`/`paged` in AssistantState or read the
notifier's `unreachable_since`.

**L-2 Recovery lines are sent in quiet hours** (gateway_watch.py:300-308): replay 10-14 → "✅ IB Gateway
port open again 19:05 ET" at 02:07 Israel. Documented in 4.9 ("whenever"), not in the 2.3 quiet-hours
row. See recommendations.

**L-3 In-window restart-cap lines are dropped even when the notifier is not running**
(overnight_watch.py:234-235). A notifier crash-loop/suspension during the session (`CHILD_SUSPENDED
notifier`) is paged by nobody until the notifier itself resumes; a line written between the notifier's
last poll and its 16:15 exit is lost. Pre-existing gap the watch could close: forward in-window lines
that name `notifier`.

**L-4 T-4b pages on one 20 s request, no retry, no fallback contract** (pre_session_check.py:195-220).
No false page on the real healthy mornings (10-13, 10-15: bars returned at both leads in the replay),
but a slow first request of the day pages "restart IB Gateway". Confirm once (e.g. +2 min, like the
port's open_after) before paging; on "no bars" (not a timeout) try the next pinned symbol.

**L-5 The rotation's "one instant" is not atomic across processes.** `SupervisorLog.rotate` flushes the
carried lines (supervisor.py:1462), then ~22 archive prunes run (:1571), then the marker is emitted
(:1577, via `_rotate_logs_if_due`). A follower poll can fall between them: `RotationCarried.fresh` then
passes the carried lines as fresh. Harmless today (carried kinds are SUPERVISOR_START/SESSION_*/
CHILD_START/CHILD_EXIT, never restart-cap kinds; the notifier never runs across a rotation; the heartbeat
reads them idempotently), fragile for any future carried kind. Write the carried lines and the marker
in one write.

**L-6 Shutdown residual risk.** 134bde4: remaining `wait_for` in tasks cancelled at shutdown —
TWSPaperActor connect/requests (tws_actor.py:606, 614, 698-1036; T-4b, /quote, /status, tools),
ChatResponder (telegram_bot.py:941, 1046, 1105), manual-order account snapshot (telegram_bot.py:2172,
telegram_tools.py:1187), fence/tool reads (telegram_tools.py:1245, 1306, 1317). Covered by the loop guard
(overnight_watch.py:197) and `stop()`'s 5×1 s re-cancel; the main task's SIGTERM path
(`cli._cancel_on_sigterm`, one cancel) relies on the supervisor's SIGKILL after 20 s. LIVE 664c93b:
reproduced deterministically that `stop()` (single cancel + gather) hangs when the question worker's
`wait_for` swallows the cancel (scripts/repro_live_stop.py: "live stop() HANGS"; same script on 134bde4:
"stop() returned; worker cancelled"). On SIGTERM it is bounded by SIGKILL; on a fatal poll error the
child would sit with no polling and no exit. Window is microseconds at the end of a Claude/tool call —
negligible; no action on live before P1-B.

**L-7 LEARNED/docstring call a test flake "live".** LEARNED.md:67 "on 2026-10-17 the T-4 overnight watch
kept running after the Telegram poll loop had stopped on a 401" and overnight_watch.py:31 "live flake
2026-10-17" describe the composition-root test (T-4 has never run live). Say "in the composition-root
test".

**L-8 The agent-run dry run now reads a secret.** cli.py:962 passes `heartbeat_url_from_keychain` to
`dry_run_report`; 4.1.2 says WHO: AGENT. The URL is never printed (host only), but an agent preflight that
reads a Keychain secret conflicts with the agents' no-Keychain practice. Check presence only
(no `-w`) in the dry run; validate the URL in the child.

**L-9 Fixture id `ACCOUNT = "DU123456"`** (test_pre_session_check.py:29): fake, but not the DU1234567
convention the plan's identifier scan (`DU[0-9]{5,}`) is written for. No real id, chat id, token or URL
in the added lines (scan of `git diff a070848 134bde4`; only hc-ping.example / 127.0.0.1 fixtures).

**L-10 Digest vs OPS flap counts disagree, and the digest wording is wrong for a sustained outage.**
`take_flap_line` (notify_service.py:1253-1262) counts every hysteresis flap; `ops_flaps` (:1064) excludes a
held-back open that the sustain limit later announced. Demonstrated (scripts/flaps_demo.py): one announced
outage 09:00, a second at 09:10 held back then announced at 09:15 → digest "⚡ IB Gateway flapped 1× since
09:10 ET (held back from separate alerts)" while that outage *was* paged; OPS "2 outages, 0 flaps". Also
a data-refused outage is "121 min unreachable" in OPS and "unreachable since 08:00 ET" in the recovery
line while the Gateway was reachable.

**L-11 (observation for the order-safety reviewer; risk-reducing, not an order-path defect).** The real
10-16 day through the 134bde4 notifier, with the bridge state staged at its real times, auto-fences
MOMO_HOD_V2 at **11:52:30 ET**: "losing streak 3 losing trades in a row (-0.72 R, -1.07 R, -0.89 R net)" =
T (10-13), HST, MCHP (10-16). The live ledger skips the three 10-15 MOMO brackets (IP, CPRT, VEEV) that
the bridge state still shows `open` after MAC-SLEEP-1015's failed flatten, so the streak bridges trades of
unknown outcome; MOMO would start the next session fenced until the owner resumes it. OPS then shows
"Fenced: MOMO_HOD_V2 (sentinel: losing streak …)".

**L-12 A notifier-window outage that recovers 07:50-07:55 gets no ✅.** If the last pre-window check failed
(e.g. port reopened 07:52 after a 07:40 page), the handover sends nothing (gateway_watch.py:260-271) and
BrokerWatch never opened: the owner's last word is "⛔ closed". Documented in 4.9; acceptable, noted.

**L-13 Dark wakes can make a sleeping Mac look alive to the monitor after 16:15 ET.** pmset 10-15:
battery from ≤14:16 ET, clamshell sleep 14:45:12 ET, dark wakes every ~15-17 min (14:47, 15:05, 15:22,
15:39, 15:56, 16:12, 16:31, 16:46, 17:02, 17:07, 17:24 ET), lid wake 17:32:55 ET. Processes run in dark
wakes (live: the notifier's Telegram sends at 15:05/15:22/15:39 ET, the recorder started 16:31:14 ET in
one). In 07:40-16:15 ET T-3 sends `/fail?reason=battery` (good: it would have paged ~14:17 ET on 10-15,
28 min before the sleep, and ~10:21 and ~15:03 ET on 10-16 when pmset shows battery 10:20-12:53 and from
15:03 ET); after 16:15 a dark-wake ping is a success ping. A monitor grace ≥ ~17 min would hide the sleep.
The repo has no grace guidance (heartbeat.py:5 only "its own grace period"). Document period 1 min, grace
≤ 5 min; expect a monitor page during every deploy (pings stop while the supervisor is down).

## Replay table — what T-4/T-4b would have sent (134bde4 `OvernightWatch.run` + real `PreSessionCheck`)

Port-closed spans inferred from the real logs: 16:45:00 ET 10-13 (recorder "TWS connection closed"
23:45:00 Israel, then 502 "Couldn't connect") → ~11:30 ET 10-14 (screener reconnect 18:30:46 Israel);
16:45:00 ET 10-14 → 19:04 ET (owner login 02:04 Israel). Telegram child lifetimes from the real
supervisor.log (restarts 09:42, 09:46, 19:28/19:29 ET 10-13; 18:42, 18:45 ET 10-15). Mac asleep
14:45-17:32 ET 10-15 (pmset). Real 18:35 rotations interleaved (real `LogRotation`): identical output.
T-4b times are the lead; in reality +20 s (the actor's request timeout).

| Night / morning | Real state (evidence) | Sent (ET / Israel) |
|---|---|---|
| 10-12 eve → 10-13 morning | no supervised telegram child before 02:51 ET 10-13; port open, data healthy (screener connected 08:00, first sweep 08:10) | nothing; T-4b 05:55 and 07:40: bars → silent |
| 10-13 evening | Gateway logged off 16:45:00 ET | **16:50:29 / 23:50** "⛔ IB Gateway closed since 16:45 ET (its API port does not answer) — log in to IB Gateway" |
| 10-13 night → 10-14 morning | port closed all night; telegram restarted 19:28 ET (deploy) | quiet: nothing; **05:55 / 12:55** "🌙 … port closed since Tue 19:29 ET, still closed" + "⛔ … closed since Tue 19:29 ET … session starts 07:55 ET (05:55 ET check)"; **07:40 / 14:40** same page "(07:40 ET check)"; T-4b not run (port closed); 07:55 handover silent |
| 10-14 evening | logged off 16:45 ET again; owner login 19:04 ET | **16:50 / 23:50** "⛔ … closed since 16:45 ET"; **19:07 / 02:07** "✅ IB Gateway port open again 19:05 ET (closed since 16:45 ET)" |
| 10-15 morning | port open, data healthy (first sweep 08:10) | nothing (both checks returned bars) |
| 10-15 evening | Mac in clamshell sleep to 17:32 ET; port open; deploy restarts 18:42/18:45 | nothing from T-4 (evening watch is port-only). T-3 (if configured): /fail from ~14:17 ET, then silence → monitor page |
| 10-16 morning | port open; IBKR refused data from ≤08:00:20 ET (2110 + 2103/2105/2157 at the bridge's 09:25 connect) | A (broken before 05:55): **05:55 / 12:55** and **07:40 / 14:40** "⛔ IB Gateway is not returning prices (HH:MM ET check) — 1) log out of the IBKR app and website, 2) restart IB Gateway: File → Exit, reopen, log in". C (broke 05:55-07:40): 07:40 only. B (07:40-08:00): nothing. Every case: notifier **08:02:20 / 15:02** "⛔ IBKR is refusing prices since 08:00 ET (Gateway connected) …", 08:32/09:02/09:32 "restart IB Gateway now", 10:02:37 "✅ reconnected 10:00 ET" |
| 10-16 eve → 10-17 (Sat) | port open, data healthy (recorder completed 23:52 Israel); Saturday closed | nothing (Saturday quiet throughout) |

When did the 10-16 link break? Earliest direct evidence: the screener's first daily-history request
after connecting at 08:00:00 ET timed out at 08:00:20 ET (screener.log), every one after it until the
09:51 Gateway restart; the bridge's 09:25:00 connect logged 2110 and the three broken farms. Nothing
requested IBKR data between the recorder's SIGTERM at 17:33 ET 10-15 and 08:00 ET 10-16 (replay uses no
TWS; no owner /quote). My first reading of the recorder's hour of silence on 10-15 as "data refused" was
wrong: pmset shows the Mac asleep with dark wakes until 17:32 ET. The Gateway (up since 19:04 ET 10-14)
lived through that 2 h 47 min sleep — a plausible, unproven cause. The Gateway's own logs (~/Jts) would
date the 2110; they are off-limits to this review.

No false page on any healthy night or morning. No double page: inside 07:55-16:15 the watch neither
probes nor speaks (M2 killed); T-4b runs only with the port open and its `port_closed` verdict sends
nothing (M13 killed); the competing-login windows do not overlap (notifier to 16:15, watch 16:15-17:30
from recorder.log only; the notifier never follows recorder.log); restart-cap lines are split by their
own timestamp (M11 killed), carried lines and archives are never forwarded (M4, M5a, M5b killed).

## OPS block — the real 2026-10-16 through the 134bde4 notifier

All real screener/bridge/supervisor/children lines of 07:55-16:15 ET appended at their own times
(fake clock, fake DB, real bridge states staged at their real times):

```
OPS
• Restarts today: bridge=9 gap-v2=8 momo=3 momo-ap=3 screener=1 vwap-v2=4 (starts after each child's first today)
• Suspensions/storms: none
• Gateway: 1 outage, 121 min unreachable, 0 flaps
• Refusals: none
• Fenced: MOMO_HOD_V2 (sentinel: losing streak 3 losing trades in a row (-0.72 R, -1.07 R, -0.89 R net))
• Close: flat at 15:50 ET
• GAP_GO_V2: 0 trades · P&L $0.00
• MOMO_HOD_V2: 2 trades · P&L -$1,420.68
• ORB_V2: 0 trades · P&L $0.00
• VWAP_PB_V2: 0 trades · P&L $0.00
```

Checked against the real logs: restarts equal the CHILD_EXIT counts in supervisor.log for 10-16 (bridge 9
exit-75s 09:25-09:51, gap-v2 8, momo 3, momo-ap 3, vwap-v2 4, screener 1); one announced outage
08:00:20-10:00:36 = 120.3 min → 121, counted once, 0 flaps; no LAUNCH/PREFLIGHT refusals; MOMO P&L =
MCHP −812.43 + HST −608.25; the 15:50 check was flat (both stopped out, 11:05 and 11:52 ET). The fence line
is the Sentinel's own 11:52:30 write (L-11; the real 10-16 had no fence because P1-B was not deployed).

## Recommendations for the builder's open questions
- **Evening 16:15-18:30 ET (23:15-01:30 Israel) pages at once:** keep paging through the recorder window
  (16:15-17:30 ET, to 00:30 Israel): both real outages (10-13, 10-14) began 16:45 ET and cost the
  recorder's runs, and the owner is plausibly awake. Make 17:30-18:30 ET quiet (digest): the replay
  needs no Gateway (replay.log has no TWS use). Owner's call; either is defensible.
- **Recovery lines in quiet hours:** keep them (they answer a page the owner has, e.g. 02:07 Israel right
  after his 02:04 login), but send them silently (`disable_notification`, not supported by the notifier
  today) or fold them into the 05:55 digest when no page was sent in the last ~2 h.
- **port_unknown:** keep it out of `outage`. `gateway_port_open` maps every OSError, timeouts included,
  to closed; `unknown` only follows a non-OSError exception in the probe — a code defect, not a Gateway
  outage, and the state is informational to the monitor. Better: have the watch/notifier forward
  `PORT_PROBE_FAILED` once (a broken probe silently disables the restart cap).
- **Digest vs OPS flaps:** use one rule — `ops_flaps` ("a flip the phone never heard as an outage") —
  for both, and drop "held back from separate alerts" for an open the sustain limit announced; say
  "IBKR refused data" instead of "unreachable" for a data-refused outage.
- **AAPL vs SPY:** keep AAPL. It is the screener's own request type for a row the universe resolved
  (live universe: AAPL conId 265598, `resolved`, 503 resolved rows); SPY is not in the universe and would
  need its own contract path and a request the screener never makes. Add the retry/fallback of L-4.

## Mutation table (each applied to the review clone, targeted tests run sandboxed, reverted with
`git checkout`; clone clean after every revert)

| # | Mutation | Result | First failing test |
|---|---|---|---|
| M1 | quiet hours ignored (`is_quiet` → False in `_apply`) | KILLED | test_a_port_closed_at_0245_et_goes_to_the_digest_and_pages_nobody |
| M2 | handover double page (watch keeps probing/paging inside 07:55-16:15) | KILLED | test_inside_the_notifier_window_the_watch_says_nothing_and_checks_nothing |
| M3 | T-4b status-only (no bars request; healthy unless a 21xx code) | KILLED | test_bars_returned_is_healthy_and_silent_after_one_screener_style_request |
| M4 | rotation replays an archive (old file re-read from 0 at a rename) | KILLED | test_a_restart_cap_line_at_1800_et_is_forwarded_once_and_never_from_the_archive |
| M5a | carried lines forwarded (watch bypasses `RotationCarried.fresh`) | KILLED | test_the_rotations_carried_lines_are_never_forwarded |
| M5b | carried lines before the marker not dropped (the T-6 bug the integrator found) | KILLED | test_the_rotations_carried_lines_are_never_forwarded (and notifier tests) |
| M6 | heartbeat pings success on battery in session | KILLED | 3 tests incl. test_battery_in_the_session_window_pages_via_the_fail_endpoint_every_15_minutes |
| M7 | `stop()` single cancel (eededf9's) | KILLED | test_stop_cancels_again_a_background_task_that_lost_its_first_cancel |
| M8 (own) | no `Task.cancelling()` guard before the sleep | KILLED | test_a_stop_that_lands_as_the_port_check_returns_still_stops_the_watch |
| M9 (own) | `wait_for` back in the port probe | KILLED | test_the_port_check_itself_never_drops_a_stop |
| M10 (own) | heartbeat counts `port_unknown` as outage | KILLED | test_outage_lasts_ten_minutes_after_the_newest_closed_port_exit |
| M11 (own) | F-2 forwards lines dated inside the notifier window | KILLED | test_a_restart_cap_line_at_1800_et_is_forwarded_once_and_never_from_the_archive |
| M12 (own) | a lead's first failed check pages at once (no confirmation) | KILLED | test_a_closure_spanning_the_lead_is_confirmed_before_it_pages |
| M13 (own) | T-4b pages a `port_closed` verdict too (double page) | KILLED | test_a_connection_refused_after_the_port_check_is_the_port_watchs |
| M14 (own) | OPS counts a held-back-then-announced open as a flap | KILLED | test_an_outage_counts_once_as_an_outage_never_also_as_a_flap_in_the_ops_counts[held-back-then-sustained-…] |
| M15 (own) | no evening page 16:15-18:30 (quiet from 18:15) | KILLED | test_the_leads_and_windows_are_derived…, test_inside_the_notifier_window_… |

## Item 5 — the shutdown fix (087e9f0)
- gh-86296 on this interpreter: `wait_for` returned the inner result to a cancelled task
  (`task.cancelling()=1`), scripts/repro_wait_for.py case 1.
- eededf9: watch probe released, cancel landed in the window, eededf9's own `TelegramAssistant.stop()`
  → **HANG** (8 s guard) for the probe case and for a library-level swallow (k=0,1,2).
- 134bde4: same script → `stop()` returned, watch cancelled, for every case; k=0 shows a cancel really
  swallowed inside the library (`cancelling()=1`) and caught by the loop guard before any sleep.
- The builder's six new stop tests run against eededf9 code: 6/6 fail; on 134bde4 pass. Composition-root
  pair (telegram + pre-session) 20/20 on 134bde4.

## Test counts
- Full backend suite (134bde4, sandboxed): **3052 passed, 2 skipped** in 230.8 s. Skips are environmental:
  tests/test_launchd.py:186 (no backend/.venv inside the clone), tests/test_manual_orders_migration.py:159
  (no live DB under the fake HOME). The builder reported 3053/1 (their worktree has a .venv).
- Targeted: test_overnight_watch 25, test_pre_session_check 16, test_log_rotation 17, test_heartbeat 34
  (92 passed); test_notify_service 76, test_supervisor 138, test_telegram_bot 176 collected and green in
  the full run.

## What I could not verify
- When the Gateway's IBKR link broke before 10-16 08:00 ET (needs ~/Jts logs; off-limits). Hence the
  three 10-16 scenarios.
- The real overnight port state (inferred from children's connect errors and the owner's login time; no
  overnight probe exists in the live logs). The live logs predate P1-A's restart cap: no
  CHILD_SUSPENDED/STORM/RESUMED or CHILD_EXIT_UNCOUNTED lines exist to forward, so F-2 and the heartbeat's
  `outage` state are verified by tests and mutations only, not by real lines.
- The real TWS answer to a 05:55 ET daily-history request (fake actor), and the Gateway's reaction to the
  5-minute bare TCP probe (never connected to 7497).
- The external monitor (not configured; Keychain not read) and the heartbeat's behaviour inside macOS
  dark wakes (inferred from pmset and live process activity during them).
- Order-safety scope (T-8 sidecar, Sentinel thresholds and writes, tws_actor listener, client-21 sharing)
  and Docker gates: not run here.
- Out of P1-B scope, noticed in the 10-16 replay: the P1-A notifier forwards three bridge CRITICALs per
  exit-75 restart during an announced outage (≈24 messages 09:25-09:40 ET).

## Artifacts (review-n15b, not committed)
scripts/repro_wait_for.py, scripts/repro_live_stop.py, scripts/replay_t4.py, scripts/replay_t4_rot.py,
scripts/test_review_replay_1016.py, scripts/test_review_deploy_tail.py, scripts/flaps_demo.py,
scripts/mutate.py; outputs replay-t4-{A,B,C}.txt, replay-t4-A-rot.txt, replay-1016-staged.txt,
deploy-tail.txt, mutations.txt, full-suite.log. reallogs/ and realstate/ hold copies of live data (real
account id inside): do not commit or share.
