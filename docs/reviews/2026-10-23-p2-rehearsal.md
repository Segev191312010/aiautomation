# P2 rehearsal R1-R8: `autotraderctl deploy apply` / `deploy undo` (ops-p2 P2.1 56e7be2)

Status (2026-10-21 15:25 Israel): the harness is pointed at the fix head **P2.1
`56e7be2cbb75371c670c90f025176a913179df12`** (N17 findings, rehearsal F-1/F-2/F-3 and
observations fixed). Seed branches rebuilt on 56e7be2; scenario expectations adapted to
P2.1's intended behaviour; R8 added. Checked during the session with `zsh -n` and
pure-Python checks only (`harness/check_harness.py`, 34/34 ok, `out/check-harness-p2.1.txt`):
no supervisor, no scenario, no database read. The SMOKE and PRE-RUN results further down
were taken on the previous head 099618c (before P2.1) and are kept as history; the
official R1-R8 runs on 56e7be2 are next: `all` 01:40-13:15 Israel, `R5` 13:15-13:48 Israel.
This file is updated by `harness/report.py` after every run (tables) and by hand
(findings).

## What is rehearsed (plan)

- Tool under rehearsal: branch `ops-p2`, head **`56e7be2cbb75371c670c90f025176a913179df12`**
  (P2.1, on top of ae2b945 N17 docs and 099618c P2), `backend/trading/operations/deploy_apply.py`,
  imported unmodified from a worktree of the rehearsal clone
  (`<clone>/.claude/worktrees/ops-p2`, the live layout). The ops-p2 worktree in the real
  repository was only read.
- "Live checkout": a clone of `/Users/salomon/aiautomation` at
  `9558442b4e0d200f6244e1b7b7a62fb757ad5648` (schema v16, bridge pin `7d313fd075d2...`), on
  branch `rebuild/autotrader-v1`, origin removed. A fresh clone per scenario.
- Candidate: `ops-p2` (56e7be2) + one rehearsal-only docs/reviews commit appending to
  `docs/reviews/2026-10-15-ledger.md`:
  `2026-10-21T16:00:00+03:00 | P2.1 56e7be2cbb75 verdict=GO scope=P2,N17,N17b bridge_sha=7d313fd0...(64) | REHEARSAL ONLY ... | ...`
  (label `P2.1`; `deploy check` matches it through the I-11 docs-only rule). Every run
  requests `--scope P2,N17,N17b`, `--expect-parent 9558442...`, `--expect-pin 7d313fd0...`.
- Rehearsal-only candidate branches (seed `seed/repo`, shas in `seed/shas.json`; the 099618c
  seed is kept in `attic/seed-repo-099618c`):

