P1-B 134bde4 verdict=GO scope=ES:T-3,T-4b,T-8,T-9,tws_actor bridge_sha=7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4

# N15a: P1-B execution-safety review (head 134bde4, base a070848)

Reviewer: independent, read-only. All work ran in `/Users/salomon/autotrader-work/review-n15a`
(clone `clone/` at 134bde4, mutation clone `mut/`). Every python/pytest ran under
`sandbox.sb` (a copy of `sandbox-gates.sb` with only `(allow file-write* (subpath
"/Users/salomon/autotrader-work/review-n15a"))`; every deny kept: 7496/7497/4000-4002,
`~/.local/share/autotrader-v1`, `~/Library/Logs/AutotraderV1`, `~/Jts`, `~/.config`, the repo),
with `HOME=review-n15a/fakehome`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`,
interpreter `/Users/salomon/aiautomation/backend/.venv/bin/python`. Nothing touched the
repository's working tree, index, worktrees or branches. No Gateway port, launchctl,
supervisor, bridge or Keychain was used. No account id appears in this report.

## Summary

- **GO: no HIGH finding.** No P1-B change can place, change or cancel an order, widen a size,
  resume a fence or write the global `paper` row. The bridge is untouched. Its sha256 matches
  the pin. `scripts_local` and `trading/risk` have no diff against bridge-v7b. The only
  orchestration diff is T-8's sidecar. `SENDABLE_IDENTITIES` is unchanged.
- **T-8 leaves decisions unchanged.** The decision payload, quantity, golden digests and every
  stored row are byte-identical with a working sidecar, a failing one, a slow one and a hung
  one. The sidecar is written after the decision is recorded, at all three call sites. The
  bridge never reads it.
- **MEDIUM-1: an unbounded sidecar await can freeze a strategy's shadow runner for the rest of
  the session, silently.** One way to trigger it with the default production writer is a FIFO
  at the run's sidecar path, because the file is opened before its "regular file" check. The
  decision already recorded is unaffected, but no later decision is ever made and nothing logs.
  This fails safe for orders but hides the outage. A fix is proposed below.
- **T-9 Sentinel matches the brief.** It can only stop one sendable identity through
  `fence_identity`, never the global row, and never resumes. Its thresholds are the
  owner-accepted ones and are pinned to the bridge's caps. While blind it sends a message and
  writes no fence. It fences once per identity per day. A fence-write failure is paged and
  retried and never crashes the notifier. The nightly mismatch hook cannot fence today (it has
  no comparer). Once a comparer is wired, its counting rule would fence on `missing_from_replay`
  (LOW-3).
- **T-4/T-4b and T-3 are clean.** The tws_actor listener changes nothing when unset, cannot
  raise into `_on_error`, and hears only 2103/2105/2110/2157. The pre-session check holds the
  client-21 lock, and that connection carries no orders. The 087e9f0 changes touch no
  manual-order write. The heartbeat URL never appears in a log, argv or exception text, and
  exit 69 is pinned. Three mutations survived the full suite (M1, M7, M16). Each is a test gap
  on code that is correct today: LOW-1, LOW-2, LOW-6.

## Invariants (item 7)

| Check | Result |
|---|---|
| `git diff --stat bridge-v7b 134bde4 -- backend/scripts_local backend/trading/risk` | empty |
| `shasum -a 256 backend/scripts_local/paper_bridge.py` | `7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4` = `supervisor.REVIEWED_BRIDGE_SHA256` (supervisor.py:232) |
| `git diff bridge-v7b 134bde4 -- backend/trading/orchestration` | `pipeline.py` only, +43/-1. The changes are two imports, the `SizingRecorder` alias, `SIZING_RECORD_FAILED`, the `sizing_recorder` constructor argument and its storage, its copy in `with_decision_recorder`, four `_record_sizing` calls (each after `_record`), and the `_record_sizing` method. No decision-construction line changed. |
| `SENDABLE_IDENTITIES` (`trading/sendable_identities.py:28`) | no diff vs bridge-v7b |
| Other order-path modules vs bridge-v7b (`trading/execution`, `engine`, `rules`, `contracts`, `scheduler`, `db`, `config.py`, `routers`, `storage/runtime_fence.py`, `storage/migrations`) | no diff. `trading/storage` adds only `decision_sizing.py`. No migration. |
| bridge-v7b (5d0eb1c) vs merge-base f315430 | docs-only commits (3 files under `docs/reviews/`) |

## Findings

### HIGH

None.

### MEDIUM-1: the sidecar write is awaited with no bound; a stall freezes the shadow runner for the rest of the session, silently

- **Where:**
  - `backend/trading/orchestration/pipeline.py:663`: `await self._sizing_recorder(row)` has no deadline.
  - `backend/trading/storage/decision_sizing.py:229`: `await asyncio.to_thread(append_decision_sizing, ...)`.
  - `backend/trading/storage/decision_sizing.py:205-208`: `os.open(target, O_WRONLY|O_CREAT|O_APPEND|O_NOFOLLOW)` runs before the `S_ISREG` check. Opening an existing FIFO for writing blocks in the kernel until a reader appears, so the check never runs.
- **Evidence (reviewer probes in `mut/backend/tests/test_probe_n15a.py`, all passing, sandboxed):**
  - Hanging recorder: the decision was recorded (`shadow_accepted`), and `assess_one` had not returned 1 s later. It would never return.
  - 1.5 s recorder, two candidates in one `run`: the second decision was written 1.50 s after the first. The whole sidecar latency lands on the next candidate.
  - Default production recorder with a FIFO at `<db>-decision-sizing/<run>.jsonl`: the decision was recorded, then `assess_one` stalled until the probe opened a reader.
  - The normal path is not a problem: the median append is 0.058 ms (p99 0.113 ms, max 0.217 ms over 200 appends on this Mac).
- **What stays byte-identical:** the decision that preceded the stall is committed unchanged. The bridge's `DECISIONS_SQL` (paper_bridge.py:819-829) filters on `outcome` and `session_date` only, not on run status. So nothing the bridge already holds changes.
- **Failure scenario:** during RTH, ORB_V2's runner decides candidate A, and the sidecar append for A blocks. A FIFO or special file at the run's path is enough, and so is a hung operation on that directory. Then:
  - The session runner's loop is sequential (session_runner.py:568/601/622), so candidates B..N are never assessed.
  - `ShadowRunSession._decisions` is never updated, `finalize` never runs at the cutoff, and the run stays `running`.
  - The lease heartbeat keeps renewing in the background, so nothing detects the stall.
  - Nothing is logged: the `except` that logs `sizing_record_failed` is never reached.
  - The identity sends nothing more that day.

  For orders this fails safe. It is still an invisible outage of a sendable identity, and the process cannot exit cleanly afterwards: `asyncio.run` waits for the parked default-executor thread until the supervisor's SIGKILL.

  A slow write, as opposed to a hung one, does not change decision content. It shifts later candidates' timing, and a quote that goes stale meanwhile leaves the candidate pending instead of accepted. The 15 s risk-approval TTL (paper_bridge.py:1500-1509) runs from each decision's own creation, so decisions already recorded are not affected.
- **Fix (small):**
  1. In `_record_sizing`, wrap the await in `async with asyncio.timeout(SIZING_RECORD_DEADLINE_S):` (for example 2 s). `TimeoutError` is an `Exception`, so it is already caught and logged as `sizing_record_failed`.
  2. After the first timeout, latch the sidecar off for that pipeline instance and log CRITICAL once. `asyncio.timeout` cannot stop the worker thread, so every later call would park another default-executor thread.
  3. In `append_decision_sizing`, open with `os.O_NONBLOCK`, or `lstat` and require `S_ISREG` before `open`. A FIFO then fails at once (ENXIO) instead of blocking.
  4. Add tests: a never-returning recorder must give the same decision within the deadline plus a logged failure, and a FIFO at the run path must give a logged `OSError` with no stall.

### LOW-1: no test pins "sidecar after the decision" (mutation M1 survives the full suite)

- **Where:** pipeline.py:410-411, 549/563, 637-638.
- **Evidence:** M1 swapped `_record_sizing` before `_record` at all three call sites. Result: 3052 passed, 2 skipped. The existing tests check that both writes happen and the order of sidecar writes, but not decision-before-sidecar.
- **Scenario if it regressed:** combined with MEDIUM-1, a slow sidecar would delay the decision commit and eat into the 15 s approval TTL, so the bridge would skip the decision as `decision expired`. A hung sidecar would mean the decision is never recorded at all. This fails safe, but the stated invariant is unguarded.
- **Fix:** add one test with a shared event log. The recorder appends `("decision", id)` and the sidecar appends `("sizing", id)`. Cover the terminal, main and `record_data_blocked` paths and assert the decision event comes first for every id.

### LOW-2: no test pins that the pre-session check holds the client-21 lock (mutation M7 survives the full suite)

- **Where:** telegram_tools.py:313 (`async with self._lock:` in `TWSDataSession.use`).
- **Evidence:**
  - Today the lock does serialize the check. Probe: while the check's request ran (fake Gateway, silent history, 0.6 s actor timeout), a second `session.use()` (the manual-order facts path) entered only after 0.60 s, and it saw both listeners already removed (`None`).
  - M7 (`nullcontext()` instead of the lock when listeners are passed) kept all 287 targeted tests green. Full suite: 3052 passed, 2 skipped.
- **Scenario if it regressed:** the check runs only at 05:55-06:00 and 07:40-07:45 ET (`LEAD_GRACE` 5 min, overnight_watch.py:318-326). That is outside the manual-order window (open to close minus 6 min, manual_orders.py:1114-1125). An overlap with `/quote` or order-fact gathering would race `_connected`: two client-21 actors, or one user's `_drop` under the other. The result is fail-closed refusals ("Nothing sent"). Client 21 carries no orders, and the listeners only record codes, so no order acknowledgement can be misrouted.
- **Fix:** promote the probe to a test that asserts the second `use()` waits for the check and sees no listeners.

### LOW-3: the nightly replay-mismatch hook would fence on missing replay data once a comparer exists

- **Where:** nightly.py:157-163. `decision_mismatches` counts every `category == "decisions"` mismatch, including reason `missing_from_replay` (replay/equality.py:121-122).
- **Evidence:** probe: two `missing_from_replay` decisions give `{"ORB_V2": 2}`, which `fence_replay_mismatches` would fence.
- **Today:** `NightlyEnvironment.current()` sets no `compare_decisions` (nightly.py:113-117), so the status is `not_compared` and nothing can fence. A test pins this (`test_without_a_comparer_..._fences_nothing`). A comparer that raises is caught and reported, and fences nothing.
- **Scenario once a comparer is wired:** a night whose stored bars for the session are incomplete leaves the replay without those decisions. At about 17:40 ET, ORB_V2 is fenced with reason "sentinel: replay mismatch N decisions", and it stays fenced until the owner resumes it. This is stop-only, so it reduces risk, but it costs an identity on missing data, which the brief says must never happen.
- **Fix:** count only `fingerprint_mismatch` (and `unexpected_in_replay`). Report `missing_from_replay` as "not compared" for that identity. Add a test.

### LOW-4: promotion reads fences from `environment.database`, while the mismatch fence is written to `cfg.DB_PATH`

- **Production uses one file.** `NightlyEnvironment.current()` sets `database = Path(cfg.DB_PATH).expanduser()` (nightly.py:117). `DB_PATH` is unset in production, so it is the absolute `~/.local/share/autotrader-v1/autotrader.db` from `UserPaths`. The same file is used by:
  - the bridge: `Path(cfg.DB_PATH).expanduser().resolve()` (paper_bridge.py:3006);
  - `SQLitePaperKillStore`, which the Sentinel and the replay hook write through (`aiosqlite.connect(cfg.DB_PATH)`, db/core.py:29);
  - `read_fences` (`path.resolve()`, read-only URI).
- **Divergent in any injected environment:**
  - The nightly tests themselves: `fence_db` (cfg) differs from `night["database"]` (test_nightly_evidence.py:267-276, 302-340). So the claim "a mismatch fence written tonight holds tonight's proposal" (nightly.py:315-318) is never exercised.
  - `read_fences` on a missing file returns `{}`, i.e. "nothing fenced", silently (promotion_proposals.py:574-575).
- **Today harmless:** `OPEN_RISK_TERM_DEPLOYED = False` holds every eligible identity as HELD anyway, and no comparer writes fences. Separately, and not a P1-B change: `aiosqlite.connect` does not expand `~`, so a `DB_PATH` containing `~` would split the kill store from the bridge.
- **Fix:** build the replay hook's fence store from `environment.database`, or refuse to write when `Path(environment.database).resolve() != Path(cfg.DB_PATH).expanduser().resolve()`. Add a nightly test where the proposals step sees that night's fence as `held_identity_fenced`.

### LOW-5: "owner resume holds" depends only on the notifier's per-date state file

- **Where:** sentinel.py:606-617 (memory key `sentinel_fence:<ID>`). The memory is lost in two cases:
  - an unreadable or odd state file starts fresh (notify_service.py:~493-505);
  - `_checkpoint` swallows a save failure (notify_service.py:2850-2855).
- **Scenario:** at 11:00 the Sentinel fences GAP_GO_V2 for a daily loss, and the owner resumes it at 11:30. The state file is corrupt, or its save failed, and the notifier restarts at 12:00. The memory is empty, the loss still exceeds 1.25 R, and `is_stopped` is False, so the Sentinel writes `stopped=1` again, overriding the owner. The re-fence reduces risk and is paged, but it reverses an explicit owner decision.
- **Related race (sentinel.py:174-176):** `is_stopped` and `stop` are separate transactions. An owner `/halt` landing between them gets its reason overwritten by the Sentinel's. The row stays stopped.
- **Fix:** also treat a row with `stopped=0`, `updated_at` today and a reason not starting with `sentinel:` as "owner resumed today, do not re-fence". Make the write one conditional UPSERT (`... DO UPDATE ... WHERE stopped=0`).

### LOW-6: the Sentinel's lenient fallback R unit is untested (mutation M16 survives)

- **Where:** sentinel.py:352. `fallback = max(known)` measures a bracket with no usable R using the identity's largest R that day.
- **Evidence:** M16 (`min`) passed the 98 targeted tests. Full suite: 3052 passed, 2 skipped.
- **Scenario if it regressed:** losses would be measured in a smaller unit, so the Sentinel would fence more often. This reduces risk but costs availability. It is practically unreachable today: the bridge refuses any intent without `0 < stop < entry`.
- **Fix:** add a test with two measurable brackets of different R plus one with no `entry`.

### Informational (no rating)

- **Locked DB.** Every fence-store call opens `transaction()`, which is `BEGIN IMMEDIATE` with `busy_timeout` 10 s (db/core.py:22, 36-37).
  - During the session, a locked database can block one notifier cycle for at most about 10 s per triggered identity per 30 s check. The failure is caught and paged with the copy-paste stop command, and retried.
  - At 07:55, `_announce_fences` catches the failure and retries after the cooldown.
  - At 16:05, the report shows `Fenced: unknown (<error>)`.
  - None of these crashes or silences the notifier: `_run_sentinel`, `_fence` and the cycle all catch `Exception`.
  - The notifier's `BEGIN IMMEDIATE` reads are microseconds long. The bridge writes through the same `db.core` path with a 10 s busy timeout, and its fence reads are WAL readers, so neither is blocked.
- **`TelegramAssistant.stop()` "leaving them".** After 5 attempts it logs and returns, but `asyncio.run`'s teardown cancels and gathers the remaining tasks again. A task that truly never ends would still block process exit. That is covered by the supervisor's SIGKILL after 20 s for a SIGTERM stop, but not for a self-initiated 401 exit. This is the operations reviewer's area; the order path is unaffected (below).
- **Unmeasurable daily loss is only logged, not paged** (sentinel.py:580-589).

## Verification per scope item

1. **T-8 sidecar:** byte-identity, "after the record", the copy invariant, a failure leaving no state, and no bridge read.
   - `test_the_golden_decisions_do_not_move_while_the_sidecar_records` (GOLDEN_ORB digests for PROPOSE/ABSTAIN/fail) and `test_a_failed_sidecar_write_never_changes_a_decision_on_the_production_path` cover: every write failing, the first only, and a non-async writer. Payload, outcome, quantity 931 and stored rows are equal, and a later decision equals the reference.
   - Raise: decisions unchanged (tests, plus M2 killed).
   - Slow and hang: decisions unchanged; the probes show only the stall (MEDIUM-1).
   - With the sidecar placed after the decision and the lease-guarded recorder: a lease loss raises in `_record`, so no sidecar line is written for an unrecorded decision.
   - `with_decision_recorder` copies `sizing_recorder` (pipeline.py:287). `test_with_decision_recorder_copies_every_constructor_argument` covers it, and M12 was killed.
   - The bridge never reads the sidecar: no reference in `paper_bridge.py` (sha pinned). Its only readers are `research/size_history.py` and `research/promotion_proposals.py`, both read-only (`mode=ro`).
2. **Promotion Stage 1:**
   - Writes no constant, row or fence: the test digests the DB and the sidecar before and after, and M5 was killed.
   - `OPEN_RISK_TERM_DEPLOYED = False` gives HELD with `held_open_risk_term_not_deployed`.
   - A fenced identity is HELD (`held_identity_fenced`) whatever the gate. A fence never holds back a demotion. An unreadable fence table makes the report unreadable, so nothing is proposed.
   - Same database file: yes in production, latent divergence otherwise (LOW-4).
   - `proposals.json` has no consumer outside the nightly writer.
3. **T-9 Sentinel:**
   - `fence_identity` accepts only an exact member of `SENDABLE_IDENTITIES`, so `fence_name(None)` (the global row) is unreachable, and it requires the `sentinel: ` reason prefix (M3 killed).
   - It never resumes: there is no `.resume(` in the module, and both a literal and a `getattr` resume mutation were killed by behaviour (M4a, M4b).
   - Thresholds `CAPPED_LOSS_FACTOR=1.25` (strictly greater), `UNCAPPED_LOSS_R=2` (at least), `LOSING_STREAK=3`, and `MAX_ENTRIES_PER_DAY` are pinned to the bridge's `IDENTITY_CAPS` (M11 killed).
   - Blind gives a message and no fence (M9, M19 killed).
   - Once per identity per day, and an owner resume holds within the day (M10 killed; LOW-5 for the cross-restart edge).
   - The nightly hook cannot fence today; non-sendable identities are reported, never fenced (M14 killed).
4. **tws_actor (T-4):**
   - The diff adds only an attribute read inside the 2100-2199 warning branch. With no listener the path is byte-for-byte the old one, and `last_error` and request routing are unchanged.
   - A listener `Exception` is swallowed (M6 killed). The code set is exactly {2103, 2105, 2110, 2157} (M18, which adds 2104, was killed).
   - The listener's only users are the screener (`StatusCodeLog`, which only logs) and the pre-session check, which removes it after use (M15 killed).
5. **telegram (T-4b, 087e9f0):**
   - Client 21 (`TWSDataSession`) has no order method. `TWSPaperActor` has no `placeOrder`/`cancelOrder`, and no new module imports any execution write path.
   - Manual orders reach IBKR only through the pinned bridge's claim of a ledger row. The ledger row is written in `poll_once`/`handle_update` on the main task, after the facts and validation, and never in a background task.
   - `stop()` re-cancels only `telegram-questions`, `telegram-order-watch` and `telegram-overnight-watch`. The order watch only reads (`check_orders`).
   - `asyncio.timeout` in `_connected` has the same outcome as `wait_for` for order facts: a timeout means "Nothing sent", and nothing is persisted before the facts are gathered.
   - The check holds the lock (probe; LOW-2 for the missing test) and runs only outside the manual window.
6. **T-3 heartbeat:**
   - The child's argv is `(python, -m, trading.operations.heartbeat)`, and the URL is read from the Keychain inside the child.
   - `PingTarget.repr` hides the URL. `send_ping` raises `PingFailed(kind) from None`. Log lines carry only the kind or HTTP status. The dry-run and preflight lines show the host only.
   - `test_the_url_never_appears_in_any_log_line` covers this (M8 killed). `PING_FAILED_CODE == 69 == os.EX_UNAVAILABLE` is pinned, `NOT_CONFIGURED_CODE` is 78, and 75 is never used.
   - Imports are stdlib plus `keychain` and `paths` only: no order path.
   - The test suite never reaches the real Keychain: an autouse fixture in `test_supervisor.py` fails a test that reaches it, and `dry_run_report` defaults to "not checked".

## Mutation table (sandboxed; targeted test set, then the full suite for every survivor)

| # | Mutation | Result | Killed by |
|---|---|---|---|
| M1 | sidecar write before the decision record (all 3 call sites) | **SURVIVED** (full suite: 3052 passed, 2 skipped) | none: LOW-1 |
| M2 | a sidecar failure halves the identity factor (alters later quantities) | killed | test_decision_sizing (production-path test) |
| M3 | Sentinel writes the global row (`identity=None`) | killed | test_sentinel::test_a_trigger_writes_its_identity_row_once_per_day... |
| M4a | Sentinel resumes (literal `.resume(`) | killed | same |
| M4b | Sentinel resumes (`getattr(store, "res"+"ume")`, evades the static check) | killed | same (behaviour) |
| M5 | promotion writes a fence row (read-write connection in `read_fences`) | killed | test_promotion_proposals::..._writes_nothing |
| M6 | a status-listener exception propagates into `_on_error` | killed | test_overnight_watch::test_the_actor_status_listener_hears_only_the_four_codes... |
| M7 | pre-session check runs without the client-21 lock | **SURVIVED** (targeted 287 passed; full suite: 3052 passed, 2 skipped) | none: LOW-2 |
| M8 | heartbeat URL in the failure log line | killed | test_heartbeat::test_http_500_is_logged_once_per_ten_minutes... |
| M9 (own) | Sentinel fences while blind (stale heartbeat, readable file) | killed | test_sentinel::test_a_stale_bridge_state_is_blind... |
| M10 (own) | once-per-day memory not set (owner resume overridden) | killed | test_sentinel::test_a_trigger_writes... |
| M11 (own) | capped loss limit inclusive (≥ 1.25 R) | killed | test_sentinel::test_the_thresholds_are_the_owner_accepted_defaults... |
| M12 (own) | `with_decision_recorder` drops `sizing_recorder` | killed | test_decision_sizing::test_the_repository_runner_keeps_the_identity_factor_and_the_sidecar |
| M13 (own) | `STOP_ATTEMPTS = 1` | killed | test_telegram_bot (stop with a task that drops its first cancel) |
| M14 (own) | nightly fences non-sendable identities | killed | test_nightly_evidence::test_a_replay_mismatch_fences... |
| M15 (own) | pre-session listeners left attached | killed | test_pre_session_check::test_the_shared_connection_keeps_its_default_listeners... |
| M16 (own) | Sentinel fallback R unit `max` changed to `min` | **SURVIVED** (targeted 98 passed; full suite: 3052 passed, 2 skipped) | none: LOW-6 |
| M17 (own) | sidecar records managed factor 1 instead of the applied one | killed | test_decision_sizing::test_every_decision_records_identity_times_managed... |
| M18 (own) | status listener also hears 2104 | killed | test_overnight_watch (four codes) |
| M19 (own) | Sentinel fences every identity when the state file is missing | killed | test_sentinel::test_an_unreadable_or_missing_state_is_blind... |

## Test counts

- Full backend suite at 134bde4, sandboxed, once: **3052 passed, 2 skipped** in 231 s. The two skips are environment-only: `test_launchd` needs `backend/.venv` inside the checkout, and `test_manual_orders_migration` needs a live DB, which does not exist under the fake HOME. The commit message reports 3053 passed and 1 skipped; the difference is these environment skips.
- Reviewer probes: 6/6 passed (`mut/backend/tests/test_probe_n15a.py`, not part of the product).
- Flake re-runs of the 087e9f0 tests (`test_pre_session_check.py`, `test_overnight_watch.py`, `test_telegram_bot.py`), sandboxed: 3 runs × 217 passed, no flake. That is 3 runs, not the commit message's 30.

## What I could not verify

- Real IB Gateway behaviour: the handshake codes, whether 2110 really arrives on the handshake, and HMDS answers at 05:55 ET. Only the fakes in the tests were used, and no port was touched.
- The real Keychain item and a real monitor endpoint.
- The launchd and supervisor environment of every child: I assumed `DB_PATH` is unset and `HOME` is the same, as the code's defaults imply.
- APFS behaviour under a genuinely hung volume. The FIFO probe stands in for "a blocking open".
- Long-run behaviour of the Sentinel's ledger re-read as the bridge state history grows.
- Operations scope (rotation, OPS report text, watch scheduling) is the other reviewer's.
