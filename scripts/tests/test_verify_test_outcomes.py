import datetime as dt
import json
from pathlib import Path

import pytest

from scripts.verify_test_outcomes import VerificationError, verify_report


def write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value))
    return path


def test_pytest_pass_and_allowlisted_skip(tmp_path: Path):
    allow = write(tmp_path / "a.json", {"schema_version": 1, "entries": [{"test_id": "x", "status": "skip", "owner": "qa", "reason": "dependency", "expires": "2026-12-31"}]})
    report = write(tmp_path / "r.json", {"tests": [{"nodeid": "ok", "outcome": "passed"}, {"nodeid": "x", "outcome": "skipped"}]})
    assert verify_report(report, allow, "pytest", dt.date(2026, 7, 1))["status"] == "PASS"


@pytest.mark.parametrize("status", ["skipped", "xfail", "xpass", "failed"])
def test_unauthorized_or_bad_status_fails(tmp_path: Path, status: str):
    allow = write(tmp_path / "a.json", {"schema_version": 1, "entries": []})
    report = write(tmp_path / "r.json", {"tests": [{"nodeid": "x", "outcome": status}]})
    with pytest.raises(VerificationError):
        verify_report(report, allow, "pytest", dt.date(2026, 7, 1))


def test_vitest_empty_selection_fails(tmp_path: Path):
    allow = write(tmp_path / "a.json", {"schema_version": 1, "entries": []})
    report = write(tmp_path / "r.json", {"testResults": []})
    with pytest.raises(VerificationError, match="empty"):
        verify_report(report, allow, "vitest")


def test_expired_allowlist_fails_closed(tmp_path: Path):
    allow = write(tmp_path / "a.json", {"schema_version": 1, "entries": [{"test_id": "x", "status": "skip", "owner": "qa", "reason": "r", "expires": "2026-01-01"}]})
    report = write(tmp_path / "r.json", {"tests": [{"nodeid": "ok", "outcome": "passed"}]})
    with pytest.raises(VerificationError, match="expired"):
        verify_report(report, allow, "pytest", dt.date(2026, 7, 1))
