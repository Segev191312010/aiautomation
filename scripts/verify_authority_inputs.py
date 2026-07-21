#!/usr/bin/env python3
"""Validate pre-T authority-input contracts without granting authority.

This checker only proves that supplied manifests have the minimum review and
binding fields. It does not verify cryptographic signatures or approve any
artifact; those operations remain the responsibility of the signed evidence
and independent C9 review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"(?i)(?:tbd|todo|example|placeholder|changeme|unknown|local[-_ ]only)")


class AuthorityInputError(ValueError):
    pass


def _fail(message: str) -> None:
    raise AuthorityInputError(message)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"invalid JSON {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"{path}: root must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or PLACEHOLDER.search(value):
        _fail(f"{label}: non-placeholder text is required")
    return value.strip()


def _sha(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    value = _text(value, label)
    if not pattern.fullmatch(value):
        _fail(f"{label}: invalid digest")
    return value


def _signed(doc: dict[str, Any], label: str) -> None:
    if doc.get("schema_version") != 1 or doc.get("status") != "approved":
        _fail(f"{label}: schema_version=1 and status=approved are required")
    if doc.get("authority_granted") is not True:
        _fail(f"{label}: authority_granted must be true in an approved input")
    _text(doc.get("candidate_sha"), f"{label}.candidate_sha")
    if not SHA40.fullmatch(doc["candidate_sha"]):
        _fail(f"{label}.candidate_sha: invalid commit SHA")
    _sha(doc.get("lock_digest"), f"{label}.lock_digest", SHA64)
    for role in ("owner_signature", "risk_signature"):
        sig = doc.get(role)
        if not isinstance(sig, dict):
            _fail(f"{label}.{role}: signature object required")
        _text(sig.get("identity"), f"{label}.{role}.identity")
        _text(sig.get("signature_ref"), f"{label}.{role}.signature_ref")
    verification = doc.get("signature_verification")
    if not isinstance(verification, dict) or verification.get("status") != "PASS":
        _fail(f"{label}.signature_verification.status must be PASS")
    expires = _text(doc.get("expires_at"), f"{label}.expires_at")
    try:
        expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"{label}.expires_at: invalid ISO-8601 timestamp")
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        _fail(f"{label}.expires_at: input is expired or timezone-less")


def verify(repo_root: Path, toolchain: Path, dependency: Path, signing: Path) -> None:
    # Keep this command intentionally fail-closed: absence is an error and no
    # generated defaults are accepted.
    for path in (toolchain, dependency, signing):
        try:
            path.resolve().relative_to(repo_root.resolve())
        except ValueError:
            _fail(f"manifest outside repository: {path}")
        if not path.is_file() or path.is_symlink():
            _fail(f"required authority manifest missing or symlinked: {path}")
    tool = _read(toolchain)
    if tool.get("schema_version") != 1 or not isinstance(tool.get("tools"), dict) or not tool["tools"]:
        _fail("toolchain: complete schema and non-empty tools are required")
    if tool.get("authority_granted") is not True:
        _fail("toolchain: authority_granted must be true")
    _sha(tool.get("manifest_digest"), "toolchain.manifest_digest", SHA64)
    for name, entry in tool["tools"].items():
        if not isinstance(entry, dict):
            _fail(f"toolchain.{name}: object required")
        _text(entry.get("source"), f"toolchain.{name}.source")
        _sha(entry.get("sha256"), f"toolchain.{name}.sha256", SHA64)
    _signed(_read(dependency), "dependency-risk")
    _signed(_read(signing), "signing-trust")
    trust = _read(signing)
    for key in ("kms_key_ids", "public_roots", "signer_identities", "artifact_namespaces", "verification_commands"):
        values = trust.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or PLACEHOLDER.search(v) for v in values):
            _fail(f"signing-trust.{key}: non-empty reviewed list required")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--toolchain", default="docs/release-evidence/manifests/toolchain-lock-v1.json")
    parser.add_argument("--dependency", default="docs/release-evidence/manifests/dependency-risk-acceptance-v1.json")
    parser.add_argument("--signing", default="docs/release-evidence/manifests/signing-trust-v1.json")
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    try:
        verify(root, *(root / p for p in (args.toolchain, args.dependency, args.signing)))
    except AuthorityInputError as exc:
        raise SystemExit(f"authority-input verification failed: {exc}")
    print("authority-input verification: PASS (no authority granted by verifier)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
