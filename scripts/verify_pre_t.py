#!/usr/bin/env python3
"""Aggregate, fail-closed verifier for the ULTRAPLAN pre-T contract.

The aggregate gate is intentionally boring: the manifest is the allow-listed
evidence index, and every listed file and command must exist and pass.  It does
not infer success from a related artifact, a dirty worktree, or an omitted
sub-gate.  Commands are argv arrays (never shell strings) and may use the
literal ``{repo_root}`` and ``{candidate}`` substitutions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
SCHEMA = 1


class GateError(ValueError):
    pass


def _fail(message: str) -> None:
    raise SystemExit(f"pre-T verification failed: {message}")


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"invalid manifest {path}: {exc}")
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
        _fail("manifest schema_version must be 1")
    for key in ("required_files", "checks"):
        if not isinstance(value.get(key), list) or not value[key]:
            _fail(f"manifest {key} must be a non-empty list")
    for rel in value["required_files"]:
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            _fail(f"unsafe required file path: {rel!r}")
    for check in value["checks"]:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str):
            _fail("each check requires a name")
        argv = check.get("argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x for x in argv):
            _fail(f"{check.get('name', '<unnamed>')}: argv must be non-empty strings")
        if not isinstance(check.get("required_files", []), list):
            _fail(f"{check['name']}: required_files must be a list")
        if check.get("allow_failure", False) is not False:
            _fail(f"{check['name']}: allow_failure is forbidden")
    return value


def _safe_rel(root: Path, rel: str) -> Path:
    path = root / rel
    try:
        path.relative_to(root)
    except ValueError:
        _fail(f"path escapes repository: {rel}")
    return path


def _git(root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        _fail(f"git {' '.join(args)} failed: {exc}")
    return result.stdout.strip()


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(root: Path, candidate: str, manifest_path: Path) -> list[str]:
    manifest = _load(manifest_path)
    if not SHA.fullmatch(candidate):
        _fail("--candidate must be a lowercase 40-character commit SHA")
    # A candidate is immutable only if it resolves to a commit and is exactly
    # the checked-out revision being gated; accepting an arbitrary ancestor
    # would allow evidence from a different tree to masquerade as T.
    resolved = _git(root, ["rev-parse", f"{candidate}^{{commit}}"])
    if resolved != candidate:
        _fail(f"candidate does not resolve exactly: {candidate}")
    head = _git(root, ["rev-parse", "HEAD"])
    if head != candidate:
        _fail(f"candidate {candidate} is not HEAD {head}")
    errors: list[str] = []
    required = list(manifest["required_files"])
    for check in manifest["checks"]:
        required.extend(check.get("required_files", []))
    for rel in dict.fromkeys(required):
        path = _safe_rel(root, rel)
        if not path.is_file():
            errors.append(f"required file missing: {rel}")
    for check in manifest["checks"]:
        name = check["name"]
        argv = [item.format(repo_root=str(root), candidate=candidate) for item in check["argv"]]
        env = None
        if isinstance(check.get("env"), dict):
            env = dict(__import__("os").environ)
            for key, value in check["env"].items():
                if not isinstance(key, str) or not isinstance(value, str):
                    errors.append(f"{name}: env entries must be strings")
                    env = None
                    break
                env[key] = value.format(repo_root=str(root), candidate=candidate)
        try:
            result = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=int(check.get("timeout_seconds", 300)))
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{name}: could not execute: {exc}")
            continue
        if result.returncode != 0:
            output = (result.stdout + "\n" + result.stderr).strip().replace("\n", " ")[-500:]
            errors.append(f"{name}: exit {result.returncode}: {output}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--manifest", default="docs/release-evidence/manifests/pre-t-gate-v1.json")
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        manifest = root / manifest
    errors = verify(root, args.candidate, manifest)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"pre-T verification: PASS candidate={args.candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
