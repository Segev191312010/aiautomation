# N17e — merge re-read of ops-p2 at 8fb9ea7 (P2.3 + P1-B.4 8675055)

- **Candidate:** branch `ops-p2`, head `8fb9ea75af324954830ed2f8786c150a2ae8689f`. Worktree clean.
  - `072f76a` merges `8675055` (P1-B.4, live since 2026-10-22 10:03 Israel) into `c2a89d1` (P2.3 2439b9f + the N17d docs).
  - `8fb9ea7` appends the DEPLOY P1-B.4 ledger line.
- **Merge base:** `8d22b57`.
- **Live HEAD:** `8675055c0b57200d395534568af8eae031f293e2` (the coordinator; the DEPLOY line is in the real deploys.log, read from a copy).
- **Written:** 2026-10-22 10:07-10:16 Israel, inside the allowed run window.
  - Read-only in the worktree.
  - Every run was on a `git archive 8fb9ea7` copy (`~/autotrader-work/review-n17/merge-8fb9ea7/`), sandboxed, with a fake HOME and `nice -n 19`.
  - No port, no launchctl, no autotraderctl verb. Nothing written under `/Users/salomon/aiautomation`.

## Verdict

**GO for `8fb9ea75af324954830ed2f8786c150a2ae8689f`.**

- **The merge is faithful.**
  - Every file equals one parent, except the one ledger file both sides changed. That ledger keeps all 11 added lines once each, each side in its own order, and in time order.
  - `SENTINEL_FENCES_LIVE = True`. The bridge pin `7d313fd075d2…` is unchanged.
- **The P2 tool needs nothing new for the parent 8675055.**
  - There is no literal sha in its code.
  - 8675055 changed only `sentinel.py` (and its tests), so the old code's dry-run lines are the ones the undo's text contract pins.
  - The Sentinel's fence rows are the same rows the tool already reads.
- **The fast-forward 8675055 → 8fb9ea7 is clean:** 8675055 is an ancestor of 8fb9ea7.
- **Gates on 8fb9ea7** (coordinator, not re-run):
  - backend 3487 passed, 1 skipped;
  - no known vulnerabilities;
  - dashboard 44 passed;
  - `=== ALL QUALITY GATES PASSED ===`.
  - **Docker gates were skipped in that run.**
- **No finding.** Two owner-facing notes for the Saturday L-P2 runbook are below.

## 1. Merge faithfulness

- **Every file equals one parent, except the ledger.** Each blob of `8fb9ea7` was compared with both parents (`git ls-tree -r` of 8fb9ea7, c2a89d1, 8675055 and the base 8d22b57):
  - no non-docs file equals neither parent;
  - the only file equal to neither is `docs/reviews/2026-10-15-ledger.md`;
  - no path was lost;
  - the only file changed on both sides since the base is that ledger;
  - no one-sided change was left out of the merge.
- **`git diff 8675055..8fb9ea7`** holds only P2's files: deploy_apply.py, deploy_check.py, cli.py, their three test files, the ledger (+5) and the four N17-series reports.
- **`git diff c2a89d1..8fb9ea7`** holds only 8675055's files:
  - sentinel.py and its three test files;
  - OPERATOR_PROMPT.md;
  - the N16 review and the P1-B.4 rehearsal reports;
  - the ledger (+8).
- **Ledger.** The base's 85 lines are a prefix of both sides and of the merge.
  - c2a89d1 adds 4 lines and 8675055 adds 7. The merge's 11 additions are exactly that multiset, with no duplicate, and each side keeps its own relative order.
  - `8fb9ea7` appends exactly one line, `2026-10-22T10:03:21+03:00 | DEPLOY P1-B.4 8675055 PREV_SHA=9558442… pin=7d313fd075d2 result=PASS | …`.
  - The merged ledger has **no time-order inversion** across all timestamped lines (13:22:28 … 15:44:06 on 10-21, then 01:35:00, 01:50:00 and 02:26:58 on 10-22, then 10:03:21).
- **Switch and pin.**
  - `sentinel.py:147` is `SENTINEL_FENCES_LIVE = True`.
  - `supervisor.py:238` is `REVIEWED_BRIDGE_SHA256 = "7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4"`.
  - `shasum -a 256` of the 8fb9ea7 bridge blob gives the same value.
  - `git diff 9558442..8fb9ea7 -- backend/scripts_local backend/trading/operations/supervisor.py backend/trading/operations/heartbeat.py` is empty.

## 2. The P2 tool against the new live parent 8675055

- **No literal sha.** `deploy_apply.py`, `deploy_check.py` and `cli.py` at 8fb9ea7 contain no 40-hex literal and no `9558442`/`8675055`/`7d313fd`. Every sha comes from git, `--expect-parent` or `--expect-pin`.
  - The tests' `9558442` strings are deploys.log parser fixtures (format only).
- **PREV = 8675055.** `prev_sha_problem` passes: it is 40 hex, a commit, and an ancestor of 8fb9ea7. `bridge_pin_at(8675055)` reads `7d313fd075d2…`, so the plan's old pin and the undo's expected `reviewed` line are right.
  - On Saturday `--expect-parent` must be `8675055c0b57200d395534568af8eae031f293e2`, not 9558442.
- **The undo's text contract (N17b-L3, `test_the_undo_line_texts_are_a_cross_version_contract`)** holds for 8675055 as for 9558442.
  - `git diff --stat 9558442..8675055 -- backend/` lists only sentinel.py and the tests of sentinel, notify and nightly.
  - So supervisor.py, paper_bridge.py and heartbeat.py, the producers of the reviewed, schema, session, sendable, fenced and heartbeat lines, are byte-identical. The pinned literals are what 8675055 prints.
  - The test's docstring names 9558442 as the oldest head, which is still true.
