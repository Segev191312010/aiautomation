# Phase 0 Repository Disposition Manifest

**Captured:** 2026-08-02 (Asia/Jerusalem)  
**Work package:** Phase 0 / A — Preserve and classify  
**Canonical checkout:** `/Users/salomon/aiautomation`  
**Branch:** `feature/ultraplan-v4`  
**HEAD:** `0bde712c01f3cc16f45c1e36a21d2fcac7fa3f8a`  
**Release state:** LIVE **NO-GO**  
**Manifest status:** reviewed evidence; no cleanup or movement authorized by this document

## 1. Scope and method

This manifest classifies the complete initial tracked and untracked working-tree
snapshot before Phase 0 isolation. It was produced from read-only Git history,
blob, worktree, file-tree, import/reference, and nested-repository inspection.
No `.env` contents were read. No application lifespan, broker connection,
network fetch, order operation, file movement, deletion, staging, or commit was
performed.

Two audit corrections were added to `LEARNED.md` after the initial snapshot:
avoid zsh's special `path` variable and require `git rev-parse --verify` for
tree-path comparisons. Results from the two faulty intermediate comparisons
were discarded and rerun correctly.

The initial tracked delta was 56 paths with approximately 1,506 insertions and
1,066 deletions. The tree combines at least four provenance layers:

1. committed ULTRAPLAN v4 at `0bde712`;
2. uncommitted Stage 9A containment/governance work;
3. later uncommitted paper-startup/auth/offline-fallback work; and
4. local/generated/nested-repository artifacts.

Therefore this checkout is preservation evidence, not a clean implementation
base or release candidate.

## 2. Recovery anchors

| Anchor | Identity | What it preserves | Limitation |
|---|---|---|---|
| Current committed base | `0bde712c01f3` / tag `v4-legacy-reference` | Last committed v4 tree before dirty Stage 9A work | Missing all dirty-tree work |
| Stage 9A partial isolation | `76f62f0` / tag `v4-containment` | 22 tracked backend containment/test files | Omits Docker one-worker defaults, governance documents, some boundary code/tests, and other dirty-tree changes |
| Stage 9B containment branch | `f33545ff3f4b` / `origin/stage-9b-containment` | Stage 9A partial isolation plus execution-lease/fencing work | Not a complete Stage 9A snapshot; its checked-out worktree is also dirty |
| Nested reference main | `faabe81a255f` / nested `main` | Nested reference frontend main branch | Unrelated Git history to canonical application |
| Nested PR3 reference | `de380a163dff` / nested `fix/ux-and-trading-automation` | Unique nested frontend review commit | Unrelated history; linked to nested main's Git metadata |
| Canonical Git history | current root repository | Every tracked deletion below | Does not preserve untracked files or dirty hunks |

### Critical baseline warning

Neither `v4-containment` nor `stage-9b-containment` is the complete Stage 9A
tree. Both Dockerfiles and Compose at that isolation point still default to two
workers, and the branch omits dirty-tree broker-boundary material including the
working-tree `IBKRClient.connect()` SIM/real-money check and
`test_lifespan_safety.py`. Work Package B must start from `f33545f` only with a
reviewed, hunk-level recovery matrix for these omissions. It must not label the
branch complete merely because it is committed.

## 3. Duplicate and reference directories

