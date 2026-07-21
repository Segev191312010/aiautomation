#!/usr/bin/env python3
"""Fail-closed verification of the files that can affect a runtime image.

The manifest records the runtime roots and a canonical SHA-256 snapshot.  A
snapshot is deliberately independent of filesystem mtimes and permissions:
paths and bytes are hashed in sorted POSIX-path order.  Ignored runtime data is
also classified explicitly, so an operator cannot accidentally treat a dirty
runtime directory as an immutable build input.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

SCHEMA = 1
MANIFEST = "docs/release-evidence/manifests/runtime-file-manifest-v1.json"


def _fail(message: str) -> None:
    raise SystemExit(f"runtime-file-manifest verification failed: {message}")


def _repo_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        _fail(f"git ls-files failed: {exc}")
    return sorted(p.decode("utf-8") for p in result.stdout.split(b"\0") if p)


def _covered(path: str, roots: list[str], excludes: list[str]) -> bool:
    if any(fnmatch.fnmatch(path, pattern) for pattern in excludes):
        return False
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def _tree_hash(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in paths:
        data = (root / rel).read_bytes()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _has_symlink_component(root: Path, rel: str) -> bool:
    """Return true when a repository-relative path traverses a symlink.

    Runtime inputs must be regular files in the repository.  Following a
    symlink here could hash bytes outside the checkout (or change underneath
    verification), defeating the manifest's path/bytes guarantee.
    """
    current = root
    for component in Path(rel).parts:
        current = current / component
        try:
            if current.is_symlink():
                return True
        except OSError:
            # An inaccessible path is unsafe; the caller will report it as a
            # missing/unreadable runtime input rather than following it.
            return True
    return False


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"invalid manifest: {exc}")
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
        _fail("schema_version must be 1")
    for key in ("runtime_roots", "excluded_paths", "ignored_runtime_patterns"):
        if not isinstance(value.get(key), list) or (key != "excluded_paths" and not value[key]):
            _fail(f"{key} must be a list (and non-empty for runtime roots/patterns)")
        if any(not isinstance(item, str) or not item for item in value[key]):
            _fail(f"{key} entries must be non-empty strings")
    if value.get("hash_algorithm") != "sha256-path-length-bytes-v1":
        _fail("unsupported hash_algorithm")
    if not isinstance(value.get("tree_sha256"), str) or len(value["tree_sha256"]) != 64:
        _fail("tree_sha256 must be a 64-character hex digest")
    return value


def verify(root: Path, manifest_path: Path) -> list[str]:
    manifest = load_manifest(manifest_path)
    files = _repo_files(root)
    roots = manifest["runtime_roots"]
    excludes = manifest["excluded_paths"]
    runtime = [p for p in files if _covered(p, roots, excludes)]
    errors: list[str] = []
    if not runtime:
        errors.append("runtime roots resolve to no tracked files")
    # Every runtime-root file must be tracked and every declared root must be
    # repository-relative; this prevents an unmanifested source tree.
    for item in roots + excludes:
        item_path = Path(item)
        if item_path.is_absolute() or ".." in item_path.parts or "" in item_path.parts:
            errors.append(f"unsafe repository path pattern: {item}")
    # Never follow symlinked runtime roots/files.  This includes a symlinked
    # parent directory, not only the final path component.
    for item in roots:
        root_path = root / item
        if root_path.is_symlink() or _has_symlink_component(root, item):
            errors.append(f"runtime root traverses symlink: {item}")
    safe_runtime: list[str] = []
    for rel in runtime:
        if _has_symlink_component(root, rel):
            errors.append(f"runtime file traverses symlink: {rel}")
        else:
            safe_runtime.append(rel)
    actual = _tree_hash(root, safe_runtime)
    if actual != manifest["tree_sha256"]:
        errors.append(
            f"runtime tree digest mismatch: expected {manifest['tree_sha256']}, got {actual}"
        )
    # Dirty files matching runtime patterns are never silently accepted.  The
    # verifier reports them; callers may only proceed after cleanup/quarantine.
    try:
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True, capture_output=True, text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"git status failed: {exc}")
    else:
        for line in status:
            rel = line[3:] if len(line) > 3 else ""
            if any(fnmatch.fnmatch(rel, p) for p in manifest["ignored_runtime_patterns"]):
                errors.append(f"dirty ignored runtime path: {rel}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--manifest", default=MANIFEST)
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    path = Path(args.manifest)
    if not path.is_absolute():
        path = root / path
    errors = verify(root, path)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("runtime-file-manifest verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
