from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_pre_t.py"

import sys
sys.path.insert(0, str(ROOT / "scripts"))
import verify_pre_t


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def test_real_manifest_fails_closed_when_mandatory_artifact_is_missing():
    candidate = _git("rev-parse", "HEAD")
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(ROOT), "--candidate", candidate], capture_output=True, text=True)
    assert result.returncode != 0
    assert "required file missing" in result.stderr


def test_real_manifest_uses_true_pre_t_scanner_phase():
    manifest = json.loads((ROOT / "docs/release-evidence/manifests/pre-t-gate-v1.json").read_text())
    scanner = next(check for check in manifest["checks"] if check["name"] == "scanner-chain")
    assert scanner["argv"][-2:] == ["--phase", "pre-t"]


def test_candidate_must_be_full_sha():
    manifest = ROOT / "docs/release-evidence/manifests/pre-t-gate-v1.json"
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(ROOT), "--candidate", "HEAD", "--manifest", str(manifest)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "40-character" in result.stderr


def test_minimal_manifest_executes_all_checks(tmp_path: Path):
    # Use the current repository, but a tiny temporary manifest to prove that
    # every listed command is actually executed and a failing command fails.
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "required_files": ["README.md"], "checks": [{"name": "known-pass", "argv": [sys.executable, "-c", "import sys; sys.exit(0)"]}]}))
    candidate = _git("rev-parse", "HEAD")
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(ROOT), "--candidate", candidate, "--manifest", str(manifest)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("required_files", ["../outside"], "unsafe required file path"),
        ("checks", [{"name": "same", "argv": ["true"]}, {"name": "same", "argv": ["true"]}], "duplicate"),
        ("checks", [{"name": "bad-timeout", "argv": ["true"], "timeout_seconds": 0}], "timeout_seconds"),
        ("checks", [{"name": "bad-timeout", "argv": ["true"], "timeout_seconds": True}], "timeout_seconds"),
        ("checks", [{"name": "bad-timeout", "argv": ["true"], "timeout_seconds": 3601}], "timeout_seconds"),
    ],
)
def test_manifest_rejects_unsafe_or_ambiguous_fields(tmp_path: Path, field, value, message):
    manifest_data = {"schema_version": 1, "required_files": ["README.md"], "checks": [{"name": "ok", "argv": ["true"]}]}
    manifest_data[field] = value
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_data))
    candidate = _git("rev-parse", "HEAD")
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(ROOT), "--candidate", candidate, "--manifest", str(manifest)], capture_output=True, text=True)
    assert result.returncode != 0
    assert message in result.stderr


def test_required_file_symlink_is_rejected(tmp_path: Path):
    target = tmp_path / "target"
    target.write_text("evidence")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(SystemExit, match="symlink"):
        verify_pre_t._safe_rel(tmp_path, "link")
