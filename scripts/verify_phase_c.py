"""Cross-platform, fail-closed Phase C verification driver.

The C0 verifier deliberately uses only the Python standard library.  It never
imports TradeBot runtime modules and it opens SQLite only below a directory it
created and marked for this run.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final


REPORT_SCHEMA_VERSION: Final = 1
MANIFEST_SCHEMA_VERSION: Final = 1
D14_EXPECTED_CURRENT_COUNT: Final = 77
D14_EXPECTED_MATCHED_FILE_COUNT: Final = 46
D14_EXPECTED_BROAD_OR_BARE_COUNT: Final = 188
D14_EXPECTED_PASS_COUNT: Final = 25
D14_EXPECTED_COMBINED_COUNT: Final = 213
D14_EXPECTED_AGGREGATE_HASH: Final = (
    "c620bbfad06d9d8a839c20fedfe23b96262c4fbf32f4491fbc660f5858c886f4"
)
D14_ACCEPTED_INVENTORY_DOCUMENT_HASH: Final = (
    "0935b532bc9ac03b59a4a9b5217dc312a8c463346b02be3574f2859e0f597bb3"
)
TEMP_PREFIX: Final = "tradebot-phase-c-"
TEMP_NAME_PATTERN: Final = re.compile(r"^tradebot-phase-c-[0-9a-f]{32}$")
MARKER_NAME: Final = ".tradebot-phase-c-owned.json"
CASE_ID_PATTERN: Final = re.compile(r"^C0-[A-Z0-9]+-[0-9]{3}$")
GIT_COMMIT_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
SAFE_ENVIRONMENT: Final = {
    "SIM_MODE": "true",
    "AUTOPILOT_MODE": "OFF",
}
VALID_RESULT_STATUSES: Final = frozenset({"pass", "fail", "skip", "xfail", "xpass"})
DEFAULT_LEGACY_PATHS: Final = (
    "trading_bot.db",
    "backend/trading_bot.db",
)
PRUNED_SCAN_DIRECTORIES: Final = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist"}
)
OPERATIONAL_DIRECTORIES: Final = frozenset(
    {
        "data",
        "backups",
        "staging",
        "runtime",
        ".runtime",
        "backend/data/bars",
        "backend/data/event_logs",
        "backend/backups",
        "backend/staging",
        "backend/runtime",
    }
)


class VerificationError(RuntimeError):
    """Base error for an invalid or failed verification operation."""


class SafetyViolation(VerificationError):
    """Raised before an operation could address an unowned path."""


class CaseFailure(VerificationError):
    """A case failure with structured, JSON-safe evidence."""

    def __init__(self, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


@dataclasses.dataclass(frozen=True)
class ArtifactMetadata:
    path: str
    kind: str
    size: int | None
    mtime_ns: int | None
    reason: str


@dataclasses.dataclass(frozen=True)
class Case:
    case_id: str
    description: str
    execute: Callable[["VerificationContext"], Mapping[str, Any]]


@dataclasses.dataclass(frozen=True)
class CaseResult:
    case_id: str
    status: str
    duration_ms: int
    details: Mapping[str, Any]


@dataclasses.dataclass(frozen=True)
class OutcomeAllowance:
    case_id: str
    status: str
    expires: dt.date
    reason: str


@dataclasses.dataclass(frozen=True)
class OutcomeAssessment:
    errors: tuple[str, ...]
    open_gates: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors and not self.open_gates


@dataclasses.dataclass(frozen=True)
class VerificationContext:
    repo_root: Path
    owned_root: "OwnedTempRoot"
    manifest_path: Path
    legacy_paths: tuple[Path, ...]
    expected_source_commit: str | None
    expected_remote_ref: str | None


def _absolute(path: os.PathLike[str] | str) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _paths_overlap(first: Path, second: Path) -> bool:
    return _is_relative_to(first, second) or _is_relative_to(second, first)


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _is_reparse(info: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _safe_repo_relative_path(relative: str) -> PurePosixPath:
    if not relative or "\\" in relative:
        raise CaseFailure("repository file path is empty or non-POSIX")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
        raise CaseFailure("repository file path is unsafe or non-canonical")
    return path


def _repo_file_identities(repo_root: Path, relative: str) -> tuple[Path, tuple[tuple[int, int], ...]]:
    path = _safe_repo_relative_path(relative)
    current = repo_root
    try:
        root_info = current.lstat()
    except OSError as exc:
        raise CaseFailure(
            "repository root metadata is unavailable",
            {"error_type": type(exc).__name__},
        ) from None
    if stat.S_ISLNK(root_info.st_mode) or _is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise CaseFailure("repository root is not a real, non-reparse directory")
    identities: list[tuple[int, int]] = [(root_info.st_dev, root_info.st_ino)]
    for index, part in enumerate(path.parts):
        current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise CaseFailure(
                "repository file path metadata is unavailable",
                {"path": relative, "error_type": type(exc).__name__},
            ) from None
        final = index == len(path.parts) - 1
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise CaseFailure("repository file path contains a symlink or reparse point", {"path": relative})
        if final and not stat.S_ISREG(info.st_mode):
            raise CaseFailure("repository content target is not a regular file", {"path": relative})
        if not final and not stat.S_ISDIR(info.st_mode):
            raise CaseFailure("repository content parent is not a directory", {"path": relative})
        identities.append((info.st_dev, info.st_ino))
    return current, tuple(identities)


def _read_repo_bytes(repo_root: Path, relative: str) -> bytes:
    path, identities = _repo_file_identities(repo_root, relative)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CaseFailure(
            "repository content could not be opened safely",
            {"path": relative, "error_type": type(exc).__name__},
        ) from None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != identities[-1]:
            raise CaseFailure("repository content identity changed before open", {"path": relative})
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read()
    finally:
        os.close(descriptor)
    _path_after, after_identities = _repo_file_identities(repo_root, relative)
    if identities != after_identities:
        raise CaseFailure("repository content path identity changed during read", {"path": relative})
    return payload


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _json_safe_error(exc: BaseException) -> Mapping[str, str]:
    return {"error_type": type(exc).__name__, "message": str(exc)}


def known_application_roots(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Return explicit/native TradeBot roots that must never be verifier-owned."""
    values = os.environ if environ is None else environ
    roots: set[Path] = set()
    home = _absolute(Path.home())
    local_app_data = values.get("LOCALAPPDATA")
    app_data = values.get("APPDATA")
    if local_app_data:
        roots.add(_absolute(Path(local_app_data) / "TradeBot"))
    if app_data:
        roots.add(_absolute(Path(app_data) / "TradeBot"))
    data_home = Path(values.get("XDG_DATA_HOME", home / ".local" / "share"))
    state_home = Path(values.get("XDG_STATE_HOME", home / ".local" / "state"))
    cache_home = Path(values.get("XDG_CACHE_HOME", home / ".cache"))
    roots.update(_absolute(root / "tradebot") for root in (data_home, state_home, cache_home))
    for variable in ("TRADEBOT_HOME", "DB_PATH", "RUNTIME_LOCK_PATH"):
        configured = values.get(variable)
        if not configured or not Path(configured).is_absolute():
            continue
        configured_path = _absolute(configured)
        roots.add(configured_path if variable == "TRADEBOT_HOME" else configured_path.parent)
    return tuple(sorted(roots, key=lambda item: os.path.normcase(str(item))))


