"""Fail when local binary artifacts are placed in active repo paths."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


FORBIDDEN_SUFFIXES = {
    ".7z",
    ".bin",
    ".dll",
    ".dmg",
    ".dylib",
    ".exe",
    ".msi",
    ".pkg",
    ".rar",
    ".so",
    ".zip",
}

SCAN_DIRS = (
    ".github",
    "backend",
    "dashboard",
    "docs",
    "frontend",
    "handoffs",
    "sessions",
)

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".venv",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
}

ALLOWLIST: set[Path] = set()


def iter_forbidden_files(root: Path) -> list[Path]:
    findings: list[Path] = []

    for child in root.iterdir():
        if child.is_file() and child.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(child)

    for rel_dir in SCAN_DIRS:
        scan_root = root / rel_dir
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            if path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES:
                findings.append(path)

    return sorted(
        {
            path.relative_to(root)
            for path in findings
            if path.relative_to(root) not in ALLOWLIST
        },
        key=lambda value: value.as_posix().lower(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect local binary artifacts in active repository paths.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to scan. Defaults to this script's repo.",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    findings = iter_forbidden_files(root)

    if findings:
        print("Workspace hygiene failed. Move these binary artifacts out of the repo:")
        for path in findings:
            print(f"- {path.as_posix()}")
        return 1

    print("Workspace hygiene OK: no forbidden binary artifacts found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