| Path | Classification | Evidence | Final Phase 0 disposition | Recovery procedure |
|---|---|---|---|---|
| `aiautomation/` | nested repository/reference | Clean independent repository, 5.6 MB; `main` at `faabe81`; owns PR3 worktree metadata; no merge base with canonical app; no active build/source dependency | Preserve in place during Gate A. Before any later move, create and verify one all-ref Git bundle, then archive together with PR3 outside the canonical checkout | Remote ref plus verified all-ref bundle; record bundle checksum |
| `aiautomation-pr3/` | nested repository/reference | Clean linked worktree, 127 MB (mostly `node_modules`); `fix/ux-and-trading-automation` at `de380a1`; `.git` points into `aiautomation/.git`; no active dependency | Preserve with `aiautomation/`; never move independently; omit regenerated `node_modules` from archive | Common-repository bundle plus optional `git archive de380a1`; dependencies recover with `npm ci` |
| `stocksdashboard/` | nested repository/reference | 80 KB empty Git repository; unborn `main`; no commits, refs, objects, or worktree files; already root-ignored | Keep until owner-approved cleanup; metadata-only archive is optional | Record remote/config; no source payload exists |
| `frontend/` | tracked legacy reference | Three tracked files, 116 KB, unchanged; FastAPI still mounts it at `/static` and serves `/trading`; not copied into current containers | **Retain read-only in Stage 0**. Moving it now would break the local `/trading` route | Canonical root Git at `0bde712` |

The nested React trees have path-unique code, but path uniqueness does not imply
missing canonical functionality. PR3's unique commit adds chart, alert, rules,
settings, screener, and mock-backend files while the canonical dashboard has
substantial newer equivalents. Salvage, if ever desired, must be a separate
review—not a Phase 0 merge.

## 4. Modified tracked paths

### 4.1 Local configuration

| Path | Classification | Disposition | Recovery |
|---|---|---|---|
| `.claude/settings.local.json` | local configuration | Preserve untouched; exclude from Stage 0 commits and diff attribution | Current local file plus committed ancestor; contents intentionally not audited here |

### 4.2 Stage 9A containment/governance paths

These paths are attributable to the Stage 9A containment wave, although several
were never included in commit `76f62f0`. Work Package B must recover them by
reviewed hunks, not by copying the entire dirty file blindly.

