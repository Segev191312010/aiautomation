# N8 — independent execution-safety review of bridge v7a (commit 3354c3c, branch bridge-v7)

v7a 3354c3c verdict=GO bridge_sha=a63982cc01c36ffcb7d1b7877dfdec4b0b2c7371911d5909e5f36893cbbfb440

Reviewed read-only in `/Users/salomon/aiautomation/.claude/worktrees/n2-bridge-v7` (HEAD 3354c3c, clean, one commit
over 904af38, 18 files +1701/-100). `shasum -a 256 backend/scripts_local/paper_bridge.py` = `a63982cc…` =
`REVIEWED_BRIDGE_SHA256` (`backend/trading/operations/supervisor.py:153`). No TWS, Telegram or network; nothing written
under `~/.local/share`, `~/.config`, `~/Library`; the live checkout and the live DB were not opened (only
`~/Library/Logs/AutotraderV1/bridge.log` was read). Probes and mutations ran against a byte-identical copy of `backend/`
in the scratchpad (`es_review/mut/backend`, bridge sha verified equal) with `HOME` pointed at a scratch home.

GO is for the order path: every v7a claim verified, every plan mutation killed, no HIGH finding. One MEDIUM (F-1) is a
test-hermeticity defect outside the bridge file that must be fixed in a test-only commit before the deploy commit;
it changes no bridge bytes, so no re-pin.

## Claims verified (hunk + probe for each)

1. Exact-match allowlist. `may_send_orders` = `strategy_name.strip() in SENDABLE_STRATEGY_NAMES` (`paper_bridge.py:305-306`),
   `SENDABLE_STRATEGY_NAMES = frozenset({"MOMO_HOD_V2"})` (`:209`), `ORDER_STRATEGY_PREFIX`/`MAX_OPEN` deleted.
   Pinned equal to `trading.sendable_identities.SENDABLE_IDENTITIES` by `test_sendable_identities.py::test_the_bridge_gate_is_the_pinned_tuple_exactly`.
   Case/whitespace variants: `test_v7a_a_name_that_is_not_exactly_sendable_is_shadow_only[momo_hod_v2, MOMO_HOD_V2X, AP_MOMO_HOD_V1, …]`;
   my probe P1 (` MOMO_HOD_V2 ` sends, ref and fence lookup use the stripped name; the ledger path strips in
   `strategy_name_from_spec` `:287` anyway). Mutations M1 (prefix restored) and M13 (strip removed) killed. Live ledger
   name is exactly `MOMO_HOD_V2` (`session_runner.py:59`; 10-13 bridge.log `SENT T (MOMO_HOD_V2)`).
