# N18f: merge re-read of the v7c deploy-head candidate e4046a3 (the 8675055 condition)

v7c e4046a33133a verdict=GO bridge_sha=b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 imports_sha=7b1df1d4824e migration=17

Reviewer: N18. I am independent and did not write any of this code.

Candidate: `bridge-v7c` at `e4046a33133a30710621727e72282c12486ae90e`.
- Parents: ee5c70e (my N18e docs on 60236f6) and 8fb9ea7 (ops-p2).
- 8fb9ea7 is 072f76a, the merge of 8675055 into ops-p2, plus the DEPLOY P1-B.4 ledger line.
- Live HEAD is `8675055c0b57200d395534568af8eae031f293e2` (P1-B.4, Sentinel live, installed 10:03 Israel).

How I worked:
- I used a read-only `git` in the worktree and a `git archive e4046a3` copy under `~/autotrader-work/review-n18/n18f/`; the gates were read, not re-run.
- Tests ran sandboxed with a fake HOME at 10:09-10:14 Israel, inside the allowed window.
- I did not touch any port, launchctl or autotraderctl.
- The identifier scan of `git diff 8675055 e4046a3` and of my files found 0 real hits. The 3 regex matches are the tail of an upper-case test constant name (the "production migrations" map in test_migration_freeze), not ids.

## Verdict

**GO for e4046a3. The merge is faithful, and it closes the 8675055 condition kept open since N18.**

There is no HIGH, MEDIUM or LOW finding. One process point (P-1 below) needs the coordinator's attention before `deploy apply`: the verdict scope must list `P2`.

This is not the final L-v7c head, because P3-B3 is still unbuilt.

## 1. The merge is faithful

**Code.**
- The only non-docs paths that differ between 60236f6 and e4046a3 are the four files 8675055 changed since the merge base 8d22b57: `trading/operations/sentinel.py`, `tests/test_sentinel.py`, `tests/test_nightly_evidence.py` and `tests/test_notify_service.py`.
- All four are byte-identical to 8675055's blobs.
- bridge-v7c never touched them, since `git diff 8d22b57 60236f6` over these paths is empty.
- `docs/OPERATOR_PROMPT.md` equals 8675055's.
- The bridge, supervisor, manual_orders, config, sessions, manual_orders_schema, migrations, deploy_apply and deploy_check blobs are identical to 60236f6's.

**Sentinel.** `SENTINEL_FENCES_LIVE = True` (`sentinel.py:147`).

**Pins.**
- The bridge sha256 is `b40fa40b...f8e4`, equal to `REVIEWED_BRIDGE_SHA256` (`supervisor.py:241`).
- The `REVIEWED_BRIDGE_IMPORTS_SHA256` entries match the files.
- `imports_digest` gives `7b1df1d4824e`, unchanged.

**Ledger** (`docs/reviews/2026-10-15-ledger.md`; 94 lines in ee5c70e, 97 in 8fb9ea7, 102 merged).
- Every line of each parent, and of live 8675055, is present, in its parent's order.
- No line comes from neither parent, and no line is duplicated.
- All 68 timestamped lines are in non-decreasing time order. That includes the new `2026-10-22T10:03:21+03:00 | DEPLOY P1-B.4 8675055 ... result=PASS` as the last line.

**Ancestry.**
- 8675055 (live HEAD) and 60236f6 (the reviewed v7c code) are both ancestors of e4046a3.
- `deploy apply` from live can fast-forward.
- The deploy tool (`deploy_apply.py`, `deploy_check.py`, `launchd.py`, `db_admin.py`) equals e63a599's: the N17d-GO P2.3 code plus the P3-B0 hunks N18 reviewed. 8fb9ea7's copy equals c2a89d1's.

## 2. Sentinel-live and v7c

- **Entry gates.** Both are entry-only gates, and neither ever blocks an exit.
  - A Sentinel fence writes `paper:<ID>` rows. The bridge reads them in `process_decision` before the budget, so a fenced identity skips `identity_stopped:<ID>` (forwarded to the phone) before v7c is consulted.
  - v7c's account-level blocks (budget, unprotected, -2 %) stay in force for every other identity.
- **The bridge state the Sentinel reads.**
  - v7c keeps `paper-bridge-state.v5` and the bracket fields the Sentinel uses (`qty`, `entry`, `stop`, `realized_pnl`, `status`, `sent_at`).
  - v7c's budget skips are `skipped` brackets, which `_traded` excludes, so they never count toward a daily-loss or streak fence.
