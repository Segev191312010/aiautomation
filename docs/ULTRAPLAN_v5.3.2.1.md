# ULTRAPLAN v5.3.2.1 — Phase C–Aligned Governance for Master Integration & Scanner-Canary

DATE: 2026-07-20
STATUS: PROPOSED GOVERNANCE AMENDMENT — AWAITING PHASE C OWNER AUTHORIZATION
SUPERSEDES: ULTRAPLAN v5.3.2 (governance-only; applies the v5.3.2.1 ten-point pass)
REVIEW BASELINE: freshly fetched origin/master @ 163c27b76119c90472f9e94d0874d465eb5e5dad

PLANNING_BASE_SHA: (to be filled: origin/master commit used for planning)
IMPLEMENTATION_BASE_SHA: (literal 40-char protected-PR merge OID from 1.03;
recorded immediately in the signed owner record and corroborated later in E_C;
never self-recorded inside its own merge commit)
T (Candidate SHA): (to be filled in evidence commit; not this file)
IMAGE_DIGEST_BACKEND: (to be filled in evidence commit; registry content digest)
IMAGE_DIGEST_DASHBOARD: (to be filled in evidence commit; registry content digest)
DEPLOY_MANIFEST_HASH_PAPER:(to be filled in evidence commit)
DEPLOY_MANIFEST_HASH_LIVE: (to be filled in evidence commit)
SCANNER_PAPER_CONFIG_HASH:(to be filled in evidence commit; sanitized canonical profile)
SCANNER_LIVE_CONFIG_HASH: (to be filled in evidence commit; sanitized canonical profile)
CANARY_POLICY_SCHEMA_HASH:(to be filled in E_C; policy schema+controller impl and immutable hard safety ceilings, hashed pre-T)
CANARY_POLICY_HASH: (overlay Q; filled AFTER S, BEFORE B; exact signed policy values)
SOAK_PROTOCOL_HASH: (to be filled BEFORE P; exact signed soak protocol)
STRATEGY_HASH: (to be filled in evidence commit; strategy/rule-set)
SECRET_VERSION_FINGERPRINT:(to be filled in evidence commit; secret-version ids, never raw secrets)
RESTORE_GENERATION_FENCE: (external, monotonic; see 7.01; not stored in trading DB)
BROKER_VERSION: (to be filled in evidence commit; IBKR/TWS/Gateway version)

LEGACY BRANCH: feature/ultraplan-v4 @ 0bde712c01f3cc16f45c1e36a21d2fcac7fa3f8a
REFERENCE TAG: v4-legacy-reference (PLANNED; Section 2.01 creates, pushes, verifies, and protects it)

DOC TYPE: PROPOSED AMENDMENT / EXTENSION TO docs/PHASE_C_ULTRAPLAN.md (PHASE C PLAN)
AUDIENCE: PHASE C OWNERS · COMMANDER LIAL · SAFETY/OPS SQUADS · SUPER DEV SQUADS

> **DO NOT EXECUTE ANY IMPLEMENTATION STAGE IN THIS DOCUMENT.**
> This file governs how future work is planned and verified. It does not
> authorize C1–C12 implementation, paper soak, or any live trading.
> It grants NO checkpoint authority and NO live authority.

## SECTION 0 — PHASE C CONTRACT, EVIDENCE CHAINS, AND GLOBAL RULES

### 0.1 — Phase C sequence (unchanged)

The canonical Phase C ordering is preserved and NOT redefined here:

C1 → C2 → C3 → C4 → C5/C6 → C7 → C8 → C9 → C10 → C11 → C12

v5.3.2.1 only specifies how scanner-related capabilities and v4 porting map into
these checkpoints. It does not change the definition of any checkpoint.

### 0.2 — Evidence chains (two fully separated chains)

Phase C chain (governs T only; NO paper/live evidence here):

T (technical candidate: exact commit on master with immutable images) →

exact-T verification + external C9 result review →

E_C (Phase C evidence commit naming T and artifacts) →

Owner approval naming T and E_C →

C_C (Phase C closeout commit) →

CI on C_C →

administrative C12 PASS.

Post-C12 scanner-release chain (scanner-only; promotes existing digests only):

P (post-C12 authorization to run scanner PAPER only) →

S (immutable scanner-paper PASS evidence on T) →

Q (create + sign exact canary policy, CANARY_POLICY_HASH; after S, before B) →

B (immutable rollback-rehearsal PASS evidence rehearsing exact Q) →

A (signed one-live-intent authorization; requires S=PASS AND B=PASS) →

arm dedicated canary permit (DISARMED → ARMED) →

atomically reserve exactly one entry intent and consume the permit →

broker outcome / cancel / partial-fill / reconciliation handling →

L (live-canary evidence) →

Owner HOLD / NO-EXPAND decision →

protected R_SUCCESS merge →

CI on R_SUCCESS →

outcome tag →

STOP.

Every non-success branch uses the longest valid prefix → canonical failure
evidence F → owner STOP/NO-EXPAND → protected R_NOGO → CI → non-success outcome
tag → STOP. It never fabricates missing success-chain artifacts; see Sections
7.04, 8, and the authoritative failure branches.

Rules that fix the former C12 circularity:

Paper-soak (S) and live-canary (L) evidence are NEVER part of C12. C12 is
strictly verification of T and its artifacts.

ALL runtime/code/config/dependency-bearing merges and immutable image builds
occur BEFORE T and BEFORE C12. AFTER T, ONLY evidence, authorization, and
closeout merges may occur (E_C, C_C, P/S/Q/B/A/L/F/HOLD/STOP artifacts,
R_SUCCESS or R_NOGO); runtime-bearing
trees and immutable images remain byte-unchanged.

Post-C12 actions may only PROMOTE already-approved digests. Post-C12 MUST NOT
rebuild or change code, dependencies, images, or strategy.

Policy/config hash chronology is explicit (single model, no ambiguity):

CANARY_POLICY_SCHEMA_HASH (policy schema + controller implementation + immutable
hard safety ceilings) is validated and hashed BEFORE T/C12 and recorded in E_C.
Q may select exact values only inside those ceilings and may tighten, but never
relax, them. A value outside the frozen envelope fails closed and requires a new T.

SOAK_PROTOCOL_HASH (exact signed soak protocol) is created BEFORE P; P names it.

CANARY_POLICY_HASH (exact signed canary policy VALUES = overlay Q) is created
AFTER S and BEFORE B (so B rehearses the exact Q), then named by A. It is
never placed in T/E_C and is never a pre-T runtime profile.

### 0.3 — Mapping v5 capabilities into C1–C12 (summary only)

C1 / C7: retention foundations and safe retention for new signals/ledger.

C2–C6: backup/migration/path/restore for new schema, migrations, paths.

C4: signal and ledger schema.

C6: new-table migration matrix.

C7: signal retention policy.

C8: Claude worker / background-task lifecycle and operational gating.

C9: proposal context, durable intent, broker reconciliation (ADR-0009,
fake-broker crash matrix K01–K17, external design review, external evidence
review). C9 depends on C4, C7, and C8.

C10: webhook secrets, proxy boundaries, metrics, redaction.

C11: Signals API/UI and operator documentation.

C12: verification of T and its artifacts; E_C and C_C closeout.

C9 external-review ORDER is fixed and non-negotiable:
external design review → owner design acceptance → C9 implementation →
persistent fake-broker K01–K17 crash/reconciliation evidence →
external result review → C9 PASS.

No paper soak or scanner-live phase may start until:

C12 is administratively PASS for T, AND

Phase C owners separately issue post-C12 authorization P (paper) and, later,
A (one live intent).

### 0.4 — Global safety and scanner-live scope

v5.3.2.1 is scanner-only. CLAUDE_LIVE_TRADING_ENABLED is out of scope; TV/Claude
live authority requires a future, separate ULTRAPLAN.

Multi-worker execution is unsafe: trading runtimes enforce WORKERS=1.

The proposal helper must not operate under fake $1 equity / empty portfolio
once C9 work completes; it must use one freshness-bounded snapshot and fail
closed on missing/inconsistent data.

Ledger semantics use SEPARATE fields for execution venue, autopilot authority,
and signal source; never overload one mode field.

TradingView IP validation trusts only the exact configured nginx peer and its
overwritten header. Arbitrary X-Forwarded-For and direct-backend ingress are
rejected.

Autopilot / live-authority rollback is database-authoritative through the
authenticated control plane. Raw SQLite edits and DB restore are NOT primary
rollback mechanisms and are never used to change authority.

Progressive rollout beyond a single bounded canary remains PROHIBITED; any
expansion is a separate owner-approved plan.

### 0.5 — Authorization boundaries (docs vs implementation vs live)

The boundary is locked and non-circular:

Owners must explicitly authorize OPENING the planning PR before anything runs.

Only Section 1 (docs-only) may execute after that authorization.

Sections 2–8 remain PROHIBITED until the docs-only PR merges into master.

Merging the docs PR does NOT authorize C1–C12 work; each checkpoint keeps its
own approval gate.

A long-lived implementation branch (feature/ultraplan-v5) never implies
authority to execute any checkpoint.

Post-C12: separate written authorization P is required for paper; separate
signed authorization A is required for the single live intent.

### 0.6 — Toolchain, environment, and executable gate protocol

Prerequisites (verified, not assumed):

Python 3.12 is a HARD PREREQUISITE. The current machine ships python3 == 3.14.5;
do NOT invoke python3.12 until it is explicitly provisioned. Provision via the
team-approved toolchain (pyenv/deadsnakes/uv) and pin the interpreter.

The approved secret scanner is gitleaks (pinned version recorded in the
tracker). It is not currently installed; provisioning is a prerequisite for any
patch/secret step in Section 2.

Initial baseline gates for the implementation worktree—runnable against current
master before new checkpoint artifacts exist (paths/args verified against
origin/master @ 163c27b). This is NOT the final pre-T gate; after the Section 9
deliverables land, `scripts/verify_pre_t.py` additionally invokes every mandatory
toolchain, audit, test-outcome, Compose, OCI, chain, inventory, process, and CAS
verifier.

```bash
set -euo pipefail
# Isolated, verifier-owned, unique environment (never operator paths)
export SIM_MODE=true
export AUTOPILOT_MODE=OFF
export ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF=false
VERIFIER_TMP_PARENT="${TMPDIR:-/tmp}"
VERIFIER_TMP_PARENT="${VERIFIER_TMP_PARENT%/}"
export TRADEBOT_HOME="$(mktemp -d "$VERIFIER_TMP_PARENT/aia_v5_home.XXXXXX")"
export DB_PATH="$TRADEBOT_HOME/trading_bot.db"
cleanup_verifier_home() {
  case "$TRADEBOT_HOME" in
    "$VERIFIER_TMP_PARENT"/aia_v5_home.??????) rm -rf -- "$TRADEBOT_HOME" ;;
    *) echo "unsafe verifier cleanup target; retained: $TRADEBOT_HOME" >&2 ;;
  esac
}
trap cleanup_verifier_home EXIT

cd ~/aia_v5_impl                                # repository ROOT (not backend)
source backend/.venv/bin/activate              # Python 3.12 venv (provisioned)

# Workspace hygiene + verifier unit tests live at repo-root scripts/
python scripts/check_workspace_hygiene.py
python -m pytest scripts/tests/test_verify_phase_c.py -v

# verify_phase_c takes a checkpoint subcommand + explicit flags (NOT --expected-sha)
python scripts/verify_phase_c.py c0 \
  --repo-root . \
  --expected-source-commit "$(git rev-parse HEAD)" \
  --expected-remote-ref refs/heads/feature/ultraplan-v5 \
  --json

cd backend
python scripts/check_contract_frontend_vs_openapi.py
python -m pytest tests/ -v --tb=short -p no:cacheprovider

cd ../dashboard
npm ci
npm run typecheck
npm run build
npx vitest run

cd ..
git diff HEAD --check                           # whitespace/conflict markers
```

Gate enforcement rules:

Skip / xfail / XPASS and empty-selection enforcement is not provided by plain
pytest. Before T, every pytest/Vitest run emits a machine-readable report and
`scripts/verify_test_outcomes.py` validates it against
`docs/release-evidence/manifests/test-outcome-allowlist-v1.json` as specified in
9.09. Any unauthorized or expired skip/xfail/XPASS, skipped Vitest case, absent
report, or empty selection is a failure.

Do NOT run docker compose in the Section 3.02 baseline. Current master's
Compose file requires an ignored root .env (reproduced failure:
env file /Users/salomon/aiautomation/.env not found), which a clean worktree
will not have. Compose verification is added later under an explicit checkpoint
(see 3.03), using a COMMITTED non-secret override — never a created operator
.env as evidence.

