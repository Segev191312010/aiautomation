**VERDICT: GO for 27a9d3432ee65ef8fb7e0f0b616ca57fb302944c** (ops-p3-sizes, merged size layer: P3-C + P3-F + P3-E + fix 2; tree 7118aa4095b2b11ea246209b846dbd1f69b3968c).

Execution-safety (ES) result:
- No HIGH and no MEDIUM.
- I found no path that makes the robot trade a larger size than intended, trade when it should block, or change a size outside D2/D6.
- No code at this head can raise a size at all: `PHONE_APPROVAL_LIVE` is False and `telegram_size_approval.py` does not exist. Every size is at or below its reviewed baseline, and the only writer moves sizes down.
- Three LOW findings and three informational notes (section 2).

# N19b-ES: execution-safety review of the merged size layer (27a9d34)

- **Reviewer:** N19a, now acting as N19b-ES. Independent: I did not write this code. The OPS reviewer covers delivery, notifier, Telegram, docs and deploy.
- **Run:** 2026-10-23 04:49-05:19 Israel time (report finished 2026-10-23T05:19:16+03:00). No run fell in 13:05-13:50 or 14:50-23:15.
- **Setup:**
  - Every run used a fresh `git archive 27a9d34` extract under `sandbox-exec -f ~/autotrader-work/sandbox-n19.sb`, with `HOME=~/autotrader-work/review-n19/fakehome`.
  - No port, launchctl, live DB, logs, Keychain, worktree, ledger or commit was touched.
- **Evidence** (under `~/autotrader-work/review-n19/n19b-es/`):
  - `sql/attacks.out`, `sql/fix1-attacks.out`, `sql/collision.out`
  - `mut-run.log`, `mut/results.jsonl`
  - `full-suite.log`

## 1. What I verified

### The shared read rule and its callers

`identity_sizing.size_from_events` (identity_sizing.py:380-428) is pure. It fails closed in this order:
1. no ceiling row;
2. a name the row does not cover;
3. no baseline (→ `identity_sizing_missing`);
4. a broken chain;
5. the in-force selection;
6. above the ceiling.

**Chain check.** `_chain_problem` (:322-356) now places each event at `_takes_effect_at`: the later of
- the first session on or after its effective date, and
- the first session that starts after `created_at`.

It refuses two events at one start. Any calendar error is a chain problem, never a raise.

**In-force selection.** It uses `effective_session <= session_date AND created_at < starts_at`. For a session date this is exactly `takes_effect_at <= session_date`, because session starts are monotonic. The chain makes take-effect starts strictly increasing in id order, so the events in force are a prefix and the size is the last one's `to_mult`.

**Callers** (all three use `size_from_events` with `trading_day_start` of the session):
- `session_identity_multiplier` (:442-516; the 07:55 guard stays at :477; v18 pending gives None);
- `size_in_force` (:538-562; raises `SizeNotInForce`, never falls back to the baseline);
- `size_history.size_in_force_from_events` (:493-512; factor None means "unknown", never the baseline).

`shadow_service.py` is unchanged since 857aceb.

**Restart same day.** A restart gives the same size or None. It can never give a different size: only a night write can change the chain.

**Mutants killed:** R1-R6.

### Fail-closed paths re-proven on the merged head

- The SQL battery (50 + 20 attacks) re-run on 27a9d34's migration 18. Every refusal from fix 1 holds:
  - REPLACE, OR IGNORE/ABORT/FAIL/ROLLBACK, upserts, `INSERT…SELECT`;
  - rowid, `_rowid_` and oid; ids 0 and -1; a NULL key.
- The new horizon CHECK refuses events effective 2028-06-01 and 2027-10-29 (written 2026-10-14).
- An event dated 2001 with a 2001 `created_at` passes the DB, but the reader now returns None. It does not raise, which closes N19a-2 LOW-1.
- A raise written past the CHECKs via `ignore_check_constraints` ([45]) is refused by the reader: VWAP reads None.

### size_writer.py (P3-E + fix 1 + fix 2)

