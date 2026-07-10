# A1/A2 Late Workspace Binary Quarantine Evidence

Date: 2026-07-10

Stages: A1 - workspace binary inventory; A2 - quarantine and hygiene policy

Status: **TECHNICALLY REMEDIATED; OWNER DISPOSITION PENDING A12 SIGN-OFF**

## Detection Context

A completion audit after the main Phase A re-verification found an ignored
installer at the repository root. Git porcelain was clean because `.gitignore`
matched the file, but both the policy checker and the manual's hidden/ignored
all-file scan correctly treated an ignored root executable as a failure.

Source state at detection:

```text
branch: master
HEAD: 5bc95e43b08253c30c3be981351e989267c219b1
origin/master: 5bc95e43b08253c30c3be981351e989267c219b1
git status --porcelain: empty (the artifact was ignored)
```

The detached clean worktree used for the earlier raw replay did not contain
this local ignored artifact. Its recorded source results remain valid, but the
primary workspace had to be remediated before A1/A2 and downstream current-
checkout claims could pass again.

## Artifact Record

| Field | Value |
| --- | --- |
| Repository-relative path | `ntws-latest-standalone-windows-x64.exe` |
| Size | 169,291,448 bytes |
| SHA-256 | `3AD4F99AD7ACCA7E8773CCACB9EDB3C3C759E1BE9AC0AF84E0CA6D6CD2D50F82` |
| Git state | untracked and ignored by `.gitignore:12` (`*.exe`) |
| Created UTC | `2026-07-10T13:46:28.7303188Z` |
| Modified UTC | `2026-07-10T13:46:41.1971955Z` |
| Authenticode | `Valid` - signature verified |
| Signer | Interactive Brokers Group, Inc. (`OU=TWS`) |
| Signer certificate thumbprint | `222EDFB4FFDE9791C747A91C877077C4D1A43875` |
| Signer certificate validity | `2026-06-17T00:00:00Z` through `2027-07-09T23:59:59Z` |
| Issuer | DigiCert Trusted G4 Code Signing RSA4096 SHA384 2021 CA1 |
| Timestamp signer | DigiCert SHA256 RSA4096 Timestamp Responder 2025 1 |
| Alternate streams observed | default `:$DATA` stream only |
| Checker execution | never executed |

The valid signature identifies the installer; it does not make a repository-
root binary policy-compliant.

## Quarantine Disposition

The single file was moved without executing, deleting, or overwriting it to:

```text
$env:USERPROFILE\Downloads\trading-workspace-quarantine\2026-07-10\ntws-latest-standalone-windows-x64.exe
```

Post-move checks proved:

- the repository-root source path is absent;
- the destination exists outside the repository;
- the destination SHA-256 is unchanged;
- no existing destination file was overwritten.

Owner disposition remains deliberately pending. Final A12 acceptance must
explicitly approve retaining this signed installer at the external quarantine
path or direct a different non-repository disposition.

## Post-Quarantine Verification

The exact A1 scans and affected/downstream gates were first rerun after moving
the installer, while `master` was clean at
`5bc95e43b08253c30c3be981351e989267c219b1`:

```text
python scripts/check_workspace_hygiene.py
Workspace hygiene OK: no forbidden binary artifacts found.

tracked binary scan: 589 tracked paths; 0 findings; rg exit 1 (expected no-match)
hidden/ignored all-file scan: 0 findings; rg exit 1 (expected no-match)
supplemental tracked/active `.bin`, `.so`, and `.dylib` scan: 0 findings

backend full pytest: 640 passed
dashboard typecheck: PASS
dashboard build: PASS - 610 modules transformed
dashboard Vitest: 27 files / 372 tests passed

final HEAD == origin/master: true
final git status --porcelain: empty
```

## Expanded Policy Verification

The policy and executable manual were then expanded to enforce the original
checker's `.bin`, `.so`, and `.dylib` suffixes in addition to the existing eight
suffixes. Local pre-commit verification ran against the staged policy worktree
based on `5bc95e43b08253c30c3be981351e989267c219b1`; it did not claim that this
still-dirty evidence-writing worktree was the clean immutable source.

```text
implementation commit: 2b4db50101b6202eb7ac0a1d631264a122ea961d
GitHub Actions run: https://github.com/Segev191312010/aiautomation/actions/runs/29099407063
Dashboard Ubuntu job: PASS
Backend Ubuntu job: PASS

backend full pytest: 640 passed
dashboard typecheck: PASS
dashboard build: PASS - 610 modules transformed
dashboard Vitest: 27 files / 372 tests passed
workspace hygiene: PASS

11 isolated suffix probes: 11 rejected with exit 1 and the expected filename
11 temporary probe roots removed: PASS
post-probe real-workspace hygiene: PASS
policy/manual/regex/iglob set equality: 11 each; symmetric differences 0
manual PowerShell parser: 33 fenced blocks / 0 errors
```

GitHub Actions provides the clean, immutable same-commit execution proof for
the policy source. Current tracked and active-tree scans contain zero files with
any of the 11 enforced extensions.

## Stage Effect

- A1: transient regression found, quarantined, rescanned, and technically
  remediated; durable evidence is this record.
- A2: the checker correctly rejected the ignored executable; clean-workspace
  and all-11-suffix negative-probe verification passed after the policy change.
- A10/A11/A12: global gates passed after quarantine on the unchanged source;
  the later policy commit also passed local gates and Ubuntu CI. Owner/lead
  acceptance remains required.
