**VERDICT: GO for 4788049c24b58a4ad8e205fbfedf8457f8ee90a8** (ops-p3-sizes, fix 3 on 27a9d34; tree 55fecb9dde5a9a56b5f2f834a3059d18900ab37e). All three of N19b-ES's findings (LOW-1, LOW-2, LOW-3) are closed in code and by behavioural tests. From the execution-safety side, none of OPS items 1-9 changes what size is traded, touches a decision, or writes a size. There is no HIGH, no MEDIUM and no new LOW. One informational note is recorded on the bridge's import coverage (section 3).

# N19b-ES-2: re-read of fix 3 (27a9d34..4788049)

- **Reviewer:** N19a acting as N19b-ES. I did not write this code.
- **When:** 2026-10-23 07:15-07:33 Israel time (report finished 2026-10-23T07:33:25+03:00), outside every blackout window.
- **How:** fresh `git archive 4788049` extracts under `sandbox-exec -f ~/autotrader-work/sandbox-n19.sb`, with `HOME=~/autotrader-work/review-n19/fakehome`.
- **What I did not touch:** ports, launchctl, the live DB, logs, Keychain, the worktree, the ledger. I made no commit.
- **Evidence** (under `~/autotrader-work/review-n19/n19b-es/`):
  - `sql/collision-fix3.out`
  - `mut-fix3-run.log`, `mut-fix3/results.jsonl`
  - `full-suite-fix3.log`
  - `bridge-closure.txt`, `bridge-importtime.log`
  - `changed-b5f2339-4788049.txt`

## 1. Closure of N19b-ES

| Finding | Fix in 4788049 (read in code) | Evidence | Status |
|---|---|---|---|
| LOW-1: a step-down that shares a start with an earlier mid-session event blocks the identity for good | `size_writer._chain_after_step_down` (size_writer.py:553-582) runs inside the identity's `BEGIN IMMEDIATE`:<br>• reads the identity's own history on the writer's connection (`identity_sizing.read_size_events_from`, the same query);<br>• runs `size_from_events` on that history plus the candidate for `plan.session`;<br>• if the chain is broken, or the candidate would not be the size in force, it rolls back and skips with `size_writer_identity_skipped … step_down_would_break_chain`.<br>It only reads, so it can prevent a write and can never cause one. An unparseable row raises `SizeStoreUnreadable`, which the per-identity guard skips. | **My MOMO repro through the writer's own check** (`collision-fix3.out`):<br>• Monday night (`plan.session` Tue 10-20): "size_event_chain_broken … one step per session", nothing written<br>• Tuesday night (`plan.session` Wed): OK<br>• MOMO still reads ×0.5 Tue, Wed, Nov 2<br>**Builder's test:** `test_a_step_down_sharing_a_session_start_with_an_earlier_event_is_never_written`<br>**Mutants:** X3 (check reads an empty history) killed; X1 killed; the builder's "writer chain unchecked" killed | **Closed** |
| LOW-2: W2 and W10 test gaps | Tests only | **W2 killed** by `test_the_per_identity_clock_check_refuses_a_night_that_crossed_the_margin`; **W10 killed** by `test_a_demotion_that_skips_a_rung_is_refused_by_the_writer_itself` | **Closed** |
| LOW-3: the D6 scan misses method-called literals (S1) | `_fold` now evaluates plain `str` methods with literal arguments, slices, indexes, `str()` and `*`. Each writer has the other's word fragments as foreign words. A writer may not build an INSERT's table at run time. The events table may appear only in a SELECT or in the writer's own INSERT. | **S1 (my exact helper) killed** by `test_only_two_writers_insert_size_events`; the scan self-tests include S1 verbatim. The whole backend passes with no exemption. Data built at run time remains the documented residual. | **Closed** |

## 2. OPS items 1-9 from the ES side