AGENTS/automation gates run after every FIFTH edit and BEFORE every
commit. CI runs AFTER push on the protected PR (not "before every commit").

Dependency audits use the exact commands and signed, expiring finding acceptance
contract in 9.02. Current critical/high findings must be remediated or matched
individually by hash; blanket acceptance is invalid.

C1A zero-mutation protections stay in force for critical modules; v4 porting
must not bypass them. Update the D14 critical-module inventory to include
webhook, MCP, Claude worker, proposal, metrics, and execution modules.

## SECTION 1 — DOCS-ONLY PHASE C AMENDMENT (NO CODE)

### 1.01 — Create docs worktree atomically from a verified base

Owner: Phase C owner or delegate
Worktree: ~/aia_docs_v5_3_2_1 (exact, collision-checked path)

Steps (from current repo, only after owner authorizes opening the planning PR):

```bash
set -euo pipefail
git fetch origin --prune
PLANNING_BASE_SHA="$(git rev-parse origin/master)"   # 40-char, verified
[[ "$PLANNING_BASE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "bad planning base; STOP"; exit 1; }
git cat-file -e "${PLANNING_BASE_SHA}^{commit}"

# Collision checks BEFORE creation
test ! -e ~/aia_docs_v5_3_2_1
set +e
git show-ref --verify --quiet refs/heads/docs/amendment-v5.3.2.1
lrc=$?
set -e
case "$lrc" in
  0) echo "local branch exists; STOP"; exit 1 ;;
  1) : ;;                                  # exit 1 = local ref absent
  *) echo "show-ref failed (rc=$lrc); STOP"; exit 1 ;;
esac
set +e; git ls-remote --exit-code --heads origin docs/amendment-v5.3.2.1 >/dev/null 2>&1; drc=$?; set -e
case "$drc" in
  0) echo "remote branch exists; STOP"; exit 1 ;;
  2) : ;;                                  # exit 2 = ref absent (the only OK case)
  *) echo "ls-remote failed (rc=$drc; network/auth); STOP"; exit 1 ;;
esac

# Atomic worktree + branch creation in one command
git worktree add -b docs/amendment-v5.3.2.1 ~/aia_docs_v5_3_2_1 "$PLANNING_BASE_SHA"
```

Notes:

git worktree add does NOT change the shell's current directory; every
subsequent step must cd into the worktree explicitly (see 1.02).

Gate:

~/aia_docs_v5_3_2_1 exists, clean, based on the literal verified PLANNING_BASE_SHA.

### 1.02 — Add ULTRAPLAN_v5.3.2.1, update Phase C docs, stage/commit/push explicitly

Owner: Phase C owner

Steps:

Add this file as docs/ULTRAPLAN_v5.3.2.1.md.

Update docs/PHASE_C_ULTRAPLAN.md with an "Amendment v5.3.2.1" section that
references this document as the scanner-governance extension once approved.

Update the Phase C tracker at the canonical path
docs/release-evidence/2026-07-phase-c-tracker.md to map scanner work to
C1–C12 per 0.3. (There is no docs/PHASE_C_TRACKER.md.)

Register BOTH scanner policy artifact paths as
docs/release-evidence/protocols/scanner-soak-v1.json and
docs/release-evidence/protocols/scanner-canary-v1.json (see 6.04–6.05). DO NOT
reference docs/paper_review_protocol.md: it does not exist on current master,
and the v4 copy is a 7-day TradingView/Claude soak incompatible with
scanner-only governance.

Create and read the docs-stage session prompt at
`sessions/stage-c-scanner-governance-prompt.md`. Generate the handoff at
`handoffs/2026-07-20-stage-c-scanner-governance.md`, including Scope and
Authority, Validation, Preserved Boundaries, Wrap-up, and Stop Boundary. Add the
matching Phase C planning entry to `learning-log.md`. These are documentation,
not runtime authority.

Explicit, allowlisted staging + conventional commit + gates + push:

```bash
set -euo pipefail
cd ~/aia_docs_v5_3_2_1                          # worktree add did NOT cd for us

# Provision the hard-prerequisite interpreter in this clean worktree, then run
# every AGENTS quality gate before the docs commit. A docs-only change must still
# leave the complete backend/frontend suite green.
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt

python scripts/check_workspace_hygiene.py
python -m pytest scripts/tests/test_verify_phase_c.py -v
( cd backend && \
  python scripts/check_contract_frontend_vs_openapi.py && \
  python -m pytest tests/ -v --tb=short -p no:cacheprovider )
( cd dashboard && \
  npm ci && \
  npm run typecheck && \
  npm run build && \
  npx vitest run )

git add -- \
  docs/ULTRAPLAN_v5.3.2.1.md \
  docs/PHASE_C_ULTRAPLAN.md \
  docs/release-evidence/2026-07-phase-c-tracker.md \
  handoffs/2026-07-20-stage-c-scanner-governance.md \
  learning-log.md \
  sessions/stage-c-scanner-governance-prompt.md

# Prove the staged allowlist exactly; reject unstaged tracked or unexpected
# untracked files in the clean docs worktree.
diff -u \
  <(printf '%s\n' \
      docs/PHASE_C_ULTRAPLAN.md \
      docs/ULTRAPLAN_v5.3.2.1.md \
      docs/release-evidence/2026-07-phase-c-tracker.md \
      handoffs/2026-07-20-stage-c-scanner-governance.md \
      learning-log.md \
      sessions/stage-c-scanner-governance-prompt.md | LC_ALL=C sort) \
  <(git diff --cached --name-only | LC_ALL=C sort)
git diff --quiet
test -z "$(git ls-files --others --exclude-standard)"
git diff --cached --check

# 'docs(infra)' — 'phase-c' is NOT an allowed AGENTS commit scope. The body
# states WHY the amendment is required and references the stage explicitly.
git commit -m "docs(infra): propose ULTRAPLAN v5.3.2.1 governance amendment" -m \
"Part of Stage Phase-C governance (Section 1). Establishes a fail-closed,
non-circular boundary between Phase C closeout and scanner-canary authority;
this commit changes documentation only and grants no runtime or live authority."
git push -u origin docs/amendment-v5.3.2.1
```

After push, run the clean-source identity gate separately; C0 correctly rejects
the intentionally dirty pre-commit tree:

```bash
set -euo pipefail
cd ~/aia_docs_v5_3_2_1
source backend/.venv/bin/activate
DOCS_COMMIT="$(git rev-parse HEAD)"
REMOTE_DOCS="$(
  git ls-remote --heads origin refs/heads/docs/amendment-v5.3.2.1 |
    awk 'NR == 1 { print $1 }'
)"
[ "$REMOTE_DOCS" = "$DOCS_COMMIT" ] || {
  echo "remote docs branch does not equal local commit; STOP"
  exit 1
}
test -z "$(git status --porcelain=v1 -uall)"
python scripts/verify_phase_c.py c0 \
  --repo-root . \
  --expected-source-commit "$DOCS_COMMIT" \
  --expected-remote-ref refs/heads/docs/amendment-v5.3.2.1 \
  --json
```

Gate:

Full backend + frontend gates PASS; the six-file documentation/process allowlist
is the exact staged diff; the conventional docs(infra) commit has a WHY body and
stage reference; remote identity C0 PASSes after push.

### 1.03 — Open and merge docs-only PR; obtain the immutable merge OID

Owner: Phase C owner

Steps:

Open the PR against protected master.

Owners review for: no code/config/runtime changes; correct C1–C12 mapping;
correct two-chain evidence model.

Merge via protected master.

Treat the protected PR's merge OID as the literal, immutable implementation
base. It is an INPUT obtained from the merge result — NOT something appended
to a file inside its own merge commit (that append would be uncommitted and
self-referential). Validate it strictly:

```bash
set -euo pipefail
git fetch origin --prune

# These are externally supplied immutable facts: PLANNING_BASE_SHA is already
# literal in the merged planning document/tracker; DOCS_MERGE_SHA comes from the
# protected PR platform merge event. A commit is never asked to contain its own OID.
read -r -p "Paste PLANNING_BASE_SHA from the planning-PR record: " PLANNING_BASE_SHA
read -r -p "Paste DOCS_MERGE_SHA from the protected PR merge result: " DOCS_MERGE_SHA

[[ "$PLANNING_BASE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "bad planning base; STOP"; exit 1; }
[[ "$DOCS_MERGE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "bad docs merge SHA; STOP"; exit 1; }
git cat-file -e "${PLANNING_BASE_SHA}^{commit}"
git cat-file -e "${DOCS_MERGE_SHA}^{commit}"
git merge-base --is-ancestor "$PLANNING_BASE_SHA" "$DOCS_MERGE_SHA"
git merge-base --is-ancestor "$DOCS_MERGE_SHA" origin/master

EXPECTED_DOCS_PATHS=(
  docs/PHASE_C_ULTRAPLAN.md
  docs/ULTRAPLAN_v5.3.2.1.md
  docs/release-evidence/2026-07-phase-c-tracker.md
  handoffs/2026-07-20-stage-c-scanner-governance.md
  learning-log.md
  sessions/stage-c-scanner-governance-prompt.md
)
diff -u \
  <(printf '%s\n' "${EXPECTED_DOCS_PATHS[@]}" | LC_ALL=C sort) \
  <(git diff --name-only "$PLANNING_BASE_SHA" "$DOCS_MERGE_SHA" | LC_ALL=C sort)

# Never silently start implementation behind a newer protected master tip.
[ "$(git rev-parse origin/master)" = "$DOCS_MERGE_SHA" ] || {
  echo "master advanced after docs merge; owner must authorize a refreshed base; STOP"
  exit 1
}
IMPLEMENTATION_BASE_SHA="$DOCS_MERGE_SHA"
printf 'IMPLEMENTATION_BASE_SHA=%s\n' "$IMPLEMENTATION_BASE_SHA"
```

Record the PR URL, PLANNING_BASE_SHA, and IMPLEMENTATION_BASE_SHA immediately in
the protected PR merge event and signed owner implementation authorization.
Repeat `Implementation-base: $IMPLEMENTATION_BASE_SHA` plus `Part of Phase C Cn` in every
implementation commit body, and corroborate the same value later in E_C. The
former optional second-PR route is prohibited because it simply recreates the
self-reference problem.

Gate:

IMPLEMENTATION_BASE_SHA is a validated lowercase 40-hex merge OID with proven
existence and ancestry; it is used literally in Section 3.

Outcome:

Governance amendment merged. Implementation work is still NOT authorized.

## SECTION 2 — V4 PRESERVATION FROM CURRENT REPO

### 2.01 — Idempotent, fail-fast v4 tag/branch creation

Owner: Repo hygiene squad
Run from the CURRENT repo (the v4 object/branch does not exist on a fresh clone).
Before running, the repository owner must configure protection rules for the
exact `feature/ultraplan-v4` branch and `v4-legacy-reference` tag and attach
platform/API evidence to the tracker; do not create an unprotected remote ref.