| Path | Classification | Disposition / caveat |
|---|---|---|
| `Dockerfile` | Stage 9A intended change | Recover one-worker default after review; absent from containment commit |
| `LEARNED.md` | Stage 9A + Phase 0 governance | Preserve Stage 9A async-fence rule and Phase 0 audit corrections |
| `backend/Dockerfile` | Stage 9A intended change | Recover one-worker default after review; absent from containment commit |
| `backend/ai_optimizer.py` | Stage 9A intended change | Proposal-only scheduler; current blob matches Stage 9B branch |
| `backend/ai_rule_lab.py` | Stage 9A intended change | Proposal-only enforcement support; current blob matches Stage 9B branch |
| `backend/autopilot_api.py` | Stage 9A intended change | Runtime LIVE fence; omitted from partial containment commit; review hunk |
| `backend/db/core.py` | Stage 9A intended change | Current blob matches partial Stage 9A isolation, not later lease schema |
| `backend/db/rate_limits.py` | Stage 9A intended change | Cross-process rate limiter; current blob matches Stage 9B branch |
| `backend/main.py` | Stage 9A intended change | Current blob matches partial Stage 9A isolation; Stage 9B contains later ownership work |
| `backend/metrics.py` | Stage 9A intended change | Default-deny/bounded-label metrics; current blob matches Stage 9B branch |
| `backend/order_executor.py` | Stage 9A intended change | Current blob matches partial Stage 9A isolation; known direct broker calls remain |
| `backend/routers/status.py` | mixed Stage 9A + later paper work | Recover Stage 9A connect fence only after hunk review; quarantine offline-fallback hunks |
| `backend/startup.py` | Stage 9A intended change | Current blob matches partial Stage 9A isolation; retain LIVE/config/topology validation |
| `backend/tests/test_ai_optimizer.py` | Stage 9A intended test | Current blob matches Stage 9B branch |
| `backend/tests/test_ai_rule_lab.py` | Stage 9A intended test | Current blob matches Stage 9B branch |
| `backend/tests/test_auth_gaps.py` | Stage 9A intended test | Current blob matches Stage 9B branch |
| `backend/tests/test_autopilot_mode_semantics.py` | Stage 9A intended test | Current blob matches Stage 9B branch |
| `backend/tests/test_batch8_post_review.py` | Stage 9A intended test | Current blob matches Stage 9B branch |
| `backend/tests/test_bot_health.py` | Stage 9A intended test | Current blob matches Stage 9B branch |
| `backend/tests/test_direct_trade_gate.py` | Stage 9A intended test | Current blob matches Stage 9B branch |
| `backend/tests/test_execution_idempotency.py` | Stage 9A intended test | Current blob matches partial Stage 9A isolation; later Stage 9B differs |
| `backend/tests/test_metrics_endpoint.py` | Stage 9A intended test | Current blob matches partial Stage 9A isolation; later Stage 9B differs |
| `backend/tests/test_orphan_reaper_and_rate_cap.py` | Stage 9A intended test | Current blob matches partial Stage 9A isolation; documents unsafe reaper |
| `backend/tests/test_rate_limit_cross_process.py` | Stage 9A intended test | Current blob matches Stage 9B branch |
| `backend/tests/test_startup_config.py` | Stage 9A intended test | Current blob matches Stage 9B branch |
| `docker-compose.yml` | Stage 9A intended change | Recover one-worker default after review; absent from containment commit |
| `docs/DEPLOYMENT.md` | Stage 9A governance | Preserve NO-GO and one-worker warnings; current deployment document also has unrelated legacy defects |
| `docs/DEPLOYMENT_v4.md` | Stage 9A governance | Preserve blocked/historical safety annotation |
| `docs/LIVE_FLIP_RUNBOOK.md` | Stage 9A governance | Preserve prominent BLOCKED/HISTORICAL annotation |
| `docs/ULTRAPLAN_v4.md` | Stage 9A governance | Preserve superseded-for-release annotation |
| `docs/ULTRAPLAN_v4_STATUS.md` | Stage 9A governance | Preserve historical warning; do not treat stale status as code truth |
| `docs/operator_api_v4.md` | Stage 9A governance | Preserve default-off isolated metrics semantics after code verification |
| `docs/paper_review_protocol.md` | Stage 9A governance | Preserve PAPER-evidence-only semantics; no LIVE authorization |
| `learning-log.md` | Stage 9A/9B governance | Preserve factual entries; reconcile exact commit/test facts during closeout |

### 4.3 Later paper-startup/auth/offline-fallback attempt

These pre-existing changes were written after the original Stage 9A wave and
before this Phase 0 session. They are preserved as earlier product work but are
**not accepted implementation**. Several conflict with the canonical Phase 0
invariants and ADR 0008.

| Path | Classification | Disposition / reason |
|---|---|---|
| `README.md` | earlier product work | Quarantine one-command claim until the command is safe and verified |
| `backend/config.py` | earlier product work | Quarantine `OFFLINE_FALLBACK`; fabricated broker/account state is not an approved execution source |
| `backend/health.py` | mixed Stage 9A + earlier product work | Retain useful liveness/readiness separation concept, but current readiness is configuration-derived and incomplete |
| `backend/ibkr_client.py` | mixed Stage 9A + earlier product work | Recover SIM/real-money connection fence; reject fabricated fallback account/positions; compare with Stage 9B guarded wrappers |
| `backend/routers/positions.py` | earlier product work | Quarantine fabricated account/position fallback responses |
| `dashboard/src/App.tsx` | earlier product work | Preserve for later auth-flow review; not part of clean baseline by default |
| `dashboard/src/components/auth/AuthGuard.tsx` | earlier product work | Quarantine unconditional `localStorage` persistence; conflicts with ADR 0008 target and changes remember-me semantics |
| `dashboard/src/components/auth/LoginPage.tsx` | earlier product work | Quarantine token-persistence and demo-login changes pending auth contract tests |
| `dashboard/src/components/layout/Header.tsx` | earlier product work | Quarantine fallback-state UI; salvage explicit SIM connection error handling later |
| `dashboard/src/services/api/auth.ts` | earlier product work | Preserve request-dedup idea for later tested review; do not assume it fixes session security |
| `dashboard/src/services/api/client.ts` | earlier product work | Preserve typed HTTP-status idea for later review |
| `dashboard/src/store/botStore.ts` | earlier product work | Quarantine `offlineFallback` state with rejected fallback feature |
| `dashboard/src/types/index.ts` | earlier product work | Quarantine `offline_fallback` contract with rejected fallback feature |