def _validate_temp_location(
    candidate: Path,
    repo_root: Path,
    application_roots: Sequence[Path],
) -> None:
    temp_root = _absolute(tempfile.gettempdir())
    if candidate.parent != temp_root or not TEMP_NAME_PATTERN.fullmatch(candidate.name):
        raise SafetyViolation("Phase C root must be a nonce-named direct child of the OS temp root")
    if _paths_overlap(candidate, repo_root):
        raise SafetyViolation("Phase C root must not overlap the repository")
    if any(_paths_overlap(candidate, protected) for protected in application_roots):
        raise SafetyViolation("Phase C root must not overlap a configured or native application-data root")


@dataclasses.dataclass
class OwnedTempRoot:
    path: Path
    nonce: str
    repo_root: Path
    application_roots: tuple[Path, ...]
    root_identity: tuple[int, int]
    parent_identity: tuple[int, int]
    cleaned: bool = False

    @classmethod
    def create(
        cls,
        repo_root: os.PathLike[str] | str,
        requested: os.PathLike[str] | str | None = None,
        application_roots: Sequence[os.PathLike[str] | str] | None = None,
    ) -> "OwnedTempRoot":
        nonce = uuid.uuid4().hex
        candidate = _absolute(requested or (Path(tempfile.gettempdir()) / f"{TEMP_PREFIX}{nonce}"))
        if requested is not None:
            match = TEMP_NAME_PATTERN.fullmatch(candidate.name)
            nonce = candidate.name.removeprefix(TEMP_PREFIX) if match else nonce
        protected_source = (
            known_application_roots() if application_roots is None else application_roots
        )
        protected = tuple(_absolute(item) for item in protected_source)
        normalized_repo = _absolute(repo_root)
        _validate_temp_location(candidate, normalized_repo, protected)
        if _path_exists(candidate):
            raise SafetyViolation("Phase C root must not exist before the verifier creates it")
        candidate.mkdir(mode=0o700)
        created_stat = candidate.lstat()
        parent_stat = candidate.parent.lstat()
        owned = cls(
            candidate,
            nonce,
            normalized_repo,
            protected,
            (created_stat.st_dev, created_stat.st_ino),
            (parent_stat.st_dev, parent_stat.st_ino),
        )
        try:
            owned._write_marker()
        except BaseException:
            candidate.rmdir()
            raise
        return owned

    @property
    def marker_path(self) -> Path:
        return self.path / MARKER_NAME

    def _write_marker(self) -> None:
        payload = json.dumps(
            {"schema_version": 1, "nonce": self.nonce, "root": str(self.path)},
            sort_keys=True,
        ).encode("utf-8")
        descriptor = os.open(self.marker_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def validate_marker(self) -> Mapping[str, Any]:
        _validate_temp_location(self.path, self.repo_root, self.application_roots)
        root_stat = self.path.lstat()
        parent_stat = self.path.parent.lstat()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        root_attributes = getattr(root_stat, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(root_stat.st_mode)
            or not stat.S_ISDIR(root_stat.st_mode)
            or bool(reparse_flag and root_attributes & reparse_flag)
        ):
            raise SafetyViolation("Phase C owned root is not a real, non-reparse directory")
        if (root_stat.st_dev, root_stat.st_ino) != self.root_identity:
            raise SafetyViolation("Phase C owned root identity changed after creation")
        if (parent_stat.st_dev, parent_stat.st_ino) != self.parent_identity:
            raise SafetyViolation("Phase C temp parent identity changed after creation")
        marker_stat = self.marker_path.lstat()
        if stat.S_ISLNK(marker_stat.st_mode) or not stat.S_ISREG(marker_stat.st_mode):
            raise SafetyViolation("Phase C ownership marker is not a regular file")
        with self.marker_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        expected = {"schema_version": 1, "nonce": self.nonce, "root": str(self.path)}
        if payload != expected:
            raise SafetyViolation("Phase C ownership marker does not match this verifier run")
        return payload

    def cleanup(self) -> None:
        if self.cleaned:
            return
        self.validate_marker()
        shutil.rmtree(self.path)
        if _path_exists(self.path):
            raise SafetyViolation("Phase C owned root remains after cleanup")
        self.cleaned = True


class SafeEnvironment:
    """Set the mandatory safe environment and restore the caller on exit."""

    def __init__(self, root: OwnedTempRoot) -> None:
        self.root = root
        self.previous: dict[str, str | None] = {}

    def __enter__(self) -> Mapping[str, str]:
        values = {
            **SAFE_ENVIRONMENT,
            "TRADEBOT_HOME": str(self.root.path),
            "DB_PATH": str(self.root.path / "data" / "tradebot.db"),
        }
        for name, value in values.items():
            self.previous[name] = os.environ.get(name)
            os.environ[name] = value
        return values

    def __exit__(self, *_error: object) -> None:
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _metadata(path: Path, relative_path: str, reason: str) -> ArtifactMetadata:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        kind = "symlink"
    elif stat.S_ISDIR(info.st_mode):
        kind = "directory"
    elif stat.S_ISREG(info.st_mode):
        kind = "file"
    else:
        kind = "other"
    size = info.st_size if stat.S_ISREG(info.st_mode) else None
    return ArtifactMetadata(relative_path, kind, size, info.st_mtime_ns, reason)


def _live_file_reason(relative_path: str) -> str | None:
    path = PurePosixPath(relative_path)
    name = path.name.lower()
    is_env = name == ".env" or name.endswith(".env.local") or name.startswith(".env.")
    if is_env and not name.endswith((".example", ".sample", ".template")):
        return "environment secret/configuration artifact"
    if re.search(r"\.(?:db|sqlite|sqlite3)(?:-(?:wal|shm))?$", name):
        return "operational SQLite artifact"
    if name == "tradebot-runtime.lock":
        return "runtime ownership lock"
    if name.endswith(".pid"):
        return "runtime process identifier"
    if name.endswith(".log") and (len(path.parts) == 1 or path.parts[0] in {"backend", "logs"}):
        if relative_path != "docs/release-evidence/2026-07-10-phase-a-reverification-raw.log":
            return "runtime log artifact"
    return None


def scan_checkout_live_artifacts(repo_root: os.PathLike[str] | str) -> tuple[ArtifactMetadata, ...]:
    """Inspect path metadata only; never read a candidate artifact's contents."""
    root = _absolute(repo_root)
    findings: dict[str, ArtifactMetadata] = {}
    for relative in sorted(OPERATIONAL_DIRECTORIES):
        candidate = root / Path(relative)
        if _path_exists(candidate):
            findings[relative] = _metadata(candidate, relative, "runtime operational directory")
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in PRUNED_SCAN_DIRECTORIES)
        current_path = Path(current)
        for name in sorted(files):
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            reason = _live_file_reason(relative)
            if reason:
                findings[relative] = _metadata(candidate, relative, reason)
    return tuple(findings[path] for path in sorted(findings))


