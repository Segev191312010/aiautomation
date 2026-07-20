from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.check_phase_c_process_artifacts import verify


def _repo(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "AGENTS.md").write_text("Read session prompt; quality gates every 5 edits; Generate handoff; Run /wrap-up\n")
    (root / "prompt.md").write_text("Scope and authority\nRequired validation\nWrap-up\nStop boundary\n")
    (root / "handoff.md").write_text("Scope and Authority\nValidation\nPreserved Boundaries\nWrap-up\nStop Boundary\n")
    (root / "learning.md").write_text("2026-07-20\nCompleted:\nLearned:\nVerified:\nNext:\n")
    (root / "tracker.md").write_text("Status: C0 PASS\nC1-C12 PLANNED - NOT AUTHORIZED\nC0\nC12\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "seed"], check=True)
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    manifest = {
        "schema_version": 1,
        "required_artifacts": [
            {"path": "AGENTS.md", "required_commit": commit, "required_markers": ["Read session prompt", "quality gates every 5 edits", "Generate handoff", "Run /wrap-up"], "scan_placeholders": False},
            {"path": "prompt.md", "required_commit": commit, "required_markers": ["Scope and authority", "Required validation", "Wrap-up", "Stop boundary"]},
            {"path": "handoff.md", "required_commit": commit, "required_markers": ["Scope and Authority", "Validation", "Preserved Boundaries", "Wrap-up", "Stop Boundary"]},
            {"path": "learning.md", "required_commit": commit, "required_markers": ["2026-07-20", "Completed:", "Learned:", "Verified:", "Next:"]},
            {"path": "tracker.md", "required_commit": commit, "required_markers": ["Status: C0 PASS", "C1-C12 PLANNED - NOT AUTHORIZED", "C0", "C12"]},
        ],
        "tracker": {"path": "tracker.md", "required_status": "C0 PASS", "forbidden_statuses": ["C1-C12 PASS"]},
        "forbidden_placeholders": ["TODO", "TBD", "<SHA>"],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return root, manifest_path, commit


def test_process_artifacts_pass(tmp_path: Path) -> None:
    root, manifest, commit = _repo(tmp_path)
    assert verify(root, manifest, commit) == []


def test_process_artifacts_reject_missing_field(tmp_path: Path) -> None:
    root, manifest, commit = _repo(tmp_path)
    (root / "handoff.md").write_text("Scope and Authority\nValidation\n")
    errors = verify(root, manifest, commit)
    assert any("missing marker" in error for error in errors)


def test_process_artifacts_reject_non_ancestor_candidate(tmp_path: Path) -> None:
    root, manifest, commit = _repo(tmp_path)
    subprocess.run(["git", "-C", str(root), "checkout", "-qb", "other"], check=True)
    (root / "tracker.md").write_text("Status: C1-C12 PASS\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "bad"], check=True)
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    errors = verify(root, manifest, head)
    assert any("stale artifact commit" in error or "forbidden status" in error for error in errors)
