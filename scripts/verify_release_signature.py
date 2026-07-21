#!/usr/bin/env python3
"""Fail-closed interface for verifying ULTRAPLAN detached signatures."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

OID = re.compile(r"^[0-9a-f]{64}$")
TRAVERSAL = re.compile(r"(?:^|/)\.\.(?:/|$)")

def fail(message: str) -> None:
    raise SystemExit(f"release-signature verification failed: {message}")

def checked_path(label: str, raw: str) -> Path:
    """Accept non-traversing, non-symlinked regular files."""
    if not raw or "\x00" in raw or TRAVERSAL.search(raw):
        fail(f"{label} path contains traversal or NUL")
    path = Path(raw)
    if not path.is_file() or path.is_symlink():
        fail(f"{label} must be an existing regular file")
    return path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trust-manifest", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--signature", required=True)
    args = parser.parse_args()
    trust = checked_path("trust manifest", args.trust_manifest)
    artifact = checked_path("artifact", args.artifact)
    signature = checked_path("signature", args.signature)
    if artifact.stat().st_size == 0 or signature.stat().st_size == 0:
        fail("artifact and signature must be non-empty")
    try:
        value = json.loads(trust.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid trust manifest: {exc}")
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        fail("trust manifest schema_version must be 1")
    if value.get("authority_granted") is not True or value.get("status") != "approved":
        fail("trust manifest is not approved; verification remains fail-closed")
    required = ("canonicalization", "signature_algorithm", "trusted_keys", "revocation")
    if any(key not in value for key in required):
        fail("trust manifest is incomplete")
    if value["canonicalization"] != "RFC8785-JCS-SHA256" or value["signature_algorithm"] != "Ed25519-detached":
        fail("unapproved canonicalization or signature algorithm")
    keys = value["trusted_keys"]
    if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or not OID.fullmatch(k) for k in keys):
        fail("trusted_keys must contain approved 32-byte public-key hashes")
    revocation = value["revocation"]
    if not isinstance(revocation, dict) or revocation.get("checked") is not True:
        fail("revocation status must be explicitly checked")
    if not isinstance(revocation.get("epoch"), int) or revocation["epoch"] < 0:
        fail("revocation epoch must be a non-negative integer")
    fail("approved cryptographic verifier binding is not configured")

if __name__ == "__main__":
    main()
