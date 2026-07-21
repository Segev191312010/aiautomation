from __future__ import annotations

import json
from pathlib import Path

import pytest

from verify_authority_inputs import AuthorityInputError, verify


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    tool = _write(tmp_path / "tool.json", {"schema_version": 1, "authority_granted": True,
        "manifest_digest": "a" * 64, "tools": {"python": {"source": "official", "sha256": "b" * 64}}})
    common = {"schema_version": 1, "status": "approved", "authority_granted": True,
        "candidate_sha": "c" * 40, "lock_digest": "d" * 64,
        "owner_signature": {"identity": "owner@corp.invalid", "signature_ref": "sig-owner"},
        "risk_signature": {"identity": "risk@corp.invalid", "signature_ref": "sig-risk"},
        "signature_verification": {"status": "PASS"}, "expires_at": "2099-01-01T00:00:00Z"}
    dep = _write(tmp_path / "dep.json", common)
    signing = dict(common, kms_key_ids=["kms/prod-1"], public_roots=["root-ed25519"],
        signer_identities=["owner@corp.invalid"], artifact_namespaces=["registry/release"], verification_commands=["cosign verify"])
    return tool, dep, _write(tmp_path / "sign.json", signing)


def test_inputs_require_reviewed_shape(tmp_path: Path):
    verify(tmp_path, *_inputs(tmp_path))


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(authority_granted=False),
    lambda d: d.update(candidate_sha="bad"),
    lambda d: d.update(signature_verification={"status": "FAIL"}),
    lambda d: d.update(expires_at="2000-01-01T00:00:00Z"),
])
def test_approved_inputs_fail_closed(tmp_path: Path, mutation):
    tool, dep, signing = _inputs(tmp_path)
    doc = json.loads(dep.read_text())
    mutation(doc)
    dep.write_text(json.dumps(doc))
    with pytest.raises(AuthorityInputError):
        verify(tmp_path, tool, dep, signing)