| # | Change | ES check | Result |
|---|---|---|---|
| 1 | Sidecar lot→share conversion, `contracts.TWS_VOLUME_LOT_SHARES` | **Decision path:** `shadow_service.IBKR_API_VOLUME_MULTIPLIER` is now that object, `Decimal("100")`, the same value and type as before. `PaperIBKRMarketData` therefore scales volumes exactly as before, so selection and sizing are unchanged.<br>**Sidecar:** only `DecisionSizing.size_pct_of_bar_volume` (decision_sizing.py:191-204) converts. It reads `decision.risk.intent.quantity` and changes neither the decision nor its payload; the call stays inside pipeline.py's `try/except`.<br>**Tests:** `test_the_realism_flags_are_evidence_only` (no production module but the sidecar reads a flag) and `test_sidecar_v2_fields_and_flags` (the ×1 row is still `shadow_accepted` at 3726 shares). | No effect on traded size |
| 2 | 7-night resend | `size_notify.night_items` and `unsent_items` are SELECT-only on a `mode=ro` connection; the delivery record is a file (`telegram-proposals.json`). No INSERT, UPDATE or `mode=rw` was added anywhere in `backend/trading` (grep of the added lines). | Cannot write a size |
| 3 | session_check WARN | A post-session reader | No runtime effect |
| 5, 6, 9 | Per-(token, identity) pages, `size_writer_night_failed`, silent night pages | `notify_service`, `overnight_watch` and `telegram_bot`: paging only. `nightly` now logs one CRITICAL when the whole writer fails, with no write. | No size effect |
| 4, 8 | Docs and texts | The promoter gains `approval_live`, handed in by nightly, and still imports no operations code. Only texts change; verdicts, statuses and the evidence stored are unchanged. | No size effect |
| 7 | Tests only | — | — |

**Pins:**
- Migration 18 is unchanged: 763db17ce5e6f958233882cfff38dfb4e3aa91d9459d357b89baedb76b24785f, the same as 27a9d34. size_schema.py, migrations.py and test_migration_freeze.py are not in 27a9d34..4788049.
- v1..v17 are identical to b5f2339.
- Bridge sha256 is b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4.
- config.py, manual_orders.py and sessions.py are unchanged, so imports_sha stays 7b1df1d4824e.

## 3. contracts.py and the bridge (coordinator's addendum)

**Measured.** I imported the bridge module (`scripts_local/paper_bridge.py`, module level only, `main()` not run) in an extract of 4788049, then ran its `backend_modules()` and its three lazy import statements: sessions, config and manual_orders. This was sandboxed, with the fake HOME and no network (`bridge-closure.txt`, `bridge-importtime.log`).

**What the bridge process loads:** 29 backend files. Four are covered by pins: the bridge itself (sha) and the 3 pinned imports. The other **25 are unpinned:**

`db/__init__.py`, `db/core.py`, `trading/__init__.py`, `trading/authority.py`, `trading/contracts.py`, `trading/execution/__init__.py`, `trading/execution/ibkr_paper.py`, `trading/execution/tws_paper_transport.py`, `trading/operations/__init__.py`, `trading/operations/paths.py`, `trading/operations/settings.py`, `trading/receipt_signing.py`, `trading/runtime.py`, `trading/scheduler/__init__.py`, `trading/scheduler/decision_keys.py`, `trading/scheduler/lease.py`, `trading/storage/__init__.py`, `trading/storage/broker_inbox.py`, `trading/storage/execution_ledger_schema.py`, `trading/storage/live_market.py`, `trading/storage/manager_schema.py`, `trading/storage/manual_orders_schema.py`, `trading/storage/migrations.py`, `trading/storage/schema.py`, `trading/storage/size_schema.py`.

**Bridge-loaded files the size layer changes** (b5f2339..4788049): exactly three.
- **`trading/contracts.py`:** +7 lines, only the `TWS_VOLUME_LOT_SHARES = Decimal("100")` constant and its comment.
- **`trading/storage/migrations.py`:** +13 lines; registers migration 18 and imports size_schema.
- **`trading/storage/size_schema.py`:** new; DDL strings and constants only.
- The import path is `manual_orders → db.core → migrations → contracts / size_schema` (importtime log).
- Not loaded by the bridge: `decision_sizing`, `shadow_service`, `identity_sizing`, `size_writer`.

**(a) The bridge's behaviour is unchanged.**
- The bridge never reads the constant: `TWS_VOLUME_LOT_SHARES` is not in paper_bridge.py, and its only users are `shadow_service.py` and `decision_sizing.py`, neither of which the bridge loads.
- The import has no side effect: it is one module-level `Decimal` literal assignment, and no model or function in contracts.py changes.
- The bridge never runs migration code:
  - its runtime touches `db.core` only through `manual_orders`' `get_db` / `transaction`;
  - `apply_migrations` is reached only from `init_db`, which the bridge never calls;
  - paper_bridge.py has no `migrat` / `init_db` / `inspect_schema` call, only a comment at :954.
