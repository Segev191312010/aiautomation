"""Regression tests for backend launch manifests."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKER_FLAG_RE = re.compile(r"--workers(?:=|\s+)(?P<count>[^\s\\]+)")
WORKER_OVERRIDE_RE = re.compile(
    r"(?im)^\s*(?:-\s*)?(?:(?:ENV|ARG)\s+|Environment=)?"
    r"(WORKERS|WEB_CONCURRENCY|UVICORN_WORKERS|GUNICORN_CMD_ARGS)"
    r"\s*(?:[:=]|\s+)"
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _worker_flag_values(text: str) -> list[str]:
    return [match.group("count").strip("\"'") for match in WORKER_FLAG_RE.finditer(text)]


def test_dockerfiles_pin_backend_to_one_worker():
    for relative_path in ("Dockerfile", "backend/Dockerfile"):
        text = _read(relative_path)

        assert _worker_flag_values(text) == ["1"]
        assert not WORKER_OVERRIDE_RE.search(text)


def test_compose_does_not_expose_worker_override():
    text = _read("docker-compose.yml")

    assert not WORKER_OVERRIDE_RE.search(text)


def test_compose_uses_host_stable_runtime_lock_volume():
    text = _read("docker-compose.yml")

    assert "RUNTIME_LOCK_PATH: \"/runtime/tradebot-runtime.lock\"" in text
    assert "- tradebot-runtime-lock:/runtime" in text
    assert "name: tradebot-runtime-lock" in text
    assert "./.runtime:/runtime" not in text

    for relative_path in ("Dockerfile", "backend/Dockerfile"):
        dockerfile = _read(relative_path)
        mount_setup = "mkdir -p /data /runtime && chown appuser:appgroup /data /runtime"
        assert mount_setup in dockerfile
        assert dockerfile.index(mount_setup) < dockerfile.index("USER appuser")


def test_operational_docs_do_not_recommend_multi_worker_backend():
    for relative_path in (
        "README.md",
        "docs/DEPLOYMENT.md",
        "sessions/phase2-paper-soak-runbook.md",
    ):
        text = _read(relative_path)
        worker_values = _worker_flag_values(text)

        assert all(value == "1" for value in worker_values)
        assert not WORKER_OVERRIDE_RE.search(text)
