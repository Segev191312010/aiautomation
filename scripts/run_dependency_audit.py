#!/usr/bin/env python3
"""Capture real dependency-audit evidence for dependency-risk-acceptance-v1.

NON-AUTHORIZING. This script only RUNS the audits and records their raw output
plus content digests. It does NOT write dispositions, identities, signatures, or
any `authority_granted`/`status: approved` field. A human owner and risk
operator must review the captured findings and sign the resulting
`docs/release-evidence/manifests/dependency-risk-acceptance-v1.json`, which is
independently enforced by `scripts/verify_authority_inputs.py`.

What it does:
  - Runs `pip-audit` against backend/requirements.lock (JSON output).
  - Runs `npm audit --json` in dashboard/ (uses package-lock.json).
  - Writes raw reports under docs/release-evidence/audits/ with SHA-256 digests.
  - Prints a finding summary and the digests to paste into the (human-signed)
    dependency-risk-acceptance manifest.

Exit codes: 0 = audits captured (regardless of findings; triage is human work);
2 = a tool was missing or a report could not be written (fail closed on IO).

Usage:
  python scripts/run_dependency_audit.py --repo-root .
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, bytes]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=600)
    except FileNotFoundError:
        return 127, f"tool not found: {cmd[0]}".encode()
    except subprocess.TimeoutExpired:
        return 124, f"timeout: {' '.join(cmd)}".encode()
    # pip-audit / npm audit emit findings on stdout; keep stdout as the report.
    return proc.returncode, proc.stdout or proc.stderr


def _write_report(out_dir: Path, name: str, data: bytes) -> tuple[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_bytes(data)
    return str(path.relative_to(out_dir.parents[2])) if len(out_dir.parents) >= 3 else str(path), _sha256_bytes(data)


def _pip_findings(data: bytes) -> int:
    try:
        doc = json.loads(data or b"{}")
    except json.JSONDecodeError:
        return -1
    # pip-audit --format json => {"dependencies": [{"vulns": [...]}, ...]} or a list
    deps = doc.get("dependencies", doc) if isinstance(doc, dict) else doc
    if not isinstance(deps, list):
        return -1
    return sum(len(d.get("vulns", [])) for d in deps if isinstance(d, dict))


def _npm_findings(data: bytes) -> int:
    try:
        doc = json.loads(data or b"{}")
    except json.JSONDecodeError:
        return -1
    meta = (doc.get("metadata") or {}).get("vulnerabilities") if isinstance(doc, dict) else None
    if isinstance(meta, dict):
        return int(meta.get("total", sum(v for v in meta.values() if isinstance(v, int))))
    return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args()
    root = Path(args.repo_root).resolve()
    out_dir = root / "docs" / "release-evidence" / "audits"

    lock = root / "backend" / "requirements.lock"
    dashboard = root / "dashboard"

    summary: dict[str, object] = {"generated_at_utc": _now(), "authority_granted": False, "reports": {}}
    io_error = False

    # pip-audit
    if lock.is_file():
        code, data = _run(["pip-audit", "--format", "json", "-r", str(lock)])
        if code == 127:
            io_error = True
            summary["reports"]["pip"] = {"status": "TOOL_MISSING", "detail": data.decode(errors="replace")}
        else:
            rel, digest = _write_report(out_dir, "pip-audit.json", data)
            summary["reports"]["pip"] = {
                "raw_report_path": rel, "sha256": digest,
                "vulnerability_count": _pip_findings(data), "exit_code": code,
            }
    else:
        summary["reports"]["pip"] = {"status": "LOCK_MISSING", "expected": "backend/requirements.lock"}

    # npm audit
    if (dashboard / "package-lock.json").is_file():
        code, data = _run(["npm", "audit", "--json"], cwd=dashboard)
        if code == 127:
            io_error = True
            summary["reports"]["npm"] = {"status": "TOOL_MISSING", "detail": data.decode(errors="replace")}
        else:
            rel, digest = _write_report(out_dir, "npm-audit.json", data)
            summary["reports"]["npm"] = {
                "raw_report_path": rel, "sha256": digest,
                "vulnerability_count": _npm_findings(data), "exit_code": code,
            }
    else:
        summary["reports"]["npm"] = {"status": "LOCKFILE_MISSING", "expected": "dashboard/package-lock.json"}

    summary_path = out_dir / "dependency-audit-summary.json"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"could not write summary: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, indent=2))
    print(
        "\nNEXT (human, non-automatable): triage each finding, record dispositions, "
        "and have the owner + risk operator SIGN "
        "docs/release-evidence/manifests/dependency-risk-acceptance-v1.json "
        "(status=approved, authority_granted=true, candidate_sha, lock_digest, "
        "owner_signature, risk_signature, signature_verification=PASS, expires_at). "
        "verify_authority_inputs.py enforces this; this script grants no authority."
    )
    return 2 if io_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
