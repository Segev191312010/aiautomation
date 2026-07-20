#!/usr/bin/env python3
"""Fail-closed verification of the repository's pinned build toolchain."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

EXACT = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


def fail(message: str) -> None:
    raise SystemExit(f"toolchain verification failed: {message}")


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"cannot read {path}: {exc}")


def check_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid manifest: {exc}")
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        fail("schema_version must be 1")
    tools = value.get("tools")
    if not isinstance(tools, dict) or not tools:
        fail("tools must be a non-empty object")
    for name, entry in tools.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
            fail(f"{name}: version is required")
        version = entry["version"]
        if not EXACT.fullmatch(version) or version.lower() in {"latest", "tbd", "todo"}:
            fail(f"{name}: version must be an exact numeric x.y.z value")
        command = entry.get("version_command")
        pattern = entry.get("version_regex")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
            or not isinstance(pattern, str)
            or not pattern
        ):
            fail(f"{name}: version_command and version_regex are mandatory")
        try:
            re.compile(pattern)
        except re.error as exc:
            fail(f"{name}: invalid version_regex: {exc}")
    return value


def check_repo(root: Path, manifest: dict[str, Any]) -> None:
    tools = manifest["tools"]
    py = tools.get("python")
    if py and (root / ".python-version").exists():
        actual = read(root / ".python-version").strip()
        if actual != py["version"]:
            fail(f".python-version={actual!r} disagrees with python={py['version']!r}")
    node = tools.get("node")
    if node and (root / ".nvmrc").exists():
        actual = read(root / ".nvmrc").strip().removeprefix("v")
        if actual != node["version"]:
            fail(f".nvmrc={actual!r} disagrees with node={node['version']!r}")
    package = root / "dashboard" / "package.json"
    if package.exists() and isinstance(tools.get("node"), dict):
        data = json.loads(read(package))
        engines = data.get("engines", {})
        declared = engines.get("node")
        if declared is not None and declared != tools["node"]["version"]:
            fail(f"dashboard/package.json engines.node={declared!r} disagrees")
        manager = data.get("packageManager")
        npm = tools.get("npm")
        if manager is not None and npm and manager != f"npm@{npm['version']}":
            fail(f"packageManager={manager!r} disagrees with npm={npm['version']!r}")
    for rel in manifest.get("required_files", []):
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or not (root / rel).is_file():
            fail(f"required file missing or invalid: {rel!r}")


def check_executables(manifest: dict[str, Any]) -> None:
    """Prove the selected binaries actually report the pinned versions."""
    for name, entry in manifest["tools"].items():
        command = entry["version_command"]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            fail(f"{name}: version command failed: {exc}")
        output = (result.stdout + "\n" + result.stderr).strip()
        match = re.search(entry["version_regex"], output, re.MULTILINE)
        if not match or match.group(1) != entry["version"]:
            fail(f"{name}: executable output does not prove pinned version {entry['version']!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest = check_manifest(manifest_path)
    check_repo(root, manifest)
    check_executables(manifest)
    print("toolchain verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