- **Sentinel blindness.** It needs 5 minutes without fresh state. v7c's longest tick holds (an owner sell's 10 s snapshot, a `/closeall` decide pass) are seconds.
- **Dry run and `--expect-fenced`.**
  - With the Sentinel live, a fence row may exist at deploy time. The bridge's preflight then prints `CRITICAL fenced identities = <IDs>` instead of `none`.
  - `deploy apply` must be given `--expect-fenced <IDs>` (P2's `dry_run_fenced`), or it refuses before anything is touched.
  - Runbook point for L-v7c: read `autotraderctl paper status` first and pass exactly the stopped sendable identities.
  - A CRITICAL line is not a `!!!` or `WARNING:` line, so it does not trip the bang or warning checks.
- **Undo (I-6).**
  - L-v7c is a migration deploy, so `deploy apply` never runs the automatic undo. A failure after the migrate halts, leaves the robot disabled, and prints the restore.
  - PREV will be 8675055, or the L-P2 head if that installs first. Both predate P3-B0, so `pinned_imports_count(prev)` is None and nothing would change.
  - I-6 stays a later-layer item for a future code-only deploy whose PREV is post-P3-B0 (deferred item 7 of N18e).

## 3. Process point P-1: the verdict scope must list P2

- e4046a3 changes `deploy_apply.py` and `deploy_check.py` relative to live 8675055, which does not carry ops-p2.
- `DeployOperator`'s self-change guard (`deploy_apply.py:1531`) refuses `deploy_tool_scope_missing` unless `--scope` lists `P2`.
- `verdict_lines` (`deploy_check.py:371`) then matches only verdict lines whose `scope=` lists every requested slice.
- A line with `scope=P3-B,N18,...,N18f` alone would therefore make `deploy apply --scope P3-B,P2` refuse for lack of a matching verdict.
- The P2 content of e4046a3 is the reviewed P2.3 (N17d GO, 2439b9f) plus the P3-B0 delta and its merge resolution (N18 check 7), all byte-identical since e63a599.
- I therefore propose the ledger line below with `P2` in the scope. If the coordinator prefers a separate P2 line, it must name e4046a3 and carry the same `bridge_sha=`, `imports_sha=` and `migration=` tokens.
- This holds whether or not L-P2 installs first: v7c's deploy tool still differs from ops-p2's by the P3-B0 hunks.

## 4. Tests

- **Targeted** (`git archive e4046a3`, sandboxed, fake HOME): `test_sentinel`, `test_nightly_evidence`, `test_notify_service`, `test_paper_bridge`, `test_deploy_apply`, `test_supervisor`. **880 passed in 318.90 s**, no failure, no skip (`n18f/targeted.log`).
- **Gates** (the coordinator's run, read, not re-run): `~/autotrader-work/gates/bridge-v7c-merge-p3a.log` (written 10:22 Israel; the log names no sha, and the worktree it ran in has been at e4046a3 since before 10:07): backend **3661 passed, 1 skipped**; pip-audit "No known vulnerabilities found"; diff check clean; dashboard typecheck and build ok; dashboard tests 9 files, 44 passed; **ALL QUALITY GATES PASSED** (Docker skipped). Identifier scan of the log: 0.

## Deferred items

The consolidated list in `docs/reviews/2026-10-22-v7c-n18e-review.md` (N18e) is unchanged by this merge: items 1-9 for code, 10-18 accepted, 19 the owner's IP/VEEV action. Item 20 (merge 8675055) is now done.

## Proposed ledger line

```
2026-10-22T10:23:00+03:00 | v7c e4046a33133a30710621727e72282c12486ae90e verdict=GO scope=P2,P3-B,N18,N18b,N18c,N18d,N18e,N18f bridge_sha=b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 imports_sha=7b1df1d4824e migration=17 | reviewer N18f: merge re-read of e4046a3 (ee5c70e + 8fb9ea7 = ops-p2 with 8675055 Sentinel live and its DEPLOY line); non-docs paths beyond 60236f6 are exactly 8675055's four (sentinel.py and three tests), byte-identical; SENTINEL_FENCES_LIVE = True; bridge sha b40fa40b..., imports_sha 7b1df1d4824e and every other pinned or deploy-tool blob unchanged; ledger: every line of both parents and of live kept, in order, 68 timestamps non-decreasing, no duplicates; 8675055 and 60236f6 ancestors (fast-forward from live); Sentinel and v7c are both entry-only gates, v7c skips never feed the Sentinel's loss/streak rules; L-v7c must pass --expect-fenced for any stopped fence; scope lists P2 because e4046a3 changes the deploy tool against live (P2.3 N17d GO + P3-B0 delta N18); targeted tests 880 passed; gates (coordinator) 3661 passed 1 skipped, ALL QUALITY GATES PASSED; P3-B3 still unbuilt, so not yet the final L-v7c head | docs/reviews/2026-10-22-v7c-n18f-review.md
```
