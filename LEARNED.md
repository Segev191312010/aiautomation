# Learned Rules

## Repository truth beats planning assumptions
- Inspect imports and runtime entry points before deleting or preserving a file.
- A test is retained only when it protects surviving production behavior.

## Authority belongs in code
- Environment variables configure infrastructure; they do not grant PAPER or LIVE execution authority.
- New authority requires a reviewed code change and evidence for the exact source SHA.

## Rebuilds need one path
- Do not keep parallel legacy and rebuild entry points after the replacement becomes canonical.
- Delete compatibility exports instead of letting removed architecture leak back through imports.

## The trading day follows the session calendar
- A child's planned finish comes from the session's close (half days close at 13:00), never a fixed clock time.
- A finished session is never re-entered: run-once children (recorder, replay) usually end before their windows do.
- Same-user IBKR logins elsewhere (phone app, web) take the market data: keep them logged out during the session (error 10197).
- The IB Gateway's daily restart must fall outside the session and the 16:20-18:30 ET research windows.
- Opus 5.5 always thinks: control cost with `output_config` effort, never by disabling thinking or forcing `tool_choice`.

## Operations lessons (2026-10-13/14)
- 2026-10-13: an IBKR login on the phone app or the web takes the API market
  data (errors 162/165 "different IP address", 10197): real MOMO had no bars for
  an hour with no alert. It is now its own fault, `competing_login`, never
  pacing; the screener logs it within a minute and the notifier forwards it.
- 2026-10-13: a mid-session deploy needs the owner's own two commands,
  `autotraderctl db migrate --during-session-i-know` and
  `autotraderctl shadow agent enable`; the agents' auto-mode classifier blocks
  both, by design. Plan deploys after 18:30 ET.
- 2026-10-14: a read-only sqlite open (`sqlite3 -readonly`, a plain connection)
  creates `-wal`/`-shm` files beside a WAL database and fails right after a
  migration with `unable to open database file (14)`. Use
  `sqlite3 'file:...autotrader.db?immutable=1'` to read a live database.
- 2026-10-14: in the live checkout use `git reset --keep <sha>`, never
  `--hard`: `--keep` refuses to discard local modifications (the operator's
  local settings file), `--hard` wipes them silently.
- 2026-10-14: a worktree's processes default to the live database
  (`UserPaths.current()` is `~/.local/share/autotrader-v1`). Run every test and
  CLI under `sandbox-exec` with a profile that denies the live state, with
  `HOME` and `DB_PATH` pointed at temporary paths.
- 2026-10-14: usage limits cut agents off mid-task; commit per slice and resume
  from `git status`/`git log`, never from memory of what was "about to" land.

## Operations lessons (2026-10-15/16)
- 2026-10-15 MAC-SLEEP-1015: `caffeinate` prevents idle sleep, not lid-close
  sleep on battery. Keep the Mac on AC power with the lid open on every session day.