The current `backend/tests/test_paper_smoke.py` only asserts that
`SIM_MODE=true` plus `IS_PAPER=true` produces a simplistic readiness flag. It
does not prove the canonical golden path, broker isolation, deterministic data,
safety, persistence, audit, restart, or deduplication.

## 5. Deleted tracked paths

All deletions below are earlier documentation-cleanup work. They are fully
recoverable from `0bde712` and appear deleted in both active dirty worktrees.
`LEARNED.md` records that completed session files should not remain active, but
each deletion must still be checked against code before inclusion in the clean
baseline.

| Path | Classification | Proposed disposition |
|---|---|---|
| `sessions/dashboard-hardening-plan.md` | earlier product cleanup | Keep deleted only after code-verified completion |
| `sessions/phase-b-f7-01-auth-gap-analysis.md` | earlier product cleanup | Keep deleted only after findings have tracking IDs or closure evidence |
| `sessions/phase1-dedup-fmtUSD.md` | earlier product cleanup | Keep deleted after code verification |
| `sessions/phase2-memoize-watchlist.md` | earlier product cleanup | Keep deleted after code verification |
| `sessions/phase3-dead-code-cleanup.md` | earlier product cleanup | Keep deleted after code verification |
| `sessions/phase4-encode-symbol-params.md` | earlier product cleanup | Keep deleted after code verification |
| `sessions/phase5-adaptive-polling.md` | earlier product cleanup | Keep deleted after code verification |
| `sessions/swing-screener-backend-plan.md` | earlier product cleanup | Keep deleted after code verification |

## 6. Untracked paths