```bash
set -euo pipefail
umask 077
V4_SHA=0bde712c01f3cc16f45c1e36a21d2fcac7fa3f8a
V4_FORK_SHA=0a0d88cec52c0c54c8f2ce1602e2aed02ddfc2a1
V4_HISTORY_REPORT="$(mktemp "/Users/salomon/aia_v4_history_gitleaks.XXXXXX")"

# 1. Verify the literal commit object exists locally
git cat-file -e "${V4_SHA}^{commit}"
git cat-file -e "${V4_FORK_SHA}^{commit}"

# Secret-scan the unique committed v4 history BEFORE publishing the preservation
# refs. The approved gitleaks version must already be provisioned per 0.6.
: > "$V4_HISTORY_REPORT"
set +e
gitleaks git \
  --log-opts="--all ${V4_FORK_SHA}..${V4_SHA}" \
  --redact \
  --report-format json \
  --report-path "$V4_HISTORY_REPORT" \
  .
history_scan_rc=$?
set -e
chmod 600 "$V4_HISTORY_REPORT"
[ "$history_scan_rc" -eq 0 ] || {
  echo "v4 history finding/scanner error; do not push refs; incident review required; STOP"
  exit 1
}
sha256sum "$V4_HISTORY_REPORT"

# 2. Branch: idempotent (treat ONLY exit code 2 as "absent"; net/auth errors STOP)
set +e; git ls-remote --exit-code --heads origin feature/ultraplan-v4 >/dev/null 2>&1
rc=$?; set -e
case "$rc" in
  0) REMOTE_V4="$(git ls-remote --heads origin feature/ultraplan-v4 | awk '{print $1}')"
     [ "$REMOTE_V4" = "$V4_SHA" ] || { echo "remote v4 branch mismatch; STOP"; exit 1; } ;;
  2) git push origin "${V4_SHA}:refs/heads/feature/ultraplan-v4" ;;
  *) echo "ls-remote heads failed (rc=$rc); STOP"; exit 1 ;;
esac

# 3. Tag: query OBJECT and PEELED ref SEPARATELY (annotated-tag ls-remote does
#    not emit ^{} on the same line for the object; verified against an existing
#    annotated tag on this repo).
LOCAL_TAG_OK=false
if git rev-parse -q --verify "refs/tags/v4-legacy-reference" >/dev/null; then
  # Must be an ANNOTATED tag OBJECT, not a lightweight tag pointing at the commit.
  TAG_TYPE="$(git cat-file -t "$(git rev-parse refs/tags/v4-legacy-reference)")"
  [ "$TAG_TYPE" = "tag" ] || { echo "v4-legacy-reference is lightweight, not annotated; STOP"; exit 1; }
  LOCAL_TAG="$(git rev-parse "v4-legacy-reference^{commit}")"
  [ "$LOCAL_TAG" = "$V4_SHA" ] || { echo "local tag mismatch; STOP"; exit 1; }
  LOCAL_TAG_OK=true
fi

set +e; git ls-remote --exit-code --tags origin v4-legacy-reference >/dev/null 2>&1
rc=$?; set -e
case "$rc" in
  0) # present remotely: verify the PEELED target explicitly
     OBJ="$(git ls-remote --tags origin v4-legacy-reference | awk '$2=="refs/tags/v4-legacy-reference"{print $1}')"
     PEELED="$(git ls-remote --tags origin 'v4-legacy-reference^{}' | awk '{print $1}')"
     [ -n "$PEELED" ] && [ "$PEELED" = "$V4_SHA" ] || { echo "remote peeled tag mismatch; STOP"; exit 1; } ;;
  2) # absent remotely: if a correct local tag exists, PUSH it; else create then push
     if [ "$LOCAL_TAG_OK" = true ]; then
       git push origin refs/tags/v4-legacy-reference
     else
       git tag -a v4-legacy-reference "$V4_SHA" -m "Legacy v4 integration baseline (immutable; do not modify)"
       git push origin refs/tags/v4-legacy-reference
     fi ;;
  *) echo "ls-remote tags failed (rc=$rc); STOP"; exit 1 ;;
esac
```

set -euo pipefail guarantees fail-fast: a local tag-creation failure can
never be followed by pushing a stale/mismatched local tag.

Exit code 2 is the ONLY "absent" signal; any other non-zero (network/auth) STOPs.

Object vs peeled ^{} targets are queried and verified SEPARATELY.

If a correct local tag already exists and the remote is absent, the existing
verified tag is PUSHED (never recreated).

Verify the pre-existing rulesets protect both remote refs from rewrite or deletion.

Record exact outcome/candidate tag names and collision behavior in the tracker.

The current dirty tree does NOT need cleaning to tag a literal commit.

Gate:

Remote branch and peeled remote tag both point to the exact V4_SHA.

### 2.02 — Capture dirty v4 artifacts safely (allowlist, secret-scanned)

Owner: Local operator (local only)

```bash
set -euo pipefail
umask 077

V4_SHA=0bde712c01f3cc16f45c1e36a21d2fcac7fa3f8a
[ "$(git rev-parse HEAD)" = "$V4_SHA" ] || {
  echo "current checkout is not the literal v4 tip; STOP"
  exit 1
}

# Exact dispositions for the reviewed 2026-07-20 dirty-tree snapshot. Nothing is
# silently dropped. If current status differs, STOP and amend/reapprove the list.
PRESERVE_TRACKED=(
  backend/health.py
  backend/tests/test_auth_gaps.py
  backend/tests/test_bot_health.py
  docs/ULTRAPLAN_v4_STATUS.md
  sessions/phase-b-f7-01-auth-gap-analysis.md
  sessions/phase1-dedup-fmtUSD.md
  sessions/phase2-memoize-watchlist.md
  sessions/phase3-dead-code-cleanup.md
  sessions/phase4-encode-symbol-params.md
)
EXCLUDE_TRACKED=(
  .claude/settings.local.json
)
PRESERVE_UNTRACKED=(
  docs/ULTRAPLAN_v2.txt
  docs/ULTRAPLAN_v5.3.2.1.md
  handoffs/2026-05-14-phase1-wave1-review.md
  package-lock.json
  sessions/phase1-tv-webhook-plan.md
)
EXCLUDE_UNTRACKED=(
  .claude/settings.json
  aiautomation-pr3/
  aiautomation/
)

diff -u \
  <(printf '%s\n' "${PRESERVE_TRACKED[@]}" "${EXCLUDE_TRACKED[@]}" | LC_ALL=C sort) \
  <(git -c core.quotepath=false diff --name-only "$V4_SHA" | LC_ALL=C sort)
diff -u \
  <(printf '%s\n' "${PRESERVE_UNTRACKED[@]}" "${EXCLUDE_UNTRACKED[@]}" | LC_ALL=C sort) \
  <(git -c core.quotepath=false ls-files --others --exclude-standard | LC_ALL=C sort)

PRESERVATION_ROOT="$(mktemp -d "/Users/salomon/aia_v4_preservation.XXXXXX")"
CAPTURE_ROOT="$PRESERVATION_ROOT/capture"
MANIFEST="$PRESERVATION_ROOT/manifest.tsv"
GITLEAKS_REPORT="$PRESERVATION_ROOT/gitleaks.json"
CHECKSUMS="$PRESERVATION_ROOT/checksums.sha256"
chmod 700 "$PRESERVATION_ROOT"
mkdir -p "$CAPTURE_ROOT/untracked"

{
  for path in "${PRESERVE_TRACKED[@]}"; do
    printf 'PRESERVE_TRACKED\t%s\n' "$path"
  done
  for path in "${EXCLUDE_TRACKED[@]}"; do
    printf 'EXCLUDE_TRACKED_UNTOUCHED\t%s\n' "$path"
  done
  for path in "${PRESERVE_UNTRACKED[@]}"; do
    printf 'PRESERVE_UNTRACKED\t%s\n' "$path"
  done
  for path in "${EXCLUDE_UNTRACKED[@]}"; do
    printf 'EXCLUDE_UNTRACKED_UNTOUCHED\t%s\n' "$path"
  done
} > "$MANIFEST"

git diff --binary --full-index "$V4_SHA" -- \
  "${PRESERVE_TRACKED[@]}" > "$CAPTURE_ROOT/tracked.patch"

for source_path in "${PRESERVE_UNTRACKED[@]}"; do
  test -f "$source_path"
  test ! -L "$source_path"
  destination="$CAPTURE_ROOT/untracked/$source_path"
  mkdir -p "$(dirname "$destination")"
  cp -p "./$source_path" "$destination"
done

: > "$GITLEAKS_REPORT"
set +e
gitleaks dir \
  --redact \
  --report-format json \
  --report-path "$GITLEAKS_REPORT" \
  "$CAPTURE_ROOT"
gitleaks_rc=$?
set -e

if [ "$gitleaks_rc" -ne 0 ]; then
  case "$PRESERVATION_ROOT" in
    /Users/salomon/aia_v4_preservation.??????) rm -rf "$PRESERVATION_ROOT" ;;
    *) echo "unsafe cleanup target; generated copies retained for manual cleanup; STOP"; exit 1 ;;
  esac
  echo "gitleaks finding/error; generated copies removed; originals untouched; STOP"
  exit 1
fi

find "$PRESERVATION_ROOT" -type d -exec chmod 700 {} \;
find "$PRESERVATION_ROOT" -type f -exec chmod 600 {} \;
(
  cd "$PRESERVATION_ROOT"
  {
    find capture -type f -print
    printf '%s\n' manifest.tsv gitleaks.json
  } | LC_ALL=C sort | while IFS= read -r artifact; do
    sha256sum "$artifact"
  done
) > "$CHECKSUMS"
chmod 600 "$CHECKSUMS"
cat "$MANIFEST"
cat "$CHECKSUMS"
```

Rules:

Tracked and untracked preserved files are both copied into the secure capture;
`git diff` is never claimed to preserve untracked files. Machine-local Claude
settings and nested repositories are explicitly recorded as excluded and remain
untouched in the source tree.

NEVER include .git, nested repos, node_modules, .venv, .env, DB files,
WAL/SHM, logs, or credentials.

On any gitleaks finding or scanner failure, delete only the validated generated
capture root and regenerate; never hand-redact a binary patch. Rotate any exposed
secret at source before retry. Originals are never deleted by this procedure.

The unique `mktemp` root prevents overwrite, directories are 0700, files are
0600, and literal SHA-256 values are recorded in the tracker.

Gate:

The enumerated status exactly equals the actual dirty tree; all preserved tracked
and untracked artifacts exist; exclusions are explicit; the generated capture is
secret-scanned clean, uniquely rooted, permission-restricted, and checksummed.

## SECTION 3 — IMPLEMENTATION WORKTREE FROM IMPLEMENTATION_BASE_SHA

### 3.01 — Create clean implementation worktree atomically

Owner: Repo hygiene squad
Worktree: ~/aia_v5_impl (exact, collision-checked)

```bash
set -euo pipefail
git fetch origin --prune
# Re-enter the literal OID from the protected merge result + signed owner record;
# never source it from the dirty v4 checkout or from future E_C.
read -r -p "Paste IMPLEMENTATION_BASE_SHA from the signed owner record: " IMPLEMENTATION_BASE_SHA
[[ "$IMPLEMENTATION_BASE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "bad SHA"; exit 1; }
git cat-file -e "${IMPLEMENTATION_BASE_SHA}^{commit}"
git merge-base --is-ancestor "$IMPLEMENTATION_BASE_SHA" origin/master
[ "$(git rev-parse origin/master)" = "$IMPLEMENTATION_BASE_SHA" ] || {
  echo "master differs from authorized implementation base; STOP"
  exit 1
}

test ! -e ~/aia_v5_impl
set +e
git show-ref --verify --quiet refs/heads/feature/ultraplan-v5
lrc=$?
set -e
case "$lrc" in
  0) echo "local impl branch exists; STOP"; exit 1 ;;
  1) : ;;
  *) echo "show-ref failed (rc=$lrc); STOP"; exit 1 ;;
esac
set +e; git ls-remote --exit-code --heads origin feature/ultraplan-v5 >/dev/null 2>&1; rrc=$?; set -e
case "$rrc" in 0) echo "remote impl branch exists; STOP"; exit 1 ;; 2) : ;; *) echo "ls-remote failed; STOP"; exit 1 ;; esac

git worktree add -b feature/ultraplan-v5 ~/aia_v5_impl "$IMPLEMENTATION_BASE_SHA"
cd ~/aia_v5_impl
git push -u origin feature/ultraplan-v5

[ "$(git rev-parse HEAD)" = "$IMPLEMENTATION_BASE_SHA" ]
test -z "$(git status --porcelain=v1 -uall)"
REMOTE_IMPLEMENTATION="$(
  git ls-remote --heads origin refs/heads/feature/ultraplan-v5 |
    awk 'NR == 1 { print $1 }'
)"
[ "$REMOTE_IMPLEMENTATION" = "$IMPLEMENTATION_BASE_SHA" ]
```

Gate:

~/aia_v5_impl is clean at the literal IMPLEMENTATION_BASE_SHA.

### 3.02 — Provision environment and run baseline gates (no Compose)

Owner: Backend + frontend squads

```bash
set -euo pipefail
# Python 3.12 must already be provisioned (prerequisite, 0.6)
cd ~/aia_v5_impl/backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

cd ../dashboard
npm ci
```

Then run the baseline gate block from 0.6 once (which explicitly EXCLUDES Compose).

Gate:

All non-Compose master gates pass on IMPLEMENTATION_BASE_SHA.

### 3.03 — Compose verification (mandatory pre-T gate)

Owner: Ops + Phase C owners (before T; no checkpoint may defer this gate)

The candidate tree MUST already contain a COMMITTED, non-secret
`docker-compose.test.yml` override (no ignored operator `.env`). This is a
mandatory pre-T artifact under 9.04. T cannot be established until it is merged
and verified.

Only then run the mandatory isolated, self-cleaning interface from 9.04:

```bash
scripts/run_compose_smoke.sh --candidate "$(git rev-parse HEAD)"
```

The runner must validate Compose configuration, build both images, smoke-run
FastAPI and nginx, verify health/startup, prove isolation, and tear down on every
exit path. Building alone is insufficient.

Gate:

Committed override present; config + build + health/startup smoke all pass,
with isolation and cleanup proven. Failure blocks T.

## SECTION 4 — V4 VS IMPLEMENTATION-BASE DISPOSITION MATRIX

### 4.01 — Compute tip-to-tip diffs vs the FIXED implementation base

Owner: Integration squad (in ~/aia_v5_impl)

