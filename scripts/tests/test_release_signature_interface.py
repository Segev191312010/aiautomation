import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
VERIFY = ROOT / "scripts/verify_release_signature.py"
OCI = ROOT / "scripts/build_sign_verify_oci.sh"


def test_signature_verifier_rejects_missing_trust_manifest(tmp_path):
    artifact = tmp_path / "artifact"
    signature = tmp_path / "signature"
    artifact.write_bytes(b"artifact")
    signature.write_bytes(b"sig")
    result = subprocess.run(
        [str(VERIFY), "--trust-manifest", str(tmp_path / "missing.json"), "--artifact", str(artifact), "--signature", str(signature)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "trust manifest" in result.stderr


def test_signature_verifier_rejects_non_authorizing_manifest(tmp_path):
    trust = tmp_path / "trust.json"
    artifact = tmp_path / "artifact"
    signature = tmp_path / "signature"
    artifact.write_bytes(b"artifact")
    signature.write_bytes(b"sig")
    trust.write_text(json.dumps({"schema_version": 1, "status": "template", "authority_granted": False}))
    result = subprocess.run(
        [str(VERIFY), "--trust-manifest", str(trust), "--artifact", str(artifact), "--signature", str(signature)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "not approved" in result.stderr


def test_oci_interface_rejects_missing_required_arguments():
    result = subprocess.run([str(OCI)], text=True, capture_output=True)
    assert result.returncode != 0
    assert "candidate" in result.stderr


def test_signature_verifier_requires_checked_revocation(tmp_path):
    trust = tmp_path / "trust.json"
    artifact = tmp_path / "artifact"
    signature = tmp_path / "signature"
    artifact.write_bytes(b"artifact")
    signature.write_bytes(b"sig")
    trust.write_text(json.dumps({
        "schema_version": 1,
        "status": "approved",
        "authority_granted": True,
        "canonicalization": "RFC8785-JCS-SHA256",
        "signature_algorithm": "Ed25519-detached",
        "trusted_keys": ["a" * 64],
        "revocation": {"checked": False, "epoch": 1},
    }))
    result = subprocess.run(
        [str(VERIFY), "--trust-manifest", str(trust), "--artifact", str(artifact), "--signature", str(signature)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "revocation" in result.stderr


def test_signature_verifier_rejects_empty_signature(tmp_path):
    trust = tmp_path / "trust.json"
    artifact = tmp_path / "artifact"
    signature = tmp_path / "signature"
    artifact.write_bytes(b"artifact")
    signature.write_bytes(b"")
    trust.write_text(json.dumps({"schema_version": 1}))
    result = subprocess.run(
        [str(VERIFY), "--trust-manifest", str(trust), "--artifact", str(artifact), "--signature", str(signature)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "non-empty" in result.stderr


@pytest.mark.parametrize("value", ["../artifact.json", "/tmp/artifact.json"])
def test_oci_interface_rejects_unsafe_evidence(value):
    oid = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    result = subprocess.run(
        [str(OCI), "--candidate", oid, "--evidence", value], text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "relative path" in result.stderr
