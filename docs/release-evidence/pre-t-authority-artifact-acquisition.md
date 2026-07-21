# Pre-T authority-artifact acquisition checklist

This checklist is an implementation aid, not release evidence. It does not
grant authority, satisfy the pre-T gate, or substitute for an owner/risk
signature. The three artifacts below must be created only from separately
authorized, reviewable inputs; until then the aggregate gate must continue to
fail closed.

## Toolchain lock

Create `manifests/toolchain-lock-v1.json` only after the selected toolchain is
provisioned and its executable output and source checksum have been captured.
The manifest must list exact `x.y.z` versions and a runnable
`version_command`/single-capture-group `version_regex` for Python, Node, npm,
Docker Engine, Docker Compose, gitleaks, the OCI builder, the provenance
verifier, and the signing tool. Every declared checksum must be a verified
64-character SHA-256 digest, and every required file must be present in the
candidate checkout. `latest`, ranges, local operator paths, and unresolved
placeholders are invalid. The committed `.python-version`, `.nvmrc`, package
manager declaration, CI, and Dockerfiles must agree with the lock.

Known local observations (Python 3.12.13, Node 20.20.2/npm 10.8.2,
gitleaks 8.30.1, and cosign 3.1.2) are provisioning notes only. They are not
evidence of a complete lock, and must not be copied into a release manifest
without the corresponding source/checksum and executable verification.

## Dependency-risk acceptance

Create `manifests/dependency-risk-acceptance-v1.json` only after both
`pip_audit` and `npm audit --audit-level=high` have run against the locked
inputs. Each unresolved finding must be identified by package, advisory, and
lockfile/hash evidence; remediation or a narrowly scoped exception must be
owner/risk dual-signed, have an expiry, and be bound to the exact candidate
and lock digests. A blanket, empty, unsigned, expired, or wildcard acceptance
is invalid. If there are no findings, record the tool outputs and the signed
zero-finding result rather than manufacturing an empty acceptance object.

## Signing trust

Create `manifests/signing-trust-v1.json` only after the organization supplies
the approved non-exportable KMS key identifiers, Ed25519 public-key/root set,
signer identities, artifact namespaces, dual-signature/quorum rule, rotation,
revocation, and verification commands. The manifest must bind the exact
cosign/sigstore versions and RFC 8785 JCS plus SHA-256 canonicalization rules.
Private keys, operator-local keys, unverifiable signatures, and placeholder
identities are forbidden. Trust-manifest creation requires independent C9
review and owner acceptance; until those records exist, no artifact may be
treated as signed authority.

## Gate consequence

The pre-T manifest intentionally names these paths as required files. Missing
or incomplete artifacts are expected to produce a fail-closed result. Do not
relax `pre-t-gate-v1.json` or add permissive defaults to make the gate pass.