2. Caps. `Caps` frozen dataclass (`:193-199`), `IDENTITY_CAPS` (`:210-213`), `global_max_open()` = sum (`:309-311`),
   evaluated through `slots_used()` (`:1322-1326`, unchanged: holders + unmanaged live symbols). Gate order
   (`_gate` `:1479-1525`): name → caps row → intent → expiry → `times.last_entry` → `entry_deadline(bracket)` → dup
   → symbol → per-identity `positions_of` → global → entries/day → net liq → daily loss → size. Matches the plan.
   `entries_today` = `parent is not None` (`:1225-1227`; cancelled counts: test + mutation M4 killed; probe P11).
   `positions_of` ⊆ holders — no double count (P5: 1 MOMO holder + 2 foreign live symbols → `slots_used()==3`,
   skip "max 3 positions/entries already active", `positions_of==1`; identical to v5's `test_l4`).
   `entry_deadline(bracket)` (`:1211-1219`): sendable → `min(times.last_entry, closes_at − offset)`; non-sendable →
   family cutoff; `expire_entries` `:1619` uses it. 15:00 on 2026-10-13, 12:00 on the 13:00 close of 2026-11-27,
   `ORB_V1` 12:00, unknown family 15:30 (test + probe P12, incl. a 5-min row never extending past 15:30).
   Missing row → `identity_caps_missing:<X>` skip + one CRITICAL (`:1491-1496`; M8 killed). M3 (global hard-coded)
   and M7 (literal 15:00) killed.
3. Identity fence. `IDENTITY_FENCE_SQL` `:788`, `identity_fence_active` `:826-829` (`mode=ro`, `name='paper:<X>'`),
   read in `process_decision` after the global fence, own try/except, fail closed (`:1565-1578`). Both reads happen
   after the quote wait `bid()` and the second expiry check (probe P6: call order `bid → global → identity`; a fence
   set during the quote wait is honoured). Exits/flatten/manual desk untouched (test: held bracket keeps
   managing; `flatten`/`modify_exit` never consult the fence). M2 (fence ignored) and M12 (read moved before the
   quote wait) killed. Store/CLI: `runtime_fence.py` same upsert, `fence_name` refuses whitespace; `cli.py:409-435`
   non-sendable → exit 2, `resume` without `--identity` → exit 2, global stop has no resume (tests
   `test_autotraderctl.py`, `test_runtime_fence.py`).
4. orderRef. `ref()` `:1234-1235` → `autotrader:<IDENTITY>:<decision_id[:12]>`; `parse_ref` `:329-336` (P9 edge
   cases; M5 positional killed). Consumers are prefix-only: `execution_matches` `:642-650` (`startswith(ref_prefix)`),
   `leg_fills` passes `ORDER_REF_PREFIX` `:1382-1384`, `openOrder` `:954-982` stores the raw ref, startup `manual_ids`
   use `MANUAL_REF_PREFIX` `:2037`, `foreign_bot_con_ids` `:2494-2500` `startswith(ORDER_REF_PREFIX)`. Nothing slices
   by position. `modify_exit` → `exit_order` `:1637-1638` re-encodes with `self.ref(bracket)`: a v5 bracket would get
   the new ref on a modify, but `StateStore.load` `:727-744` reads only `<session_date>.json` and refuses a file of
   another date (probe P8), so a pre-session deploy never loads a v5 bracket. Only a same-date mid-session swap could;
   the runbook forbids it ("Never mid-session").
5. Sizing upstream. `identity_sizing.py`: `MappingProxyType`, `require_size_multiplier` + Decimal check at import;
   `identity_multiplier` → `None` for a sendable name without a row (M9 killed). `pipeline.py:216-220` refuses a
   non-Decimal/out-of-range factor; `_engine_size` `:257-269` passes no keyword for unmanaged+neutral (test pins the
   engine call "exactly as before"), Decimal product otherwise; `None` → terminal `identity_sizing_missing`
   (`:431-448`), one CRITICAL; `_record_managed` gets the managed factor alone (M11 killed). `shadow_service.py:556-560`
   wires it from `spec.name`; `registry_problems` (`:181-218`) covers duplicates, keys, multiplier set, registration
   (M10 killed). Supervisor: `run_supervisor` `:1227-1239` exits 78 with `LAUNCH_REFUSED registry code=78 reason=…`
   before `Supervisor` is built (test + phone text). Dry run: `bridge_preflight_argv` = the launch argv + `--preflight`
   (`:1056-1062`, test asserts equality with `plan_session`'s bridge argv), `env=child_environment()` = the launcher's
   (`:1033-1037`, `:935`), `cwd=backend`, run only after `bridge_review_line` says reviewed (`:1447-1455`).
   `registry_refusals()` import cost: 0.18 s + 0.03 s, 100 modules, no anthropic/ibapi, no files under HOME.
6. `--preflight`. `preflight()` `:3022-3040`: `load_config` → caps lines → fences → `caps_problem()`; returns before
   the date/state/connect logic in `main` `:3153-3155`. No `App`, no `StateStore`, `mode=ro`. Probe S3 ran the REAL
   bridge `--preflight` as the real supervisor subprocess under a scratch HOME: exit 0, three lines, `CRITICAL fenced
   identities = MOMO_HOD_V2` when the row is stopped, no `bridge/` state dir, no `-wal/-shm` left beside the DB;
   missing fence table → 78 `PREFLIGHT_REFUSED fences unreadable (OperationalError…)`; absent DB file → 78 (P7);
   missing config → 78 `refusing: config …` and checked first (P7); a future `--date` is accepted (the dry run may
   rehearse tomorrow). The connect path logs the same `caps_lines()` + `log_fenced_identities(DB)` (`:3118-3126`).
   M6 (dropped caps row passes preflight) killed.
7. No new import (`test_bridge_imports.py` green, `EXPECTED_LAZY_IMPORTS` unchanged; `preflight()` imports
   nothing); `STATE_SCHEMA == "paper-bridge-state.v5"`, `Bracket` fields unchanged, a v5 state document loads (P8);
   exit codes 0/75/78 unchanged (P0); `grep` of the diff for `DU\d{6,}`, chat ids, 9+-digit numbers → nothing.

Extra probes asked for: `max_entries_per_day=0` → every entry skipped "max 0 entries/day already sent" and the
preflight line prints `entries/day=0` (P2, fail-closed, fine). Fresh DB without the fence table: the gate skips on
the GLOBAL read first ("kill switch unreadable … fail closed", forwarded to the phone); the connect line logs
`CRITICAL identity fences unreadable (…) — every entry will be skipped` and the session continues managing exits
(P3/P3b). Runtime mutation of `IDENTITY_CAPS` is honoured by the gate (P4, see F-3). Hung preflight: killed at
`BRIDGE_PREFLIGHT_TIMEOUT_S` (60 s), returns 127, dry run ends `!!! BRIDGE PREFLIGHT FAILED (exit 127)` (S1, see F-5).

## Findings (defects first)

F-1 MEDIUM — the test suite at 3354c3c is not hermetic: it runs the real bridge against `Path.home()`.
`dry_run_report` (`supervisor.py:1373-1455`) defaults `preflight=None` → `run_bridge_preflight` spawns the real
`paper_bridge.py --preflight` with `env=os.environ`; the bridge derives `CONFIG`/`DB` from `Path.home()` (`:158-159`).
Nine test call sites pass no `preflight=` stub: `tests/test_schema_gate.py:478,488,493`, `tests/test_supervisor.py:918,949,970,1549`
and `cli.main(["run","--dry-run",…])` at `tests/test_supervisor.py:1027,1032`. Run from the owner's shell they read
the LIVE config and open the LIVE database read-only from inside pytest (exactly the LEARNED 10-14 hazard: a
worktree's processes default to the live state; a `mode=ro` open can create `-wal/-shm`); run isolated they fail.
Probe: `HOME=<scratch> DB_PATH=<scratch>/x.db .venv/bin/python -m pytest -q -p no:cacheprovider tests/` on the copy →
`1 failed, 2526 passed, 1 skipped`: `test_schema_gate.py::test_the_dry_run_reports_a_pending_migration_loudly_and_reads_only`
(`assert "!!!" not in report` sees `!!! BRIDGE PREFLIGHT FAILED (exit 78)` because the scratch home has no config).
So "2528 passed" is reproducible only with the owner's live home. Required fix (test-only, no bridge bytes, no
re-pin): pass a stub `preflight=` (as `test_supervisor._PREFLIGHT_OK` already does for the new tests) at every site
above, and add a `tests/conftest.py` guard — set `HOME` to the per-process local tmp (the conftest already does this
for `DB_PATH`) so no test can ever reach `~/.local/share/autotrader-v1` or `~/.config/autotrader-v1`. Re-run the full
suite isolated and record the count.

F-2 LOW — the new fail-closed SKIP lines never reach the phone. `notify_service.bridge_event_text` (`:654-658`)
forwards a SKIP only when its body contains "daily loss" or "kill switch". Probe (ran): `identity fence unreadable
(OperationalError) — fail closed`, `identity_stopped:MOMO_HOD_V2`, `identity_caps_missing:MOMO_HOD_V2` → `None`;
`kill switch unreadable …` → forwarded; `CRITICAL fenced identities = MOMO_HOD_V2` → forwarded. In practice the
global read (same table, same connection style, read first) fails first and is forwarded, and the connect-time
CRITICAL covers a fence present at 09:25, so the gap is a mid-session identity-only failure. Fix: add `"identity"`
to the SKIP filter in `notify_service.py` (outside the bridge; no re-pin).

F-3 LOW — caps rows are not validated at import and the table is mutable. `Caps(-1, -5, timedelta(minutes=-60))`
and `Caps("3", None, 60)` construct (probe); `IDENTITY_CAPS` is a plain `dict` — `monkeypatch.setitem(pb.IDENTITY_CAPS,
"X", Caps(10, …))` makes `global_max_open()` 13 (P4). Nothing mutates it in production and `min(times.last_entry, …)`
keeps any deadline ≤ close−30; a bad row cannot widen the session, only the position count, and only via a reviewed
commit. Fix (bridge bytes → bundle with the v7b data commit, which re-pins anyway): `Caps.__post_init__` asserting
`int` ≥ 1 / `None`, `timedelta(minutes=30) ≤ offset ≤ 6 h`; `IDENTITY_CAPS = MappingProxyType({...})` (the tests
monkeypatch the attribute, not the dict, so they keep working).

F-4 LOW — a registry check that raises is a traceback, not a refusal. `run_supervisor` `:1227` calls
`registry_refusals()` with no try/except; an ImportError in the shadow_service stack propagates out of `autotraderctl
run` (probe S2: `ImportError` raised, no `LAUNCH_REFUSED` line in supervisor.log). Nothing launches — including the
notifier — so the phone stays silent and launchd may loop. The dry run at STEP 3 would show the same traceback, so
it is caught before a deploy in practice. Fix: `except Exception as exc: problems = [f"registry_check_failed={type(exc).__name__}"]`
→ the same exit-78 line.

F-5 LOW — on a preflight timeout the child's partial output is discarded. `run_bridge_preflight` `:1079-1093` returns
`"bridge preflight could not run (TimeoutExpired…)"` and drops `exc.stdout`/`exc.stderr` (S1: the "sendable
identities" line the child had printed is absent). Fix: append `exc.stdout or ""` when the exception carries it.

F-6 LOW — plan deviation: "Dashboard/status route reads the same rows" is not in the diff. Telegram `/status`
(`telegram_tools.py:890-895 _kill_switch_text`) and the dashboard show only the global row; an identity fence is
visible only via `autotraderctl paper status`, the connect-time CRITICAL and (unforwarded, F-2) SKIP lines.
Fix: extend `_kill_switch_text` with `SQLitePaperKillStore.identity_rows()`.

F-7 LOW (test quality) — `test_v7a_the_identity_position_cap_is_checked_before_the_global_one` ends with
`assert … or pb.global_max_open() == 1` (tautology). The skip-reason assertion above it carries the test; replace
the last line with `assert bridge.slots_used() == 1 == pb.global_max_open()`.

Informational (no action): the SKIP wording for the identity cap changed ("MOMO_HOD_V2: max 3 positions already
active"; the global one keeps v5's text) — the only parser of a skip reason is `manager/context.py:296`
(`DAILY_LOSS_SKIP`), unaffected. A decision skipped under an identity fence stays skipped after `paper resume`
(P10; it would be expired anyway, MAX_DECISION_AGE_S = 60). Running the brief's pytest command writes
`backend/.tmp/pytest/<pid>-…` inside the worktree (conftest), which is why my probes ran on the scratch copy.
The commit's docs entry correctly says the review is PENDING; the deploy commit should replace it with this verdict.

## Test tail

Targeted (worktree, HOME → scratch): `tests/test_paper_bridge.py tests/test_bridge_imports.py
tests/test_sendable_identities.py tests/test_identity_sizing.py tests/test_runtime_fence.py tests/test_autotraderctl.py
tests/test_supervisor.py tests/test_manager_planner.py` → `385 passed in 29.01s`.

Full suite (scratch copy, HOME → scratch): `1 failed, 2526 passed, 1 skipped in 142.67s` — the one failure is F-1.

Own probes (`es_review/mut/backend/tests/test_es_probes_bridge.py`, `…_supervisor.py`): `17 passed in 2.69s`.

Mutation set (`es_review/run_mutations.py`, each applied to the copy and restored; "KILLED" = ≥1 test fails):
M1 prefix match restored KILLED · M2 identity fence ignored KILLED · M3 GLOBAL_MAX_OPEN hard-coded 3 KILLED ·
M4 entries/day ignores cancelled KILLED · M5 parse_ref positional KILLED · M6 dropped caps row passes --preflight KILLED ·
M7 last_entry literal 15:00 KILLED · M8 sendable-without-row sent at defaults KILLED · M9 identity_multiplier returns 1
without a row KILLED · M10 registry duplicate not refused KILLED · M11 identity factor in the managed record KILLED ·
M12 identity fence read before the quote wait KILLED · M13 strip removed from the gate KILLED. 13/13.

## What changes for the owner on the first v7a session (MOMO only)

- Behaviour-neutral on a normal day: same 3-position cap, same 15:00 last entry (`close − 60 min`), no entries/day
  cap, same 1%/25%/−2% limits, same flatten. Known change: on a 13:00 close MOMO's last entry is 12:00 (was 12:30).
- New lines at connect (16:25 Israel / 09:25 ET), right after `PAPER bridge v7a connected …`:
  `sendable identities = MOMO_HOD_V2`, `caps MOMO_HOD_V2 positions=3 entries/day=none last_entry=close-60m`,
  `fenced identities = none` (INFO). Anything else is wrong: `CRITICAL fenced identities = MOMO_HOD_V2` means a
  stale `paper stop --identity` row (no entries will be sent until `paper resume --identity MOMO_HOD_V2`);
  `CRITICAL PREFLIGHT_REFUSED caps: …` or `identity fences unreadable …` means every entry will be skipped —
  the dry run at STEP 3 should have shown the same.
- Every order now carries `orderRef autotrader:MOMO_HOD_V2:<12 chars>` (was `autotrader:<12 chars>`). Watch the
  first MOMO entry: `SENT` must be followed by `FILL <sym>: n/n sh @ … (MOMO_HOD_V2)` once it fills (fills are
  attributed by the `autotrader:` prefix, so even a truncated ref would still match); a `SENT` with a position in
  TWS but no `FILL` within seconds is the item to escalate (the v7b GO gate also needs it).
- New brake: `autotraderctl paper stop --identity MOMO_HOD_V2` blocks only new MOMO entries (exits, flatten,
  `/close`, `/closeall` unaffected); `paper resume --identity MOMO_HOD_V2` lifts it; `paper status` shows the row.
  The identity SKIPs do not reach the phone (F-2) — check `bridge.log` or the dashboard orders panel.
- STEP 3 dry run must show `bridge       reviewed (sha256 a63982cc01c3)` and relay the three bridge lines with no
  `!!!`; the dry run is now the first place a config typo or an unreadable DB is exit 78.
- Rollback unchanged: FLAT, no working bot orders, `git reset --keep $PREV_SHA`, dry run shows `7ad34b582460`.

## Open questions

1. F-1: will the owner accept the deploy commit only after the test-only hermeticity fix (stubbed `preflight=`,
   `HOME` guard in conftest) and a recorded isolated full-suite count? This review's GO assumes yes.
2. Does the owner want F-2 (notifier filter) in the same deploy commit? It is outside the bridge and zero-risk.
3. Dry run after a migration: the bridge's `mode=ro` open is the same kind LEARNED 10-14 saw fail with error 14 right
   after `db migrate`; if STEP 3 shows `PREFLIGHT_REFUSED fences unreadable (OperationalError: unable to open
   database file)`, re-run the dry run once after a child has opened the DB normally before treating it as a defect.
4. `manager/context.py:BRIDGE_MAX_OPEN = 3` is now pinned to MOMO's row, not the bridge-wide sum — correct for the
   manager (it narrows MOMO) but worth a one-line note in the v7b data commit, where the sum becomes 6.
