# Post-Reconciliation Repository Disposition

**Captured:** 2026-08-16/17 (Asia/Jerusalem)  
**Frozen checkout:** `/Users/salomon/aiautomation`  
**Frozen branch:** `feat/dark-theme-foundation`  
**Frozen HEAD:** `f77023a9a14fcec578d05a208f00febee8b62d14`  
**Integration branch:** `integration/post-reconciliation`  
**Integration base before this disposition:** `58faa0f2e2b722ecb57256113b17b0cda54ee01a`  
**Release state:** LIVE **NO-GO**  
**Scope:** all 77 entries from the frozen `git status --porcelain` snapshot

## 1. Preservation Evidence

The original tree was frozen before cleanup. This manifest is a disposition
record, not the sole recovery mechanism.

| Artifact | Value |
|---|---|
| Full checkout archive | `/Users/salomon/aiautomation-freeze-20260816-232046.tar.gz` |
| Full checkout SHA-256 | `9f027877cd44f96a7fb46dae27b7f9555ee8a66297a5e79e9e5aece6cba12b06` |
| Inventory directory | `/Users/salomon/aiautomation-freeze-20260816-232046-inventory` |
| Local recovery marker | `wip/freeze-20260816` at `f77023a9a14fcec578d05a208f00febee8b62d14` |
| Immutable remote baseline | `origin/feat/screener-markets-baseline` at `f77023a9a14fcec578d05a208f00febee8b62d14` |
| Nested all-ref bundle | `/Users/salomon/aiautomation-nested-archive-20260816/aiautomation-all-refs.bundle` |
| Nested bundle SHA-256 | `d58e6442b00e61f20a3de02f6a0d21ed66eef64f60fc9f4a97e60296ac451f4a` |
| PR3 source archive | `/Users/salomon/aiautomation-nested-archive-20260816/pr3-de380a1-source.tar.gz` |
| PR3 archive SHA-256 | `4c9e77decbdb8a8acafa3873fecb6ec79e25e688d1db983e61670840bba7c856` |
| Tracked dirty patch | `/Users/salomon/aiautomation-disposition-20260817/frozen-tracked-changes.patch` |
| Tracked patch SHA-256 | `1633cf790863cccb0b6cf3954ce4834818a807fd20afce6fcf309b94044179e0` |
| Archived untracked files | `/Users/salomon/aiautomation-disposition-20260817/untracked` (26 files; two nested repositories archived separately) |
| Archived working copies | `/Users/salomon/aiautomation-nested-archive-20260816/working-copies/{main,pr3}` |

`git bundle verify` confirms that the nested bundle contains complete history
for 19 refs, including `main` at `faabe81a255f`, the linked PR3 branch at
`de380a163dff`, all remote refs, the tag, and both worktree HEADs.

The two execution lock files were checked with `lsof` before cleanup. Neither
had an owning process. Their stored PIDs were stale historical text, not active
locks.

Cleanup verification completed after the reviewed salvage commits were pushed:

