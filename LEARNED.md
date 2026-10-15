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

## Read-only SQLite opens in the data directory
- `mode=ro` is safe on the live WAL database in its writable data directory: SQLite recreates a missing `-shm`. `immutable=1` returns a stale view of a WAL database with an un-checkpointed `-wal` ("no such table" on an existing table): use it only on a checkpointed copy.

## Test through the production path
- A value threaded through a constructor is only proven when a test drives the path production uses. The identity size factor passed every unit test of `ShadowPipeline` and four reviews, yet `RepositoryBackedShadowRunner.open_session` rebuilt the pipeline without it (found 2026-10-16, before v7b sent anything). The dropped keyword also bypassed the fail-closed missing-row block (`identity_sizing_missing`) on v7a and v7b; only the launch preflight `registry_problems` held the line. Copy objects with a method tied to the constructor's signature, and replay a recorded day through the runner. A copy test ties the copy to the signature only when its fixture sets every argument off its default: v7b.1's test did not, v7b.2's does for every argument.
