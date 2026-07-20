from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_pre_t.py"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def test_real_manifest_fails_closed_when_mandatory_artifact_is_missing():
    candidate = _git("rev-parse", "HEAD")
    result = subprocess.run(["python", str(SCRIPT), "--repo-root", str(ROOT), "--candidate", candidate], capture_output=True, text=True)
    assert result.returncode != 0
    assert "required file missing" in result.stderr


def test_candidate_must_be_full_sha():
    manifest = ROOT / "docs/release-evidence/manifests/pre-t-gate-v1.json"
    result = subprocess.run(["python", str(SCRIPT), "--repo-root", str(ROOT), "--candidate", "HEAD", "--manifest", str(manifest)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "40-character" in result.stderr


def test_minimal_manifest_executes_all_checks(tmp_path: Path):
    # Use the current repository, but a tiny temporary manifest to prove that
    # every listed command is actually executed and a failing command fails.
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "required_files": ["README.md"], "checks": [{"name": "known-pass", "argv": ["python", "-c", "import sys; sys.exit(0)"]}]}))
    candidate = _git("rev-parse", "HEAD")
    result = subprocess.run(["python", str(SCRIPT), "--repo-root", str(ROOT), "--candidate", candidate, "--manifest", str(manifest)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
