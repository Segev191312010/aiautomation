# Weekend install pre-mortem: v7b + P1-A (+ P1-B), for the session of 2026-10-19

Written 2026-10-18, before the Sunday install. It extends `2026-10-17-v7b-premortem.md`: its twelve v7b scenarios still apply unchanged, and this page adds what P1-A and P1-B bring.

Question: it is Monday 23:15 Israel, and the first session on the new layers went badly. What happened, how did we notice, what limited the damage, and which layer do we undo?

| Layer | Head | Undo with `reset --keep` to |
|---|---|---|
| 1 v7b | 5d0eb1ca947a45bfa1a30ab836850df70a1bf12e | 664c93be7a48a4d1fb17f85667f0bed0f4f62e40 |
| 2 P1-A | 2608230632365c555926528ffc7f93c9d607f246 | 5d0eb1ca947a45bfa1a30ab836850df70a1bf12e |
| 3 P1-B | the ops-p1b tip named in the Sunday runbook | 2608230632365c555926528ffc7f93c9d607f246, then pause the Healthchecks check |

All three layers run the same bridge file (sha256 7d313fd075d2…). An undo of layer 2 or 3 never changes what the bridge sends; only an undo of layer 1 does (back to 9790eb3c1e81, MOMO only).

**No undo during a session.** Undo only when flat with no working robot orders, between 18:30 and 07:20 ET. During a session the brakes are `/halt <ID>` + `/confirm` (layer 2 on) or `paper stop --identity <ID>`.

## Failure scenarios added by P1-A (layer 2)

| # | What went wrong | How we notice | What limits it | Response | Undo? |
|---|---|---|---|---|---|
| A1 | The restart cap suspends a child that should run (the screener or a strategy loops on a real fault) | `CHILD_SUSPENDED <name> count=6 window=30m` + the storm page; that strategy sends no new entries | Scheduled children only: the cap never suspends the bridge or an always-on child (they page only); a probe every 5 min, one healthy 5-min run lifts it; a port-closed exit is not counted | Read `<name>.log` for the real fault. The suspension is the safe state | No; fix the fault |
| A2 | The cap misfires on Gateway trouble and suspends children while the port is down | `CHILD_EXIT_UNCOUNTED <name> reason=port_closed` lines instead of SUSPENDED | Port-closed and probe-failed exits are not counted; relaunch at once when the port opens (15 s re-check) | Fix the Gateway (scenario 5 of the v7b page) | No |
| A3 | The data-refused alert stays silent during a real outage | The screener's own timeouts; the pre-session check at 12:55/14:40 Israel (layer 3) | Nothing is sent without data; the stops rest at IBKR | 1) log out of the IBKR app and website; 2) restart IB Gateway | No |
| A4 | The data-refused alert clears too early and goes quiet mid-outage (the N14 MEDIUM-1 class) | Repeats stop while the screener still logs timeouts | Clears only after 3 successful real sweeps spanning ≥ 2 min (P1-A.4/P1-A.5; 0 false recoveries in 33 replay injections) | Treat the screener log as the truth | No |
| A5 | `/halt` does not fence | `/fences` does not list the identity; the bot does not reply "halted" | `paper stop --identity <ID>` from Terminal does the same thing | Use the Terminal brake | Only if both brakes fail, and only when flat |
| A6 | `/closeall` refused during a session | The bot replies with a reason (rehearsed: `market_closed` on Sunday) | Stops and targets rest at IBKR (GTC); the 15:45 ET flatten still runs | Close in TWS by hand; tell Claude | Layer 2, when flat |

## Failure scenarios added by P1-B (layer 3)

| # | What went wrong | How we notice | What limits it | Response | Undo? |
|---|---|---|---|---|---|
| B1 | Healthchecks pages, but the Mac is fine (false page) | Telegram from healthchecks.io; `pgrep -fl 'trading.operations.heartbeat'` shows the child; `heartbeat.log` shows `HEARTBEAT_PING_FAILED error=<kind>` | It only pages; it never touches orders | Check the internet connection. If the child is missing: `PREFLIGHT_WARNING heartbeat` or `HEARTBEAT_NOT_CONFIGURED` in the logs means the Keychain item is missing | No. Pause the check if it is noisy |
| B2 | Healthchecks stays silent while the Mac sleeps | No page at all | Only the external monitor can see a sleep; the battery page fires earlier, 07:40–16:15 ET on a session day (`/fail?reason=battery`) | Keep AC and the lid open, 24/7 from install on | No |
| B3 | The Sentinel sends 🔎 lines, and the owner reads them as real stops | `🔎 auto-fence (log-only): would stop <ID> — <trigger>` | Log-only: `SENTINEL_FENCES_LIVE = False`, no fence row is written (`/fences` stays empty); one line per identity per day | Nothing to do on Monday. Claude reviews each 🔎 line with the owner after 23:15 | No |
| B4 | The Sentinel would have fenced too often (thresholds too tight) | Many 🔎 lines | Log-only on Monday; the switch goes live Tuesday only with the owner's yes | Tune the thresholds (losing streak 4, capped loss ×1.25, MOMO 2 R) before P1-B.4 | No |
| B5 | T-4b pre-session check pages at 12:55 or 14:40 Israel | `⛔ IB Gateway is not returning prices (<time> ET check)` or `⛔ IBKR is logged in elsewhere` (10197) | One historical-bar request per check; it sends no orders | 1) log out of the IBKR app and website; 2) restart IB Gateway; then the owner's Gateway check | No |
| B6 | T-4b says "could not run" | `⚠️ Pre-session data check could not run (<time> ET check): <reason>` | Information only; the session still starts | Do the owner's Gateway check by hand before 14:55 | No |
| B7 | Log rotation fails at 18:35 ET and the logs grow, or the notifier loses its place | `LOG_ROTATION_FAILED` in `supervisor.log` | Runs between sessions only; the first start after install shows `LOG_ROTATED` | Tell Claude; no effect on orders | Layer 3, only if repeated |
| B8 | The overnight watch pages at night | A port-down page 16:15–17:30 ET; quiet from 17:30 ET; an outage that opened in quiet hours comes as one digest line at the 05:55 ET lead | Information only | Expected on weekends (the Gateway restarts at 02:30 Israel); act only if the port is still down at the 12:55 check | No |

