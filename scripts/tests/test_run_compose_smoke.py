from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run_compose_smoke.sh"
PRE_T_MANIFEST = ROOT / "docs/release-evidence/manifests/pre-t-gate-v1.json"


def _head() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()


def _run(
    *args: str, candidate_env: str | None = None, fake_docker: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("CANDIDATE_SHA", None)
    if candidate_env is not None:
        env["CANDIDATE_SHA"] = candidate_env
    if fake_docker is not None:
        docker = fake_docker / "docker"
        docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
        env["PATH"] = f"{fake_docker}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_governance_candidate_cli_reaches_docker_precondition(tmp_path: Path):
    result = _run("--candidate", _head(), fake_docker=tmp_path)

    assert result.returncode != 0
    assert "docker compose plugin is required" in result.stderr
    assert "--candidate" not in result.stderr


def test_legacy_environment_candidate_remains_compatible(tmp_path: Path):
    result = _run(candidate_env=_head(), fake_docker=tmp_path)

    assert result.returncode != 0
    assert "docker compose plugin is required" in result.stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((), "--candidate must be a lowercase 40-character commit OID"),
        (("--candidate",), "--candidate requires a value"),
        (("--candidate", "HEAD"), "lowercase 40-character"),
        (("--candidate", "A" * 40), "lowercase 40-character"),
        (("--unknown",), "unknown argument"),
        (
            ("--candidate", "a" * 40, "--candidate", "a" * 40),
            "--candidate may be specified only once",
        ),
    ],
)
def test_invalid_candidate_cli_fails_before_docker(args: tuple[str, ...], message: str):
    result = _run(*args)

    assert result.returncode != 0
    assert message in result.stderr


def test_cli_and_legacy_environment_must_agree():
    result = _run("--candidate", _head(), candidate_env="0" * 40)

    assert result.returncode != 0
    assert "--candidate and CANDIDATE_SHA disagree" in result.stderr


def test_help_documents_exact_governance_interface():
    result = _run("--help")

    assert result.returncode == 0
    assert result.stdout.strip() == (
        "usage: scripts/run_compose_smoke.sh --candidate 40-char-oid"
    )


def test_pre_t_manifest_invokes_compose_runner_with_candidate_cli():
    manifest = json.loads(PRE_T_MANIFEST.read_text(encoding="utf-8"))
    check = next(item for item in manifest["checks"] if item["name"] == "compose-smoke")

    assert check["argv"] == [
        "scripts/run_compose_smoke.sh",
        "--candidate",
        "{candidate}",
    ]
    assert "env" not in check