- **Fences with the Sentinel live.**
  - **The same rows.** The Sentinel writes the same `paper:<ID>` rows (`stopped=1`, sendable identities only, never the global row; N16 I-2) that the tool and the bridge already read by name and `stopped`; the reason text is irrelevant.
  - **The precheck.** `fenced_unexpected` requires `--expect-fenced` to equal the stopped rows ∩ the candidate's sendable set. A Sentinel fence standing on Saturday therefore refuses (78, nothing changed) until the owner passes `--expect-fenced <ID>`. The forward dry run then expects `bridge       CRITICAL fenced identities = <ID>`, which matches the real output (N17 Q6).
  - **The auto-undo.** It expects `plan.stopped_fences` (every stopped row, read once at the precheck) ∩ the old bridge's own printed sendable set. 8675055's bridge is the same file, so it prints exactly that (`test_the_automatic_undo_expects_every_stopped_fence_the_old_bridge_prints`).
  - **No fence changes mid-deploy.** The Sentinel runs in the notifier (07:55-16:15 ET), its nightly writer is `not_compared` (N16 I-5), and the robot is stopped during a deploy anyway, so no fence can appear between the precheck and the undo.
- **deploy_check** reports identity fence rows as notes only. Its `fence` check refuses only the global kill switch, which the Sentinel never writes.
- **Fast-forward.**
  - `git merge-base --is-ancestor 8675055 8fb9ea7` is true.
  - The changed paths are all deployable (`backend/`, `docs/`).
  - The self-change guard sees `deploy_apply.py`/`deploy_check.py`, so the verdict scope must list `P2`. The proposed line does, and `verdict_lines(…, scope=("P2",))` matches it, with label `P2` and `bridge_sha` read.
  - `launchd.py` and `db_admin.py` are unchanged 8675055..8fb9ea7, so the undo compares plist bytes as normal.
- **deploys.log.** Today's real hand-written line `2026-10-22T10:03:21+03:00 DEPLOY P1-B.4 8675055 PREV_SHA=9558442… pin=7d313fd075d2 result=PASS` (copy, 0 account-id hits) parses with `_DEPLOY_PASS`: short `8675055`, prev `9558442b4e0d`, pin `7d313fd075d2`. Its schema is `None`, so `deploy undo` would refuse to undo P1-B.4 itself (`undo_schema_unknown` → manual 6.3), as designed for hand-written lines.
  - L-P2's own PREV and DEPLOY lines will carry `schema=v16`.

## 3. Tests (on `git archive 8fb9ea7`, sandboxed, fake HOME, `nice -n 19`)

| Run | Result |
|---|---|
| `test_deploy_apply.py`, `test_deploy_check.py`, `test_autotraderctl.py`, `test_sentinel.py`, `test_nightly_evidence.py`, `test_runtime_fence.py` (10:08-10:11) | 428 passed |
| `test_notify_service.py` (10:11-10:13) | 144 passed |
| coordinator's full gates on 8fb9ea7 (`~/autotrader-work/gates/ops-p2-merge-p3a.log`, read, not re-run) | backend 3487 passed, 1 skipped; no known vulnerabilities; dashboard 9 files / 44 tests passed; Docker gates skipped; ALL QUALITY GATES PASSED |

## Findings

None (no HIGH, MEDIUM or LOW).

**Owner-facing notes for the Saturday L-P2 runbook.**
1. `--expect-parent 8675055c0b57200d395534568af8eae031f293e2` (live HEAD since 10:03 today).
2. If the Sentinel (or a phone `/halt`) leaves a fence standing on Saturday, the first run refuses `fenced_unexpected` and names the identities. Run it again with `--expect-fenced <ID>[,<ID>]`. Today's install ended with ORB_V2 resumed and the kill switch inactive.
3. The gates run above skipped Docker. If the install requires the Docker gates (AGENTS.md "Run Docker gates before shipping"), run them on the head the verdict names before Saturday.

## Proposed ledger line

```
2026-10-22T10:14:45+03:00 | P2 8fb9ea75af324954830ed2f8786c150a2ae8689f verdict=GO scope=P2,N17,N17b,N17c,N17d,N17e bridge_sha=7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4 | reviewer N17e: merge re-read of ops-p2 at 8fb9ea7 (P2.3 2439b9f + P1-B.4 8675055 merged in 072f76a, DEPLOY P1-B.4 ledger line in 8fb9ea7); every file equals one parent except the ledger, whose 11 added lines are all kept once, each side in order, no time-order inversion; SENTINEL_FENCES_LIVE = True; bridge pin 7d313fd075d2 unchanged (blob sha256 checked); no literal sha in deploy_apply/deploy_check/cli; 8675055 changed only sentinel.py, so the undo's cross-version line texts hold for the new parent; Sentinel fence rows are the rows the tool already reads (owner passes --expect-fenced when one stands; the auto-undo expects the stopped fences the old bridge prints); fast-forward 8675055..8fb9ea7 clean; Saturday install uses --expect-parent 8675055c0b57200d395534568af8eae031f293e2; targeted tests on an archive 428+144 passed; gates 3487 passed 1 skipped, no known vulnerabilities, Docker skipped, ALL QUALITY GATES PASSED | docs/reviews/2026-10-22-p2-merge-n17e-review.md
```
