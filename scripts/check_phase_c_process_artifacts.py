#!/usr/bin/env python3
"""Fail-closed checker for Phase C process and outcome artifacts (Section 9.09)."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

SCHEMA = 1
DEFAULT_MANIFEST = "docs/release-evidence/manifests/phase-c-process-artifacts-v1.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], check=True,
                                capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"git {' '.join(args)} failed: {exc}") from exc
    return result.stdout.strip()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid manifest: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
        raise ValueError("schema_version must be 1")
    entries = value.get("required_artifacts")
    if not isinstance(entries, list) or not entries:
        raise ValueError("required_artifacts must be non-empty")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("each artifact needs a path")
        commit = entry.get("required_commit")
        if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
            raise ValueError(f"invalid required_commit for {entry.get('path')}")
        markers = entry.get("required_markers")
        if not isinstance(markers, list) or not markers or any(not isinstance(m, str) or not m for m in markers):
            raise ValueError(f"required_markers missing for {entry['path']}")
    tracker = value.get("tracker")
    if not isinstance(tracker, dict) or not isinstance(tracker.get("path"), str):
        raise ValueError("tracker path is required")
    return value


def verify(root: Path, manifest_path: Path, candidate: str) -> list[str]:
    try:
        manifest = _load(manifest_path)
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []
    try:
        candidate_sha = _git(root, "rev-parse", "--verify", f"{candidate}^{{commit}}")
    except ValueError as exc:
        return [str(exc)]
    if not SHA_RE.fullmatch(candidate_sha):
        errors.append("candidate does not resolve to a full commit SHA")
    placeholders = manifest.get("forbidden_placeholders", [])
    for entry in manifest["required_artifacts"]:
        rel = entry["path"]
        path = root / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            errors.append(f"unsafe artifact path: {rel}")
            continue
        if not path.is_file():
            errors.append(f"missing artifact: {rel}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
            actual_commit = _git(root, "rev-list", "-1", candidate_sha, "--", rel)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot inspect {rel}: {exc}")
            continue
        if actual_commit != entry["required_commit"]:
            errors.append(f"stale artifact commit for {rel}: expected {entry['required_commit']}, got {actual_commit or 'none'}")
        try:
            _git(root, "merge-base", "--is-ancestor", entry["required_commit"], candidate_sha)
        except ValueError:
            errors.append(f"artifact commit is not ancestral to candidate: {rel}")
        for marker in entry["required_markers"]:
            if marker not in content:
                errors.append(f"missing marker in {rel}: {marker}")
        if entry.get("scan_placeholders", True):
            for placeholder in placeholders:
                if placeholder in content:
                    errors.append(f"placeholder in {rel}: {placeholder}")
    tracker_path = root / manifest["tracker"]["path"]
    if tracker_path.is_file():
        tracker = tracker_path.read_text(encoding="utf-8")
        required = manifest["tracker"]["required_status"]
        if required not in tracker:
            errors.append(f"tracker missing required status: {required}")
        for forbidden in manifest["tracker"].get("forbidden_statuses", []):
            if forbidden in tracker:
                errors.append(f"tracker contains forbidden status: {forbidden}")
    else:
        errors.append(f"missing tracker: {manifest['tracker']['path']}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        manifest = root / manifest
    try:
        errors = verify(root, manifest, args.candidate)
    except Exception as exc:  # fail closed at the CLI boundary
        errors = [f"unexpected checker error: {exc}"]
    if errors:
        for error in errors:
            print(error)
        return 1
    print("phase-C process-artifacts verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