## Install failures (Sunday)

| # | What went wrong | How we notice | Response |
|---|---|---|---|
| I1 | `shadow agent enable` was run from a worktree, so the LaunchAgent now points at it (HIGH-1) | `plutil -extract WorkingDirectory raw ~/Library/LaunchAgents/com.maccabi.autotrader-v1.shadow.plist` prints something other than `/Users/salomon/aiautomation/backend` | `"${CTL[@]}" shadow agent disable`, then enable again with the absolute `CTL` array from the live checkout; check plutil again |
| I2 | The ff fails, or `HEAD` is not the layer's literal sha | `git -C "$LIVE" rev-parse HEAD` ≠ the runbook's sha | Stop. Do not start. Tell Claude |
| I3 | `deploy check` refuses | Any `REFUSED` line, or a count other than `9 ok, 0 warn, 0 refused` | Stop that layer; the previous layer keeps running |
| I4 | A dry-run line differs from the runbook | Any `!!!` line, a missing line, or `fenced identities` ≠ `none` | Stop before start; undo that one layer |
| I5 | It is past 13:50 Israel before a layer starts | Clock | Do not start the layer. P1-B moves to Tuesday 01:30–13:50 |
| I6 | Healthchecks keeps paging after an undo of layer 3 | Pages every few minutes | Pause the `autotrader-mac` check on healthchecks.io (the undo step says so) |
| I7 | `deploy check` refuses `tree_clean` because of an untracked entry in the live checkout. Found Sun 08:30: `?? backend/Osintgram/` (a clone of a public tool made 10-16 22:56, not used by the robot; the rehearsal clone did not have it) | L1-2 shows more than ` M .claude/settings.local.json`; `REFUSE tree_clean` at L2-3 | Runbook step L0 moves it to `~/Osintgram-aiautomation-copy` (nothing deleted) before layer 1 |
| I8 | The layer-3 start rotates the logs at once (the 10-17 18:35 ET archive is missing), so the start's first lines land in `supervisor.log.2026-10-17` | `tail -n 3` ends at `LOG_ROTATED` (rehearsal L3-B); a `PREFLIGHT_WARNING heartbeat` line is in the archive, not the new file (L3-A) | Runbook L3-9 uses `tail -n 6` and greps both files; after the rotation `/status` shows `Restarts today: none` and `no run in the log tail` (expected, L3-D) |
| I9 | The dry run says the heartbeat Keychain item is `present`, but the start logs `PREFLIGHT_WARNING heartbeat not configured (no Keychain item …)` | L3-9 grep | The item exists but the start could not read it (e.g. the Keychain is locked for launchd): a read failure is reported as "not configured" (rehearsal L3-C, LOW). Heartbeat only; orders are unaffected. Tell Claude; Healthchecks will page because no pings arrive |

## Symptom → layer to undo (only when flat, 18:30–07:20 ET)

| Symptom | Undo |
|---|---|
| Wrong shares, a wrong identity sends, or an order without `autotrader:<ID>:` | Layer 1 (only it changes the bridge) |
| `/halt` or `/closeall` misbehaves, the restart cap suspends the wrong things, or the data alerts are wrong | Layer 2 (after layer 3, if installed) |
| Heartbeat, rotation, Sentinel, overnight or pre-session pages misbehave | Layer 3 |
| The robot does not start after a layer's install | That layer |

## Go / no-go before layer 3 (Sunday)

- [ ] Layers 1 and 2 PASS, with their DEPLOY lines and brake checks done.
- [ ] The ledger has `P1-B <head> verdict=GO`, with only docs/reviews after it, and `GATES Docker P1-B PASS`.
- [ ] The layer 3 rehearsal is PASS (dry run, `LOG_ROTATED`, B1–B5, undo to 2608230).
- [ ] The Keychain item exists (`security find-generic-password -a heartbeat-url -s com.maccabi.autotrader-v1.heartbeat`, exit 0).
- [ ] Healthchecks `autotrader-mac`: Period 1 min, Grace 5 min, Telegram on.
- [ ] The time is before 13:50 Israel; the Mac is on AC with the lid open.

## Watch during Monday's session (Claude, read-only), in addition to the v7b page

- `CHILD_SUSPENDED` / `CHILD_RESTART_STORM` lines; there should be none.
- Every 🔎 line, with its trigger and numbers (to review after 23:15).
- `heartbeat.log`: `HEARTBEAT_OK`, the state moving `idle→session` at the session start, no `PING_FAILED` runs.
- The 12:55 and 14:40 Israel checks are silent (silent = healthy).
- After 23:15: copy the logs, data and DB, then `session check --date 2026-10-19 --logs/--data/--db-path <copies>`, and write the SESSION line.