def _legacy_path(candidate: os.PathLike[str] | str, repo_root: Path) -> Path:
    supplied = Path(candidate)
    return _absolute(supplied if supplied.is_absolute() else repo_root / supplied)


def inventory_legacy_paths(
    repo_root: os.PathLike[str] | str,
    allowlisted_paths: Sequence[os.PathLike[str] | str],
) -> tuple[Mapping[str, Any], ...]:
    """Return lstat-only evidence for explicit candidates; never recurse or open."""
    root = _absolute(repo_root)
    if not allowlisted_paths:
        raise VerificationError("legacy path allowlist must not be empty")
    records: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(allowlisted_paths, start=1):
        candidate = _legacy_path(item, root)
        identity = os.path.normcase(str(candidate))
        if identity in seen:
            raise VerificationError(f"duplicate legacy candidate at allowlist index {index}")
        seen.add(identity)
        if _is_relative_to(candidate, root):
            label = candidate.relative_to(root).as_posix()
            scope = "repository"
        else:
            label = f"external-candidate-{index:03d}"
            scope = "external"
        if not _path_exists(candidate):
            records.append({"label": label, "scope": scope, "exists": False, "kind": "absent"})
            continue
        try:
            evidence = _metadata(candidate, label, "allowlisted legacy candidate")
        except OSError as exc:
            raise CaseFailure(
                "legacy candidate metadata is unavailable",
                {"label": label, "scope": scope, "error_type": type(exc).__name__},
            ) from None
        records.append(
            {
                "label": label,
                "scope": scope,
                "exists": True,
                "kind": evidence.kind,
                "size": evidence.size,
                "mtime_ns": evidence.mtime_ns,
            }
        )
    return tuple(records)


