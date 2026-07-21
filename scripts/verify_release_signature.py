#!/usr/bin/env python3
"""Fail-closed interface for verifying ULTRAPLAN detached signatures."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

OID = re.compile(r"^[0-9a-f]{64}$")

def fail(message: str) -> None:
    raise SystemExit(f"release-signature verification failed: {message}")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trust-manifest", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--signature")
    args = parser.parse_args()
    trust = Path(args.trust_manifest)
    artifact = Path(args.artifact)
    signature = Path(args.signature) if args.signature else None
    for label, path in (("trust manifest", trust), ("artifact", artifact), ("signature", signature)):
        if path is not None and (not path.is_file() or path.is_symlink()):
            fail(f"{label} must be an existing regular file")
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
    fail("approved cryptographic verifier binding is not configured")

if __name__ == "__main__":
    main()