```bash
set -euo pipefail
git fetch origin --tags

V4_SHA=0bde712c01f3cc16f45c1e36a21d2fcac7fa3f8a
V4_FORK_SHA=0a0d88cec52c0c54c8f2ce1602e2aed02ddfc2a1
V4_WAVE_BASE_SHA=e1f42bdcff82b8209e7d5c0d5da6461d44201ef8
read -r -p "Paste IMPLEMENTATION_BASE_SHA from the signed owner record: " IMPLEMENTATION_BASE_SHA
[[ "$IMPLEMENTATION_BASE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "bad base; STOP"; exit 1; }

for oid in "$V4_SHA" "$V4_FORK_SHA" "$V4_WAVE_BASE_SHA" "$IMPLEMENTATION_BASE_SHA"; do
  git cat-file -e "${oid}^{commit}"
done
[ "$(git merge-base "$V4_SHA" "$IMPLEMENTATION_BASE_SHA")" = "$V4_FORK_SHA" ] || {
  echo "v4 fork point changed; STOP"
  exit 1
}

FULL_MANIFEST=docs/release-evidence/manifests/v4-full-change-manifest.tsv
WAVE_MANIFEST=docs/release-evidence/manifests/v4-ultraplan-wave-manifest.tsv
PATCH_EVIDENCE=docs/release-evidence/v4-vs-implementation-base.patch
CHERRY_EVIDENCE=docs/release-evidence/v4-vs-implementation-base.cherry.txt
PATCH_SCAN_REPORT=docs/release-evidence/v4-vs-implementation-base.gitleaks.json
mkdir -p docs/release-evidence/manifests
for artifact in "$FULL_MANIFEST" "$WAVE_MANIFEST" "$PATCH_EVIDENCE" "$CHERRY_EVIDENCE" "$PATCH_SCAN_REPORT"; do
  test ! -e "$artifact" || { echo "artifact exists: $artifact; STOP"; exit 1; }
done

# Status-aware manifests preserve additions, modifications, renames, and the four
# v4 deletions; requiring every path to exist at the v4 tip would be incorrect.
git -c core.quotepath=false diff \
  --name-status --find-renames "$V4_FORK_SHA" "$V4_SHA" > "$FULL_MANIFEST"
git -c core.quotepath=false diff \
  --name-status --find-renames "$V4_WAVE_BASE_SHA" "$V4_SHA" > "$WAVE_MANIFEST"

[ "$(wc -l < "$FULL_MANIFEST" | tr -d '[:space:]')" = 123 ]
[ "$(wc -l < "$WAVE_MANIFEST" | tr -d '[:space:]')" = 65 ]
[ "$(sha256sum "$FULL_MANIFEST" | awk '{print $1}')" = \
  "1c6cedc51d0e104975139b1f0c6b5494a0f73bddf11d3fcb52597abe5c27f855" ]
[ "$(sha256sum "$WAVE_MANIFEST" | awk '{print $1}')" = \
  "7a0d66d8acf57e42f8c5583e107e2595472841921fb3d5d68cdea554b827a47c" ]

git diff --binary --full-index \
  "$IMPLEMENTATION_BASE_SHA" "$V4_SHA" > "$PATCH_EVIDENCE"
git cherry -v "$IMPLEMENTATION_BASE_SHA" "$V4_SHA" > "$CHERRY_EVIDENCE"
: > "$PATCH_SCAN_REPORT"
set +e
gitleaks dir \
  --redact \
  --report-format json \
  --report-path "$PATCH_SCAN_REPORT" \
  "$PATCH_EVIDENCE"
patch_scan_rc=$?
set -e
if [ "$patch_scan_rc" -ne 0 ]; then
  rm -f -- "$PATCH_EVIDENCE"
  chmod 600 "$PATCH_SCAN_REPORT"
  echo "tip-to-tip patch finding/scanner error; patch deleted; incident review required; STOP"
  exit 1
fi
sha256sum "$PATCH_EVIDENCE" "$CHERRY_EVIDENCE" "$PATCH_SCAN_REPORT"
```

The 123-row fork-to-v4 manifest is the complete authoritative inventory; the
65-row wave manifest isolates the direct ULTRAPLAN-v4 wave. Human-readable core
paths include `backend/order_proposal.py`, `backend/routers/webhook_routes.py`,
`backend/claude_worker.py`, `backend/claude_context.py`,
`backend/claude_prompts.py`, `backend/mcp_server.py`, `backend/metrics.py`,
`dashboard/src/components/autopilot/SignalsTable.tsx`,
`docs/paper_review_protocol.md`, and `docs/LIVE_FLIP_RUNBOOK.md`. Those v4 paper/
live documents are disposition inputs only; they must not become the new scanner
protocol unchanged.

Gate:

Both exact manifests match their fixed row counts and hashes; the complete
tip-to-tip binary diff and cherry evidence are collected against the fixed base.

### 4.02 — Disposition matrix (capability + hunk level)

Owner: Integration + Phase C owner
File: docs/release-evidence/v4_vs_impl_matrix.md

Steps:

Classify every status-aware manifest row, capability, AND individual hunk as
ALREADY LANDED / PORT / REIMPLEMENT / SUPERSEDED / DROP. Whole-commit or short
hand-list classification is insufficient; every DROP requires explicit owner
rationale and approval.

Columns: capability, files, v4 location, base location, disposition, notes.

Owners approve the completed matrix BEFORE any porting begins.

DO NOT port wholesale: v4 retention, startup, runtime-lock, Docker, DB
lifecycle, or executor changes — those follow their owning Phase C checkpoints.

Gate:

Matrix committed under docs/release-evidence/ and owner-approved.

## SECTION 5 — PHASE C IMPLEMENTATION (C1–C12) — GOVERNANCE MAPPING ONLY

v5.3.2.1 does NOT redefine C1–C12. It fixes ORDERING so nothing is circular:

ALL runtime/code/config/dependency-bearing merges and immutable image builds
occur BEFORE candidate T and BEFORE C12. AFTER T, ONLY evidence, authorization,
and closeout merges may occur (E_C, C_C, P/S/Q/B/A/L/F/HOLD/STOP artifacts,
R_SUCCESS or R_NOGO); the
runtime-bearing tree and immutable images are byte-unchanged thereafter.

This is enforced, not merely attested: the pre-T verifier records a manifest of
all runtime-bearing paths, dependency locks, deployment manifests, strategy
inputs, and image digests at T. Every post-T evidence/closeout CI job compares
that manifest against T and rejects any changed runtime path, lock, manifest,
strategy input, or image digest. Post-T commits are allowlisted to evidence,
authorization, and closeout paths; an unlisted file is a hard failure.

Each checkpoint executes only under its own separate authorization; the
long-lived branch grants none.

C9 external-review order is fixed (0.3).

After all runtime merges: build immutable backend/dashboard images; record
IMAGE_DIGEST_BACKEND, IMAGE_DIGEST_DASHBOARD, DEPLOY_MANIFEST_HASH_PAPER,
DEPLOY_MANIFEST_HASH_LIVE, SCANNER_PAPER_CONFIG_HASH, SCANNER_LIVE_CONFIG_HASH,
CANARY_POLICY_SCHEMA_HASH (schema + controller impl + hard safety ceilings; NOT
the exact value set),
STRATEGY_HASH, SECRET_VERSION_FINGERPRINT, BROKER_VERSION.
(SOAK_PROTOCOL_HASH and CANARY_POLICY_HASH are created LATER — see 0.2 and 6.01.)

Establish T = the exact merged commit + these artifacts.

Run exact-T verification and external C9 result review.

Produce E_C (names T + results and records IMPLEMENTATION_BASE_SHA), then owner
approval naming T and E_C, then C_C, then CI on C_C, then administrative
C12 PASS.

NO paper/live evidence is created in this section.

Central execution-gate requirement introduced here (used by Sections 6–7):

Implement ONE central C9 OperationGate through which EVERY non-exit broker
submission must pass, regardless of route, source, background task, or caller.
This is the mechanism that later makes "exactly one entry" enforceable
system-wide rather than per-route (see 6.02 and 7.01).

Details of C1–C12 remain in docs/PHASE_C_ULTRAPLAN.md.

## SECTION 6 — SCANNER-LIVE GOVERNANCE (POST-C12 ONLY)

### 6.01 — Post-C12 authorization sequence (P → S → Q → B → A)

Owner: Phase C owners

Distinct authorizations/evidence, never conflated, in this exact order:

P: post-C12 authorization to run scanner PAPER only. Requires C12 PASS for T.
P names/hashes at least: T; IMAGE_DIGEST_BACKEND; DEPLOY_MANIFEST_HASH_PAPER;
SCANNER_PAPER_CONFIG_HASH; paper broker + approved PAPER account fingerprint;
STRATEGY_HASH; SOAK_PROTOCOL_HASH (created before P); and P's own expiry/nonce.

S: scanner-PAPER soak PASS evidence (immutable), collected under P.

Q: create and SIGN the exact ScannerCanaryPolicyV1 values, hashed as
CANARY_POLICY_HASH. Created AFTER S and BEFORE B, so B rehearses the EXACT
thresholds that will govern the live canary.

B: rollback-rehearsal PASS evidence that rehearses the exact Q policy
thresholds. Any change to Q after B invalidates B and requires a fresh
exact-policy rollback rehearsal.

A: signed one-live-intent authorization. A may be issued only when S=PASS, Q is
valid, signed, unexpired, hash-matched, and inside the frozen hard ceilings, and
B=PASS explicitly names and rehearses that exact Q hash. Any Q change after B
invalidates B and prevents A.
A names/hashes at least: T; IMAGE_DIGEST_BACKEND; IMAGE_DIGEST_DASHBOARD;
DEPLOY_MANIFEST_HASH_LIVE; SCANNER_LIVE_CONFIG_HASH; CANARY_POLICY_HASH (= Q);
STRATEGY_HASH; SECRET_VERSION_FINGERPRINT; BROKER_VERSION; approved LIVE account
fingerprint; S hash; Q hash; B hash; maximum rollback time; and A's own
expiry/nonce.

### 6.01a — Hash/authorization chronology (single, unambiguous model)

CANARY_POLICY_SCHEMA_HASH — created/hashed BEFORE T (schema + controller impl).

SOAK_PROTOCOL_HASH — created/signed BEFORE P (exact soak protocol).

CANARY_POLICY_HASH (Q) — created/signed AFTER S, BEFORE B (exact values).

Q is classified as a SIGNED AUTHORIZATION OVERLAY, NOT a mutation to T's code,
images, strategy, or the pre-approved runtime profiles. Applying Q changes no
runtime tree byte and no image digest; it only supplies the signed threshold
values consumed by the already-frozen policy controller (CANARY_POLICY_SCHEMA_HASH).
Any change to Q after B invalidates B.

### 6.02 — Central C9 OperationGate as the ONLY live-entry path (BLOCKER-1 FIX)

Owner: Architecture + safety

Master today has multiple live-capable entry paths beyond TV/MCP, so a
scanner-local latch alone cannot guarantee "exactly one entry". Specifically the
following must be neutralized under SCANNER_LIVE:

Manual BUY (place_order(... require_autopilot_authority=False)).

Ordinary rule entries that bypass autopilot authority.

/direct-trades/execute while broad autopilot authority is active.

Bot-start, rule activation/mutation, mode/reset endpoints, auto-rule creation,
and AI optimizer tasks.

Required rule:

EVERY non-exit broker submission — every route, MCP tool, background task,
scheduled/optimizer task, and direct broker-adapter call site — MUST pass
through the single central C9 OperationGate. There is no bypass path.

Under SCANNER_LIVE the gate accepts ONLY the single scanner intent UUID bound
to A, T, policy, strategy, and account. A is NOT consumed here; the sole
linearization/consumption point is the external UNUSED→CONSUMED_BY CAS in 7.01.
The gate merely REQUIRES the exact external tuple and DB reservation to agree
before the adapter call. This removes the earlier double-consume contradiction.

Non-overridable single-entry invariants (NOT configurable; no "maximum attempts"
knob may relax them):

max_entry_intents = 1

max_entry_orders = 1

max_entry_adapter_calls = 1

These limits are enforced at the persistence and adapter boundaries, not only
by the scanner: the intent table has database uniqueness/conditional-write
constraints for the release and entry slot, and the broker adapter requires
the same immutable intent UUID as its idempotency key. A duplicate or missing
idempotency key is rejected before any broker call; retries can reconcile an
existing broker request but cannot create a second entry request.
Execution reports never consume additional intent, entry-order, or entry-adapter-
call quota. Every executed quantity is nevertheless a REAL fill and MUST be
counted in cumulative filled quantity/notional and position reconciliation.

Account scoping (required):

Every order / cancel / fill / execution callback is EXPLICITLY account-scoped.

