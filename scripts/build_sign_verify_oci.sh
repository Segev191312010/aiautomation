#!/usr/bin/env bash
# Fail-closed OCI pipeline interface for ULTRAPLAN candidate T.
set -Eeuo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
die() { printf 'oci-pipeline: %s\n' "$*" >&2; exit 1; }
candidate=
evidence=
while (($#)); do
  case "$1" in
    --candidate) (($# >= 2)) || die "--candidate requires a value"; candidate=$2; shift 2 ;;
    --evidence) (($# >= 2)) || die "--evidence requires a value"; evidence=$2; shift 2 ;;
    -h|--help) printf '%s\n' 'usage: scripts/build_sign_verify_oci.sh --candidate 40-char-oid --evidence relative-json'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ "$candidate" =~ ^[0-9a-f]{40}$ ]] || die "--candidate must be a lowercase 40-character commit OID"
[[ -n "$evidence" && "$evidence" != /* && "$evidence" != *..* ]] || die "--evidence must be a relative path without traversal"
head=$(git -C "$repo_root" rev-parse HEAD 2>/dev/null) || die "not a git checkout"
[[ "$head" == "$candidate" ]] || die "candidate does not match checked-out HEAD"
git -C "$repo_root" cat-file -e "${candidate}^{commit}" 2>/dev/null || die "candidate commit is unavailable"
[[ -f "$repo_root/$evidence" && ! -L "$repo_root/$evidence" ]] || die "evidence path must be an existing regular file"
[[ -f "$repo_root/docs/release-evidence/manifests/toolchain-lock-v1.json" ]] || die "toolchain lock is required before OCI operations"
[[ -f "$repo_root/docs/release-evidence/manifests/signing-trust-v1.json" ]] || die "signing trust manifest is required before OCI operations"
die "OCI build/sign/push is not authorized until reviewed registry, KMS, and provenance inputs are supplied"