- `/Users/salomon/aiautomation` reports zero `git status --porcelain` entries;
- the post-clean status file is empty and has the SHA-256 of an empty file,
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`;
- both archived nested working copies remain clean and resolve to `faabe81`
  and `de380a1`; and
- no deferred file was destroyed to manufacture a clean checkout.

## 2. Disposition Vocabulary

- **COMMIT** — the exact change, or a reviewed safe subset of a mixed file, is
  preserved on the integration line. A duplicate dirty copy is then restored.
- **DISCARD** — the dirty change is rejected from the canonical tree. Recovery
  remains possible from the verified freeze archive.
- **DEFER** — the artifact is removed from the canonical checkout but retained
  in the verified archive. It has a named owner and review date below.

Totals: **43 COMMIT**, **20 DISCARD**, **14 DEFER**. Every frozen status entry
appears exactly once in the table.

## 3. Exact 77-Entry Disposition

| # | Frozen status path | Decision | Integration treatment / rationale |
|---:|---|---|---|
| 1 | `.claude/settings.local.json` | DEFER | Machine-local permissions and absolute paths stay outside Git. Owner: repository owner. Review by 2026-09-01. |
| 2 | `Dockerfile` | COMMIT | Preserve the reviewed one-worker default; multi-process broker ownership remains unsafe. |
| 3 | `LEARNED.md` | COMMIT | Preserve verified safety, audit, shell, API, state, and tooling lessons. |
| 4 | `README.md` | DISCARD | Reject the unverified one-command paper-start claim. Documentation must follow a tested runtime contract. |
| 5 | `backend/Dockerfile` | COMMIT | Preserve the reviewed one-worker default. |
| 6 | `backend/ai_rule_lab.py` | COMMIT | Already preserved byte-for-byte by containment commit `76f62f0`; do not recommit the dirty duplicate. |
| 7 | `backend/autopilot_api.py` | COMMIT | Surgically preserve `ibkr_port=cfg.IBKR_PORT` in runtime matrix validation. |
| 8 | `backend/config.py` | DISCARD | Reject `OFFLINE_FALLBACK`; fabricated broker/account truth is prohibited. Database-path lifecycle is fixed separately in Track A5. |
| 9 | `backend/db/core.py` | COMMIT | Stage 9A content is already in `76f62f0`; Track A5 replaces the stale path snapshot separately. |
| 10 | `backend/db/rate_limits.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 11 | `backend/health.py` | DEFER | Reject configuration-only trading readiness. Owner: backend/release engineering. Replace with dependency- and broker-truth readiness by 2026-08-30. |
| 12 | `backend/ibkr_client.py` | COMMIT | Surgically preserve the SIM and real-money connection fence; reject fabricated fallback account and positions. |
| 13 | `backend/metrics.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 14 | `backend/order_executor.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`; direct broker mutation debt remains guarded by an architecture tripwire. |
| 15 | `backend/routers/positions.py` | DISCARD | Reject fake-success account and position responses while IBKR is offline. |
| 16 | `backend/routers/status.py` | DISCARD | Reject offline-fallback status. The reviewed manual SIM/live connect fences are already in the containment merge. |
| 17 | `backend/startup.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 18 | `backend/tests/test_ai_rule_lab.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 19 | `backend/tests/test_auth_gaps.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 20 | `backend/tests/test_autopilot_mode_semantics.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 21 | `backend/tests/test_batch8_post_review.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 22 | `backend/tests/test_bot_health.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 23 | `backend/tests/test_direct_trade_gate.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 24 | `backend/tests/test_execution_idempotency.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 25 | `backend/tests/test_metrics_endpoint.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 26 | `backend/tests/test_orphan_reaper_and_rate_cap.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 27 | `backend/tests/test_rate_limit_cross_process.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`. |
| 28 | `backend/tests/test_startup_config.py` | COMMIT | Already preserved byte-for-byte by `76f62f0`; Track A5 strengthens DB-path isolation. |
| 29 | `dashboard/src/components/auth/AuthGuard.tsx` | DISCARD | Reject unconditional JWT persistence in `localStorage`; it weakens remember-me semantics and ADR 0008. |
| 30 | `dashboard/src/components/auth/LoginPage.tsx` | DISCARD | Reject unconditional token persistence and demo-login coupling. |
| 31 | `dashboard/src/components/layout/Header.tsx` | DISCARD | Reject fake fallback-state UX. Connection error UX can be reimplemented without fabricated state. |
| 32 | `dashboard/src/services/api/auth.ts` | DISCARD | Reject the dirty token-cache implementation because it couples request deduplication to persistent bearer storage. |
| 33 | `dashboard/src/store/botStore.ts` | DISCARD | Reject `offlineFallback` application state. |
| 34 | `docker-compose.yml` | COMMIT | Preserve the reviewed one-worker default. |
| 35 | `docs/DEPLOYMENT.md` | COMMIT | Preserve NO-GO and one-worker deployment warnings. |
| 36 | `docs/DEPLOYMENT_v4.md` | COMMIT | Preserve the blocked/historical safety annotation. |
| 37 | `docs/LIVE_FLIP_RUNBOOK.md` | COMMIT | Preserve the prominent BLOCKED/HISTORICAL annotation. |
| 38 | `docs/ULTRAPLAN_v4.md` | COMMIT | Preserve the superseded-for-release annotation. |
| 39 | `docs/ULTRAPLAN_v4_STATUS.md` | COMMIT | Preserve the historical warning; this file is not release truth. |
| 40 | `docs/operator_api_v4.md` | COMMIT | Preserve reviewed default-off and isolation semantics. |
| 41 | `docs/paper_review_protocol.md` | COMMIT | Preserve PAPER-evidence-only semantics and explicit LIVE prohibition. |
| 42 | `learning-log.md` | COMMIT | Preserve factual Stage 9A/9B history and limitations. |
| 43 | `sessions/dashboard-hardening-plan.md` | DISCARD | Discard the deletion and restore the tracked session file; repository policy prohibits deleting `sessions/`. |
| 44 | `sessions/phase-b-f7-01-auth-gap-analysis.md` | DISCARD | Discard the deletion; active ADRs still cite this analysis. |
| 45 | `sessions/phase1-dedup-fmtUSD.md` | DISCARD | Discard the deletion and restore the tracked session file. |
| 46 | `sessions/phase2-memoize-watchlist.md` | DISCARD | Discard the deletion and restore the tracked session file. |
| 47 | `sessions/phase3-dead-code-cleanup.md` | DISCARD | Discard the deletion and restore the tracked session file. |
| 48 | `sessions/phase4-encode-symbol-params.md` | DISCARD | Discard the deletion and restore the tracked session file. |
| 49 | `sessions/phase5-adaptive-polling.md` | DISCARD | Discard the deletion and restore the tracked session file. |
| 50 | `sessions/swing-screener-backend-plan.md` | DISCARD | Discard the deletion; roadmap and history still reference the work. |
| 51 | `.claude/settings.json` | DEFER | Local plugin/tool configuration is not yet a reviewed team policy. Owner: repository owner. Review by 2026-09-01. |
| 52 | `ROADMAP_TEAM_PLAN.md` | COMMIT | Governed plan and NO-GO source; it must be reviewable on an immutable SHA. |
| 53 | `aiautomation-pr3/` | DEFER | Clean linked reference worktree archived outside the canonical checkout. Owner: repository owner. Review by 2026-09-15. |
| 54 | `aiautomation/` | DEFER | Clean independent nested repository archived as a verified all-ref bundle. Owner: repository owner. Review by 2026-09-15. |
| 55 | `backend/tests/test_execution_architecture.py` | COMMIT | Preserve the exact direct-broker-mutation tripwire; the allowlist may only shrink. |
| 56 | `backend/tests/test_lifespan_safety.py` | COMMIT | Preserve process-lock, SIM isolation, and broker-fence regression coverage. |
| 57 | `backend/tests/test_paper_smoke.py` | DEFER | Reject configuration-derived readiness as smoke evidence. Owner: backend QA. Replace with dependency/broker truth by 2026-08-30. |
| 58 | `backend/trading_bot.db.execution.lock` | DISCARD | Generated stale lock with no owning process. |
| 59 | `docs/REPLAN_LIVE_TRADING_2026_08_02.md` | DEFER | Historical planning input contains superseded assumptions. Owner: release engineering. Reconcile by 2026-09-15. |
| 60 | `docs/ULTRAPLAN_v2.txt` | DEFER | Legacy plan retained only in the verified archive. Owner: product engineering. Review by 2026-09-15. |
| 61 | `docs/ULTRAPLAN_v5.3.2.1.md` | DEFER | Separate future plan must not silently enter the Phase 0 authority chain. Owner: product engineering. Review by 2026-09-15. |
| 62 | `docs/adr/0006-execution-authority-ownership-and-intent.md` | COMMIT | Preserve as Proposed; it does not authorize LIVE. |
| 63 | `docs/adr/0007-broker-protection-reconciliation-and-account-risk.md` | COMMIT | Preserve as Proposed; open acceptance evidence remains explicit. |
| 64 | `docs/adr/0008-security-data-and-release-boundary.md` | COMMIT | Preserve as Proposed; current demo auth is explicitly non-production. |
| 65 | `docs/evidence/` | COMMIT | Preserve the Stage 9A baseline and historical Phase 0 disposition with dirty-tree caveats. |
| 66 | `docs/phase_0_foundation.md` | COMMIT | Preserve as the executable release-foundation plan. |
| 67 | `docs/phase_1_live_stock_trading.md` | DEFER | Premature LIVE assumptions require reconciliation. Owner: release/risk engineering. Review by 2026-09-15. |
| 68 | `docs/phase_2_analytics_robustness.md` | DEFER | Future-phase reference retained in the archive. Owner: product engineering. Review by 2026-09-15. |
| 69 | `docs/phase_3_crypto.md` | DEFER | Future-phase reference retained in the archive. Owner: product/risk engineering. Review by 2026-09-15. |
| 70 | `docs/risk/` | COMMIT | Preserve the open residual-risk register; unsigned/open risks keep LIVE blocked. |
| 71 | `docs/security/` | COMMIT | Preserve the Stage 9A threat model and isolated-development assumptions. |
| 72 | `docs/testing/` | COMMIT | Preserve the pre-live fault matrix as required work, not completion evidence. |
| 73 | `handoffs/2026-05-14-phase1-wave1-review.md` | DEFER | Historical handoff caveats lack current tracking IDs. Owner: engineering management. Reconcile by 2026-09-15. |
| 74 | `handoffs/2026-07-27-stage-9a-live-safety-foundation.md` | COMMIT | Preserve the Stage 9A safety handoff with its explicit limitations. |
| 75 | `package-lock.json` | DISCARD | Empty generated root lockfile with no owning root package manifest workflow. |
| 76 | `scripts/start-paper.sh` | DEFER | Unsafe script binds broadly, mutates `.env`, and uses persistent state without proving isolation. Owner: deployment engineering. Replace by 2026-08-30. |
| 77 | `trading_bot.db.execution.lock` | DISCARD | Generated stale lock with no owning process. |

## 4. Duplicate Branch Determination

`origin/feat/dark-theme-foundation` and
`origin/feat/dark-theme-foundation-integration` are not duplicates:

- neither ref is an ancestor of the other;
- their merge base is `0a0d88c`;
- their tree hashes differ;
- stable patch IDs and unique commit sets differ; and
- open PR #10 depends on the integration ref.

Both refs therefore remain. Matching commit messages and timestamps were rebase
artifacts, not deletion evidence. The full command record is preserved at
`/Users/salomon/aiautomation-freeze-20260816-232046-inventory/branch-determination.md`.

## 5. Release Consequences

1. The canonical tree must contain no fake broker/account fallback data.
2. Broker/background ownership remains single-process until durable lease and
   fencing semantics are implemented and proven.
3. Proposed ADRs, risk registers, threat models, and fault matrices are
   governance inputs. Their presence is not acceptance evidence.
4. Deferred files have recovery evidence but are not release inputs.
5. The original worktree becomes clean only after the COMMIT entries are
   preserved on immutable branches and the archived DISCARD/DEFER entries are
   removed or restored according to this table.
6. Final acceptance still requires all backend and frontend gates on one clean,
   immutable integration SHA followed by a SHA-pinned re-audit.