Reject default routing, ambiguity across multiple managed accounts, or any
account mismatch versus the approved fingerprint.

Deny-by-default inventory test (required, automated):

Enumerate every route, MCP tool, background/scheduled task, and direct
broker-adapter call site.

Assert that under SCANNER_LIVE, manual BUY, direct-AI entry, rule entry,
arbitrary bot-start, rule mutation, mode/reset, auto-rule creation, and
optimizer entry paths are ABSENT or REJECT.

Assert that verified exits, targeted cancellation, reconciliation, status, and
emergency stop remain AVAILABLE.

The test runs via both direct FastAPI and nginx ingress and fails closed on any
unlisted entry path (default-deny for newly added paths).

### 6.03 — Canary arming instead of broad LIVE authority (BLOCKER-2 FIX)

Owner: Architecture + safety

Broad LIVE authority is NEVER used for the canary. The active model is:

Start with AUTOPILOT_MODE=OFF.

Force ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF=false.

Start the bot and all entry-capable background tasks DISABLED.

Keep global entry authority OFF throughout the canary.

A signed canary ARM operation changes ONLY the dedicated canary state from
DISARMED to ARMED; it does not unlock manual, AI, or rule-based live entries.

The central C9 OperationGate does NOT consume A. The sole consumption point is
the external CAS in 7.01. Before the one entry intent is submitted, the gate
validates A == CONSUMED_BY(this exact intent UUID and payload hash) and exact DB
mirror agreement.

### 6.04 — SCANNER_LIVE runtime profile: validate A AND actual artifacts (BLOCKER-7 FIX)

Owner: Architecture + safety

The SCANNER_LIVE profile refuses startup — and re-validates immediately before
RESERVED/SUBMITTING — unless ALL of the following hold. On failure it enters a
RECOVERY-ONLY process state (not full termination). Reconciliation/status reads
remain possible in BOTH recovery classes; targeted cancellation and verified
exits are permitted ONLY in TRUSTED recovery. UNTRUSTED recovery is read-only
(see the two-class split below).

Static profile conditions (ALL code-enforced in the SCANNER_LIVE profile itself,
not merely asserted in surrounding prose):

WORKERS=1; SIM_MODE=false.

AUTOPILOT_MODE=OFF.

ALLOW_LIVE_RULES_WHEN_AUTOPILOT_OFF=false.

Global entry authority OFF.

Ordinary bot-start, rule, AI-optimizer, and all other entry-capable scheduler
tasks DISABLED (see the dedicated one-shot controller below).

Broker environment is live AND broker-reported account matches the exact
approved account fingerprint.

CLAUDE_WORKER_ENABLED=false and CLAUDE_LIVE_TRADING_ENABLED=false; startup fails
if either Claude enable flag is true.

TV webhook write route ABSENT from the OpenAPI/route inventory.

MCP order-proposal tool ABSENT from the tool registry.

Runtime lock held for this process instance.

Emergency-stop state readable AND clear; daily-loss lock clear.

SECRET_VERSION_FINGERPRINT matches the value named in A.

Dedicated one-shot canary controller (removes the underspecification between
"all entry tasks disabled" and "the normal scanner produces the intent"):

A single dedicated controller invokes the NORMAL scanner decision path EXACTLY
ONCE while every ordinary scheduler/background entry task stays disabled.

It produces at most one intent, drives the 7.01 reservation, and then does
nothing further. It is the only actor permitted to originate the canary intent.

Authorization/artifact validation — PHASE-SPECIFIC (BLOCKER fix: the full
P→S→Q→B→A→L chain cannot be validated before those artifacts exist):

SCANNER_PAPER startup: validate T, P, paper images/manifest
(IMAGE_DIGEST_BACKEND, DEPLOY_MANIFEST_HASH_PAPER, SCANNER_PAPER_CONFIG_HASH),
STRATEGY_HASH, SOAK_PROTOCOL_HASH, and the approved PAPER account. A-status
is not checked here (A does not yet exist).

B rehearsal: validate T, S, and the exact signed policy Q (CANARY_POLICY_HASH).

SCANNER_LIVE startup: validate T, S, Q, B, and A (A: valid signature, matching
nonce, not expired, status UNUSED — A has not yet been reserved at startup).

Pre-SUBMIT re-validation (after reservation): validate the same LIVE chain PLUS
A == CONSUMED_BY(this exact intent UUID). A is NOT expected to be UNUSED here.

R_SUCCESS CI: validate the complete success chain including L and HOLD.
R_NOGO CI: validate only the longest valid prefix plus F, STOP, and R_NOGO.

Common to LIVE startup and pre-SUBMIT:

T / source SHA and IMAGE_DIGEST_BACKEND / IMAGE_DIGEST_DASHBOARD match the
actual running artifacts.

DEPLOY_MANIFEST_HASH_LIVE matches the deployed manifest.

SCANNER_LIVE_CONFIG_HASH and CANARY_POLICY_HASH match loaded config/policy.

STRATEGY_HASH matches the loaded strategy/rule-set.

S hash and B hash match the referenced PASS evidence.

Actual broker/Gateway version equals BROKER_VERSION.

Stable C9 reconciliation; NO unresolved interventions; NO unexpected working
order or exposure. At LIVE startup there may be NO unresolved intent. At
pre-SUBMIT there MUST be exactly one unresolved intent—the current A-bound,
payload-hash-matched RESERVED intent—and every other unresolved intent is fatal.

Any validation failure follows its exact phase-specific failure branch and
permits no new broker adapter call. A post-CAS failure is never mislabeled as an
arming failure: A remains CONSUMED and the intent closes non-success without
submission. The startup matrix and OpenAPI/tool inventory are tested through
both direct FastAPI and nginx ingress.

Recovery-only is SPLIT INTO TWO CLASSES (BLOCKER-5 FIX) — permissions depend on
what failed, because blanket cancel/exit is unsafe under a wrong account/image:

TRUSTED recovery: exact approved image digest, runtime lock, approved account
fingerprint, AND canary intent ownership are ALL proven. Only then are targeted
cancel (by durable orderRef/intent UUID) and broker-position-BOUNDED exit
permitted.

UNTRUSTED recovery: account, image, lock, or intent ownership cannot be proven.
The application is READ-ONLY (status/reconciliation reads only). No cancel, no
exit from the app; broker-side operator recovery is required.

Preconditions re-checked at ARM, RESERVE, and SUBMIT (all three):

Emergency-stop state is CLEAR.

Daily-loss lock is CLEAR.

Account risk data (equity/positions) is FRESH within the policy freshness bound.

All ScannerCanaryPolicyV1 limits still PASS.
Failure before a successful ARM ⇒ no ARM and no consumption; an otherwise valid
external UNUSED A is revoked before any future use as required by the failure
branch. Failure after ARM but before the external consumption CAS ⇒ atomically close the gate, transition
the external A nonce from UNUSED to terminal REVOKED_BY(intent UUID or arm nonce),
persist DISARMED_REVOKED, and require a NEW A; never auto-resume or create another
intent. Failure after the consumption CAS follows the post-reservation branch in
Section 7 and A remains CONSUMED. No phase proceeds on stale data.

### 6.05 — Mandatory versioned policy artifacts

Owner: Risk + Phase C owners

Two mandatory, fail-closed artifacts, defined with EXACT values (no examples,
no defaults), signed by the owner AND the risk operator, validated at startup and
by the canary controller, with the chronology from 6.01:

Scanner soak protocol: docs/release-evidence/protocols/scanner-soak-v1.json
(canonical JSON + human-readable rendering), hashed as SOAK_PROTOCOL_HASH,
created BEFORE P and named by P.

Scanner canary policy: docs/release-evidence/protocols/scanner-canary-v1.json
(canonical JSON + rendering), hashed as CANARY_POLICY_HASH, created + signed as
overlay Q AFTER S and BEFORE B (6.01/6.01a), rehearsed by B, and named by A.
(Its schema+controller are already frozen pre-T as CANARY_POLICY_SCHEMA_HASH.)

"Before approval" in 6.06–6.07 means before the RELEVANT step (P for the soak
protocol; Q/B for the canary policy) — NOT before the documentation amendment
merges. Both artifacts fail closed if missing, expired, altered (hash mismatch),
or incomplete.

### 6.06 — Scanner soak protocol parameters (S) — session-based

Owner: Phase C owners + risk + ops

scanner-soak-v1.json must define, in terms of qualifying market SESSIONS (not
calendar days), for T in the scanner PAPER profile:

What counts as an eligible scanner decision.

Required number of full, eligible market sessions.

Minimum number of eligible decisions.

Exact limits on failures, duplicates, expiries, orphans, and reconciliation
mismatches.

Disconnect / restart tolerances.

Mandatory restart, reconnect, emergency-stop, and recovery drills, and their
effect on the clock.

Frozen strategy / configuration / dependency / artifact identities.

Rules for any change: any code/config/dependency/image change restarts the
relevant soak evidence.