**Write paths.** Four literal plain INSERTs:
- `_STEP_DOWN_EVENT`, with kind `'step_down'` and mode `'paper'` hard-coded;
- `_PROPOSAL`;
- `_DECISION`;
- `_EVALUATION`.

There is no UPDATE, DELETE, REPLACE or conflict clause.

**Transactions and clock.**
- Each identity's night is one `BEGIN IMMEDIATE` transaction, with one `_utc_now()` reading taken after the lock (:546-616).
- `_session_started` is checked globally (:709), per identity (:560) and before a fence (:660), each against the writer's own clock and the 30-minute margin.
- `plan_night` picks the first session that starts more than 30 minutes ahead.
- Stamps are `isoformat()` UTC, so they pass `_utc_exact`. `plan.session` is an XNYS session, so it passes the weekday and ≤14-day horizon CHECKs.

**It cannot make a step up or a size increase.** I tried:

| Attempt | What stops it |
|---|---|
| Wrong `current` | `_check` (:619) requires the promoter's `current` to equal `size_in_force` for `plan.session` (W1 killed). |
| Stale `current` | DB `_from_current` and `_after_previous` refuse; the identity is skipped and nothing is written. |
| `from_mult` below the store's actual | DB `_from_current` refuses; a first event off the baseline is refused by the reader's chain check. |
| An event labelled down that goes up | Proposal and event CHECKs refuse (attack [43]); `_check` also wants exactly `step_down(current)` (W10 survives, DB backstop). |
| A re-used or expired proposal | The writer only ever uses the proposal id it just inserted, and the DB has one event per proposal. |
| A self-approval | No `approved` / `owner_telegram` / step-up event anywhere in the writer: AST test plus D6 rule 5; see N19b-LOW-3 for the residual. |

**Other writer checks:**
- **Idempotency:** the evaluation row is checked inside the transaction, and the fence path checks it first too.
- **`DEMOTION_MIN_NEW_TRADES = 5`:** counts trades since the latest event's session (W3, W4, W12 killed).
- **Demotion below ×0.25:** goes through `sentinel.fence_identity`, which obeys `SENTINEL_FENCES_LIVE`. An owner's resume holds until a new trade (W11 killed).
- **Supersede / expire:** new decision rows only.

### Promoter

- `collect_proposals(current=None)` reads the store and fails closed (W7 killed).
- `nightly` hands over only the identities whose size could be read.
- The purity AST test passes. Note: `trading_day_start` lazily imports `trading.operations.db_admin`, which is read-only planning code.

### D6 rights

- `WRITER_RIGHTS` / `_beyond_rights` (test_size_store.py:1355, :1499-1560): the nightly writer may insert only decisions that are `?` or superseded/expired/auto_applied/fenced/would_fence, events only with the literal `'step_down'`, and no event id.
- The merged tree passes.
- `size_notify` is `mode=ro`.
- `size_writer` is imported only by `nightly.py`.
- No production module holds `DROP TRIGGER`, `writable_schema`, `ignore_check_constraints` or `recursive_triggers`.
- Residual: N19b-LOW-3.

### Pins

- bridge sha256 b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 (= `REVIEWED_BRIDGE_SHA256`);
- config.py, manual_orders.py and sessions.py hashes unchanged, so imports_sha is 7b1df1d4824e;
- v1..v17 checksums identical to b5f2339;
- v18 = 763db17ce5e6f958233882cfff38dfb4e3aa91d9459d357b89baedb76b24785f (= `PINNED_MIGRATIONS`);
- no bridge, pinned-import or supervisor file in `b5f2339..27a9d34`.

### Pipeline (P3-F)

- Only the sidecar call gains `bars`/`quote`, inside the existing `try/except` (pipeline.py:648-697). The decision is read, never changed.
- Wording changes only.

### Tests

- Size-related set: 590 passed, 1 skipped.
- Full backend suite on the extract (with the `.venv` symlink): **3833 passed, 2 skipped**. The extra skip is the extract's missing `.git`. 3833 + 2 = 3835, which equals the builder's 3834 + 1.
- Builder's combined sweep: 114/114 as logged (`mutation-fix2.log`).

