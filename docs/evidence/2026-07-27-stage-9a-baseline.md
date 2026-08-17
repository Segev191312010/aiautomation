# Stage 9A Baseline Evidence Manifest

**Captured:** 2026-07-27, Asia/Jerusalem  
**Purpose:** Development baseline for the live-safety program  
**Release status:** **NO-GO**

This manifest identifies the source and toolchain used to begin Stage 9A. It is
not a release attestation: the worktree was already dirty, no broker/account
identity was verified, and the Phase 0 decisions do not yet have human
approvals.

## Source Identity

| Field | Value |
|---|---|
| Repository branch | `feature/ultraplan-v4` |
| HEAD | `0bde712c01f3cc16f45c1e36a21d2fcac7fa3f8a` |
| HEAD timestamp | `2026-05-29T22:51:44+03:00` |
| HEAD subject | `fix(ultraplan-v4): hard paper fence — TV/Claude orders refused on a live broker account` |
| Initial tracked delta | 10 files changed, 99 insertions, 22 deletions |
| Initial index delta | none |
| Clean release candidate | no |

The initial worktree contained modified and untracked user files before Stage
9A began. Those changes are preserved and excluded from any Stage 9A release
claim. A clean commit/image must be created and independently identified before
Phase 0 can exit.

## Toolchain

| Tool | Version |
|---|---|
| Python | `3.11.15` |
| pytest | `9.0.3` |
| Node.js | `v22.22.2` |
| npm | `10.9.7` |
| Vitest | `4.0.18` |

## Input Fingerprints

SHA-256 fingerprints allow the evidence to be tied to inputs without copying
credentials or configuration values into the repository.

| Input | SHA-256 |
|---|---|
| `backend/requirements.txt` | `3bc36548f778928ce9d7a4fabf26f84867c81f73dea9a762bbadab36a811db94` |
| `dashboard/package-lock.json` | `8561509754db4a7e64de18b74311662ab6ab93bdaa7b94ba9ec4ae92f4702dd2` |
| `dashboard/package.json` | `5101a2840b0c0e5dae6bec319b5424ec4cce563027a5e776bc6c3611a01b1bd1` |
| `docker-compose.yml` | `509c451d6fb149a6759f780b44a041c1884778ded648ffa53eac596562d3ffe1` |
| `backend/.env` | Excluded: secret-bearing input; no raw-content fingerprint retained |

The environment file requested `AUTOPILOT_MODE=PAPER` when inspected. That
allowlisted, non-secret fact is recorded without hashing the complete
secret-bearing file. A future release needs a canonical sanitized configuration
manifest containing only approved keys/value classes. The persisted database
guardrail can override the environment value, so this is not evidence of the
effective runtime authority state. Stage 9A did not start the application,
connect to IBKR, or inspect/change the persisted mode.

## Pre-Launch Quality Baseline

The following gates passed before Stage 9A edits:

| Gate | Result |
|---|---|
| Backend pytest | 784 passed |
| Frontend Vitest | 364 passed |
| TypeScript typecheck | passed |
| Vite production build | passed |

These results establish only that the then-current dirty development tree
passed its suites. They do not prove order idempotency, broker-native
protection, complete reconciliation, continuous account-risk enforcement,
production authentication, or operational recovery.

## Missing Release Evidence

- Clean source commit and immutable image digest
- Sanitized, signed configuration manifest
- Approved broker account identifier and environment assertion
- Fixed broker client identity and single execution owner
- Dependency/SBOM and vulnerability evidence
- Approved model, prompt, routing, and fallback fingerprints
- Phase 0 ADR approvals and named accountable owners
- Deterministic fault-matrix results
- Paper-broker soak and recovery evidence
- Backup/restore and stop/rollback drill evidence
- Security review and penetration-test evidence

Until these items and all Phase 1 controls are complete, this repository is a
development system and must not be represented as ready for unattended
real-money operation.

