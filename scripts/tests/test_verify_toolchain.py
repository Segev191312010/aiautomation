from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import verify_toolchain


def _manifest(tmp_path: Path, entry: dict | None = None) -> Path:
    path = tmp_path / "toolchain.json"
    path.write_text(json.dumps({"schema_version": 1, "tools": {"python": entry or {
        "version": "3.12.13", "version_command": ["python", "--version"],
        "version_regex": r"Python (3\.12\.13)",
    }}, "required_files": []}))
    return path


def test_manifest_accepts_exact_version_command_and_optional_provenance(tmp_path: Path):
    path = _manifest(tmp_path, {
        "version": "3.12.13", "version_command": ["python", "--version"],
        "version_regex": r"Python (3\.12\.13)",
        "sha256": "a" * 64, "source": "official release", "provenance": "signed manifest",
        "timeout_seconds": 10,
    })
    assert verify_toolchain.check_manifest(path)["schema_version"] == 1


@pytest.mark.parametrize("entry,needle", [
    ({"version": "latest", "version_command": ["python"], "version_regex": "(x)"}, "exact numeric"),
    ({"version": "1.2.3", "version_command": ["python"], "version_regex": "x"}, "exactly one"),
    ({"version": "1.2.3", "version_command": ["python\x00"], "version_regex": "(x)"}, "NUL"),
    ({"version": "1.2.3", "version_command": ["python"], "version_regex": "(x)", "sha256": "bad"}, "sha256"),
    ({"version": "1.2.3", "version_command": ["python"], "version_regex": "(x)", "timeout_seconds": 0}, "timeout_seconds"),
])
def test_manifest_rejects_ambiguous_or_unsafe_fields(tmp_path: Path, entry: dict, needle: str):
    with pytest.raises(SystemExit, match=needle):
        verify_toolchain.check_manifest(_manifest(tmp_path, entry))


@pytest.mark.parametrize("executable", ["/opt/tools/python", "./python", "tools\\python"])
def test_manifest_rejects_operator_local_executable_path(tmp_path: Path, executable: str):
    path = _manifest(tmp_path, {
        "version": "3.12.13", "version_command": [executable, "--version"],
        "version_regex": r"Python (3\.12\.13)",
    })
    with pytest.raises(SystemExit, match="stable command name"):
        verify_toolchain.check_manifest(path)


def test_required_files_reject_traversal(tmp_path: Path):
    path = _manifest(tmp_path)
    data = json.loads(path.read_text())
    data["required_files"] = ["../outside"]
    path.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match="required file missing or invalid"):
        verify_toolchain.check_repo(tmp_path, verify_toolchain.check_manifest(path))


def test_main_rejects_manifest_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    outside = tmp_path.parent / "external-toolchain.json"
    outside.write_text(json.dumps({"schema_version": 1, "tools": {
        "python": {"version": "3.12.13", "version_command": ["python", "--version"],
                   "version_regex": r"Python (3\\.12\\.13)"}
    }, "required_files": []}))
    monkeypatch.setattr("sys.argv", ["verify_toolchain.py", "--repo-root", str(tmp_path), "--manifest", str(outside)])
    with pytest.raises(SystemExit, match="inside repo-root"):
        verify_toolchain.main()