- 2026-10-16 GW-HMDS-1016: a connected IB Gateway can refuse all data for hours
  (TWS 2110, data farms broken) while its port is open: every screener
  qualification timed out from 08:00 ET and no sweep came until 10:00. The fix, in
  order: log out of the IBKR app and website (the orders came back), then restart
  IB Gateway (the price history came back). Set the nightly restart in the
  Gateway itself: Configure → Settings → Lock and Exit → Auto restart.
  Corrected 2026-10-17: verify an auto-restart by `~/Jts/launcher.log`'s first
  line (its date and time: the restart's) and its line "Running in auto-restart
  mode: restoring session token and session ID" (02:30:04 Israel on 2026-10-17,
  after the owner set Auto restart 02:30), never by "Daily auto-restart is
  enabled.": the same log still printed "Daily auto-restart is not enabled."
  after that real auto-restart, and jts.ini still had AutoRestart only under the
  settings-folder section (so "10.46 reads it from the `[u:<userDirname>]`
  section" was never shown).

## Read-only SQLite opens in the data directory
- `mode=ro` is safe on the live WAL database in its writable data directory: SQLite recreates a missing `-shm`. `immutable=1` returns a stale view of a WAL database with an un-checkpointed `-wal` ("no such table" on an existing table): use it only on a checkpointed copy.

## Test through the production path
- A value threaded through a constructor is only proven when a test drives the path production uses. The identity size factor passed every unit test of `ShadowPipeline` and four reviews, yet `RepositoryBackedShadowRunner.open_session` rebuilt the pipeline without it (found 2026-10-16, before v7b sent anything). The dropped keyword also bypassed the fail-closed missing-row block (`identity_sizing_missing`) on v7a and v7b; only the launch preflight `registry_problems` held the line. Copy objects with a method tied to the constructor's signature, and replay a recorded day through the runner. A copy test ties the copy to the signature only when its fixture sets every argument off its default: v7b.1's test did not, v7b.2's does for every argument.

## A stop must never be lost (Python 3.11 asyncio)
- On Python 3.11 `asyncio.wait_for` runs its awaitable in its own task; a cancel
  that lands just as that task finishes is dropped and the result returned
  (gh-86296, fixed in 3.12). A background loop built on it can survive its own
  shutdown: on 2026-10-17, in the composition-root test
  (`test_end_to_end_the_composition_root_runs_the_check_at_the_lead`; T-4 has
  not run live yet), the T-4 overnight watch kept running after the Telegram
  poll loop had stopped on a 401, so the telegram child neither polled nor
  exited. In code that is cancelled at shutdown use `asyncio.timeout` (it
  runs in the caller's task), check `Task.cancelling()` before sleeping, and
  have `stop()` re-cancel a task that is still running. A test that fails only
  "sometimes" by timing out is evidence of this kind of race, not noise.

## Test fixtures copy the real log format
- One screener sweep logs TWO `sweep D:` lines (the main `qualified=… quoted=…` line and, since 2026-10-14, a `day universe …` line about 10 ms later). P1-A.4's "3 successes" rule was correct against one-line fixtures and wrong in production, where it cleared after two real sweeps (review N14b, 2026-10-17). Build alert-counting fixtures from real log lines, and add a meta-test that the fixture has the production shape.

## Deploy tool lessons (P2, 2026-10-21/22)
- Always validate PREV_SHA, and re-read HEAD after `git reset --keep`. On 2026-10-16
  (v7b rehearsal) an empty `$PREV_SHA` made `git reset --keep` a silent no-op in zsh. PREV_SHA
  must be 40 hex, a commit, and an ancestor of the candidate before anything stops
  (`deploy apply`: `prev_sha_invalid`), and after the reset HEAD must print PREV_SHA (the undo
  stops with `undo_reset_noop`, exit 5; the manual 6.3 block reads `rev-parse HEAD`).
- `deploy apply` and `deploy undo` are TTY-only by design: with no terminal they refuse
  `not_interactive`, and nothing changes until the owner types back the short sha. The terminal
  is a speed bump, not a lock (review N17 I-1): agents never run them, also not through
  `script`, `expect`, a Python `pty` or `tmux send-keys`. An agent-run path would be a separate
  reviewed change that the owner decides.
- A stale `.git/index.lock` is refused before the stop (P2.3, `live_index_lock`; review
  N17c-L1). On P2.2 it passed every pre-check: the deploy stopped the robot, the fast-forward
  failed, and the undo would not restart the previous code on that tree (exit 5, the robot
  disabled). Check every precondition of a git write before the first step that changes the
  robot, and give the refusal its remedy. The tool waits 5 s for a concurrent reader's lock
  (N17c-L2); keep IDEs and agents' git out of the live checkout during a deploy.
- "git never moved" is said only before the first git write (P2.2; review N17b-L1). HEAD alone
  does not prove it: a `merge --ff-only` or `reset --keep` cut short can leave `index.lock` and
  part of the new tree under the old HEAD. The tool marks the write before it starts, holds
  signals until git returns (git runs in its own session), and says "git never moved" only when
  no write started, HEAD is PREV_SHA and the tree is clean with no `index.lock`; otherwise it
  prints runbook 6.3.
- A typed confirmation stands for the plan printed at that moment only (P2.1; review N17
  MEDIUM-1). The prompt waits for ever: in N17's repro an answer typed 12 hours late stopped the
  robot outside the window, and one typed after 07:55 ET on a session day would have left it
  stopped and disabled (the automatic undo could not restart it: a mid-session start writes no
  `SESSION_WAIT`). The answer now expires after 300 s, and the window, live HEAD and the upcoming
  session are read again when it comes.
- When `db migrate` printed no `backup` line, never restore "the newest" backup (P2.1; review
  N17 MEDIUM-2). Backups are named by time and version pair only, so an older attempt's file can
  match, and copied over the live data it loses every row since. Copy only the file this run
  printed; with none, `db status` decides whether the database changed at all.
- `deploy apply` and `deploy undo` act on the real LaunchAgent whatever `HOME` and `DB_PATH`
  say, so they refuse `paths_not_canonical` in a shell pointed at a copy (the 2026-10-14 rule
  for agents' tests; review N17 LOW-4). They run from the owner's own Terminal.
- Two tests failed only sometimes (review N17 LOW-7). One typed the short sha upper-cased, which
  is the right sha when its 7 characters are all digits (about 3.7 % of commits); one restored a
  file by a plain write, and git's whole-second index times judged it by whether the second had
  rolled over. A fixture must not depend on a sha's letters or on the wall-clock second.