| Path | Classification | Phase 0 disposition / recovery |
|---|---|---|
| `.claude/settings.json` | local/project tool configuration | Preserve untouched; review separately before deciding whether it is portable/committable |
| `ROADMAP_TEAM_PLAN.md` | Stage 9A governance | Preserve as release-governance input; reconcile with current Phase 0 plan |
| `aiautomation/` | nested repository/reference | See Section 3 |
| `aiautomation-pr3/` | nested repository/reference | See Section 3 |
| `backend/tests/test_execution_architecture.py` | Stage 9A characterization test | Preserve; it freezes known bypass debt rather than claiming the gateway is complete |
| `backend/tests/test_lifespan_safety.py` | Stage 9A containment test | Preserve and include in hunk-level recovery review |
| `backend/tests/test_paper_smoke.py` | earlier product work | Quarantine; replace/extend only through Work Package E |
| `backend/trading_bot.db.execution.lock` | generated runtime artifact | Do not commit; validate no owner holds it before cleanup; add precise ignore rule |
| `docs/REPLAN_LIVE_TRADING_2026_08_02.md` | earlier planning work | Preserve as historical input; canonical Phase 0 and Stage 9A governance supersede unsafe claims |
| `docs/ULTRAPLAN_v2.txt` | earlier planning/reference | Preserve separately; not current Stage 0 authority |
| `docs/ULTRAPLAN_v5.3.2.1.md` | separate v5 planning/reference | Preserve separately; do not mix its governance chain into Phase 0 without disposition review |
| `docs/adr/0006-execution-authority-ownership-and-intent.md` | Stage 9A governance | Preserve as **Proposed**; implementation hold remains binding |
| `docs/adr/0007-broker-protection-reconciliation-and-account-risk.md` | Stage 9A governance | Preserve as **Proposed**; implementation hold remains binding |
| `docs/adr/0008-security-data-and-release-boundary.md` | Stage 9A governance | Preserve as **Proposed**; implementation hold remains binding |
| `docs/evidence/2026-07-27-stage-9a-baseline.md` | Stage 9A evidence | Preserve with dirty-tree caveat; not immutable release evidence |
| `docs/phase_0_foundation.md` | Phase 0 canonical plan | Track as the current executable Phase 0 source of truth |
| `docs/phase_1_live_stock_trading.md` | earlier planning work | Preserve as historical/blocked; wrong mode model and premature LIVE scope make it non-executable |
| `docs/phase_2_analytics_robustness.md` | earlier planning work | Preserve as future reference; out of Phase 0 scope |
| `docs/phase_3_crypto.md` | earlier planning work | Preserve as future reference; out of Phase 0 scope |
| `docs/risk/stage-9a-residual-risk-register.md` | Stage 9A governance | Preserve; open/unsigned state remains authoritative for LIVE NO-GO |
| `docs/security/stage-9a-threat-model.md` | Stage 9A governance | Preserve; isolated-development assumptions remain in force |
| `docs/testing/stage-9a-prelive-fault-matrix.md` | Stage 9A governance | Preserve as required future evidence matrix, not proof of completion |
| `handoffs/2026-05-14-phase1-wave1-review.md` | earlier product handoff | Preserve as historical evidence; caveats require tracking IDs before closure |
| `handoffs/2026-07-27-stage-9a-live-safety-foundation.md` | Stage 9A handoff | Preserve with its explicit dirty-tree/Docker caveats |
| `package-lock.json` | generated artifact | Root has no corresponding active root package workflow; do not commit without an owning manifest; candidate for owner-approved cleanup |
| `scripts/start-paper.sh` | earlier product work | Quarantine: binds services to `0.0.0.0`, creates `.env`, uses the persistent DB by default, and does not prove deterministic isolation |
| `trading_bot.db.execution.lock` | generated runtime artifact | Do not commit; validate no owner holds it before cleanup; add precise ignore rule |

`stocksdashboard/` and the ignored legacy `frontend/` do not appear in normal
untracked output; they are still explicitly classified in Section 3.

## 7. Architecture facts affecting disposition

The preliminary code audit found six direct broker-mutation expressions in
`order_executor.py` and `safety_kernel.py`. The current architecture test
records this debt but does not close it. Manual orders can still bypass
autopilot authority, broker exceptions can be mapped to a non-null ERROR trade
that callers misreport as success, recovery lacks durable `UNKNOWN`, and
readiness is configuration-derived rather than broker/account/reconciliation
derived. Risks R01, R03-R14, R21-R23, and R32 remain open or only partially
mitigated.

These findings do not expand Phase 0 scope. They explain why the dirty paper
startup attempt is quarantined and why LIVE remains NO-GO.

## 8. Gate A disposition

### Approved without destructive action

- Retain canonical `dashboard/` as the only active React implementation.
- Retain `frontend/` read-only until `/trading` and `/static` are separately
  deprecated and tested.
- Preserve the nested React repositories together; do not merge them into the
  canonical history.
- Preserve Stage 9A governance and containment hunks, including omitted
  one-worker and broker-boundary work.
- Quarantine the later fabricated offline-fallback and unsafe startup hunks.
- Treat lock files and root `package-lock.json` as generated candidates, but do
  not remove them until exact targets and ownership are validated.
- Keep all LIVE/real-money fences intact.

### Required before any move or cleanup

1. Create and verify an all-ref bundle for the common nested repository.
2. Record a checksum and an optional source archive for PR3's unique commit.
3. Validate that no process owns either execution-lock artifact.
4. Review `.claude` configuration ownership without exposing secrets.
5. Create the clean Phase 0 worktree from an explicit base; do not mutate this
   preservation checkout to manufacture cleanliness.

No path is classified as irrecoverably unknown. No move or deletion is required
to begin Work Package B safely.

