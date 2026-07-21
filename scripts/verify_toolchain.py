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
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


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
        if not isinstance(name, str) or not name or not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
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
        if any("\x00" in item for item in command):
            fail(f"{name}: version_command contains NUL")
        # A lock must invoke provisioned tools by stable command name.  An
        # operator-local absolute/relative path would make the attestation
        # non-reproducible and could silently verify a different binary.
        executable = command[0]
        if Path(executable).is_absolute() or executable in {".", ".."} or "/" in executable or "\\" in executable:
            fail(f"{name}: version_command executable must be a stable command name")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            fail(f"{name}: invalid version_regex: {exc}")
        if compiled.groups != 1:
            fail(f"{name}: version_regex must contain exactly one capture group")
        if "sha256" in entry and (not isinstance(entry["sha256"], str) or not SHA256.fullmatch(entry["sha256"])):
            fail(f"{name}: sha256 must be a 64-character hexadecimal digest")
        if "source" in entry and (not isinstance(entry["source"], str) or not entry["source"].strip()):
            fail(f"{name}: source must be a non-empty string")
        if "provenance" in entry and (not isinstance(entry["provenance"], str) or not entry["provenance"].strip()):
            fail(f"{name}: provenance must be a non-empty string")
        if "timeout_seconds" in entry and (
            isinstance(entry["timeout_seconds"], bool)
            or not isinstance(entry["timeout_seconds"], (int, float))
            or not 0 < entry["timeout_seconds"] <= 300
        ):
            fail(f"{name}: timeout_seconds must be in (0, 300]")
    required = value.get("required_files", [])
    if not isinstance(required, list) or any(not isinstance(item, str) or not item.strip() for item in required):
        fail("required_files must be a list of non-empty strings")
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
        candidate = (root / rel).resolve()
        if Path(rel).is_absolute() or root not in candidate.parents or not candidate.is_file():
            fail(f"required file missing or invalid: {rel!r}")


def check_executables(manifest: dict[str, Any]) -> None:
    """Prove the selected binaries actually report the pinned versions."""
    for name, entry in manifest["tools"].items():
        command = entry["version_command"]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=entry_timeout(manifest, name))
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            fail(f"{name}: version command failed: {exc}")
        output = (result.stdout + "\n" + result.stderr).strip()
        match = re.search(entry["version_regex"], output, re.MULTILINE)
        if not match or match.group(1) != entry["version"]:
            fail(f"{name}: executable output does not prove pinned version {entry['version']!r}")


def entry_timeout(manifest: dict[str, Any], name: str) -> float:
    value = manifest["tools"][name].get("timeout_seconds", 30)
    return float(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    try:
        manifest_path.resolve().relative_to(root)
    except ValueError:
        fail("manifest must be inside repo-root")
    manifest = check_manifest(manifest_path)
    check_repo(root, manifest)
    check_executables(manifest)
    print("toolchain verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
