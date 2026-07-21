import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
VERIFY = ROOT / "scripts/verify_release_signature.py"
OCI = ROOT / "scripts/build_sign_verify_oci.sh"


def test_signature_verifier_rejects_missing_trust_manifest(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"artifact")
    result = subprocess.run(
        [str(VERIFY), "--trust-manifest", str(tmp_path / "missing.json"), "--artifact", str(artifact)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "trust manifest" in result.stderr


def test_signature_verifier_rejects_non_authorizing_manifest(tmp_path):
    trust = tmp_path / "trust.json"
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"artifact")
    trust.write_text(json.dumps({"schema_version": 1, "status": "template", "authority_granted": False}))
    result = subprocess.run(
        [str(VERIFY), "--trust-manifest", str(trust), "--artifact", str(artifact)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "not approved" in result.stderr


def test_oci_interface_rejects_missing_required_arguments():
    result = subprocess.run([str(OCI)], text=True, capture_output=True)
    assert result.returncode != 0
    assert "candidate" in result.stderr


@pytest.mark.parametrize("value", ["../artifact.json", "/tmp/artifact.json"])
def test_oci_interface_rejects_unsafe_evidence(value):
    oid = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    result = subprocess.run(
        [str(OCI), "--candidate", oid, "--evidence", value], text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "relative path" in result.stderr