Automatic NO-GO semantics: any threshold breach ⇒ NO-GO ("deviation
documented" is insufficient).

Baseline reference: 15 qualifying sessions and 100 eligible decisions; owners
adopt this or explicitly sign an alternative.

SCANNER_PAPER runtime profile (fail-closed, comparable to SCANNER_LIVE):

Refuses startup unless WORKERS=1, the PAPER broker environment is selected, the
broker-reported account matches the approved PAPER account fingerprint,
DEPLOY_MANIFEST_HASH_PAPER and SCANNER_PAPER_CONFIG_HASH match, STRATEGY_HASH
matches, SOAK_PROTOCOL_HASH matches, Claude worker/live flags are false, TV
write route and MCP order tool are absent, runtime lock is held, and
emergency-stop state is readable.

S MUST exercise the REAL C9 broker pipeline against an approved IBKR PAPER
account (proving the actual adapter + reconciliation path). SIM_MODE=true
synthetic runs may SUPPLEMENT S but must NEVER silently replace real paper
execution. Evidence S is an immutable PASS artifact collected under this
protocol.

### 6.07 — Scanner canary policy parameters (created + signed as Q, before B)

Owner: Phase C owners + risk + ops

This exact policy is created and signed as authorization overlay Q AFTER S and
BEFORE B (see 6.01a). ScannerCanaryPolicyV1 must define exact, owner-owned
financial values (no "e.g.", no undefined symbols/notional/latency):

Every value MUST validate inside the non-overridable pre-T safety envelope bound
to CANARY_POLICY_SCHEMA_HASH. Q may tighten that envelope but cannot enlarge
symbol scope, quantity/notional, loss, position, time, price, slippage, latency,
or rollback ceilings. Any attempted relaxation fails closed and requires a new T.

Symbol allowlist.

Per-order quantity and per-order notional.

Aggregate daily notional cap.

max_entry_orders = 1 and max_entry_adapter_calls = 1 are the NON-OVERRIDABLE
hard constants (6.02); the policy MUST NOT redefine or relax them. There is no
configurable "maximum attempts".

Maximum CUMULATIVE FILLED QUANTITY and maximum cumulative filled NOTIONAL for
the single order (multiple partial-fill execution records are real fills but
all remain tied to the one order; there is never a second order).

Maximum open positions.

Daily loss limit.

No-short policy.

Regular-hours session window and timezone.

Allowed order type, price guard, slippage band, and TIF.

Cancel timeout.

Partial-fill behavior.

Disconnect / halt / rejection behavior.

Latency statistic, window, and threshold.

Persistent automatic-stop behavior on any threshold breach.

Manual reset authority and process.

Maximum rollback time.

Canary observation duration after final reconciliation.

### 6.08 — Operational rollback gate → evidence B (BLOCKER-5 FIX)

Owner: Safety + ops

Before A can be approved, an end-to-end one-shot canary and rollback rehearsal
against the approved IBKR PAPER account must PASS and be captured as immutable
evidence B. B runs the same frozen controller, external authorization/CAS path,
OperationGate, broker adapter, state graph, reconciliation, and stop path that
LIVE will use, with a PAPER-only rehearsal authorization that can never be valid
for a live account. B is executed AFTER Q exists and exercises the EXACT Q values
(CANARY_POLICY_HASH), including symbol/order/price/fill/latency/risk behavior—not
only its stop thresholds. Any later change to Q invalidates B. B proves:

Authenticated emergency stop is persisted and affects runtime AND DB.

Runtime and database both report stopped authority.

New entries are blocked while safe exits remain possible.

Working orders are preserved/reconciled per ADR-0009.

If the control API fails, the process/network path is stopped.

A broker-side fallback is documented and rehearsed.

Every canary threshold invokes the same automatic stop path.

Maximum rollback time is measured.

Restart cannot silently restore live authority.

No raw SQLite edit or DB restore is used to change authority.

A must NOT exist until S=PASS, Q is signed, and B=PASS (with B having rehearsed
that exact Q).

### 6.09 — Two config profiles, not one hash (BLOCKER-6 FIX)

Owner: Architecture + Phase C owners

Paper and live legitimately differ (broker env, account, authority state), so a
single EXEC_CONFIG_HASH is impossible. Instead:

Pre-approve (before T) TWO immutable, content-addressed runtime profiles:
SCANNER_PAPER_CONFIG_HASH and SCANNER_LIVE_CONFIG_HASH. CANARY_POLICY_HASH is
NOT a pre-T runtime profile — only its schema/controller
(CANARY_POLICY_SCHEMA_HASH) is pre-T; the exact policy VALUES are created later
as signed overlay Q (after S, before B; see 6.01a and 6.07).

Post-C12 the ONLY permitted runtime action is SELECTION between these two
already-approved profiles, plus applying the signed Q overlay. Code, images,
dependencies, and strategy remain byte-identical.

The pre-T profiles freeze Q delivery as a read-only mount at exactly
`/run/tradebot/authorization/scanner-canary-v1.json` with its sibling
`scanner-canary-v1.sig.json`. There is no environment-variable, database,
command-line, or control-API override for policy values. Startup and every
ARM/RESERVE/SUBMIT check canonicalize and verify those bytes; mutation, inode/
content replacement, expiry, or hash/signature mismatch invokes the uniform stop
path. The mount mechanism and negative override tests are part of T.

Any unapproved semantic change invalidates S/A; any runtime code change creates
a NEW T requiring renewed Phase C proof.

Config attestations use sanitized canonical representations and
SECRET_VERSION_FINGERPRINT; raw secrets are never stored or hashed.

## SECTION 7 — EXACTLY ONE SCANNER CANARY (LIVE INTENT)

### 7.01 — Crash-safe one-shot latch state machine (BLOCKER-3 FIX)

Owner: Ops + safety + Phase C owners

The single-entry guarantee is a persistent, crash-safe state machine with ONE
atomic linearization/consumption point (BLOCKER-1 FIX — A is consumed exactly
once, never twice):

DISARMED → ARMED(A) → PREPARED(intent UUID, payload hash)
→ RESERVED(intent UUID) → SUBMITTING
→ broker state graph defined in 7.02a → RECONCILED
→ CLOSED_SUCCESS | CLOSED_NO_GO

Before consumption, an abort path is also explicit and terminal for that A:
ARMED/PREPARED → REVOCATION_PENDING (only while external state is unavailable)
→ DISARMED_REVOKED(A, intent-or-arm nonce). After consumption, every abort path
retains A as CONSUMED and terminates through reconciliation.

A-lifecycle — the EXTERNAL fence store is the at-most-once authority; the trading
DB only MIRRORS it (BLOCKER-3 FIX). The consume is NOT a single trading-DB tx,
because a DB restore could otherwise replay an UNUSED row. Exact fail-closed
reservation protocol, in order:

1. PREPARE (trading DB): create ONE immutable PREPARED intent — fresh intent
UUID + immutable payload + payload hash + A nonce + RESTORE_GENERATION — under a uniqueness constraint
(exactly one PREPARED slot for this scanner-canary release). Not yet authorized
to submit. PREPARED is a first-class durable state, not an informal intermediate.
2. EXTERNAL COMPARE-AND-SET (the linearization point): perform an atomic CAS on
the dedicated control-plane PostgreSQL store (separate database, storage,
credentials, backup, and operator role from the trading DB), using a
serializable transaction and a unique `(release_id, A_nonce)` key:
A-nonce: UNUSED → CONSUMED_BY(intent UUID, payload hash, RESTORE_GENERATION).
Exactly one CAS can succeed for this A. Success is DURABLE before step 3.
3. MIRROR (trading DB): record the exact external CONSUMED_BY tuple and set
intent.status = RESERVED, referencing intent UUID, payload hash, and generation.
4. The pre-adapter gate (6.02) then REQUIRES BOTH: the external record ==
CONSUMED_BY(this intent UUID, this payload hash, this generation) AND the DB
reservation for the same tuple. The two must AGREE; disagreement ⇒ fail closed,
no submit.

5. CALL-INTENT journal (immediately before the adapter call): under the same
intent lock, write a durable `CALL_INTENT` record containing the intent UUID,
payload hash, adapter idempotency key, account fingerprint, and generation;
commit it before invoking the broker adapter. A uniqueness constraint permits
at most one `CALL_INTENT` for the entry intent. On restart, a committed
`CALL_INTENT` means the adapter call may have happened and the process MUST
reconcile only—never invoke the adapter again. Only an intent with no committed
`CALL_INTENT` may make the single adapter invocation, while holding the lock.
This journal is required evidence in the C9 crash matrix.

Crash semantics:

Crash after PREPARE but before the external consumption CAS ⇒ startup finds the
same immutable PREPARED intent and MUST NOT create or auto-submit another one.
It atomically revokes the still-UNUSED A in the external store as
REVOKED_BY(intent UUID, payload hash, RESTORE_GENERATION), marks the local intent ABORTED_PRE_SUBMIT,
and requires a NEW signed A. If revocation cannot be proven because the external
store is unreachable, remain recovery-only; never consume or submit.

Crash after step 2 (external CAS committed) but before/at step 3 ⇒ A is BURNED
safely. On restart, external = CONSUMED but DB may lack the mirror; recovery
re-mirrors the known intent and NEVER creates a new intent. There is no path
that yields a second entry.

The control-plane store is the sole restore-fence authority. Its generation is
monotonic and cannot be restored from a trading-DB backup. A trading-DB restore
is detected by generation mismatch, invalidates every authorization bound to the
old generation, and forces RECOVERY_ONLY until a new signed authorization is
issued. If the control-plane store, CAS, or generation read is unavailable, the
application cannot ARM, RESERVE, or SUBMIT and remains recovery-only.

A is UNUSED only until either the consumption CAS succeeds or a pre-consumption
abort atomically moves it to REVOKED_BY. CONSUMED_BY and REVOKED_BY are terminal;
the OperationGate never consumes or revokes (it validates external+DB agreement
only, 6.02/6.03).

Rules:

Rejection, timeout, no-fill, crash, or ambiguity NEVER permits another entry
and never re-arms; the invariants max_entry_intents/orders/adapter_calls = 1
(6.02) are non-overridable.

Startup ALWAYS enters RECONCILING before scanner/background tasks start.

Any uncertainty after SUBMITTING becomes a durable AMBIGUOUS_INTERVENTION
state — never retry, never re-arm.

Only the normal scanner path may initiate the intent; there is no bypass
endpoint. The central C9 OperationGate (6.02) is the sole submission path.

External fence store as the at-most-once authority (BLOCKER-2 FIX — a DB restore
must never replay A):

The external store holding the A-nonce and RESTORE_GENERATION marker is OUTSIDE
the restorable trading DB and is the sole source of truth for whether A is
UNUSED, CONSUMED_BY, or REVOKED_BY. Restoring or hand-editing the trading DB
cannot change an external terminal record, so it can never resurrect a consumed
or revoked permit.

A monotonic RESTORE_GENERATION is bound into A. The only supported restore tool
must atomically increment the external generation before replacing/opening the
DB and must bind the restored snapshot identity to that new generation. Startup
compares the external generation, DB generation, snapshot identity, and A. An
out-of-band file replacement or any restore whose increment/binding cannot be
proved is UNTRUSTED recovery, never live authority. Thus a restored stale DB
cannot reuse A; a NEW authorization must be signed against the new generation.

MANDATORY DESIGN, selected BEFORE T and externally reviewed under C9 (a generic
"host counter or KV" is NOT yet a design): the concrete external store; its
durability boundary (independent failure domain from the trading DB); the exact
atomic CAS semantics and consistency guarantees; behavior on external-store
outage (fail-closed: no reservation may proceed if the fence is unreachable);
and full crash-recovery behavior across each step of the reservation protocol.
This design is a pre-T gate (Section 9) and part of the C9 crash/restore matrix
(7.02b), which MUST include a trading-DB restore that attempts to replay a
consumed A and demonstrates it cannot yield a second entry.

### 7.02a — Full broker-order state graph (BLOCKER-3 FIX)

Owner: Ops + safety

SUBMITTING enters an explicit broker state graph. These are possible transitions,
not a single linear sequence:

```text
SUBMITTING
├─> REJECTED_TERMINAL_ZERO_FILL
├─> ACKNOWLEDGED_WORKING
├─> PARTIALLY_FILLED
├─> FILLED
└─> UNKNOWN_INTERVENTION

ACKNOWLEDGED_WORKING
├─> PARTIALLY_FILLED
├─> FILLED
├─> CANCEL_PENDING
├─> CANCELLED
└─> UNKNOWN_INTERVENTION

PARTIALLY_FILLED
├─> PARTIALLY_FILLED
├─> FILLED
├─> CANCEL_PENDING
├─> CANCELLED
└─> UNKNOWN_INTERVENTION

CANCEL_PENDING
├─> CANCELLED
├─> PARTIALLY_FILLED
├─> FILLED
└─> UNKNOWN_INTERVENTION
```

State meanings and mandatory dispositions:

REJECTED_TERMINAL_ZERO_FILL is valid only after account-scoped broker
reconciliation proves terminal rejection, zero cumulative executions, and no
resulting position.

ACKNOWLEDGED_WORKING and PARTIALLY_FILLED are accepted non-terminal states. The
owned canary order must be target-cancelled by durable orderRef, intent UUID, and
broker IDs before closeout.

CANCELLED is terminal for the entry order but does NOT imply no exposure.
Reconciliation must determine cumulative filled quantity from execution IDs
deduplicated exactly once and from the account-scoped broker position. CANCELLED
with positive cumulative fill follows the same exposure disposition as
PARTIALLY_FILLED or FILLED.

Every positive cumulative filled quantity—whether reported before, during, or
after cancellation—must reach exactly one of:
(a) broker-CONFIRMED FLAT through a durable, idempotent, account-scoped exit
intent bounded to the broker-reported held quantity; or
(b) TRANSFERRED to a separately signed ownership plan naming the exact quantity,
broker account, protective stop/order, deadline, responsible operator, backup
operator, escalation contact, and independent verification evidence. The plan
must prove acceptance by the named operator and define what happens on missed
deadline or failed stop. An ownership transfer is valid only after those fields
and acceptance are verified; otherwise the position remains an unresolved
intervention and no closeout is permitted. A live position is never quietly
left open.

UNKNOWN_INTERVENTION means the broker outcome or account-scoped reconciliation
cannot be proven. It becomes durable AMBIGUOUS_INTERVENTION: remain OFF, continue
monitoring and broker fallback, and perform no closeout until the order is proven
broker-terminal and exposure is FLAT or signed-transferred.

Every AMBIGUOUS_INTERVENTION creates an incident with a named safety owner,
backup owner, broker-escalation contact, and review cadence (at least every 15
minutes while the market is open and hourly otherwise). The incident has a
maximum resolution deadline set by the canary policy; missed deadlines trigger
the signed operator escalation and permanent NO-GO disposition. It cannot be
silently carried into a later release or cleared by restarting the service.

Cancel and exit operations do not increase the entry quotas
max_entry_intents/orders/adapter_calls = 1. They remain separately idempotent,
account-scoped, ownership-checked, and quantity-bounded.

### 7.02b — Uniform stop / neutralization sequence (BLOCKER-4 FIX)

Owner: Ops + safety

EVERY stop condition—daily-loss lock, disconnect, trading halt, latency breach,
emergency stop, timeout, no-fill, rejection handling, or any other automatic
stop—uses the same ordered neutralization procedure. Master defaults can leave
resting GTC orders that fill later, so authority OFF is never sufficient alone:

1. Atomically close the entry gate and persist canary authority OFF.
2. Persist the stop reason and state durably; restart cannot silently re-arm.
3. Reconcile the account-scoped order, executions, and position from the broker.
4. If an accepted non-terminal owned order exists, target-cancel only that order
   by durable orderRef, intent UUID, and broker IDs. Never globally cancel.
5. Continue reconciliation until the entry order is broker-terminal.
6. If cumulative filled quantity is positive, cancel any remaining entry
   quantity first, then drive the broker-confirmed held quantity to CONFIRMED_FLAT
   using the bounded idempotent exit intent, or complete a signed ownership
   transfer.
7. If terminality, executions, or exposure cannot be proven, persist
   AMBIGUOUS_INTERVENTION, remain OFF, and continue broker fallback. Never label
   an unconfirmed outcome rejected, no-fill, cancelled, or closed.

The single canary remains consumed throughout this procedure; never re-enter.

C9 crash/restore matrix (required, automated) — must include, at minimum: crash
before PREPARE; crash after PREPARE but before CAS; concurrent consume-versus-
revoke CAS; crash after consumption CAS but before DB mirror; crash between
RESERVED and adapter call; crash after adapter call but before ack; crash
mid-partial-fill; crash after full fill; process restart; broker
disconnect/reconnect; late fill after cancel; and trading-DB restore from every
durable state (PREPARED, RESERVED, SUBMITTING, WORKING, partial/full fill,
terminal). Restore cases must attempt to replay a consumed/revoked A and prove
the RESTORE_GENERATION fence invalidates stale authority and forces recovery-
only. No case may yield a second intent, entry order, or entry adapter call.

### 7.03 — Execute the single canary

Owner: Ops + safety + Phase C owners

Steps (prerequisite: S=PASS, Q signed, B=PASS rehearsing exact Q, A issued):

Deploy T and the PROMOTED immutable images (no rebuild) to SCANNER_LIVE.

LIVE startup validates the LIVE-phase chain (T, S, Q, B, A) and actual
artifacts (6.04); RECONCILING runs before any task starts.

Sign the canary ARM (6.03): DISARMED → ARMED(A). Global entry authority
stays OFF; ordinary schedulers stay disabled.

The dedicated one-shot controller invokes the normal scanner path ONCE →
reservation protocol (7.01): PREPARE intent → EXTERNAL CAS (sole consume of A)
→ MIRROR to DB → pre-SUBMIT re-validation asserts A == CONSUMED_BY(this intent)
and external+DB agreement → SUBMITTING via the central gate.

Broker outcome handling per the full state graph (7.02a) using the uniform
stop sequence (7.02b):

REJECTED_TERMINAL_ZERO_FILL / timeout / proved no-fill ⇒ neutralize (7.02b),
NO-GO, record L plus failure evidence F.

ACKNOWLEDGED_WORKING with no fill on stop ⇒ target-cancel and reconcile to
CANCELLED with zero cumulative executions and zero exposure.

PARTIAL fill, including a late fill during cancellation ⇒ cancel remainder,
deduplicate and sum every execution, then use the bounded idempotent exit for
the broker-confirmed held quantity to CONFIRMED FLAT (or signed ownership transfer).

FULL fill ⇒ durable idempotent exit to broker-CONFIRMED FLAT (or signed
ownership transfer); a full fill is NEVER left live.

UNKNOWN/INTERVENTION ⇒ AMBIGUOUS_INTERVENTION; monitor + broker fallback.

Reconcile broker, order ledger, trade ledger, executions, and positions to an
order-terminal state plus FLAT or signed-transfer exposure disposition.

Observe for the ScannerCanaryPolicyV1 observation duration.

Close the dedicated canary permit via the tested rollback path; global entry
authority has remained OFF throughout. Verify runtime, external fence, and DB
all report terminal/stopped authority within maximum rollback time.

Record canonical live evidence L (7.04 schema).

Gate:

Exactly one intent reserved; no second entry under any outcome; authority OFF
and reconciled at close.

### 7.04 — Machine-verifiable signed evidence schemas (BLOCKER-8 FIX)

Owner: Safety + Phase C owners

P, S, Q, B, A, L, HOLD, and R_SUCCESS are each canonical signed artifacts
(canonical JSON + a human-readable Markdown rendering). Every non-success branch
produces canonical signed failure evidence F and a closeout R_NOGO. A failure
before the entry adapter call has F instead of L; a known, resolved NO-GO after
the adapter call has both L and F, with F referencing L. Each schema specifies:

Exact path and schema version.

Canonical encoding fixed NORMATIVELY as RFC 8785 JSON Canonicalization Scheme
(JCS) with UTF-8 output, and SHA-256 computed over the exact canonical bytes
(no "e.g."; this is the single mandated scheme).

Signature / trust mechanism (the normative signature mechanism defined in the
pre-T gates, Section 9) and enumerated signer identities.

Expiry and nonce rules.

Account-ID redaction (fingerprint only, never raw account).

Immutable commit/artifact identity references.

L additionally includes: intent/order reference, broker IDs, execution IDs, full
state-machine transitions, fills, commissions, pre/post positions, reconciliation
digests, actual artifact attestations (digests/hashes observed at runtime), and
stop/rollback timing.

F additionally names the last valid chain prefix, exact failure phase and reason,
external A state, intent/order/adapter-call counts, broker/exposure disposition,
and proof that entry authority is OFF. R_NOGO names that F, the applicable valid
prefix, the owner STOP/NO-EXPAND decision, and the exact non-success closeout commit.

SEMANTIC SAFETY ASSERTIONS (not just valid hashes) — L and the R_SUCCESS verifier
MUST assert ALL of the following; any violation ⇒ non-success (no release tag):

reserved_intents = 1

entry_orders = 1

entry_adapter_calls = 1

`entry_orders` counts the single durable outbound entry-order submission keyed
by intent/orderRef whether the broker rejects or accepts it; it never counts exit
or cancel operations.

unauthorized_orders = 0

entry_authority = OFF

working_canary_orders = 0

unresolved_interventions = 0

final_exposure ∈ { FLAT, SIGNED_TRANSFER }

broker_entry_order_terminal = true

execution_ids_unique = true

cumulative_filled_quantity/notional <= Q limits

The equal-to-one intent/order/adapter-call assertions apply to L only after the
entry adapter was called. F uses phase-specific expected values of zero or one,
but always enforces each count <= 1, unauthorized_orders = 0, authority OFF,
working canary orders = 0, unresolved interventions = 0, and FLAT or signed
transfer before closeout.

The chain verifier is invoked phase-specifically (per 6.04): PAPER startup, B
rehearsal, LIVE authorization/startup, pre-SUBMIT, and closeout. It validates only
the artifacts that exist at each phase, plus at pre-SUBMIT the exact external/DB
CONSUMED_BY tuple. R_SUCCESS CI validates the complete
P→S→Q→B→A→L→HOLD→R_SUCCESS chain and L assertions. R_NOGO CI validates only the
longest valid prefix plus F→owner STOP/NO-EXPAND→R_NOGO, proves that prohibited
downstream artifacts/authority do not exist, and applies the phase-specific F
assertions. A failure prefix is never presented as a complete success chain.

Storage: docs/release-evidence/ (evidence) and
docs/release-evidence/protocols/ (policy artifacts).

## SECTION 8 — SCANNER-RELEASE CLOSEOUT (R_SUCCESS / R_NOGO)

### 8.01 — Full tag chronology: candidate, closeout, CI, then outcome tag

Owner: Phase C owner + docs squad

C_C's CI and administrative C12 MUST already have passed before the candidate
tag and before P. Scanner closeout CI is separate and must not rerun or conflate
C_C.

The CANDIDATE/RUNTIME tag is created on T only after C_C CI and administrative
C12 PASS, and before the entire post-C12 scanner-release chain. Consequently P,
S, Q, B, A, and L all reference an already-immutable T deployed by digest.

Success closeout, in strict order:

1. Owner issues canonical signed HOLD/NO-EXPAND naming T, P, S, Q, B, A, and L.
2. Merge the evidence-only R_SUCCESS commit through protected master. It names
   T, every image/config/protocol/policy/strategy hash, the complete
   P→S→Q→B→A→L chain, and HOLD; updates `learning-log.md`; adds the AGENTS-form
   handoff `handoffs/YYYY-MM-DD-stage-c-scanner-canary.md`; and records wrap-up.
3. Run CI on the exact R_SUCCESS commit, including complete-chain and semantic
   verification.
4. Only after that CI passes, create the success outcome tag.

Non-success closeout uses the longest valid chain prefix plus F, an owner
STOP/NO-EXPAND decision, and R_NOGO. Its CI must prove authority OFF, no forbidden
downstream artifact/authority, broker terminality where an adapter call occurred,
and FLAT or signed-transfer exposure. Only after that CI passes may the non-
success outcome tag be created. AMBIGUOUS_INTERVENTION receives no closeout or
outcome tag until ambiguity is resolved and terminal exposure disposition is proven.

Tag names contain the FULL lowercase 40-character target OID; abbreviated SHAs
are forbidden:

```text
scanner-canary-candidate-${T}
scanner-canary-release-${R_SUCCESS}
scanner-canary-nogo-${R_NOGO}
```

T, R_SUCCESS, and R_NOGO must match `^[0-9a-f]{40}$` and resolve to the exact
intended commit. R_NOGO denotes a protected non-success closeout and is never a
release R_SUCCESS.

Every candidate/outcome tag is an annotated, cryptographically signed Git tag
verified against the Section 9.03 trust manifest. Creation uses the checked-in
interface:

```bash
set -euo pipefail
: "${TAG_KIND:?set literal candidate, release, or nogo}"
: "${TAG_NAME:?set the full-OID tag name above}"
: "${FULL_TARGET_OID:?set the full target OID}"
case "$TAG_KIND" in
  candidate|release|nogo) : ;;
  *) echo "invalid authorized tag kind; STOP"; exit 1 ;;
esac
scripts/create_verified_release_tag.sh \
  --kind "$TAG_KIND" \
  --name "$TAG_NAME" \
  --target "$FULL_TARGET_OID" \
  --trust-manifest docs/release-evidence/manifests/signing-trust-v1.json
```

The script treats only `git ls-remote --exit-code` status 2 as absence; network
or authentication errors stop. If present, the annotated tag object, signature,
and peeled `^{}` target must match. If absent, the script creates, locally
verifies, pushes, re-fetches, and remotely verifies the signed annotated tag. A
protected tag ruleset must exist before creation.

Gate:

Candidate tag `scanner-canary-candidate-${T}` points to full T. Exactly one
applicable outcome tag points to the full protected R_SUCCESS or R_NOGO commit
after its CI. All are signed annotated tags with verified peeled targets. Any
further rollout requires a separate owner-approved plan.

## SECTION 9 — MANDATORY PRE-T ACCEPTANCE CONTRACT

This governance amendment defines mandatory deliverables and stable verifier
interfaces. It does NOT claim those implementations, operational values, or
credentials exist today and does not authorize creating them. Separately
authorized C1–C12 work must create them before T.

Before T is established, every selected value, path, schema, command, signer,
store, and tool version must be literal and committed. The T gate rejects `TBD`,
`TODO`, illustrative/example values, `latest`, wildcard versions, unresolved
angle-bracket placeholders, missing files, unimplemented commands, skipped
sub-gates, and indirect evidence.

The root evidence index is exactly:

```text
docs/release-evidence/manifests/pre-t-gate-v1.json
```

The authoritative aggregate gate is exactly:

```bash
T_CANDIDATE="$(git rev-parse HEAD)"
[[ "$T_CANDIDATE" =~ ^[0-9a-f]{40}$ ]] || exit 1
python scripts/verify_pre_t.py \
  --repo-root . \
  --candidate "$T_CANDIDATE" \
  --manifest docs/release-evidence/manifests/pre-t-gate-v1.json
```

Only an aggregate PASS permits `T="$T_CANDIDATE"`.

### 9.01 — Toolchain contract

Exact Python, Node, npm, Docker Engine, Docker Compose, gitleaks, OCI builder,
provenance verifier, and signing-tool versions are recorded in:

```text
docs/release-evidence/manifests/toolchain-lock-v1.json
```

`.python-version`, `.nvmrc`, `dashboard/package.json` `packageManager`/`engines`,
CI, and Dockerfiles must agree with that manifest. Ranges and shorthand are
invalid. The current host Python 3.14.5 and missing gitleaks remain known failures
until separately authorized provisioning supplies the selected literal versions.

Verifier:

```bash
python scripts/verify_toolchain.py \
  --manifest docs/release-evidence/manifests/toolchain-lock-v1.json
```

### 9.02 — Reproducible dependencies and audits

Backend installation uses `backend/requirements.lock` with exact versions and
hashes and succeeds only with:

```bash
python -m pip install --require-hashes -r backend/requirements.lock
python -m pip check
python -m pip_audit -r backend/requirements.lock
```

Dashboard installation uses committed `dashboard/package-lock.json`:

```bash
cd dashboard
npm ci
npm audit --audit-level=high
```

The exact audit-tool versions are in the toolchain manifest. Every finding is
remediated or named by hash in an owner/risk-signed, expiring acceptance at
`docs/release-evidence/manifests/dependency-risk-acceptance-v1.json`. The
aggregate verifier rejects expired, unmatched, or blanket acceptance. No
floating build-time resolution is allowed.

### 9.03 — Canonicalization and signatures

RFC 8785 JCS over UTF-8 bytes and SHA-256 over those exact bytes are mandatory.
The normative signature mechanism is **Ed25519 detached signatures**, produced
by the pinned `sigstore`/`cosign` toolchain using organization-controlled,
non-exportable keys in the approved KMS. Trust is anchored to the committed
organization root/public-key set; key rotation, revocation, signer identities,
signature namespaces, and verification commands are defined in:

```text
docs/release-evidence/manifests/signing-trust-v1.json
```

One approved Ed25519 trust model covers P, S, Q, B, A, L, F, HOLD/STOP, R_SUCCESS,
R_NOGO, every policy artifact, and candidate/outcome tags. Owner/risk dual-
signature requirements are machine-checked; private keys are never committed.
The manifest MUST name the exact cosign/sigstore versions, KMS key IDs, signer
identities, quorum/dual-signature rule, and revocation behavior before T; a
placeholder, operator-local key, or unverifiable signature invalidates T.

Verifier interface:

```bash
python scripts/verify_release_signature.py \
  --trust-manifest docs/release-evidence/manifests/signing-trust-v1.json \
  --artifact "$LITERAL_ARTIFACT_PATH"
```

Each phase manifest supplies an exact artifact path; an unresolved shell value
or placeholder is forbidden at execution.

### 9.04 — Compose smoke

`docker-compose.test.yml` is committed, non-secret, and independent of ignored
operator `.env` files. The sole runner is:

```bash
scripts/run_compose_smoke.sh --candidate "$T_CANDIDATE"
```

The runner creates a unique Compose project; installs an EXIT/ERR/INT/TERM
cleanup trap before startup; always executes `down -v --remove-orphans`; proves
FastAPI and nginx startup/health; proves that no operator container, network,
volume, DB, or account was touched; and fails on any leaked test resource.

### 9.05 — OCI provenance

The sole pipeline interface is:

```bash
scripts/build_sign_verify_oci.sh \
  --candidate "$T_CANDIDATE" \
  --evidence docs/release-evidence/build/oci-build-v1.json
```

It builds backend/dashboard from the exact candidate, pushes immutable digests,
signs both, emits provenance, and verifies that source revision/material equals
the full `T_CANDIDATE` OID. Deployment references digests only. Failure to verify
registry digest, signature, or provenance-to-T binding fails closed.

### 9.06 — Scanner schemas and phase-aware verifier

Pre-T commits schemas/templates, not post-T evidence instances. Exact instance
paths are reserved as follows:

```text
docs/release-evidence/schemas/scanner-chain-v1.schema.json
docs/release-evidence/manifests/canary-hard-limits-v1.json
docs/release-evidence/protocols/scanner-soak-v1.json
docs/release-evidence/scanner/P.v1.json
docs/release-evidence/scanner/S.v1.json
docs/release-evidence/protocols/scanner-canary-v1.json
docs/release-evidence/scanner/B.v1.json
docs/release-evidence/scanner/A.v1.json
docs/release-evidence/scanner/L.v1.json
docs/release-evidence/scanner/F.v1.json
docs/release-evidence/scanner/HOLD.v1.json
docs/release-evidence/scanner/STOP.v1.json
docs/release-evidence/scanner/R-SUCCESS.v1.json
docs/release-evidence/scanner/R-NOGO.v1.json
```

`scanner-chain-v1.schema.json` contains versioned definitions for every listed
artifact type. `canary-hard-limits-v1.json` contains the immutable absolute
safety ceilings enforced by the frozen controller; it is part of
CANARY_POLICY_SCHEMA_HASH, and Q must be equal to or stricter than every bound.
For each present JSON artifact, the sibling rendering is obtained
by replacing `.json` with `.md`, and the detached signature record by replacing
`.json` with `.sig.json`; those derived paths are mandatory and unambiguous. The
single verifier is
`scripts/verify_scanner_chain.py`, with non-interchangeable modes:

```text
--phase paper-startup       T, P, PAPER artifacts/account/protocol only
--phase rollback-input      T, S, and exact Q only
--phase live-authorization  T, S, Q, B, and A
--phase live-startup        LIVE prefix plus actual runtime artifacts
--phase pre-submit          LIVE prefix plus exact external/DB CONSUMED_BY tuple
--phase success-closeout    P→S→Q→B→A→L→HOLD→R_SUCCESS plus L semantics
--phase nogo-closeout       longest valid prefix→F→STOP→R_NOGO plus F semantics
```

PAPER startup never requires Q, B, A, L, HOLD, or either closeout artifact. No phase may require an
artifact that cannot yet exist; every unexpected downstream artifact is fatal.

### 9.07 — Broker-call inventory

Exact inventory and checks:

```text
docs/release-evidence/manifests/broker-call-inventory-v1.json
```

```bash
python scripts/verify_broker_call_inventory.py \
  --manifest docs/release-evidence/manifests/broker-call-inventory-v1.json
python -m pytest backend/tests/test_scanner_live_operation_gate_inventory.py -v
```

Every route, MCP tool, task, scheduler, optimizer, replacement/resubmit path,
exit, cancel, and direct adapter call is classified. A new unlisted call site or
SCANNER_LIVE entry bypass fails closed.

### 9.08 — Runtime-file manifest

The exact manifest is:

```text
docs/release-evidence/manifests/runtime-file-manifest-v1.json
```

It classifies every ignored/runtime-only path and proves no unmanifested file can
alter code, config, credentials, DB selection, account routing, or Compose behavior.

### 9.09 — AGENTS and test-outcome gates

Every contributing checkpoint follows the repository `AGENTS.md`: read its
`sessions/stage-N-*-prompt.md`; run quality gates every five edits and before
commit; use an allowed conventional scope with a WHY body and stage reference;
create `handoffs/YYYY-MM-DD-stage-N-*.md`; update `learning-log.md`; and record
`/wrap-up`. `pre-t-gate-v1.json` indexes each required artifact.

The repository currently contains no checked-in `/handoff` or `/wrap-up`
executable. Where the interactive client provides those commands they are used;
portable proof is the checked-in handoff, learning-log entry, tracker transition,
and explicit Wrap-up/Stop Boundary fields. The evidence must never claim that a
nonexistent repository command executed.

Before T, all resolved process paths and full SHAs are enumerated and checked by:

```text
docs/release-evidence/manifests/phase-c-process-artifacts-v1.json
scripts/check_phase_c_process_artifacts.py
```

```bash
python scripts/check_phase_c_process_artifacts.py \
  --manifest docs/release-evidence/manifests/phase-c-process-artifacts-v1.json \
  --candidate "$T_CANDIDATE"
```

The verifier rejects placeholders, missing files, non-40-character commits,
commits not ancestral to T, missing learning-log entries, missing Wrap-up/Stop
Boundary fields, or checkpoint/evidence/tracker disagreement.

Exact test-outcome policy and verifier paths are:

```text
docs/release-evidence/manifests/test-outcome-allowlist-v1.json
scripts/verify_test_outcomes.py
```

All pytest and Vitest invocations emit machine-readable reports. Then:

```bash
python scripts/verify_test_outcomes.py \
  --allowlist docs/release-evidence/manifests/test-outcome-allowlist-v1.json \
  --pytest-report "$PYTEST_JUNIT_XML" \
  --vitest-report "$VITEST_JUNIT_XML"
```

Unauthorized skip, xfail, XPASS, skipped Vitest case, expired allowlisting, or
empty test selection is a failure. The aggregate gate rejects an absent report.

### 9.10 — External CAS

Exact design record:

```text
docs/release-evidence/manifests/canary-fence-design-v1.json
```

It selects the concrete store, independent durability boundary, atomic primitive,
consistency model, provisioning, access control, outage behavior, backup/restore
interaction, RESTORE_GENERATION behavior, and terminal UNUSED→CONSUMED_BY and
UNUSED→REVOKED_BY transitions. The sole supported restore interface is
`scripts/restore_trading_db.py`; it increments the external generation before DB
replacement and binds snapshot identity as required by 7.01. Generic host-
counter/KV wording or an ungoverned raw-file restore is invalid.

Conformance command:

```bash
python -m pytest backend/tests/test_canary_fence_conformance.py -v
python -m pytest backend/tests/test_restore_generation_fence.py -v
```

It proves exactly one winner under concurrent consumption; terminal states never
return to UNUSED; revocation races cannot produce consumption; the DB mirror
cannot override external state; crash-before/after-CAS recovery is safe; an
external-store outage fails closed; and DB restore cannot produce a second
intent, entry order, or entry adapter call.

Gate:

`scripts/verify_pre_t.py` executes and aggregates every 9.01–9.10 verifier and
records literal command, exit status, tool versions, input hashes, and output
hashes. All pass before T. Any missing artifact, unresolved value, skipped
command, or indirect evidence means no valid T.

## TERMINAL SEQUENCE (AUTHORITATIVE)

Planning-PR authorization
→ docs-only amendment merge (v5.3.2.1)
→ v4 preservation
→ clean implementation base
→ disposition matrix approval
→ separately authorized C1–C12 runtime work
→ protected runtime merges and immutable builds
→ T
→ exact-T verification + external C9 result review
→ E_C
→ owner approval naming T / E_C
→ C_C
→ CI on C_C
→ administrative C12 PASS
→ create + protect CANDIDATE/RUNTIME tag on T (after C12 PASS, BEFORE P)
→ P: paper-soak authorization (names SOAK_PROTOCOL_HASH, created before P)
→ S: scanner-paper PASS evidence [S = NO-GO branch below]
→ Q: create + sign exact CANARY_POLICY_HASH (after S, BEFORE B)
→ B: end-to-end PAPER one-shot + rollback PASS rehearsing exact Q [B = NO-GO branch below]
→ A: signed one-intent live authorization (requires S=PASS, valid Q, B=PASS bound to Q)
→ arm dedicated canary permit (DISARMED → ARMED)
→ reservation protocol (7.01): PREPARE intent → EXTERNAL CAS (sole consume) →
MIRROR to DB → gate validates external+DB agreement
→ broker state-graph outcome / target-cancel / partial+full-fill exit /
reconciliation to broker-terminal or signed ownership transfer
→ L (with semantic-safety assertions, 7.04)
→ owner HOLD / NO-EXPAND (names T, P, S, Q, B, A, L)
→ protected R_SUCCESS merge (names T, P, S, Q, B, A, L, HOLD)
→ CI on R_SUCCESS (validates full chain + semantic assertions)
→ create + protect scanner-canary-release-${R_SUCCESS} after CI
→ STOP

## FAILURE / NON-SUCCESS BRANCHES (authoritative; no success tag on any of these):

PRE-ARM validation failure ⇒ remain DISARMED; no intent and no adapter call. If
the artifact contains an otherwise valid authoritative external UNUSED A,
atomically revoke it before any later use. An invalid/unverifiable A is rejected
and grants no authority. Produce F only after the permit state is provably safe.

POST-ARM / PRE-CAS failure ⇒ no reservation or adapter call. Atomically transition
external A from UNUSED to REVOKED_BY, close any PREPARED record as
PREPARED_ABORTED, remain OFF, and prohibit re-arm or a second intent. If the
external store is unavailable, enter REVOCATION_PENDING/RECOVERY_ONLY; no
closeout or outcome tag is allowed until terminal external state is proven.

POST-CAS / PRE-ADAPTER failure ⇒ A remains CONSUMED_BY the exact intent; the
intent enters CLOSED_NO_GO_NO_SUBMIT. No submission, re-arm, second intent, or
entry adapter call is permitted. Produce phase-appropriate F.

AFTER THE ENTRY ADAPTER CALL ⇒ first reconcile through the broker graph. A known
REJECTED_TERMINAL_ZERO_FILL, ACKNOWLEDGED_WORKING, PARTIALLY_FILLED, FILLED,
CANCEL_PENDING, or CANCELLED outcome follows its exact 7.02a/7.02b path. Only an
outcome that cannot be proven, or conflicting order/execution/position evidence,
becomes AMBIGUOUS_INTERVENTION. Remain OFF and continue broker fallback; no final
closeout or outcome tag is allowed until broker terminality and FLAT-or-signed-
transfer exposure disposition are proven. Every resolved live NO-GO records L
and F before R_NOGO.

P invalid/expired, S=NO-GO, Q missing/invalid/unsigned/expired/outside the frozen
ceilings, or B=NO-GO/not bound to exact Q ⇒ no A is created; global entry
authority remains OFF; produce canonical F; owner issues STOP/NO-EXPAND; merge
protected R_NOGO; run its phase-specific CI; create only
`scanner-canary-nogo-${R_NOGO}`; STOP.

Every non-success branch produces canonical phase-appropriate F and may reach
R_NOGO only after all applicable authority, broker, order, execution, and
exposure states are terminal and verified. It never receives R_SUCCESS or a
release tag.

Exact financial and soak values are deferred to the mandatory signed policy
artifacts (fail-closed), which is intentional. This document remains
governance-only: it does not authorize implementation, paper soak, or live
trading, and grants no checkpoint authority.