- Loading migrations.py evaluates migration 18's SQL strings and nothing more; checksums are computed lazily.
- `tests/test_bridge_imports.py` (in the full suite) still imports the bridge's lazy statements cleanly.

**(b) Recommendation: keep the constant where it is. Severity INFO; this is the pre-existing N18 LOW-6 gap, now measured.**
- Moving the constant to `decision_sizing.py` would not make "this layer changes no bridge-loaded byte" true: migration 18 has to be registered in `migrations.py`, and that pulls `size_schema.py` into the bridge, so two bridge-loaded files change regardless.
- `contracts.py` is also the right owner: the unit belongs beside `CompletedBar.volume`, which carries it, and `shadow_service` and MOMO's `volume_multiplier` share it, as the test pins.
- The real gap is that 25 bridge-loaded modules are unpinned.
- The fix belongs in a later bridge-pin layer with its own ES review (it changes imports_sha):
  - pin the measured closure: every backend file the bridge process loads, found by a probe like `bridge-closure.txt`;
  - or have `deploy check` print the closure's digest beside `imports_sha` so a verdict line can carry it.
- It is not needed for L-sizes: each of the three changed bridge-loaded files is reviewed here, and none changes bridge behaviour.

## 4. Mutants (fresh extract of 4788049)

Test set: 15 files, 635 tests + 1 skip; baseline green.

| # | Mutant | Result | Killed by |
|---|---|---|---|
| W2 | per-identity session-started check dropped | killed | test_the_per_identity_clock_check_refuses_a_night_that_crossed_the_margin |
| W10 | writer's own one-rung demotion check dropped | killed | test_a_demotion_that_skips_a_rung_is_refused_by_the_writer_itself |
| S1 | unused writer helper records an approval from method-called literals | killed | test_only_two_writers_insert_size_events |
| X1 | writer chain check stamps the candidate at the session start | killed | 22 tests (e.g. the failed-night resend and the step-down tests) |
| X2 | writer chain check accepts a candidate that is not in force at the session | survived, **equivalent** | `_session_started` refuses that stamp first in the same transaction, so the candidate (effective `plan.session`, written ≥30 min before its start) is always in force once its chain is valid |
| X3 | writer chain check reads another account mode's history (always empty) | killed | test_a_step_down_sharing_a_session_start_with_an_earlier_event_is_never_written (+2) |

Result: 5/6 killed, and the survivor is equivalent. Builder's sweep: 167/167, as logged.

**Full backend suite on the 4788049 extract** (with the `.venv` symlink): **3866 passed, 2 skipped**, exit 0. The extra skip is the extract's missing `.git`; 3866 + 2 = 3868, the builder's 3867 + 1.

## 5. Suggested ledger line

```
2026-10-23T07:33:25+03:00 | P3-C 4788049c24b58a4ad8e205fbfedf8457f8ee90a8 verdict=GO scope=P3-C,P3-E,P3-F,N19a,N19a2,N19b-es,N19b-es2 bridge_sha=b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 imports_sha=7b1df1d4824e migration=18 | reviewer N19b-ES-2: re-read of 27a9d34..4788049 (fix 3); N19b-ES LOW-1 closed: the writer runs the one read rule on its own history plus the candidate inside its transaction and refuses a step-down that would break the chain (the MOMO repro is refused Monday night, written Tuesday night, MOMO stays x0.5); LOW-2 closed (W2, W10 killed); LOW-3 closed (the scan folds string methods on literals, S1 killed); OPS items 1-9 change no traded size and write no size (IBKR_API_VOLUME_MULTIPLIER the same Decimal 100, the sidecar reads the decision only, the resend is SELECT-only on mode=ro, the rest pages or texts); migration 18 unchanged 763db17c, v1..v17 unchanged, bridge and imports pins unchanged; bridge process closure measured: 29 backend files, 25 unpinned, of which this layer changes contracts.py (one constant the bridge never reads), migrations.py and size_schema.py (never executed by the bridge): behaviour unchanged, INFO, pin the closure in a later bridge layer (N18 LOW-6); no HIGH, no MEDIUM, no new LOW; my mutants 5/6 killed (X2 equivalent), builder's 167/167 as logged; suite 3866 passed 2 skipped | docs/reviews/2026-10-23-sizes-n19b-es2-review.md
```