def _run_command(arguments: Sequence[str], cwd: Path, timeout: int = 20) -> str:
    executable = shutil.which(arguments[0])
    if executable is None:
        raise CaseFailure(f"required executable is unavailable: {arguments[0]}")
    completed = subprocess.run(
        [executable, *arguments[1:]],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise CaseFailure(
            f"command failed: {arguments[0]}",
            {"returncode": completed.returncode, "stderr": completed.stderr.strip()[:500]},
        )
    return completed.stdout.strip()


def _remote_commit(repo_root: Path, remote_ref: str) -> str:
    if not remote_ref.startswith("refs/heads/") or remote_ref == "refs/heads/":
        raise CaseFailure("expected remote ref must be a full refs/heads/* name")
    _run_command(("git", "check-ref-format", remote_ref), repo_root)
    remote_line = _run_command(
        ("git", "ls-remote", "--exit-code", "origin", remote_ref),
        repo_root,
    )
    commit = remote_line.split()[0] if remote_line else ""
    if not GIT_COMMIT_PATTERN.fullmatch(commit):
        raise CaseFailure("remote source ref did not resolve to a full Git commit hash")
    return commit


def collect_source_identity(
    repo_root: os.PathLike[str] | str,
    expected_source_commit: str | None = None,
    expected_remote_ref: str | None = None,
) -> Mapping[str, Any]:
    root = _absolute(repo_root)
    head = _run_command(("git", "rev-parse", "HEAD"), root)
    branch = _run_command(("git", "branch", "--show-current"), root)
    dirty = _run_command(("git", "status", "--porcelain=v1", "--untracked-files=all"), root)
    remote_sha = _remote_commit(root, "refs/heads/master")
    if not GIT_COMMIT_PATTERN.fullmatch(head):
        raise CaseFailure("source identity did not resolve to full Git commit hashes")
    if expected_source_commit is not None and not GIT_COMMIT_PATTERN.fullmatch(
        expected_source_commit
    ):
        raise CaseFailure("expected source commit must be a lowercase full Git commit hash")
    expected_ref_commit = (
        _remote_commit(root, expected_remote_ref) if expected_remote_ref is not None else None
    )
    if (
        expected_source_commit is not None
        and expected_ref_commit is not None
        and expected_source_commit != expected_ref_commit
    ):
        raise CaseFailure("expected source commit and live remote ref disagree")
    expected_commit = expected_ref_commit or expected_source_commit or remote_sha
    return {
        "head": head,
        "branch": branch,
        "clean": not dirty,
        "dirty_entry_count": len(dirty.splitlines()) if dirty else 0,
        "live_origin_master": remote_sha,
        "head_matches_live_origin_master": head == remote_sha,
        "expected_source_commit": expected_commit,
        "expected_remote_ref": expected_remote_ref,
        "head_matches_expected_source": head == expected_commit,
    }


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _node_lock_versions(repo_root: Path) -> Mapping[str, Any]:
    try:
        lock = json.loads(_read_repo_bytes(repo_root, "dashboard/package-lock.json").decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CaseFailure("dashboard package lock is not valid UTF-8 JSON") from exc
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise CaseFailure("dashboard package lock has no packages map")
    names = ("react", "typescript", "vite", "vitest")
    return {
        name: (packages.get(f"node_modules/{name}") or {}).get("version") for name in names
    }


def collect_versions(repo_root: os.PathLike[str] | str) -> Mapping[str, Any]:
    root = _absolute(repo_root)
    command_versions = {
        "git": _run_command(("git", "--version"), root),
        "node": _run_command(("node", "--version"), root),
        "npm": _run_command(("npm", "--version"), root),
    }
    python_packages = {
        name: _distribution_version(name) for name in ("fastapi", "pydantic", "aiosqlite", "pytest")
    }
    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "commands": command_versions,
        "python_packages": python_packages,
        "dashboard_lock": _node_lock_versions(root),
    }


def run_synthetic_sqlite_smoke(owned_root: OwnedTempRoot) -> Mapping[str, Any]:
    owned_root.validate_marker()
    database_path = _absolute(os.environ.get("DB_PATH", ""))
    if not _is_relative_to(database_path, owned_root.path):
        raise SafetyViolation("synthetic SQLite path is outside the verifier-owned root")
    if _path_exists(database_path):
        raise SafetyViolation("synthetic SQLite path must not exist before the smoke test")
    if _path_exists(database_path.parent):
        raise SafetyViolation("synthetic SQLite parent must not exist before the smoke test")
    database_path.parent.mkdir(mode=0o700)
    parent_stat = database_path.parent.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    parent_attributes = getattr(parent_stat, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(parent_stat.st_mode)
        or not stat.S_ISDIR(parent_stat.st_mode)
        or bool(reparse_flag and parent_attributes & reparse_flag)
    ):
        raise SafetyViolation("synthetic SQLite parent is not a real, non-reparse directory")
    parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
    connection = sqlite3.connect(database_path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        connection.execute("CREATE TABLE c0_smoke (id INTEGER PRIMARY KEY, marker TEXT NOT NULL)")
        connection.execute("INSERT INTO c0_smoke(marker) VALUES (?)", (owned_root.nonce,))
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        row_count = connection.execute("SELECT COUNT(*) FROM c0_smoke").fetchone()[0]
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        connection.close()
    owned_root.validate_marker()
    final_parent = database_path.parent.lstat()
    if (final_parent.st_dev, final_parent.st_ino) != parent_identity:
        raise SafetyViolation("synthetic SQLite parent identity changed during the smoke test")
    artifacts = []
    for suffix in ("", "-wal", "-shm"):
        artifact = Path(f"{database_path}{suffix}")
        if _path_exists(artifact):
            info = artifact.lstat()
            artifacts.append({"name": artifact.name, "size": info.st_size})
    if integrity != "ok" or row_count != 1:
        raise CaseFailure("synthetic SQLite integrity or row-count proof failed")
    return {
        "journal_mode": journal_mode,
        "integrity_check": integrity,
        "row_count": row_count,
        "checkpoint": list(checkpoint),
        "artifacts": artifacts,
    }


def _validate_manifest_path(relative: Any) -> str:
    if not isinstance(relative, str) or not relative:
        raise CaseFailure("D14 current_paths entries must be non-empty strings")
    if "\\" in relative:
        raise CaseFailure(f"D14 path is not POSIX-normalized: {relative}")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
        raise CaseFailure(f"D14 path is unsafe or non-canonical: {relative}")
    if not relative.startswith("backend/") or not relative.endswith(".py"):
        raise CaseFailure(f"D14 path is outside the backend Python boundary: {relative}")
    return relative


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalized_repo_source(repo_root: Path, relative: str) -> str:
    try:
        source = _read_repo_bytes(repo_root, relative).decode("utf-8")
    except UnicodeError as exc:
        raise CaseFailure(
            "D14 source cannot be read as UTF-8",
            {"path": relative, "error_type": type(exc).__name__},
        ) from None
    return source.replace("\r\n", "\n").replace("\r", "\n")


def _typed_string_list(manifest: Mapping[str, Any], key: str) -> list[str]:
    value = manifest.get(key)
    if not isinstance(value, list) or not value:
        raise CaseFailure(f"D14 manifest {key} must be a non-empty list")
    if any(not isinstance(item, str) or not item for item in value):
        raise CaseFailure(f"D14 manifest {key} entries must be non-empty strings")
    if len(value) != len(set(value)):
        raise CaseFailure(f"D14 manifest {key} entries must be unique")
    return value


def _compile_operational_patterns(census: Mapping[str, Any]) -> Mapping[str, re.Pattern[str]]:
    patterns = census.get("operational_patterns")
    required = {"broad_catch", "bare_catch", "standalone_pass"}
    if not isinstance(patterns, dict) or set(patterns) != required:
        raise CaseFailure("D14 operational_patterns must contain the exact three site kinds")
    compiled: dict[str, re.Pattern[str]] = {}
    for kind in sorted(required):
        expression = patterns[kind]
        if not isinstance(expression, str) or not expression:
            raise CaseFailure(f"D14 operational pattern {kind} must be a non-empty string")
        try:
            compiled[kind] = re.compile(expression)
        except re.error as exc:
            raise CaseFailure(f"D14 operational pattern {kind} does not compile") from exc
    return compiled


def _validate_baseline_source(manifest: Mapping[str, Any], repo_root: Path) -> Mapping[str, str]:
    baseline = manifest.get("baseline_source")
    if not isinstance(baseline, dict):
        raise CaseFailure("D14 baseline_source must be an object")
    relative = baseline.get("accepted_inventory_document")
    declared_hash = baseline.get("accepted_inventory_document_normalized_lf_sha256")
    if not isinstance(relative, str) or not isinstance(declared_hash, str):
        raise CaseFailure("D14 baseline_source document and hash must be strings")
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != relative
        or not relative.startswith("docs/")
        or not relative.endswith(".md")
    ):
        raise CaseFailure("D14 accepted inventory document path is unsafe")
    actual_hash = _sha256(_normalized_repo_source(repo_root, relative).encode("utf-8"))
    if declared_hash != actual_hash or actual_hash != D14_ACCEPTED_INVENTORY_DOCUMENT_HASH:
        raise CaseFailure("D14 accepted inventory document hash differs from the accepted boundary")
    return {"accepted_inventory_document": relative, "normalized_lf_sha256": actual_hash}


def _site_fingerprint(path: str, line: int, kind: str, source: str) -> str:
    payload = "\0".join((path, str(line), kind, source)).encode("utf-8")
    return _sha256(payload)


def _site_set_hash(sites: Sequence[Mapping[str, Any]]) -> str:
    payload = [
        {"kind": site["kind"], "line": site["line"], "normalized_source": site["normalized_source"]}
        for site in sites
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(encoded)


def _source_sites(
    relative: str,
    source: str,
    patterns: Mapping[str, re.Pattern[str]],
    first_id: int,
) -> list[Mapping[str, Any]]:
    sites: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        matches = [kind for kind, pattern in patterns.items() if pattern.fullmatch(line)]
        if len(matches) > 1:
            raise CaseFailure("D14 operational patterns overlap", {"path": relative, "line": line_number})
        if not matches:
            continue
        kind = matches[0]
        normalized = line.strip()
        site_number = first_id + len(sites)
        sites.append(
            {
                "baseline_id": f"D14-BASE-{site_number:04d}",
                "fingerprint_sha256": _site_fingerprint(relative, line_number, kind, normalized),
                "kind": kind,
                "line": line_number,
                "normalized_source": normalized,
            }
        )
    return sites


def _recompute_baseline_file(
    repo_root: Path,
    relative: str,
    patterns: Mapping[str, re.Pattern[str]],
    first_id: int,
) -> Mapping[str, Any]:
    source = _normalized_repo_source(repo_root, relative)
    sites = _source_sites(relative, source, patterns, first_id)
    broad_or_bare = sum(site["kind"] in {"broad_catch", "bare_catch"} for site in sites)
    standalone_pass = sum(site["kind"] == "standalone_pass" for site in sites)
    return {
        "broad_or_bare_catch_count": broad_or_bare,
        "combined_site_count": len(sites),
        "normalized_lf_source_sha256": _sha256(source.encode("utf-8")),
        "path": relative,
        "site_set_sha256": _site_set_hash(sites),
        "sites": sites,
        "standalone_pass_count": standalone_pass,
    }


def _validate_baseline_files(
    census: Mapping[str, Any],
    repo_root: Path,
    current_paths: Sequence[str],
) -> tuple[list[Mapping[str, Any]], list[str]]:
    baseline_files = census.get("baseline_files")
    if not isinstance(baseline_files, list) or any(not isinstance(item, dict) for item in baseline_files):
        raise CaseFailure("D14 baseline_files must be a list of objects")
    declared_paths = [item.get("path") for item in baseline_files]
    if declared_paths != list(current_paths):
        raise CaseFailure("D14 baseline_files paths must exactly equal sorted current_paths")
    patterns = _compile_operational_patterns(census)
    actual_files: list[Mapping[str, Any]] = []
    drift: list[str] = []
    next_id = 1
    for declared, relative in zip(baseline_files, current_paths, strict=True):
        actual = _recompute_baseline_file(repo_root, relative, patterns, next_id)
        actual_files.append(actual)
        next_id += actual["combined_site_count"]
        if declared != actual:
            drift.append(relative)
    return actual_files, drift


def _aggregate_site_sets(baseline_files: Sequence[Mapping[str, Any]]) -> str:
    payload = [
        {"path": item["path"], "site_set_sha256": item["site_set_sha256"]}
        for item in baseline_files
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(encoded)


def _baseline_totals(baseline_files: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    sites = [site for item in baseline_files for site in item["sites"]]
    broad = sum(site["kind"] == "broad_catch" for site in sites)
    bare = sum(site["kind"] == "bare_catch" for site in sites)
    standalone_pass = sum(site["kind"] == "standalone_pass" for site in sites)
    return {
        "inventory_file_count": len(baseline_files),
        "matched_file_count": sum(item["combined_site_count"] > 0 for item in baseline_files),
        "broad_catch_count": broad,
        "bare_catch_count": bare,
        "broad_or_bare_catch_count": broad + bare,
        "standalone_pass_count": standalone_pass,
        "combined_site_count": len(sites),
        "aggregate_site_sets_sha256": _aggregate_site_sets(baseline_files),
    }


def _validate_accepted_totals(totals: Mapping[str, Any]) -> None:
    accepted = {
        "inventory_file_count": D14_EXPECTED_CURRENT_COUNT,
        "matched_file_count": D14_EXPECTED_MATCHED_FILE_COUNT,
        "broad_or_bare_catch_count": D14_EXPECTED_BROAD_OR_BARE_COUNT,
        "standalone_pass_count": D14_EXPECTED_PASS_COUNT,
        "combined_site_count": D14_EXPECTED_COMBINED_COUNT,
        "aggregate_site_sets_sha256": D14_EXPECTED_AGGREGATE_HASH,
    }
    mismatches = {
        key: {"expected": value, "actual": totals.get(key)}
        for key, value in accepted.items()
        if totals.get(key) != value
    }
    if mismatches:
        raise CaseFailure("D14 recomputed census differs from the accepted boundary", {"mismatches": mismatches})


def _compile_capability_patterns(manifest: Mapping[str, Any]) -> Mapping[str, re.Pattern[str]]:
    entries = manifest.get("capability_trigger_patterns")
    if not isinstance(entries, list) or not entries or any(not isinstance(item, dict) for item in entries):
        raise CaseFailure("D14 capability_trigger_patterns must be a non-empty list of objects")
    compiled: dict[str, re.Pattern[str]] = {}
    for entry in entries:
        trigger_id = entry.get("id")
        expression = entry.get("regex")
        if not isinstance(trigger_id, str) or not trigger_id or trigger_id in compiled:
            raise CaseFailure("D14 capability trigger IDs must be non-empty and unique")
        if not isinstance(expression, str) or not expression:
            raise CaseFailure(f"D14 capability regex for {trigger_id} must be a non-empty string")
        try:
            compiled[trigger_id] = re.compile(expression, re.MULTILINE)
        except re.error as exc:
            raise CaseFailure(f"D14 capability regex for {trigger_id} does not compile") from exc
    return compiled


def _backend_python_sources(repo_root: Path) -> Iterable[tuple[str, str]]:
    backend = repo_root / "backend"
    for current, directories, files in os.walk(backend, followlinks=False):
        safe_directories: list[str] = []
        for name in sorted(directories):
            if name in {"tests", "__pycache__", ".pytest_cache"}:
                continue
            info = (Path(current) / name).lstat()
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                continue
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = Path(current) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISREG(info.st_mode):
                continue
            relative = path.relative_to(repo_root).as_posix()
            yield relative, _normalized_repo_source(repo_root, relative)


def _discover_capability_paths(
    repo_root: Path,
    patterns: Mapping[str, re.Pattern[str]],
) -> Mapping[str, list[str]]:
    matches = {trigger_id: [] for trigger_id in patterns}
    for relative, source in _backend_python_sources(repo_root):
        for trigger_id, pattern in patterns.items():
            if pattern.search(source):
                matches[trigger_id].append(relative)
    return matches


def _validate_capability_triggers(
    manifest: Mapping[str, Any],
    repo_root: Path,
    current_paths: Sequence[str],
) -> Mapping[str, Any]:
    descriptions = manifest.get("capability_triggers")
    if not isinstance(descriptions, list) or not descriptions or any(not isinstance(item, dict) for item in descriptions):
        raise CaseFailure("D14 capability_triggers must be a non-empty list of objects")
    description_ids = [item.get("id") for item in descriptions]
    if any(not isinstance(item, str) or not item for item in description_ids):
        raise CaseFailure("D14 capability trigger descriptions require non-empty IDs")
    patterns = _compile_capability_patterns(manifest)
    if len(description_ids) != len(set(description_ids)) or set(description_ids) != set(patterns):
        raise CaseFailure("D14 capability description and regex IDs must be unique and identical")
    matches = _discover_capability_paths(repo_root, patterns)
    discovered = sorted({path for paths in matches.values() for path in paths})
    unclassified = sorted(set(discovered) - set(current_paths))
    if unclassified:
        raise CaseFailure("D14 capability discovery found unclassified production paths", {"paths": unclassified})
    return {
        "capability_pattern_count": len(patterns),
        "triggered_path_count": len(discovered),
        "per_trigger_match_counts": {key: len(value) for key, value in sorted(matches.items())},
    }


def validate_d14_manifest(
    manifest_path: os.PathLike[str] | str,
    repo_root: os.PathLike[str] | str,
    expected_count: int = D14_EXPECTED_CURRENT_COUNT,
) -> Mapping[str, Any]:
    path = _absolute(manifest_path)
    root = _absolute(repo_root)
    if not _is_relative_to(path, root):
        raise CaseFailure("D14 manifest must be inside the repository")
    relative_manifest = path.relative_to(root).as_posix()
    try:
        manifest = json.loads(_read_repo_bytes(root, relative_manifest).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CaseFailure(
            "D14 manifest is not valid UTF-8 JSON",
            {"error_type": type(exc).__name__},
        ) from None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise CaseFailure("D14 manifest schema_version must equal 1")
    raw_paths = manifest.get("current_paths")
    if not isinstance(raw_paths, list):
        raise CaseFailure("D14 manifest current_paths must be a list")
    current_paths = [_validate_manifest_path(item) for item in raw_paths]
    if current_paths != sorted(current_paths) or len(current_paths) != len(set(current_paths)):
        raise CaseFailure("D14 current_paths must be sorted and unique")
    if len(current_paths) != expected_count:
        raise CaseFailure(
            "D14 current path count does not match the accepted boundary",
            {"expected": expected_count, "actual": len(current_paths)},
        )
    missing: list[str] = []
    invalid: list[str] = []
    for relative in current_paths:
        candidate = root / Path(relative)
        if not _path_exists(candidate):
            missing.append(relative)
            continue
        mode = candidate.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            invalid.append(relative)
    if missing or invalid:
        raise CaseFailure("D14 contains missing or non-regular source paths", {"missing": missing, "invalid": invalid})
    future_paths = _typed_string_list(manifest, "future_path_patterns")
    exclusions = _typed_string_list(manifest, "current_exclusions")
    baseline_source = _validate_baseline_source(manifest, root)
    census = manifest.get("baseline_census")
    if not isinstance(census, dict):
        raise CaseFailure("D14 baseline_census must be an object")
    actual_files, drift = _validate_baseline_files(census, root, current_paths)
    if drift:
        raise CaseFailure("D14 source/site baseline drift detected", {"paths": drift})
    totals = _baseline_totals(actual_files)
    declared_totals = {key: census.get(key) for key in totals}
    if declared_totals != totals:
        raise CaseFailure("D14 declared aggregate census does not match recomputed source")
    _validate_accepted_totals(totals)
    capability_evidence = _validate_capability_triggers(manifest, root, current_paths)
    return {
        "schema_version": manifest["schema_version"],
        "current_path_count": len(current_paths),
        "first_path": current_paths[0],
        "last_path": current_paths[-1],
        "future_pattern_count": len(future_paths),
        "current_exclusion_count": len(exclusions),
        "baseline_source": baseline_source,
        "baseline_census": totals,
        **capability_evidence,
    }


class CaseRegistry:
    def __init__(self, cases: Iterable[Case]) -> None:
        self._cases: dict[str, Case] = {}
        for case in cases:
            if not CASE_ID_PATTERN.fullmatch(case.case_id):
                raise VerificationError(f"invalid C0 case ID: {case.case_id}")
            if case.case_id in self._cases:
                raise VerificationError(f"duplicate C0 case ID: {case.case_id}")
            self._cases[case.case_id] = case
        if not self._cases:
            raise VerificationError("C0 case registry must not be empty")

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(self._cases)

    def select(self, requested: Sequence[str] | None) -> tuple[Case, ...]:
        if requested is None:
            return tuple(self._cases.values())
        normalized = tuple(item.strip() for item in requested if item.strip())
        if not normalized:
            raise VerificationError("C0 case selection must not be empty")
        if len(normalized) != len(set(normalized)):
            raise VerificationError("C0 case selection contains duplicate IDs")
        unknown = sorted(set(normalized) - set(self._cases))
        if unknown:
            raise VerificationError(f"unknown C0 case IDs: {', '.join(unknown)}")
        return tuple(self._cases[case_id] for case_id in normalized)


def validate_case_results(
    results: Sequence[CaseResult],
    expected_case_ids: Sequence[str],
    allowances: Sequence[OutcomeAllowance] = (),
    today: dt.date | None = None,
) -> OutcomeAssessment:
    errors: list[str] = []
    open_gates: list[str] = []
    expected = tuple(expected_case_ids)
    if not expected:
        errors.append("expected C0 case selection is empty")
    actual = tuple(result.case_id for result in results)
    if len(actual) != len(set(actual)):
        errors.append("C0 results contain duplicate case IDs")
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        errors.append(f"missing C0 case results: {', '.join(missing)}")
    if extra:
        errors.append(f"unexpected C0 case results: {', '.join(extra)}")
    allowance_map = {(item.case_id, item.status): item for item in allowances}
    current_date = today or _utc_now().date()
    for result in results:
        if result.status not in VALID_RESULT_STATUSES:
            errors.append(f"{result.case_id} has unknown status {result.status}")
            continue
        if result.status == "pass":
            continue
        if result.status in {"skip", "xfail"}:
            allowance = allowance_map.get((result.case_id, result.status))
            if allowance is None or allowance.expires < current_date or not allowance.reason.strip():
                errors.append(f"{result.case_id} has unexpected or expired {result.status}")
            else:
                open_gates.append(
                    f"{result.case_id} {result.status} allowed until {allowance.expires.isoformat()}: {allowance.reason}"
                )
            continue
        errors.append(f"{result.case_id} reported {result.status}")
    return OutcomeAssessment(tuple(errors), tuple(open_gates))


def _case_environment(context: VerificationContext) -> Mapping[str, Any]:
    context.owned_root.validate_marker()
    expected_home = str(context.owned_root.path)
    expected_db = str(context.owned_root.path / "data" / "tradebot.db")
    expected = {**SAFE_ENVIRONMENT, "TRADEBOT_HOME": expected_home, "DB_PATH": expected_db}
    mismatches = {name: os.environ.get(name) for name, value in expected.items() if os.environ.get(name) != value}
    if mismatches or not Path(expected_db).is_absolute():
        raise CaseFailure("safe Phase C environment is not active", {"mismatches": mismatches})
    return {"sim_mode": "true", "autopilot_mode": "OFF", "paths_absolute": True}


def _case_checkout(context: VerificationContext) -> Mapping[str, Any]:
    artifacts = scan_checkout_live_artifacts(context.repo_root)
    if artifacts:
        raise CaseFailure(
            "checkout contains known live/operator artifacts",
            {"artifacts": [dataclasses.asdict(item) for item in artifacts]},
        )
    return {"artifact_count": 0, "scan_mode": "metadata-only"}


def _case_source(context: VerificationContext) -> Mapping[str, Any]:
    identity = collect_source_identity(
        context.repo_root,
        context.expected_source_commit,
        context.expected_remote_ref,
    )
    if not identity["clean"]:
        raise CaseFailure("C0 source checkout is dirty", identity)
    if not identity["head_matches_expected_source"]:
        raise CaseFailure("C0 source HEAD does not match the required source commit", identity)
    return identity


def _case_versions(context: VerificationContext) -> Mapping[str, Any]:
    versions = collect_versions(context.repo_root)
    missing = [name for name, value in versions["python_packages"].items() if value is None]
    missing.extend(name for name, value in versions["dashboard_lock"].items() if value is None)
    if missing:
        raise CaseFailure("required version evidence is unavailable", {"missing": sorted(missing)})
    return versions


def _case_sqlite(context: VerificationContext) -> Mapping[str, Any]:
    return run_synthetic_sqlite_smoke(context.owned_root)


def _case_d14(context: VerificationContext) -> Mapping[str, Any]:
    return validate_d14_manifest(context.manifest_path, context.repo_root)


def _case_legacy(context: VerificationContext) -> Mapping[str, Any]:
    records = inventory_legacy_paths(context.repo_root, context.legacy_paths)
    return {"scan_mode": "allowlisted-lstat-only", "candidates": list(records)}


def default_registry() -> CaseRegistry:
    return CaseRegistry(
        (
            Case("C0-ENV-001", "safe environment and owned temporary root", _case_environment),
            Case("C0-CHECKOUT-001", "metadata-only checkout live-artifact refusal", _case_checkout),
            Case("C0-SOURCE-001", "clean Git and live source identity", _case_source),
            Case("C0-VERSION-001", "tool and dependency version evidence", _case_versions),
            Case("C0-SQLITE-001", "owned synthetic SQLite filesystem smoke", _case_sqlite),
            Case("C0-D14-001", "accepted D14 manifest validation", _case_d14),
            Case("C0-LEGACY-001", "allowlisted metadata-only legacy inventory", _case_legacy),
        )
    )


def execute_cases(cases: Sequence[Case], context: VerificationContext) -> tuple[CaseResult, ...]:
    results: list[CaseResult] = []
    for case in cases:
        started = time.perf_counter_ns()
        try:
            details = dict(case.execute(context))
            status = "pass"
        except CaseFailure as exc:
            details = {**_json_safe_error(exc), **exc.details}
            status = "fail"
        except BaseException as exc:
            details = _json_safe_error(exc)
            status = "fail"
        elapsed = max(0, (time.perf_counter_ns() - started) // 1_000_000)
        results.append(CaseResult(case.case_id, status, elapsed, details))
    return tuple(results)


def run_c0(
    repo_root: os.PathLike[str] | str,
    manifest_path: os.PathLike[str] | str,
    requested_cases: Sequence[str] | None = None,
    requested_temp_root: os.PathLike[str] | str | None = None,
    legacy_paths: Sequence[os.PathLike[str] | str] = DEFAULT_LEGACY_PATHS,
    expected_source_commit: str | None = None,
    expected_remote_ref: str | None = None,
) -> Mapping[str, Any]:
    started_at = _utc_now()
    root = _absolute(repo_root)
    registry = default_registry()
    cases = registry.select(requested_cases)
    owned = OwnedTempRoot.create(root, requested_temp_root)
    cleanup: dict[str, Any] = {"status": "pending"}
    results: tuple[CaseResult, ...] = ()
    assessment = OutcomeAssessment(("C0 cases did not execute",), ())
    try:
        normalized_legacy = tuple(_legacy_path(path, root) for path in legacy_paths)
        manifest = _absolute(manifest_path)
        context = VerificationContext(
            root,
            owned,
            manifest,
            normalized_legacy,
            expected_source_commit,
            expected_remote_ref,
        )
        with SafeEnvironment(owned):
            results = execute_cases(cases, context)
            assessment = validate_case_results(results, tuple(case.case_id for case in cases))
            if set(case.case_id for case in cases) != set(registry.case_ids):
                assessment = OutcomeAssessment(
                    (*assessment.errors, "formal C0 PASS requires every mandatory registry case"),
                    assessment.open_gates,
                )
    finally:
        try:
            owned.cleanup()
            cleanup = {"status": "pass", "root_removed": not _path_exists(owned.path)}
        except BaseException as exc:
            cleanup = {"status": "fail", **_json_safe_error(exc)}
    finished_at = _utc_now()
    overall_pass = (
        assessment.passed
        and cleanup["status"] == "pass"
        and cleanup.get("root_removed") is True
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "phase": "C0",
        "overall": "PASS" if overall_pass else "FAIL",
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": finished_at.isoformat(),
        "selected_case_ids": [case.case_id for case in cases],
        "cases": [dataclasses.asdict(result) for result in results],
        "errors": list(assessment.errors),
        "open_gates": list(assessment.open_gates),
        "cleanup": cleanup,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fail-closed TradeBot Phase C verification")
    subparsers = parser.add_subparsers(dest="command", required=True)
    c0 = subparsers.add_parser("c0", help="run the authorized C0 verification registry")
    c0.add_argument("--repo-root", default=".", help="clean repository checkout")
    c0.add_argument(
        "--manifest",
        default="scripts/phase_c_d14_manifest.json",
        help="repository-relative D14 machine-policy manifest",
    )
    c0.add_argument("--case", action="append", dest="cases", help="stable C0 case ID; repeatable")
    c0.add_argument("--temp-root", help="nonexistent nonce-named direct child of the OS temp root")
    c0.add_argument(
        "--legacy-path",
        action="append",
        dest="legacy_paths",
        help="explicit metadata-only legacy candidate; repeatable",
    )
    c0.add_argument(
        "--expected-source-commit",
        help="required lowercase full commit SHA (for a pushed PR candidate)",
    )
    c0.add_argument(
        "--expected-remote-ref",
        help="optional live origin refs/heads/* name that must resolve to the expected commit",
    )
    c0.add_argument("--json", action="store_true", help="emit the complete JSON report")
    return parser


def _failure_report(exc: BaseException) -> Mapping[str, Any]:
    now = _utc_now().isoformat()
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "phase": "C0",
        "overall": "FAIL",
        "started_at_utc": now,
        "finished_at_utc": now,
        "selected_case_ids": [],
        "cases": [],
        "errors": [f"{type(exc).__name__}: {exc}"],
        "open_gates": [],
        "cleanup": {"status": "not-started"},
    }


def _print_human(report: Mapping[str, Any]) -> None:
    print(f"Phase {report['phase']} verification: {report['overall']}")
    for result in report["cases"]:
        print(f"{result['case_id']}: {result['status'].upper()} ({result['duration_ms']} ms)")
    for error in report["errors"]:
        print(f"ERROR: {error}")
    for gate in report["open_gates"]:
        print(f"OPEN GATE: {gate}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repo_root = _absolute(arguments.repo_root)
        manifest = Path(arguments.manifest)
        if not manifest.is_absolute():
            manifest = repo_root / manifest
        report = run_c0(
            repo_root=repo_root,
            manifest_path=manifest,
            requested_cases=arguments.cases,
            requested_temp_root=arguments.temp_root,
            legacy_paths=arguments.legacy_paths or DEFAULT_LEGACY_PATHS,
            expected_source_commit=arguments.expected_source_commit,
            expected_remote_ref=arguments.expected_remote_ref,
        )
    except BaseException as exc:
        report = _failure_report(exc)
    if arguments.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