## 2. Findings

| ID | Sev | file:line (27a9d34) | What | Failure scenario | Fix |
|---|---|---|---|---|---|
| N19b-LOW-1 | LOW (fails closed, but permanent) | size_writer.py:546-616, :619-631 (no post-write chain check); identity_sizing.py:322-356 | The writer checks `current` but not whether its new event leaves a chain the reader accepts. Suppose an earlier event of the identity was written mid-session for its own day (a writer defect). That event takes effect at the next start. The nightly step-down for that same next session passes every DB rule, because the DB compares effective dates, not session starts. Afterwards the reader refuses the whole chain (two steps at one start). | **Reproduced** (`sql/collision.out`):<br>• MOMO 1→0.5 written Mon 10-19 10:30 ET for Monday; the child reads x1 Monday and ignores it, as designed<br>• the writer's `current` for Tuesday is 0.5, so it writes 0.5→0.25 effective Tue, and the DB accepts it<br>• MOMO now reads `size_event_chain_broken` for Mon, Tue, Wed, Nov 2 and every later session, so MOMO is **blocked for good**<br>• the append-only store can only be fixed by a DB restore or a reviewed migration<br>This needs a prior defect: the writer's own 30-minute margin keeps it from writing a mid-session row. | Before inserting, the writer runs `size_from_events(events + [candidate])` for `plan.session` (or refuses while any event takes effect at or after `plan.session`), then skips with a CRITICAL. Write the repair (re-baselining) procedure into the operator docs. |
| N19b-LOW-2 | LOW (test gaps; backstopped) | size_writer.py:560 (per-identity `_session_started`); :629 (writer's own one-rung demotion check) | **W2 survives:** dropping the per-identity session-started check passes all tests; only the global check at :709 is tested. A slow night would need more than 30 minutes between the two checks. If it happened, the row would be ignored that session (CRITICAL) and could then trigger LOW-1.<br>**W10 survives:** dropping `_check`'s one-rung demotion check passes, because the promoter always proposes `step_down(current)` and the DB's one-rung CHECKs would refuse anything else. | Not a size increase either way. | Add a test where the clock crosses the margin between the global and the per-identity check, and one where a forged `DEMOTION_PROPOSED` names a wrong rung (`proposal_not_one_rung`). |
| N19b-LOW-3 | LOW (scan residual; review is the control) | tests/test_size_store.py:1420-1560 (`_fold`, rules 4/5); test_size_writer.py:531 | **S1 survives:** an unused helper in size_writer.py that inserts a decision of `"appro".strip() + "ved"` with actor `"owner".strip() + "_telegram"` passes the D6 scan and the writer's AST test. `_fold` does not fold method calls on literals; the AST test looks for `'approved'` and for `INTO autotrader_size_events` in one constant.<br>The same trick on the table name (`"autotrader_size_events".strip()`) hides a step-up event INSERT from rule 5. `'step_up'` is not one of the nightly writer's foreign words. | A reviewed writer module could hold a statically written (not run-time-data) approval and step-up path that the tripwire does not see. The DB trusts the actor string. Today no code calls such a path, and none exists. | **`_fold`:** fold `str` methods (`strip`, `lower`, `upper`, `replace`, `casefold`) on foldable bases.<br>**Nightly writer:** add `step_up` and the fragments `approv` / `owner_tel` to its foreign words; flag any `size_events` fragment outside `_STEP_DOWN_EVENT` and SELECTs.<br>D6 stays a review control. |

Informational:
- **I-1.** No code at 27a9d34 can move a size up. Every reading is at or below its reviewed baseline, so the step-up path's ES review is P3-D's (N20).
- **I-2.** The no-cascade count uses the promoter's window. A window reset lowers the count, so a demotion can be delayed, never hastened. Larger sizes cannot result, but a needed step-down may wait. The Sentinel and v7c stay in front.
- **I-3.** `ORDER BY created_at, proposal_id` sorts correctly across the two stamp shapes, with and without a 6-digit fraction: at the same second `+` sorts before `.`.

## 3. My mutants

**Setup:**
- Each mutant ran on a fresh `git archive 27a9d34` extract.
- Test set: 14 files, 590 tests + 1 skip; baseline green.
- Harness: `scripts/mutate_n19c.py`.

| # | Mutant | Result | Killed by (first) |
|---|---|---|---|
| R1 | takes-effect: written exactly at a start counts as before it (`>=`) | killed | test_a_mid_session_event_and_the_next_sessions_never_share_a_start |
| R2 | an unplaceable calendar date placed at its own date (no refusal) | killed | test_a_date_the_calendar_cannot_place_is_refused_never_raised |
| R3 | ceiling compared with the baseline, not the size in force | killed | test_a_size_above_its_ceiling_fails_closed |
| R4 | chain not checked for an identity with one event | killed | test_the_event_chain_starts_at_the_baseline (+6) |
| R5 | session read: a broken chain falls back to the baseline | killed | test_the_event_chain_starts_at_the_baseline, test_one_step_per_session_start_not_per_date (+4) |
| R6 | `size_in_force` (writer's current): a broken chain reads as the baseline | killed | test_one_identity_never_blocks_the_others (+5) |
| W1 | writer: `current` need not equal the store's size in force | killed | test_an_unreadable_night_writes_nothing |
| W2 | writer: per-identity session-started check dropped | **SURVIVED** | (N19b-LOW-2) |
| W3 | no-cascade gate off by one (6 trades) | killed | test_no_cascade_on_the_same_evidence |
| W4 | no-cascade gate counts from the FIRST event | killed | test_no_cascade_on_the_same_evidence |
| W7 | promoter: unreadable store → baseline `current` | killed | test_an_unreadable_fence_table_proposes_nothing |
| W10 | writer: demotion need not be one rung below current | **SURVIVED** | (N19b-LOW-2; DB CHECKs backstop) |
| W11 | owner's resume holds forever (never fenced again) | killed | test_demotion_below_bottom_fences_via_fence_identity |
| W12 | the demotion fence ignores the no-cascade wait | killed | test_no_cascade_on_the_same_evidence |
| S1 | unused writer helper records an owner approval from method-called literals | **SURVIVED** | (N19b-LOW-3) |

**Result:** 12/15 killed. No survivor raises a size or trades on a blocked size.

## 4. Suggested ledger line

```
2026-10-23T05:19:16+03:00 | P3-C 27a9d3432ee65ef8fb7e0f0b616ca57fb302944c verdict=GO scope=P3-C,P3-E,P3-F,N19a,N19a2,N19b-es bridge_sha=b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 imports_sha=7b1df1d4824e migration=18 | reviewer N19b-ES: execution-safety review of the merged size layer (P3-C 857aceb+ae090da, P3-F 9d07037, P3-E afd0cba+985afb9, fix 2 27a9d34); one read rule size_from_events behind the session read, size_in_force and size_history, fail-closed everywhere (07:55 guard, mid-session ignored, holidays and mid-session writes placed at their real start, calendar errors None never raised, restart same day same size or None); size_writer writes only paper step-downs from the store's current through four literal plain INSERTs, one transaction and one real UTC stamp per identity, 30-minute refusal (global, per identity, before a fence), idempotent per night, 5-new-trade gate, fence via sentinel.fence_identity obeying SENTINEL_FENCES_LIVE; no step-up path exists at this head (PHONE_APPROVAL_LIVE False, no approval module); SQL battery 70 attacks re-run on v18 763db17c (horizon CHECK refuses 2027/2028 dates); v1..v17 unchanged; bridge and imports pins unchanged; no HIGH, no MEDIUM; LOW-1 a step-down written for the session at which an earlier mid-session-written event takes effect passes the DB and blocks the identity for good (reproduced; fails closed; writer should pre-check the resulting chain); LOW-2 W2 and W10 test gaps (DB backstop); LOW-3 D6 scan residual: method-called literals hide an approval or a step-up statement in a writer (S1); my mutants 12/15 killed, builder's 114/114 as logged; suite 3833 passed 2 skipped | docs/reviews/2026-10-23-sizes-n19b-es-review.md
```
