from __future__ import annotations

import builtins
import datetime as dt
import importlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

verifier = importlib.import_module("verify_phase_c")


def _temp_area(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    area = tmp_path / "os-temp"
    area.mkdir()
    monkeypatch.setattr(verifier.tempfile, "gettempdir", lambda: str(area))
    return area


def _owned_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> verifier.OwnedTempRoot:
    area = _temp_area(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    requested = area / f"{verifier.TEMP_PREFIX}{uuid.uuid4().hex}"
    return verifier.OwnedTempRoot.create(repo, requested, application_roots=())


def _git(cwd: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _case(case_id: str = "C0-TEST-001") -> verifier.Case:
    return verifier.Case(case_id, "synthetic", lambda _context: {"ok": True})


def _result(case_id: str, status: str) -> verifier.CaseResult:
    return verifier.CaseResult(case_id, status, 0, {})


def _write_manifest(root: Path, current_paths: list[str]) -> Path:
    manifest = {
        "schema_version": 1,
        "current_paths": current_paths,
        "future_path_patterns": ["backend/db/migrations/**/*.py"],
        "capability_triggers": ["schema DDL"],
        "current_exclusions": ["backend/db/alerts.py"],
    }
    path = root / "scripts" / "phase_c_d14_manifest.json"
    path.parent.mkdir()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_owned_root_sets_nonce_marker_and_cleans_only_owned_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _owned_root(tmp_path, monkeypatch)
    marker = owned.validate_marker()

    assert marker["nonce"] == owned.nonce
    assert marker["root"] == str(owned.path)
    assert owned.path.parent == Path(verifier.tempfile.gettempdir())

    owned.cleanup()
    assert not owned.path.exists()


def test_owned_root_refuses_existing_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = _temp_area(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    candidate = area / f"{verifier.TEMP_PREFIX}{uuid.uuid4().hex}"
    candidate.mkdir()

    with pytest.raises(verifier.SafetyViolation, match="must not exist"):
        verifier.OwnedTempRoot.create(repo, candidate, application_roots=())


def test_owned_root_refuses_non_temp_repository_and_app_data_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    area = _temp_area(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    non_temp = tmp_path / f"{verifier.TEMP_PREFIX}{uuid.uuid4().hex}"
    repo_candidate = repo / f"{verifier.TEMP_PREFIX}{uuid.uuid4().hex}"
    app_candidate = area / f"{verifier.TEMP_PREFIX}{uuid.uuid4().hex}"

    with pytest.raises(verifier.SafetyViolation, match="direct child"):
        verifier.OwnedTempRoot.create(repo, non_temp, application_roots=())
    with pytest.raises(verifier.SafetyViolation):
        verifier.OwnedTempRoot.create(repo, repo_candidate, application_roots=())
    with pytest.raises(verifier.SafetyViolation, match="application-data"):
        verifier.OwnedTempRoot.create(repo, app_candidate, application_roots=(app_candidate,))


def test_cleanup_refuses_tampered_marker_without_deleting_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _owned_root(tmp_path, monkeypatch)
    owned.marker_path.write_text("{}", encoding="utf-8")

    with pytest.raises(verifier.SafetyViolation, match="does not match"):
        owned.cleanup()

    assert owned.path.is_dir()
    shutil.rmtree(owned.path)


def test_cleanup_fails_if_recursive_removal_leaves_owned_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _owned_root(tmp_path, monkeypatch)
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(verifier.shutil, "rmtree", lambda _path: None)

    with pytest.raises(verifier.SafetyViolation, match="remains after cleanup"):
        owned.cleanup()

    assert owned.path.is_dir()
    assert not owned.cleaned
    real_rmtree(owned.path)


def test_owned_root_refuses_symlink_replacement_where_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _owned_root(tmp_path, monkeypatch)
    real_root = owned.path.with_name(f"{owned.path.name}-real")
    owned.path.rename(real_root)
    try:
        try:
            owned.path.symlink_to(real_root, target_is_directory=True)
        except OSError:
            real_root.rename(owned.path)
            owned.cleanup()
            return
        with pytest.raises(verifier.SafetyViolation, match="non-reparse"):
            owned.validate_marker()
    finally:
        if owned.path.is_symlink():
            owned.path.unlink()
        if real_root.exists():
            real_root.rename(owned.path)
    owned.cleanup()


def test_owned_root_refuses_same_path_directory_identity_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _owned_root(tmp_path, monkeypatch)
    displaced = owned.path.with_name(f"{owned.path.name}-displaced")
    owned.path.rename(displaced)
    owned.path.mkdir()
    shutil.copy2(displaced / verifier.MARKER_NAME, owned.path / verifier.MARKER_NAME)

    with pytest.raises(verifier.SafetyViolation, match="identity changed"):
        owned.validate_marker()

    shutil.rmtree(owned.path)
    displaced.rename(owned.path)
    owned.cleanup()


def test_safe_environment_is_absolute_and_restores_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _owned_root(tmp_path, monkeypatch)
    monkeypatch.setenv("SIM_MODE", "caller-value")
    monkeypatch.delenv("AUTOPILOT_MODE", raising=False)

    with verifier.SafeEnvironment(owned):
        assert os.environ["SIM_MODE"] == "true"
        assert os.environ["AUTOPILOT_MODE"] == "OFF"
        assert Path(os.environ["TRADEBOT_HOME"]).is_absolute()
        assert Path(os.environ["DB_PATH"]).is_absolute()
        assert Path(os.environ["DB_PATH"]).is_relative_to(owned.path)

    assert os.environ["SIM_MODE"] == "caller-value"
    assert "AUTOPILOT_MODE" not in os.environ
    owned.cleanup()


def test_checkout_scan_finds_ignored_secrets_sqlite_sidecars_and_runtime_paths_without_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "backend" / "data" / "event_logs").mkdir(parents=True)
    (repo / ".env").write_text("DO_NOT_READ", encoding="utf-8")
    (repo / "ignored.db-wal").write_bytes(b"DO_NOT_READ")
    (repo / "tradebot-runtime.lock").write_text("DO_NOT_READ", encoding="utf-8")
    (repo / ".env.example").write_text("SAFE_TEMPLATE", encoding="utf-8")
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("artifact was opened"))

    findings = verifier.scan_checkout_live_artifacts(repo)
    paths = {item.path for item in findings}

    assert ".env" in paths
    assert "ignored.db-wal" in paths
    assert "tradebot-runtime.lock" in paths
    assert "backend/data/event_logs" in paths
    assert ".env.example" not in paths


def test_source_identity_rejects_unpushed_head_and_accepts_live_candidate_ref(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", str(remote))
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "c0@example.invalid")
    _git(repo, "config", "user.name", "Phase C Test")
    (repo / "sentinel.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "sentinel.txt")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", "master")

    initial = verifier.collect_source_identity(repo)
    assert initial["head_matches_expected_source"] is True

    (repo / "sentinel.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "sentinel.txt")
    _git(repo, "commit", "-m", "candidate")
    head = _git(repo, "rev-parse", "HEAD")
    stale = verifier.collect_source_identity(repo)
    assert stale["head_matches_live_origin_master"] is False
    context = verifier.VerificationContext(repo, None, repo / "unused.json", (), None, None)
    with pytest.raises(verifier.CaseFailure, match="required source commit"):
        verifier._case_source(context)

    _git(repo, "push", "origin", "HEAD:refs/heads/c0-candidate")
    candidate = verifier.collect_source_identity(
        repo,
        expected_source_commit=head,
        expected_remote_ref="refs/heads/c0-candidate",
    )
    assert candidate["head_matches_expected_source"] is True
    with pytest.raises(verifier.CaseFailure, match="lowercase full Git commit"):
        verifier.collect_source_identity(repo, expected_source_commit="not-a-commit")


def test_legacy_inventory_is_allowlisted_lstat_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    backend = repo / "backend"
    backend.mkdir(parents=True)
    candidate = backend / "trading_bot.db"
    candidate.write_bytes(b"NOT_A_REAL_DATABASE_AND_MUST_NOT_BE_OPENED")
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("legacy DB was opened"))

    records = verifier.inventory_legacy_paths(repo, ("trading_bot.db", "backend/trading_bot.db"))

    assert records[0]["exists"] is False
    assert records[0]["label"] == "trading_bot.db"
    assert records[1]["exists"] is True
    assert records[1]["label"] == "backend/trading_bot.db"
    assert records[1]["kind"] == "file"
    assert records[1]["size"] == candidate.stat().st_size


def test_legacy_inventory_rejects_empty_and_duplicate_allowlists(tmp_path: Path) -> None:
    with pytest.raises(verifier.VerificationError, match="must not be empty"):
        verifier.inventory_legacy_paths(tmp_path, ())
    with pytest.raises(verifier.VerificationError, match="duplicate"):
        verifier.inventory_legacy_paths(tmp_path, ("one.db", "one.db"))


def test_legacy_inventory_sanitizes_external_candidate_labels_and_errors(tmp_path: Path) -> None:
    private_root = tmp_path.parent / "private-user-name"
    private_root.mkdir(exist_ok=True)
    candidate = private_root / "operator.db"
    candidate.write_bytes(b"sentinel")

    records = verifier.inventory_legacy_paths(tmp_path, (candidate,))

    encoded = json.dumps(records)
    assert records[0]["label"] == "external-candidate-001"
    assert records[0]["scope"] == "external"
    assert str(private_root) not in encoded
    with pytest.raises(verifier.VerificationError) as error:
        verifier.inventory_legacy_paths(tmp_path, (candidate, candidate))
    assert str(private_root) not in str(error.value)


def test_legacy_inventory_sanitizes_external_metadata_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root = tmp_path.parent / "private-metadata-error"
    private_root.mkdir(exist_ok=True)
    candidate = private_root / "operator.db"
    candidate.write_bytes(b"sentinel")
    original_lstat = Path.lstat

    def failing_lstat(path: Path) -> os.stat_result:
        if path == candidate:
            raise PermissionError(f"private path was {candidate}")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", failing_lstat)

    with pytest.raises(verifier.CaseFailure) as error:
        verifier.inventory_legacy_paths(tmp_path, (candidate,))

    assert str(private_root) not in str(error.value)
    assert str(private_root) not in json.dumps(error.value.details)


def test_synthetic_sqlite_smoke_uses_only_owned_absolute_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _owned_root(tmp_path, monkeypatch)
    with verifier.SafeEnvironment(owned):
        evidence = verifier.run_synthetic_sqlite_smoke(owned)

    assert evidence["integrity_check"] == "ok"
    assert evidence["row_count"] == 1
    assert {item["name"] for item in evidence["artifacts"]} >= {"tradebot.db"}
    owned.cleanup()


def test_synthetic_sqlite_smoke_refuses_outside_or_existing_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _owned_root(tmp_path, monkeypatch)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "operator.db"))
    with pytest.raises(verifier.SafetyViolation, match="outside"):
        verifier.run_synthetic_sqlite_smoke(owned)

    inside = owned.path / "existing.db"
    inside.write_bytes(b"sentinel")
    monkeypatch.setenv("DB_PATH", str(inside))
    with pytest.raises(verifier.SafetyViolation, match="must not exist"):
        verifier.run_synthetic_sqlite_smoke(owned)
    assert inside.read_bytes() == b"sentinel"
    owned.cleanup()


def test_actual_d14_manifest_recomputes_exact_accepted_source_boundary() -> None:
    root = SCRIPTS_DIR.parent
    manifest = root / "scripts" / "phase_c_d14_manifest.json"

    evidence = verifier.validate_d14_manifest(manifest, root)

    assert evidence["current_path_count"] == 77
    assert evidence["baseline_census"]["matched_file_count"] == 46
    assert evidence["baseline_census"]["combined_site_count"] == 213
    assert evidence["baseline_census"]["aggregate_site_sets_sha256"] == verifier.D14_EXPECTED_AGGREGATE_HASH
    assert evidence["triggered_path_count"] == 10


def test_d14_manifest_rejects_repo_local_symlink_before_opening_external_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    external = tmp_path / "operator.json"
    external.write_text('{"private": true}\n', encoding="utf-8")
    manifest = scripts / "phase_c_d14_manifest.json"
    try:
        manifest.symlink_to(external)
    except OSError:
        return
    monkeypatch.setattr(
        verifier.os,
        "open",
        lambda *_args, **_kwargs: pytest.fail("external manifest target was opened"),
    )

    with pytest.raises(verifier.CaseFailure, match="symlink or reparse"):
        verifier.validate_d14_manifest(manifest, root, expected_count=0)


def test_d14_source_and_site_drift_is_recomputed_from_synthetic_source(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source_path = root / "backend" / "alpha.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("try:\n    run()\nexcept Exception:\n    pass\n", encoding="utf-8")
    operational_patterns = {
        "bare_catch": r"^\s*except\s*:\s*(?:#.*)?$",
        "broad_catch": r"^\s*except\s+(Exception|BaseException)(\s+as\s+\w+)?\s*:\s*(?:#.*)?$",
        "standalone_pass": r"^\s*pass\s*$",
    }
    compiled = {kind: verifier.re.compile(pattern) for kind, pattern in operational_patterns.items()}
    baseline_file = verifier._recompute_baseline_file(root, "backend/alpha.py", compiled, 1)
    census = {"operational_patterns": operational_patterns, "baseline_files": [baseline_file]}

    _actual, initial_drift = verifier._validate_baseline_files(census, root, ["backend/alpha.py"])
    source_path.write_text("try:\n    run()\nexcept Exception as exc:\n    pass\n", encoding="utf-8")
    _changed, final_drift = verifier._validate_baseline_files(census, root, ["backend/alpha.py"])

    assert initial_drift == []
    assert final_drift == ["backend/alpha.py"]
    assert baseline_file["sites"][0]["baseline_id"] == "D14-BASE-0001"
    assert len(baseline_file["sites"][0]["fingerprint_sha256"]) == 64


def test_d14_capability_union_rejects_triggered_unclassified_production_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "backend" / "tests").mkdir(parents=True)
    (root / "backend" / "alpha.py").write_text("# classified\n", encoding="utf-8")
    (root / "backend" / "rogue.py").write_text("broker.placeOrder(order)\n", encoding="utf-8")
    (root / "backend" / "tests" / "ignored.py").write_text(
        "broker.placeOrder(test_order)\n", encoding="utf-8"
    )
    manifest = {
        "capability_triggers": [{"id": "raw_order", "description": "raw order"}],
        "capability_trigger_patterns": [{"id": "raw_order", "regex": r"\.\s*placeOrder\s*\("}],
    }

    with pytest.raises(verifier.CaseFailure, match="unclassified production paths") as error:
        verifier._validate_capability_triggers(manifest, root, ["backend/alpha.py"])

    assert error.value.details["paths"] == ["backend/rogue.py"]


def test_d14_rejects_invalid_capability_regex_and_untyped_array() -> None:
    manifest = {
        "capability_trigger_patterns": [{"id": "broken", "regex": "["}],
    }

    with pytest.raises(verifier.CaseFailure, match="does not compile"):
        verifier._compile_capability_patterns(manifest)
    with pytest.raises(verifier.CaseFailure, match="non-empty strings"):
        verifier._typed_string_list({"future_path_patterns": [1]}, "future_path_patterns")


def test_d14_baseline_source_rejects_unsafe_path_and_hash_drift(tmp_path: Path) -> None:
    with pytest.raises(verifier.CaseFailure, match="path is unsafe"):
        verifier._validate_baseline_source(
            {
                "baseline_source": {
                    "accepted_inventory_document": "../private.md",
                    "accepted_inventory_document_normalized_lf_sha256": "0" * 64,
                }
            },
            tmp_path,
        )
    document = tmp_path / "docs" / "inventory.md"
    document.parent.mkdir()
    document.write_text("synthetic accepted policy\n", encoding="utf-8")
    with pytest.raises(verifier.CaseFailure, match="hash differs"):
        verifier._validate_baseline_source(
            {
                "baseline_source": {
                    "accepted_inventory_document": "docs/inventory.md",
                    "accepted_inventory_document_normalized_lf_sha256": "0" * 64,
                }
            },
            tmp_path,
        )


@pytest.mark.parametrize(
    ("paths", "message"),
    [
        (["backend/zulu.py", "backend/alpha.py"], "sorted and unique"),
        (["../outside.py"], "unsafe or non-canonical"),
        (["dashboard/source.py"], "outside the backend"),
    ],
)
def test_d14_manifest_rejects_noncanonical_boundaries(
    tmp_path: Path, paths: list[str], message: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    manifest = _write_manifest(root, paths)

    with pytest.raises(verifier.CaseFailure, match=message):
        verifier.validate_d14_manifest(manifest, root, expected_count=len(paths))


def test_d14_manifest_rejects_missing_source(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    manifest = _write_manifest(root, ["backend/missing.py"])

    with pytest.raises(verifier.CaseFailure, match="missing or non-regular"):
        verifier.validate_d14_manifest(manifest, root, expected_count=1)


def test_case_registry_rejects_duplicate_invalid_and_empty_registries() -> None:
    with pytest.raises(verifier.VerificationError, match="duplicate"):
        verifier.CaseRegistry((_case(), _case()))
    with pytest.raises(verifier.VerificationError, match="invalid"):
        verifier.CaseRegistry((_case("not-stable"),))
    with pytest.raises(verifier.VerificationError, match="must not be empty"):
        verifier.CaseRegistry(())


def test_default_registry_has_unique_stable_nonempty_case_ids() -> None:
    registry = verifier.default_registry()

    assert len(registry.case_ids) == 7
    assert len(set(registry.case_ids)) == len(registry.case_ids)
    assert "C0-D14-001" in registry.case_ids


def test_case_selection_rejects_empty_unknown_and_duplicate_ids() -> None:
    registry = verifier.CaseRegistry((_case("C0-TEST-001"), _case("C0-TEST-002")))

    with pytest.raises(verifier.VerificationError, match="must not be empty"):
        registry.select([])
    with pytest.raises(verifier.VerificationError, match="unknown"):
        registry.select(["C0-TEST-999"])
    with pytest.raises(verifier.VerificationError, match="duplicate"):
        registry.select(["C0-TEST-001", "C0-TEST-001"])
    assert [case.case_id for case in registry.select(None)] == ["C0-TEST-001", "C0-TEST-002"]


def test_outcome_validation_rejects_missing_fail_skip_xfail_and_xpass() -> None:
    expected = ("C0-TEST-001", "C0-TEST-002", "C0-TEST-003", "C0-TEST-004")
    results = (
        _result("C0-TEST-001", "fail"),
        _result("C0-TEST-002", "skip"),
        _result("C0-TEST-003", "xfail"),
        _result("C0-TEST-004", "xpass"),
    )

    assessment = verifier.validate_case_results(results, expected)

    assert not assessment.passed
    assert any("reported fail" in error for error in assessment.errors)
    assert any("unexpected or expired skip" in error for error in assessment.errors)
    assert any("unexpected or expired xfail" in error for error in assessment.errors)
    assert any("reported xpass" in error for error in assessment.errors)
    missing = verifier.validate_case_results(results[:1], expected)
    assert any("missing C0 case results" in error for error in missing.errors)


def test_allowed_skip_is_an_open_gate_and_expired_allowance_is_error() -> None:
    result = (_result("C0-TEST-001", "skip"),)
    future = verifier.OutcomeAllowance(
        "C0-TEST-001", "skip", dt.date(2026, 7, 15), "platform prerequisite"
    )
    expired = verifier.OutcomeAllowance(
        "C0-TEST-001", "skip", dt.date(2026, 7, 13), "platform prerequisite"
    )

    allowed = verifier.validate_case_results(
        result, ("C0-TEST-001",), (future,), today=dt.date(2026, 7, 14)
    )
    stale = verifier.validate_case_results(
        result, ("C0-TEST-001",), (expired,), today=dt.date(2026, 7, 14)
    )

    assert not allowed.passed
    assert allowed.errors == ()
    assert len(allowed.open_gates) == 1
    assert any("expired skip" in error for error in stale.errors)


def test_empty_expected_result_selection_is_a_failure() -> None:
    assessment = verifier.validate_case_results((), ())

    assert not assessment.passed
    assert assessment.errors == ("expected C0 case selection is empty",)


def test_json_failure_report_is_stable_and_serializable() -> None:
    report = verifier._failure_report(verifier.SafetyViolation("synthetic refusal"))
    encoded = json.dumps(report, sort_keys=True)

    assert report["overall"] == "FAIL"
    assert report["phase"] == "C0"
    assert "SafetyViolation" in encoded
    assert report["selected_case_ids"] == []


def test_cli_failed_case_exits_nonzero_cleans_owned_root_and_preserves_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    area = _temp_area(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    sentinel = tmp_path / "outside-sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    candidate = area / f"{verifier.TEMP_PREFIX}{uuid.uuid4().hex}"

    def fail_case(_context: verifier.VerificationContext) -> dict[str, object]:
        raise verifier.CaseFailure("synthetic case failure")

    registry = verifier.CaseRegistry((verifier.Case("C0-TEST-001", "failure", fail_case),))
    monkeypatch.setattr(verifier, "default_registry", lambda: registry)

    exit_code = verifier.main(
        [
            "c0",
            "--repo-root",
            str(repo),
            "--manifest",
            str(repo / "unused.json"),
            "--temp-root",
            str(candidate),
            "--case",
            "C0-TEST-001",
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["overall"] == "FAIL"
    assert report["cases"][0]["status"] == "fail"
    assert report["cleanup"] == {"root_removed": True, "status": "pass"}
    assert not candidate.exists()
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_partial_case_selection_is_diagnostic_and_cannot_report_formal_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    area = _temp_area(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    candidate = area / f"{verifier.TEMP_PREFIX}{uuid.uuid4().hex}"
    passing = lambda _context: {"ok": True}
    registry = verifier.CaseRegistry(
        (
            verifier.Case("C0-TEST-001", "first", passing),
            verifier.Case("C0-TEST-002", "second", passing),
        )
    )
    monkeypatch.setattr(verifier, "default_registry", lambda: registry)

    report = verifier.run_c0(
        repo,
        repo / "unused.json",
        requested_cases=("C0-TEST-001",),
        requested_temp_root=candidate,
    )

    assert report["overall"] == "FAIL"
    assert report["cases"][0]["status"] == "pass"
    assert report["errors"] == ["formal C0 PASS requires every mandatory registry case"]
    assert report["cleanup"] == {"root_removed": True, "status": "pass"}


def test_module_imports_no_tradebot_application_modules() -> None:
    forbidden = {"config", "database", "main", "runtime_lock", "startup"}

    assert forbidden.isdisjoint(sys.modules)
    assert builtins.__import__ is not None