| Branch | Sha | Content | Used by |
|---|---|---|---|
| `r-cand` | f726f6a | 56e7be2 + the verdict commit above | R1, R4, R5a, R5b, R6, R7, R8 |
| `r-bang` | cf149a7 (code 4ebc2d3) | + a `supervisor.py` line: the dry run prints `!!! REHEARSAL-ONLY injected dry-run failure ...`; verdict | R2 |
| `r-mig17` | e2acc82 (code 47c4c11) | + synthetic migration 17 (`rehearsal_p2_marker`) + the same `!!!` line; verdict `migration=17` | R3 |
| `r-mig17ok` | 638a8f4 (code b115aa7) | migration 17 only, clean dry run; verdict `migration=17` | R5c |
| `r-live-diverged` | 24eb8eb | 9558442 + a docs commit (a live HEAD not in the candidate's history) | R4, R8 |

- What P2.1 changes in the expectations (all asserted by the driver):
  - R1: the plan prints `home / logs / data` and steps numbered 1..6; the start check ends
    only after `OK settled: telegram and heartbeat ran 20 s after their CHILD_START, no exit`.
  - R2: the undo's dry run also requires `INFO fenced identities = none` and the heartbeat
    `present` line; the undo's start also settles 20 s.
  - R3: the restore's step 1 is `pgrep -fl <live checkout>/backend/ || echo none` (anchored),
    run verbatim by hand with the rest of the block.
  - R4: + `changed_paths_unreadable` (not `deploy_tool_scope_missing`) for an unborn HEAD;
    + R4.7 `paths_not_canonical` with the tool's own `account_home` (pwd) against the fake
    HOME; + R4.8 `paths_not_canonical` for a database path other than `<data>/autotrader.db`;
    + R4.9 `candidate_worktree_dirty` (an untracked `backend/` file in the candidate
    worktree); + the CLI refuses `--db-path` (exit 2).
  - R5a: no reset; `OK HEAD is still <PREV>: git never moved, nothing to reset`;
    `DEPLOY P2.1 <short> PREV_SHA=<PREV> pin=7d313fd075d2 result=ABORTED reason=start_cutoff`;
    summary `ABORTED ... (exit 3; git never moved; 9558442 runs again)`.
  - R5c: the halt prints `OPTION 1: start the new code` before `OPTION 2: go back to PREV`
    and the RESTORE; the harness then runs OPTION 1 by hand (enable, a look at 3 s and at
    24 s, and only then its `echo ... result=PASS schema=v17`), and checks the line parses.
  - R7: the candidate's telegram child exits 78 three seconds after its CHILD_START (the
    stand-in fails only when the checkout's HEAD is the candidate; at PREV it parks) ->
    `FAIL post_start_child_exited`, automatic undo, `DEPLOY rollback to 9558442...
    result=ROLLBACK reason=post_start_child_exited`, exit 3, no PASS line, no ledger line;
    the previous code then stays up 20 s with no CHILD_EXIT.
  - R8 (new): the sha typed late. The pty is wrapped so that something happens while the
    owner types: R8.1 the fake clock moves `CONFIRM_TIMEOUT_S + 1` = 301 s (`confirm_timeout`);
    R8.2 live HEAD moves to `r-live-diverged` (`live_head_moved`); R8.3 the clock moves to the
    session day's 18:45 ET (`session_date_changed`). Each: exit 78, deploys.log unchanged, no
    live-checkout call, no launchctl request, the same supervisor still running.
- The fake HOME is injected as the account home through `DeployEnvironment.account_home`
  (the `paths_not_canonical` check itself is not touched; R4.7 runs it with the real one).
- Database: one copy of the live DB made 2026-10-21 13:39:38 Israel with the SQLite backup
  API from a `?mode=ro` connection (`seed/autotrader.db`, sha256 `53f05a34fd25...`,
  `quick_check ok`, kept read-only); each scenario gets an APFS clone of it.

## Isolation

- Every process runs under `sandbox-exec -f sandbox-rehearsal-p2-run.sb` = the given
  `~/autotrader-work/sandbox-rehearsal-p2.sb` plus one rule (as the weekend's
  `sandbox-supervisor.sb`): `(deny signal) (allow signal (target same-sandbox))`, verified:
  a kill of a process outside the sandbox fails `operation not permitted`.
- `env -i`, `HOME=$R/fakehome/<scenario>`, `PYTHONDONTWRITEBYTECODE=1`, `TMPDIR=$R/tmp/`,
  `AUTOTRADER_REHEARSAL=1`.
- Python: `$R/venv`, an APFS clone (`cp -cR`) of the live venv; `sys.prefix` verified to be
  the clone before anything was added; its bin/ scripts and pyvenv.cfg repointed to the clone.
  Each clone's `backend/.venv` is a directory (gitignored) with `pyvenv.cfg`, `bin/python*`
  links to the same base interpreter, and `lib`/`include` links to `$R/venv`
  (`sys.prefix` = the clone's `.venv`, checked at every setup). The live venv was never
  modified (no sitecustomize there).
- `$R/venv/.../site-packages/sitecustomize.py` does nothing without `AUTOTRADER_REHEARSAL=1`;
  with it, it refuses to start any process whose HOME is outside `$R/fakehome/` (exit 70,
  tested) and loads `harness/rehearsal_hooks.py`, which patches modules in memory after
  import (no tracked file is edited; every clone's tree stays clean):
  - Keychain: `/usr/bin/security` is never run; the heartbeat item exists and reads as the
    loopback URL of the scenario's fake monitor; no Telegram token, no Anthropic key.
  - launchctl: `LaunchdManager._command` goes to `harness/fakelaunchctl.py` through the
    manager's own runner (launchd.py's 10 s timeout still applies).
  - Gateway probe: `supervisor.gateway_port_open` / `telegram_tools.gateway_port_open` are
    recorders that answer closed without a socket (0 calls observed so far).
  - Telegram child: the body `run_telegram_assistant` is a stand-in parked until SIGTERM
    (without a token the real one exits 78 and restarts with backoff, so the start check
    could not see a running telegram child); `_telegram_command`'s schema check and SIGTERM
    handling stay real.
  - `/bin/ps` is setuid and sandbox-exec refuses to exec it, so `supervisor.process_table`
    (and `unsupervised_children`'s default) read the same rows from libproc/sysctl
    (`harness/proctable.py`; 629 of 631 same-user rows identical to ps in a check).
  - Tripwires, logged to `$HOME/.rehearsal/tripwire.log` and refused: a connect to a
    non-loopback address or to 7496/7497/4000-4002, a non-loopback name lookup, an exec of
    launchctl/security/sntp, a session child (screener, notifier, strategies, manager,
    recorder, replay), the bridge without `--preflight`. Tested on 192.0.2.1, example.com and
    argv shapes only; no IBKR port was ever touched.
- Fake launchd (`harness/fake_launchd.py`): a daemon per scenario on an AF_UNIX socket in the
  fake HOME; bootstrap runs the plist's real ProgramArguments
  (`/usr/bin/caffeinate -i -s <clone>/backend/.venv/bin/python <clone>/backend/autotraderctl.py run`)
  in its WorkingDirectory, new session, plist env + HOME; bootout = SIGTERM, ExitTimeOut 45 s,
  SIGKILL; KeepAlive with ThrottleInterval; bootstrap of a disabled label fails. The plist
  is written by the clone's own `shadow agent install|enable` into the fake HOME.
- The operator's injected environment: `process_table` = the scenario's processes and their
  descendants (the live robot runs on the same Mac and must not count), `sntp` = a fixed
  `+0.000500 +/- 0.010000` reading (no network), `stdin` = a real pty with the short sha
  typed, `live_runner` = the tool's own `subprocess_runner` (recorded), `launchctl` = the
  fake, `clock` = the real wall clock (below). `own_root` and `writer_factory` are the
  tool's defaults.

## Why the real clock

The supervisor writes its log lines with the real time, and the operator filters
`SUPERVISOR_STOP` / `SUPERVISOR_START` lines by `since = its own clock`. A clock shifted into
the window either hides the new lines (ahead) or accepts the previous supervisor's lines
(behind). So the injected clock is the real one, and the official runs happen when the real
ET time is inside 18:40 -> 06:50 (Israel 01:40-13:50 until 24 Oct). R5 lets the real clock
pass 06:50:01 ET between two steps (the driver holds at `_require_window(step)`, as a slow
deploy would), so it starts between 06:15 and 06:48 ET. `db migrate` (R3) also needs the
real time outside 07:55-18:30 ET.

## How to run (zsh; one line at a time)

```
zsh /Users/salomon/autotrader-work/rehearsal-p2/run_rehearsal.sh all
zsh /Users/salomon/autotrader-work/rehearsal-p2/run_rehearsal.sh R5
```

`all` = R1 R2 R3 R4 R6 R7 R8: start between 18:40 and 06:15 ET (01:40-13:15 Israel until 24 Oct),
about 7 minutes (P2.1's 20 s settle in every start, R6 waits the full 75 s stop timeout,
R7 looks 20 s more). `R5` = R5a R5b R5c in parallel: start between 06:15 and 06:48 ET
(13:15-13:48 Israel until 24 Oct); the drivers hold until 06:50:01 ET. The script refuses
14:50-23:15 Israel. Outputs: `out/<scenario>.txt` verbatim, `out/results.jsonl`,
`out/livedb.txt` (live DB size/mtime before and after, metadata only). `PRE [scenarios]`
(outside the window, before 07:35 ET and 14:35 Israel) is the harness shake-out: it shifts
only the two start-window predicates (`deploy_apply.start_window_problem`,
`deploy_check.in_deploy_window`) and writes `out/pre/`, `out/results-pre.jsonl`.

## Results (official runs)

<!-- results:start -->
| Scenario | Expected | Observed | Result |
|---|---|---|---|
| R1 | PREV line, stop, ff, dry run (every required line), start checks with the 20 s settle, PASS line; 10-18 ledger form; deploys.log parses back; plan steps 1..n | 32/32 checks passed (2026-10-22T02:22:38+03:00 -> 2026-10-22T02:23:08+03:00); out/R1.txt | PASS |
| R2 | dry run `!!!` -> automatic undo: reset --keep PREV, HEAD re-read, old pin, restart, ROLLBACK reason=dry_run_bang, exit 3 | 24/24 checks passed (2026-10-22T02:23:08+03:00 -> 2026-10-22T02:23:37+03:00); out/R2.txt | PASS |
| R3 | --expect-migration 17 + post-migration failure -> HALTED reason=dry_run_bang backup=<path>, robot disabled, restore printed; restore run by hand -> old pin, schema v16 current | 35/35 checks passed (2026-10-22T02:23:37+03:00 -> 2026-10-22T02:23:49+03:00); out/R3.txt | PASS |
| R4 | empty / short / non-ancestor PREV_SHA, paths_not_canonical (HOME, DB), candidate_worktree_dirty, CLI --db-path -> refused before any stop, nothing written (exit 78; CLI argument errors exit 2) | 67/67 checks passed (2026-10-22T02:23:49+03:00 -> 2026-10-22T02:24:00+03:00); out/R4.txt | PASS |
| R5a | 06:50 ET passes before the ff -> start_cutoff -> no reset, the unchanged code restarts, result=ABORTED, exit 3 | 18/18 checks passed (2026-10-23T13:16:32+03:00 -> 2026-10-23T13:50:25+03:00); out/R5a.txt | PASS |
| R5b | 06:50 ET passes before the start -> start_cutoff -> undo, ROLLBACK, exit 3 | 14/14 checks passed (2026-10-23T13:16:32+03:00 -> 2026-10-23T13:50:25+03:00); out/R5b.txt | PASS |
| R5c | migration deploy, 06:50 ET passes before the start -> start_cutoff -> HALT, exit 4, OPTION 1 (start the new code) before OPTION 2 (restore); OPTION 1 run by hand | 18/18 checks passed (2026-10-23T13:16:32+03:00 -> 2026-10-23T13:50:27+03:00); out/R5c.txt | PASS |
| R6 | supervisor ignores SIGTERM -> stop_wait_timeout, exit 4, git untouched, re-enable command printed | 20/20 checks passed (2026-10-22T02:24:00+03:00 -> 2026-10-22T02:25:35+03:00); out/R6.txt | PASS |
| R7 | the candidate's telegram child exits 78 3 s after its CHILD_START -> post_start_child_exited, automatic undo, ROLLBACK, no PASS line | 16/16 checks passed (2026-10-22T02:25:35+03:00 -> 2026-10-22T02:26:30+03:00); out/R7.txt | PASS |
| R8 | the sha typed after CONFIRM_TIMEOUT_S / after live HEAD moved / after the session changed -> confirm_timeout / live_head_moved / session_date_changed, exit 78, nothing written | 25/25 checks passed (2026-10-22T02:26:30+03:00 -> 2026-10-22T02:26:38+03:00); out/R8.txt | PASS |
<!-- results:end -->

## Harness checks run so far (not official results)

- P2.1 (2026-10-21 15:21 Israel, session time: pure Python only): `harness/check_harness.py`
  against a detached checkout of `r-cand` (56e7be2): 34/34 ok (`out/check-harness-p2.1.txt`):
  the new tokens exist; `CONFIRM_TIMEOUT_S` 300 and `POST_START_SETTLE_S` 20; every seed
  verdict matches with label `P2.1`, scope `P2,N17,N17b`, a clean three-way pin; the fake
  home passes `_path_problems` when injected as the account home and the real account home
  refuses it twice; the three R8 hooks produce `confirm_timeout`, `live_head_moved`,
  `session_date_changed` from `_confirmation_problems`; P2.1's printed RESTORE (anchored
  pgrep first, 10 commands) and OPTION 1 blocks parse as the driver runs them; the P2.1
  PREV/PASS/ABORTED lines parse with the tool's own regexes; the R7 stand-in exits 78 only
  at the flagged HEAD; every scenario has its function and its report row.

The two items below ran on the previous head 099618c:

- SMOKE, 2026-10-21 14:08 Israel (07:08 ET, outside the window): PASS 12/12 (`out/SMOKE.txt`).
  The clone's real supervisor started through the fake launchd
  (`SUPERVISOR_START pid=26778 mode=daily`, `CHILD_START telegram`, `CHILD_START heartbeat`,
  `SESSION_WAIT date=2026-10-21`, `LOG_ROTATED date=2026-10-20 ...`), its children descend
  from it, one heartbeat ping reached the fake monitor, the clone's dry run passed the
  candidate's `DryRunCheck` with no failure (heartbeat `present`, `fenced identities = none`,
  `schema v16 current`), the operator refused `start_cutoff` (exit 78, no live call,
  deploys.log unchanged), and `shadow agent disable` produced
  `SUPERVISOR_STOP reason=SIGTERM` with the lock free and no process left.

<!-- pre:start -->
PRE-RUN on the previous head 099618c (harness shake-out, outside the window: the two start-window predicates were evaluated at real time + a shift; every other clock reading, the supervisor and its logs were real). Not official results.

| Scenario | Checks | Result | Failed checks |
|---|---|---|---|
| R1 | 29/29 (2026-10-21T14:11:19+03:00) | PASS | - |
| R2 | 21/21 (2026-10-21T14:11:30+03:00) | PASS | - |
| R3 | 34/34 (2026-10-21T14:11:41+03:00) | PASS | - |
| R4 | 43/43 (2026-10-21T14:11:53+03:00) | PASS | - |
| R5a | 14/14 (2026-10-21T14:13:37+03:00) | PASS | - |
| R5b | 13/13 (2026-10-21T14:13:37+03:00) | PASS | - |
| R5c | 14/14 (2026-10-21T14:13:37+03:00) | PASS | - |
| R6 | 20/20 (2026-10-21T14:16:22+03:00) | PASS | - |
| R7 | 5/6 (2026-10-21T14:17:57+03:00) | FAIL | deploy apply does not report PASS when telegram dies 3 s after the start (L3-9 parity: 20 s look) |

Verbatim: `out/pre/<scenario>.txt`.
<!-- pre:end -->

## Findings

Tool defects are findings only: the ops-p2 worktree was never edited. F-1, F-2, F-3 and
observations O-b, O-c, O-e were found on 099618c; P2.1 56e7be2 states fixes for all of them
(commit message). The official runs check the fixes: F-1 by R7 (`post_start_child_exited`)
and the settle line in R1/R2, F-2 by R4.1 (`changed_paths_unreadable`), F-3 by R1 (steps
1..n), O-b by R2 (fenced and heartbeat lines in the undo's dry run), O-c by R5c (OPTION 1),
O-e by R3 (anchored pgrep). O-a now reads: R5a writes `result=ABORTED` and does no reset.

### F-1 (MEDIUM, tool): the start checks pass about 2 s after the enable; a child that dies seconds later is not seen

- Where: `backend/trading/operations/deploy_apply.py:1670-1705` (`wait_started`): it breaks out
  on the second consecutive 1 s poll (`settled`, :1676-1693) in which every start line is
  present and each child pid is alive. There is no minimum observation time after the
  children's `CHILD_START`. The manual L3-9 block it replaces looked 20 s after the enable
  (`sleep 20; tail -n 6 $L/supervisor.log`), and P2 acceptance item 4 asks for post-checks
  that "match today's manual L3-9 block line for line".
- Observed (PRE R7, `out/pre/R7.txt`): `OK started: ...` and `deploy apply: PASS P2 1473492
  (exit 0)` at 11:18:05Z; the new telegram child logged `CHILD_EXIT telegram code=78
  restart_in=15` at 11:18:06Z, then `CHILD_START` / `CHILD_EXIT ... code=78 restart_in=30`.
  The PASS line and the ledger line `... post-checks ok` were written anyway.
- Why it matters live: the real telegram child exits 78 about 1-2 s after its start when the
  token, the pinned owner or the manual-order ledger is unusable (telegram_bot.py
  `run_telegram_assistant`), and the heartbeat child exits 78 when the Keychain read fails
  under launchd (rehearsal L3-C). Whether the 1 s poll catches such a child is a race; with a
  3 s life it never does.
- Minimal repro: in the rehearsal, `echo 3 > $HOME/.rehearsal/telegram_exit_after` before
  the deploy (`run_rehearsal.sh PRE R7` or `all`). In the unit fixtures:
  `StartShape` start, then `clock.after(3, lambda: robot.write("CHILD_EXIT telegram code=78
  restart_in=15"))` and drop the telegram row from `robot.children`; `wait_started` returns
  before the exit is written.
- Suggested direction (for the author): keep polling for a settle window after the last
  `CHILD_START` (for example 20 s, as L3-9) and fail `post_start_telegram_missing` /
  `post_start_heartbeat_missing` on any `CHILD_EXIT` of an always-on child since `since`.

### F-2 (LOW, tool): a git failure listing the changed paths is reported as `deploy_tool_scope_missing`

- Where: `deploy_apply.py:1198-1202`: `except GitError` appends
  `(DEPLOY_TOOL_SCOPE_MISSING, "cannot list the changed paths: ...")`.
- Observed (PRE R4.1, unborn live HEAD): `REFUSE deploy_tool_scope_missing: cannot list the
  changed paths: git diff failed: ...` next to `prev_sha_invalid`. Fails safe (exit 78), but
  the token names the wrong cause; the owner would look for a scope problem.

### F-3 (LOW, cosmetic): the plan of a code-only deploy numbers its steps 1, 2, 3, 5, 6, 7

- Where: `deploy_apply.py:1307-1320` (step 4 is printed only with `--expect-migration`).
  Seen in every PRE plan (`out/pre/R1.txt`).

### Observations (as designed, for the reviewer and the runbook)

- O-a. The automatic undo has no window check (`undo_to`, `deploy_apply.py:1776-1845`): after
  `start_cutoff` (R5a/R5b) the previous code is restarted at 06:50 ET, before the 07:55 first
  child. R5a ("before the fast-forward") still runs the full stop / no-op `reset --keep` /
  dry run / start cycle and writes `DEPLOY rollback to 9558442... result=ROLLBACK
  reason=start_cutoff` although git never moved.
- O-b. The undo's dry run checks four lines only (`reviewed (sha256 <old pin>)`, `schema vN
  current`, `session`, `SESSION_WAIT`; `deploy_apply.py:1806-1816`, `fenced=None`,
  `heartbeat=False`). An identity fenced in the database, or a missing heartbeat item, does
  not stop the rollback's restart (weekend finding F-3: a fence prints `CRITICAL`, not `!!!`).
- O-c. R5c: a migration deploy whose dry run passed but that meets 06:50 ET before the start
  HALTs with the robot disabled and prints the full database restore; the smaller remedy
  (the migrated candidate is consistent; only the start is late) is not mentioned. As D4.
- O-d. `deploy undo --to ''` / `--to 9558442` and `deploy apply --expect-parent ''` /
  `--expect-parent 9558442` are refused by the CLI's argument checks with exit 2 (argparse)
  before the operator runs (R4.6), not 78. Nothing is written either way.
- O-e. The restore's step 1 `pgrep -fl autotraderctl.py || echo none` also lists any
  `autotraderctl.py` process of another checkout or worktree (and the caffeinate helper of a
  running supervisor, whose argv contains `autotraderctl.py run`).
- O-f. With `/bin/ps` unavailable (it is setuid; sandbox-exec refuses it) the operator's stop
  wait reads an empty table as "ps failed" and HALTs `stop_wait_timeout` (first PRE R1,
  `attic/`): fail-safe. A `deploy apply` started from a sandboxed shell would always halt.
- O-g. `deploys.log` lines: PREV `<iso> P2 PREV_SHA=<40> schema=v16`, PASS `<iso> DEPLOY P2
  1473492 PREV_SHA=<40> pin=7d313fd075d2 result=PASS schema=v16` (the 10-18 form plus
  ` schema=v16`), both read back by `_PREV_LINE`, `_DEPLOY_PASS`, `_deploy_line_for`,
  `_prev_schema` and by `deploy undo`'s own pre-checks; the five 10-16/10-18 DEPLOY lines
  still parse. The printed ledger line matches the 10-18 ledger form exactly
  (`... | owner-run via autotraderctl deploy apply (1473492); 9 dry-run lines ok; post-checks ok | ~/Library/Logs/AutotraderV1/deploys.log`).

### Harness limits (what the rehearsal does not show)

- Real launchd and launchctl are not used (C12 stands): the fake models print, enable,
  disable, bootstrap, bootout (SIGTERM, 45 s, SIGKILL) and KeepAlive only.
- The telegram child is a stand-in; the heartbeat child is real (pings reached the fake
  monitor). sntp is not run (a fixed +0.0005 s reading is injected).
- The operator's process table is narrowed to the scenario's processes plus launchd and
  itself (the live robot shares the Mac); the supervisor's own unsupervised-process scan sees
  the whole Mac through `proctable.py`.

## Per-scenario checks

<!-- checks:start -->

#### R1: pass: PREV, stop, ff, dry run, start, PASS line (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| exit code | 0 | 0 | ok |
| deploys.log keeps the 12 earlier lines byte for byte | unchanged prefix | True | ok |
| deploys.log new lines | 2 | 2 | ok |
| deploys.log mode | 0o600 | 0o600 | ok |
| the tool's _DEPLOY_PASS reads every 10-16/10-18 DEPLOY line | 5/5 | 5/5 | ok |
| PREV line form `<iso> P2.3 PREV_SHA=<40> schema=v16` | parsed by _PREV_LINE | 2026-10-22T02:22:43+03:00 P2.3 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 schema=v16 | ok |
| DEPLOY line parsed by _DEPLOY_PASS | short bed4d57 prev 9558442b4e0d pin 7d313fd075d2 schema 16 | 2026-10-22T02:23:05+03:00 DEPLOY P2.3 bed4d57 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=PASS schema=v16 | ok |
| DEPLOY line has the 10-18 deploys.log form (+ ` schema=v16`) | match, suffix ` schema=v16` |  schema=v16 | ok |
| _deploy_line_for(HEAD) and _prev_schema read the pair back | the new DEPLOY line, PREV schema 16 | DEPLOY P2.3 bed4d57 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=PASS schema=v16; prev schema 16 | ok |
| printed ledger line has the 2026-10-18 ledger form | `<iso> \| DEPLOY <label> <short> PREV_SHA=<40> pin=<12> result=PASS \| <notes> \| <where>` | 2026-10-22T02:23:05+03:00 \| DEPLOY P2.3 bed4d57 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=PASS \| owner-run via autotraderctl deploy apply (bed4d57); 9 dry-run lines ok; post-checks ok \| ~/Library/Logs/AutotraderV1/deploys.log | ok |
| the same pattern matches a 10-18 ledger DEPLOY line (shape) | match | True | ok |
| steps in order: PREV, stop, window, ff, dry run, window, start, 20 s settle, PASS | all present in order | [3899, 4007, 4203, 4303, 4337, 4463, 4572, 5475, 5525, 6023, 6100, 6331, 6564] | ok |
| plan steps numbered consecutively (F-3) | 1..n | [1, 2, 3, 4, 5, 6] | ok |
| plan names the home (the account's), logs, data | home ... (the account's: <fake home>) | home line present: True | ok |
| the start check waited the 20 s settle window | OK settled line | ['OK settled: telegram and heartbeat ran 20 s after their CHILD_START, no exit'] | ok |
| dry-run evidence printed: bridge       reviewed (sha256 7d313fd075d2) | present | True | ok |
| dry-run evidence printed: bridge       INFO bridge sha256=7d313fd075d29e1e62c11eab52fd | present | True | ok |
| dry-run evidence printed: bridge       INFO fenced identities = none | present | True | ok |
| dry-run evidence printed: bridge       INFO sendable identities = GAP_GO_V2, MOMO_HOD_ | present | True | ok |
| dry-run evidence printed: registry     sendable identities, size table and strategy re | present | True | ok |
| dry-run evidence printed: database     schema v16 current (/Users/salomon/autotrader-w | present | True | ok |
| dry-run evidence printed: heartbeat    Keychain item com.maccabi.autotrader-v1.heartbe | present | True | ok |
| dry-run evidence printed: SESSION_WAIT date= | present | True | ok |
| live clone HEAD after | bed4d57d5f3f088a5f1649ce5174351e344ba09c | bed4d57d5f3f088a5f1649ce5174351e344ba09c | ok |
| live clone tree | clean | clean | ok |
| one supervisor running, the clone's, a new pid | 1, not 35111 | [(35531, '$R/scen/R1/live/backend/.venv/bin/python $R/scen/R1/live/backend/autotraderctl.py run')] | ok |
| plist WorkingDirectory | /Users/salomon/autotrader-work/rehearsal-p2/scen/R1/live/backend | /Users/salomon/autotrader-work/rehearsal-p2/scen/R1/live/backend | ok |
| deploy undo's pre-checks read the new lines back (read-only) | no refusal, old pin 7d313fd075d2 | refusals=[] old_pin=7d313fd075d2 head=bed4d57d5f3f | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 3/3 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |

#### R2: dry run with !!! -> automatic undo, ROLLBACK, exit 3 (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| exit code | 3 | 3 | ok |
| deploys.log keeps the 12 earlier lines byte for byte | unchanged prefix | True | ok |
| deploys.log new lines | 2 | 2 | ok |
| deploys.log mode | 0o600 | 0o600 | ok |
| the tool's _DEPLOY_PASS reads every 10-16/10-18 DEPLOY line | 5/5 | 5/5 | ok |
| rollback line | <iso> DEPLOY rollback to 9558442b4e0d200f6244e1b7b7a62fb757ad5648 result=ROLLBACK reason=dry_run_bang | 2026-10-22T02:23:36+03:00 DEPLOY rollback to 9558442b4e0d200f6244e1b7b7a62fb757ad5648 result=ROLLBACK reason=dry_run_bang | ok |
| printed: FAIL dry_run_bang: 1 line(s) with !!!: !!! REHEARSAL-ONLY injected dry | present | True | ok |
| printed: UNDO automatic (code-only deploy): back to 9558442b4e0d200f6244e1b7b7a | present | True | ok |
| printed: STEP git -C /Users/salomon/autotrader-work/rehearsal-p2/scen/R2/live r | present | True | ok |
| printed: OK HEAD 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | present | True | ok |
| printed:   \| bridge       reviewed (sha256 7d313fd075d2) | present | True | ok |
| printed: OK rollback to 9558442b4e0d200f6244e1b7b7a62fb757ad5648: reason=dry_ru | present | True | ok |
| printed: OK settled: telegram and heartbeat ran 20 s after their CHILD_START, n | present | True | ok |
| printed: deploy apply: ROLLED BACK reason=dry_run_bang (exit 3; 9558442 runs ag | present | True | ok |
| the undo's dry run also checked: \| bridge       INFO fenced identities = none (P2.1) | present after UNDO | True | ok |
| the undo's dry run also checked: \| heartbeat    Keychain item com.maccabi.autotrade (P2.1) | present after UNDO | True | ok |
| order: ff, failed dry run, reset --keep, HEAD re-read, old-pin dry run, enable | ascending | [4321, 6082, 6136, 6220, 7077] | ok |
| live clone HEAD after | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| live clone tree | clean | clean | ok |
| the previous code runs again (one supervisor, new pid) | 1, not 37387 | [(37760, '$R/scen/R2/live/backend/.venv/bin/python $R/scen/R2/live/backend/autotraderctl.py run')] | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 5/5 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |

#### R3: --expect-migration 17 + post-migration failure -> HALT; restore run by hand (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| copy schema before | v16 current, pending (17,) for the candidate code | applied v16 code v17 pending (17,) | ok |
| exit code | 4 | 4 | ok |
| deploys.log keeps the 12 earlier lines byte for byte | unchanged prefix | True | ok |
| deploys.log new lines | 2 | 2 | ok |
| deploys.log mode | 0o600 | 0o600 | ok |
| the tool's _DEPLOY_PASS reads every 10-16/10-18 DEPLOY line | 5/5 | 5/5 | ok |
| HALTED line | DEPLOY P2.3 4c31701 PREV_SHA=9558442b4e0d.. pin=7d313fd075d2 result=HALTED reason=dry_run_bang backup=<path> | 2026-10-22T02:23:45+03:00 DEPLOY P2.3 4c31701 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=HALTED reason=dry_run_bang backup=/Users/salomon/autotrader-work/rehearsal-p2/fakehome/R3/.local/share/autotrader-v1/backups/autotrader.20261021T232343.545565Z.v16-to-v17.db | ok |
| PREV line carries schema=v16 | schema=v16 | 2026-10-22T02:23:42+03:00 P2.3 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 schema=v16 | ok |
| printed: STEP $R/scen/R3/live/backend/ | present | True | ok |
| printed:   \| migrated  v16 -> v17 | present | True | ok |
| printed: OK migrated v16 -> v17; backup  | present | True | ok |
| printed: FAIL dry_run_bang: | present | True | ok |
| printed: The database was migrated (or may have been): HALT, never an automatic | present | True | ok |
| printed: RESTORE (never run by this command) | present | True | ok |
| printed: git -C $R/scen/R3/live reset  | present | True | ok |
| printed:   1. pgrep -fl $R/scen/R3/liv | present | True | ok |
| printed: deploy apply: HALTED reason=dry_run_bang (exit 4; the robot is disable | present | True | ok |
| no automatic reset after the migration | no `reset --keep` before the printed RESTORE | False | ok |
| HEAD left at the candidate (never reset by the tool) | 4c31701eee53785ef0b45e1c71efb618c759d39c | 4c31701eee53785ef0b45e1c71efb618c759d39c | ok |
| robot left disabled: no supervisor | 0 supervisors | 0 | ok |
| LaunchAgent disabled and unloaded | enabled False, loaded False | {'enabled': False, 'loaded': False, 'pid': None, 'plist': None, 'spawns': 0} | ok |
| database migrated by the live checkout's db migrate | v17 current | v17 current=True | ok |
| backup is the v16 database | v16, in <data>/backups/ | $R/fakehome/R3/.local/share/autotrader-v1/backups/autotrader.20261021T232343.545565Z.v16-to-v17.db v16 | ok |
| restore script ran | exit 0 | 0 | ok |
| after the restore: HEAD | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| after the restore: database | schema v16 (current for 9558442 code) | v16 | ok |
| restore dry run shows: bridge       reviewed (sha256 7d313fd075d2) | present | True | ok |
| restore dry run shows: database     schema v16 current (/Users/salomon/autotrader-w | present | True | ok |
| restore dry run has no !!! | none | False | ok |
| the failed v17 files were moved aside, not deleted | >= 1 file | ['autotrader.db-shm.v17-failed-20261021T232345Z', 'autotrader.db-wal.v17-failed-20261021T232345Z', 'autotrader.db.v17-failed-20261021T232345Z'] | ok |
| after the printed enable: the previous code's supervisor runs | 1 supervisor | [(40186, '$R/scen/R3/live/backend/.venv/bin/python $R/scen/R3/live/backend/autotraderctl.py run')] | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 5/5 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |

#### R4: empty / short / non-ancestor PREV_SHA -> refused before any stop (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| R4.1 apply, empty PREV (live HEAD unresolvable): exit | 78 | 78 | ok |
| R4.1 apply, empty PREV (live HEAD unresolvable): refusal token | prev_sha_invalid | deploy apply: REFUSED deploy_check_tree_clean,deploy_check_parent,deploy_check_files,prev_sha_invalid,changed_paths_unreadable (exit 78; nothing changed) | ok |
| R4.1 apply, empty PREV (live HEAD unresolvable): no `deploy_tool_scope_missing` token | absent | False | ok |
| R4.1 apply, empty PREV (live HEAD unresolvable): deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R4.1 apply, empty PREV (live HEAD unresolvable): live HEAD unchanged | (unborn) | (unborn) | ok |
| R4.1 apply, empty PREV (live HEAD unresolvable): no live-checkout subprocess (no stop) | 0 calls | 0 | ok |
| R4.1 apply, empty PREV (live HEAD unresolvable): no launchctl request | 0 | [] | ok |
| R4.1 apply, empty PREV (live HEAD unresolvable): robot still running, same supervisor | pid 40659 | [40659] | ok |
| R4.1: the git diff failure is changed_paths_unreadable (F-2, P2.1) | REFUSE changed_paths_unreadable | True | ok |
| R4.2 apply, non-ancestor PREV (live HEAD diverged): exit | 78 | 78 | ok |
| R4.2 apply, non-ancestor PREV (live HEAD diverged): refusal token | prev_sha_invalid | deploy apply: REFUSED deploy_check_parent,prev_sha_invalid (exit 78; nothing changed) | ok |
| R4.2 apply, non-ancestor PREV (live HEAD diverged): deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R4.2 apply, non-ancestor PREV (live HEAD diverged): live HEAD unchanged | 1dab3e6b0b7dbcce3d3cb2cafc6a1f5ca2cc7fff | 1dab3e6b0b7dbcce3d3cb2cafc6a1f5ca2cc7fff | ok |
| R4.2 apply, non-ancestor PREV (live HEAD diverged): no live-checkout subprocess (no stop) | 0 calls | 0 | ok |
| R4.2 apply, non-ancestor PREV (live HEAD diverged): no launchctl request | 0 | [] | ok |
| R4.2 apply, non-ancestor PREV (live HEAD diverged): robot still running, same supervisor | pid 40659 | [40659] | ok |
| R4.3 undo --to '' (empty): exit | 78 | 78 | ok |
| R4.3 undo --to '' (empty): refusal token | prev_sha_invalid | deploy undo: REFUSED prev_sha_invalid,undo_prev_mismatch (exit 78; nothing changed) | ok |
| R4.3 undo --to '' (empty): deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R4.3 undo --to '' (empty): live HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| R4.3 undo --to '' (empty): no live-checkout subprocess (no stop) | 0 calls | 0 | ok |
| R4.3 undo --to '' (empty): no launchctl request | 0 | [] | ok |
| R4.3 undo --to '' (empty): robot still running, same supervisor | pid 40659 | [40659] | ok |
| R4.4 undo --to 9558442 (short): exit | 78 | 78 | ok |
| R4.4 undo --to 9558442 (short): refusal token | prev_sha_invalid | deploy undo: REFUSED prev_sha_invalid,undo_prev_mismatch (exit 78; nothing changed) | ok |
| R4.4 undo --to 9558442 (short): deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R4.4 undo --to 9558442 (short): live HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| R4.4 undo --to 9558442 (short): no live-checkout subprocess (no stop) | 0 calls | 0 | ok |
| R4.4 undo --to 9558442 (short): no launchctl request | 0 | [] | ok |
| R4.4 undo --to 9558442 (short): robot still running, same supervisor | pid 40659 | [40659] | ok |
| R4.5 undo --to <a commit not an ancestor of HEAD>: exit | 78 | 78 | ok |
| R4.5 undo --to <a commit not an ancestor of HEAD>: refusal token | undo_prev_mismatch | deploy undo: REFUSED undo_prev_mismatch (exit 78; nothing changed) | ok |
| R4.5 undo --to <a commit not an ancestor of HEAD>: deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R4.5 undo --to <a commit not an ancestor of HEAD>: live HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| R4.5 undo --to <a commit not an ancestor of HEAD>: no live-checkout subprocess (no stop) | 0 calls | 0 | ok |
| R4.5 undo --to <a commit not an ancestor of HEAD>: no launchctl request | 0 | [] | ok |
| R4.5 undo --to <a commit not an ancestor of HEAD>: robot still running, same supervisor | pid 40659 | [40659] | ok |
| R4.7 apply with the tool's own account_home (HOME is not the account home): exit | 78 | 78 | ok |
| R4.7 apply with the tool's own account_home (HOME is not the account home): refusal token | paths_not_canonical | deploy apply: REFUSED paths_not_canonical (exit 78; nothing changed) | ok |
| R4.7 apply with the tool's own account_home (HOME is not the account home): deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R4.7 apply with the tool's own account_home (HOME is not the account home): live HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| R4.7 apply with the tool's own account_home (HOME is not the account home): no live-checkout subprocess (no stop) | 0 calls | 0 | ok |
| R4.7 apply with the tool's own account_home (HOME is not the account home): no launchctl request | 0 | [] | ok |
| R4.7 apply with the tool's own account_home (HOME is not the account home): robot still running, same supervisor | pid 40659 | [40659] | ok |
| R4.8 apply with a database path that is not <data>/autotrader.db: exit | 78 | 78 | ok |
| R4.8 apply with a database path that is not <data>/autotrader.db: refusal token | paths_not_canonical | deploy apply: REFUSED paths_not_canonical,deploy_check_schema,deploy_check_fence,fenced_unexpected (exit 78; nothing changed) | ok |
| R4.8 apply with a database path that is not <data>/autotrader.db: deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R4.8 apply with a database path that is not <data>/autotrader.db: live HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| R4.8 apply with a database path that is not <data>/autotrader.db: no live-checkout subprocess (no stop) | 0 calls | 0 | ok |
| R4.8 apply with a database path that is not <data>/autotrader.db: no launchctl request | 0 | [] | ok |
| R4.8 apply with a database path that is not <data>/autotrader.db: robot still running, same supervisor | pid 40659 | [40659] | ok |
| R4.9 apply from a candidate worktree with an untracked backend/ file: exit | 78 | 78 | ok |
| R4.9 apply from a candidate worktree with an untracked backend/ file: refusal token | candidate_worktree_dirty | deploy apply: REFUSED candidate_worktree_dirty (exit 78; nothing changed) | ok |
| R4.9 apply from a candidate worktree with an untracked backend/ file: deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R4.9 apply from a candidate worktree with an untracked backend/ file: live HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| R4.9 apply from a candidate worktree with an untracked backend/ file: no live-checkout subprocess (no stop) | 0 calls | 0 | ok |
| R4.9 apply from a candidate worktree with an untracked backend/ file: no launchctl request | 0 | [] | ok |
| R4.9 apply from a candidate worktree with an untracked backend/ file: robot still running, same supervisor | pid 40659 | [40659] | ok |
| R4.6 CLI deploy undo --to '': refused before the operator | exit 2 (argparse), nothing written | exit 2 | ok |
| R4.6 CLI deploy undo --to '9558442': refused before the operator | exit 2 (argparse), nothing written | exit 2 | ok |
| R4.6 CLI deploy apply --expect-parent '': refused before the operator | exit 2 (argparse), nothing written | exit 2 | ok |
| R4.6 CLI deploy apply --expect-parent '9558442': refused before the operator | exit 2 (argparse), nothing written | exit 2 | ok |
| R4.6 CLI deploy apply --db-path '$R/fakehome/R4/.local/share/autotrader-v1/autotrader.db': refused before the operator | exit 2 (argparse), nothing written | exit 2 | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 0/0 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |

#### R5a: 06:50 ET passes before the fast-forward -> start_cutoff, ABORTED (no reset) (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| start_cutoff raised at the held step | FAIL start_cutoff: before the fast-forward: now  | True | ok |
| deploys.log keeps the 12 earlier lines byte for byte | unchanged prefix | True | ok |
| deploys.log new lines | 2 | 2 | ok |
| deploys.log mode | 0o600 | 0o600 | ok |
| the tool's _DEPLOY_PASS reads every 10-16/10-18 DEPLOY line | 5/5 | 5/5 | ok |
| exit code (git never moved: aborted) | 3 | 3 | ok |
| ABORTED line (P2.1) | DEPLOY P2.3 bed4d57 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=ABORTED reason=start_cutoff | 2026-10-23T13:50:23+03:00 DEPLOY P2.3 bed4d57 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=ABORTED reason=start_cutoff | ok |
| printed: OK HEAD is still 9558442b4e0d200f6244e1b7b7a62fb757ad5648: git never m | present | True | ok |
| printed: OK aborted: 9558442b4e0d200f6244e1b7b7a62fb757ad5648 runs again; reaso | present | True | ok |
| printed: deploy apply: ABORTED reason=start_cutoff (exit 3; git never moved; 95 | present | True | ok |
| no reset --keep at all | no `reset --keep` STEP | False | ok |
| no fast-forward ran | no `STEP git ... merge --ff-only` line | False | ok |
| HEAD still PREV | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| the unchanged code restarted after 06:50 ET | 1 supervisor, new pid | [72506] | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 4/4 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### R5b: 06:50 ET passes before the start -> start_cutoff, undo (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| start_cutoff raised at the held step | FAIL start_cutoff: before the start: now  | True | ok |
| deploys.log keeps the 12 earlier lines byte for byte | unchanged prefix | True | ok |
| deploys.log new lines | 2 | 2 | ok |
| deploys.log mode | 0o600 | 0o600 | ok |
| the tool's _DEPLOY_PASS reads every 10-16/10-18 DEPLOY line | 5/5 | 5/5 | ok |
| exit code (code-only: undo) | 3 | 3 | ok |
| rollback line | DEPLOY rollback to 9558442b4e0d200f6244e1b7b7a62fb757ad5648 result=ROLLBACK reason=start_cutoff | 2026-10-23T13:50:23+03:00 DEPLOY rollback to 9558442b4e0d200f6244e1b7b7a62fb757ad5648 result=ROLLBACK reason=start_cutoff | ok |
| reset --keep to PREV ran | STEP ... reset --keep PREV | True | ok |
| HEAD back at PREV | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| previous code restarted after 06:50 ET (the undo has no window check) | 1 supervisor, new pid | [72507] | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 5/5 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### R5c: migration deploy, 06:50 ET passes before the start -> start_cutoff, HALT with OPTION 1 then OPTION 2; OPTION 1 run by hand (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| start_cutoff raised at the held step | FAIL start_cutoff: before the start: now  | True | ok |
| deploys.log keeps the 12 earlier lines byte for byte | unchanged prefix | True | ok |
| deploys.log new lines | 2 | 2 | ok |
| deploys.log mode | 0o600 | 0o600 | ok |
| the tool's _DEPLOY_PASS reads every 10-16/10-18 DEPLOY line | 5/5 | 5/5 | ok |
| exit code (migrated: halt) | 4 | 4 | ok |
| HALTED line reason=start_cutoff with backup | result=HALTED reason=start_cutoff backup= | 2026-10-23T13:50:01+03:00 DEPLOY P2.3 f76922a PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=HALTED reason=start_cutoff backup=/Users/salomon/autotrader-work/rehearsal-p2/fakehome/R5c/.local/share/autotrader-v1/backups/autotrader.20261023T101639.503698Z.v16-to-v17.db | ok |
| P2.1: OPTION 1 (start the new code) before OPTION 2 (the restore) | OPTION 1 < OPTION 2 < RESTORE | [7368, 8681, 8740] | ok |
| no reset by the tool | no reset --keep before the printed RESTORE | False | ok |
| robot left disabled | 0 supervisors | 0 | ok |
| HEAD left at the candidate | f76922a7031f29371df60432e01b5a909a2e5923 | f76922a7031f29371df60432e01b5a909a2e5923 | ok |
| OPTION 1 block parsed | cd, enable, tail, echo | [True, True, True, True] | ok |
| OPTION 1: the migrated candidate starts and settles (no CHILD_EXIT in 24 s) | 1 supervisor, no exit | starts=1 exits=[] supervisors=1 | ok |
| OPTION 1's echo writes a PASS line the tool's parser reads | result=PASS schema=v17, short = candidate | 2026-10-23T13:50:25+03:00 DEPLOY P2.3 f76922a PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=PASS schema=v17 | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 5/5 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 8675055c0b57200d395534568af8eae031f293e2 | 8675055c0b57200d395534568af8eae031f293e2 | ok |

#### R6: supervisor ignores SIGTERM -> stop_wait_timeout, exit 4, git untouched (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| exit code | 4 | 4 | ok |
| deploys.log keeps the 12 earlier lines byte for byte | unchanged prefix | True | ok |
| deploys.log new lines | 2 | 2 | ok |
| deploys.log mode | 0o600 | 0o600 | ok |
| the tool's _DEPLOY_PASS reads every 10-16/10-18 DEPLOY line | 5/5 | 5/5 | ok |
| HALTED line | DEPLOY P2.3 bed4d57 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=HALTED reason=stop_wait_timeout | 2026-10-22T02:25:31+03:00 DEPLOY P2.3 bed4d57 PREV_SHA=9558442b4e0d200f6244e1b7b7a62fb757ad5648 pin=7d313fd075d2 result=HALTED reason=stop_wait_timeout | ok |
| printed: FAIL stop_wait_timeout: not stopped after 75 s: | present | True | ok |
| printed: no SUPERVISOR_STOP reason=SIGTERM line since | present | True | ok |
| printed: Git is untouched. When the processes above are gone, the one re-enable | present | True | ok |
| printed:   ( cd $R/scen/R6/live/backen | present | True | ok |
| printed: deploy apply: HALTED reason=stop_wait_timeout (exit 4; the robot is di | present | True | ok |
| no fast-forward | no `STEP git ... merge --ff-only` line | False | ok |
| git untouched: HEAD | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| git untouched: tree | clean | clean | ok |
| launchd SIGKILLed the supervisor after ExitTimeOut 45 s | SIGKILL line | True | ok |
| no SUPERVISOR_STOP line was written | 0 | [] | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 1/1 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |

#### R7: the candidate's telegram child exits 78 three seconds after its CHILD_START -> post_start_child_exited, ROLLBACK, no PASS (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| exit code (rolled back) | 3 | 3 | ok |
| FAIL post_start_child_exited on the telegram exit | FAIL post_start_child_exited: ... CHILD_EXIT telegram code=78 | ['FAIL post_start_child_exited: 2026-10-21T23:25:45+00:00 CHILD_EXIT telegram code=78 restart_in=15 after the start'] | ok |
| deploys.log keeps the 12 earlier lines byte for byte | unchanged prefix | True | ok |
| deploys.log new lines | 2 | 2 | ok |
| deploys.log mode | 0o600 | 0o600 | ok |
| the tool's _DEPLOY_PASS reads every 10-16/10-18 DEPLOY line | 5/5 | 5/5 | ok |
| rollback line | DEPLOY rollback to 9558442b4e0d200f6244e1b7b7a62fb757ad5648 result=ROLLBACK reason=post_start_child_exited | 2026-10-22T02:26:08+03:00 DEPLOY rollback to 9558442b4e0d200f6244e1b7b7a62fb757ad5648 result=ROLLBACK reason=post_start_child_exited | ok |
| no PASS line written | no result=PASS among the new lines | [] | ok |
| no ledger line printed | no ` \| DEPLOY ` line | [] | ok |
| summary | deploy apply: ROLLED BACK reason=post_start_child_exited (exit 3; 9558442 runs again) | deploy apply: ROLLED BACK reason=post_start_child_exited (exit 3; 9558442 runs again) | ok |
| HEAD back at PREV | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| the previous code runs and stays up (no CHILD_EXIT in the 20 s after the rollback) | 1 supervisor, no exit | supervisors=1 exits=[] | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 6/6 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |

#### R8: the sha is typed late / after live HEAD moved / after the session changed -> exit 78, nothing written (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| R8.1 the sha typed after CONFIRM_TIMEOUT_S: exit | 78 | 78 | ok |
| R8.1 the sha typed after CONFIRM_TIMEOUT_S: refused confirm_timeout after the typed sha | REFUSE confirm_timeout | ['REFUSE confirm_timeout: the sha was typed 301 s after the plan was printed (at most 300 s): run the command again for a fresh plan'] | ok |
| R8.1 the sha typed after CONFIRM_TIMEOUT_S: deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R8.1 the sha typed after CONFIRM_TIMEOUT_S: no live-checkout subprocess (no stop) | 0 | 0 | ok |
| R8.1 the sha typed after CONFIRM_TIMEOUT_S: no launchctl request | 0 | [] | ok |
| R8.1 the sha typed after CONFIRM_TIMEOUT_S: robot still running, same supervisor | pid 45302 | [45302] | ok |
| R8.1 the sha typed after CONFIRM_TIMEOUT_S: live HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| R8.2 live HEAD moved while the prompt waited: exit | 78 | 78 | ok |
| R8.2 live HEAD moved while the prompt waited: refused live_head_moved after the typed sha | REFUSE live_head_moved | ['REFUSE live_head_moved: live HEAD is 1dab3e6b0b7dbcce3d3cb2cafc6a1f5ca2cc7fff, not 9558442b4e0d200f6244e1b7b7a62fb757ad5648 as the plan showed: run the command again'] | ok |
| R8.2 live HEAD moved while the prompt waited: deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R8.2 live HEAD moved while the prompt waited: no live-checkout subprocess (no stop) | 0 | 0 | ok |
| R8.2 live HEAD moved while the prompt waited: no launchctl request | 0 | [] | ok |
| R8.2 live HEAD moved while the prompt waited: robot still running, same supervisor | pid 45302 | [45302] | ok |
| R8.2 live HEAD moved while the prompt waited: the tool did not touch git (HEAD is where the other writer put it) | 1dab3e6b0b7dbcce3d3cb2cafc6a1f5ca2cc7fff | 1dab3e6b0b7dbcce3d3cb2cafc6a1f5ca2cc7fff | ok |
| R8.3 the upcoming session changed while the prompt waited: exit | 78 | 78 | ok |
| R8.3 the upcoming session changed while the prompt waited: refused session_date_changed after the typed sha | REFUSE session_date_changed | ['REFUSE confirm_timeout: the sha was typed 83905 s after the plan was printed (at most 300 s): run the command again for a fresh plan', 'REFUSE session_date_changed: the upcoming session is 2026-10-23, not 2026-10-22 as the plan showed: run the command again'] | ok |
| R8.3 the upcoming session changed while the prompt waited: deploys.log not written | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| R8.3 the upcoming session changed while the prompt waited: no live-checkout subprocess (no stop) | 0 | 0 | ok |
| R8.3 the upcoming session changed while the prompt waited: no launchctl request | 0 | [] | ok |
| R8.3 the upcoming session changed while the prompt waited: robot still running, same supervisor | pid 45302 | [45302] | ok |
| R8.3 the upcoming session changed while the prompt waited: live HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 0/0 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |

#### SMOKE: harness smoke test outside the start window: robot start/stop, dry run, start_cutoff refusal (PASS)

| Check | Expected | Observed | |
|---|---|---|---|
| one rehearsal supervisor from the clone | 1 | [(26778, '$R/scen/SMOKE/live/backend/.venv/bin/python $R/scen/SMOKE/live/backend/autotraderctl.py run')] | ok |
| telegram and heartbeat children under it | 2, parent chain to the supervisor | [(26876, 26778, '$R/scen/SMOKE/live/backend/.venv/bin/python $R/scen/SMOKE/live/backend/autotraderctl.py telegram run'), (26877, 26778, '$R/scen/SMOKE/live/backend/.venv/bin/python -m trading.operations.heartbeat')] | ok |
| the clone's dry run passes the candidate's DryRunCheck | no failure | () | ok |
| operator outside the window | exit 78, start_cutoff | deploy apply: REFUSED start_cutoff (exit 78; nothing changed) | ok |
| nothing changed: no live call | 0 | 0 | ok |
| nothing changed: deploys.log | ed6b00e81aaebc78 | ed6b00e81aaebc78 | ok |
| SUPERVISOR_STOP reason=SIGTERM after disable | 1 line | ['2026-10-21T11:08:33+00:00 SUPERVISOR_STOP reason=SIGTERM'] | ok |
| supervisor.lock free and no scenario process | free, none | none | ok |
| tripwires | no tripwire line | none | ok |
| live subprocesses use the live checkout's python and cwd | all | 0/0 | ok |
| no rehearsal process left | none | none | ok |
| live repository HEAD unchanged | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | 9558442b4e0d200f6244e1b7b7a62fb757ad5648 | ok |
<!-- checks:end -->
