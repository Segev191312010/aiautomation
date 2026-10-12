"""LaunchAgent contract for the trading supervisor: payload, plist file, launchctl calls."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys

import pytest

from trading.operations.launchd import LaunchdInstallError, LaunchdManager
from trading.operations.paths import LAUNCH_AGENT_LABEL, UserPaths

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="launchd agents are macOS-only")

PLUTIL = Path("/usr/bin/plutil")
CAFFEINATE = Path("/usr/bin/caffeinate")


@dataclass
class FakeLaunchctl:
    """Records launchctl calls; ``print`` answers from the simulated load state."""

    loaded: bool = False
    failing_verbs: frozenset[str] = frozenset()
    calls: list[tuple[str, ...]] = field(default_factory=list)
    bootstrapped_plists: list[bytes] = field(default_factory=list)

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = tuple(command)
        self.calls.append(argv)
        verb = argv[1]
        if verb == "print":
            returncode = 0 if self.loaded else 113
        else:
            returncode = 5 if verb in self.failing_verbs else 0
        if returncode == 0 and verb == "bootstrap":
            self.bootstrapped_plists.append(Path(argv[3]).read_bytes())
            self.loaded = True
        if returncode == 0 and verb == "bootout":
            self.loaded = False
        return subprocess.CompletedProcess(list(argv), returncode, stdout="", stderr="launchctl secret-detail")

    @property
    def verbs(self) -> list[str]:
        return [call[1] for call in self.calls]


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Mirror the real layout: backend/.venv/bin/python -> python3.11 -> uv base interpreter."""

    base_python = tmp_path / "uv-python" / "bin" / "python3.11"
    base_python.parent.mkdir(parents=True)
    base_python.write_text("#!/bin/sh\n")
    base_python.chmod(0o755)
    root = tmp_path / "repo"
    venv = root / "backend" / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(f"home = {base_python.parent}\n")
    (venv / "bin" / "python3.11").symlink_to(base_python)
    (venv / "bin" / "python").symlink_to("python3.11")
    (root / "backend" / "autotraderctl.py").write_text("raise SystemExit(0)\n")
    return root


@pytest.fixture
def user_paths(tmp_path: Path) -> UserPaths:
    return UserPaths(home=tmp_path / "home")


@pytest.fixture
def launchctl() -> FakeLaunchctl:
    return FakeLaunchctl()


@pytest.fixture
def manager(repository: Path, user_paths: UserPaths, launchctl: FakeLaunchctl) -> LaunchdManager:
    return LaunchdManager(user_paths=user_paths, repository_root=repository, command_runner=launchctl)


def _service() -> str:
    return f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"


def _write_pre_fix_plist(repository: Path, user_paths: UserPaths) -> None:
    """The payload the old code installed: base interpreter, `shadow daemon`, Background."""

    venv_python = repository / "backend" / ".venv" / "bin" / "python"
    legacy = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            str(venv_python.resolve()),
            str(repository / "backend" / "autotraderctl.py"),
            "shadow",
            "daemon",
        ],
        "WorkingDirectory": str(repository),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(user_paths.launchd_stdout_file),
        "StandardErrorPath": str(user_paths.launchd_stderr_file),
    }
    user_paths.launch_agents_directory.mkdir(parents=True)
    user_paths.launch_agent_file.write_bytes(plistlib.dumps(legacy, fmt=plistlib.FMT_XML, sort_keys=True))


def test_supervisor_runs_under_caffeinate_with_the_unresolved_venv_python(
    manager: LaunchdManager, repository: Path
) -> None:
    venv_python = repository / "backend" / ".venv" / "bin" / "python"
    arguments = manager.payload()["ProgramArguments"]

    assert arguments == [
        "/usr/bin/caffeinate",
        "-i",
        "-s",
        str(venv_python),
        str(repository / "backend" / "autotraderctl.py"),
        "run",
    ]
    # Regression: the resolved path is uv's base interpreter, which has none of the
    # project's packages and crash-loops under KeepAlive.
    assert venv_python.resolve() != venv_python
    assert str(venv_python.resolve()) not in arguments


def test_payload_sets_environment_and_keeps_the_supervisor_alive(
    manager: LaunchdManager, repository: Path, user_paths: UserPaths
) -> None:
    payload = manager.payload()

    assert set(payload) == {
        "Label",
        "ProgramArguments",
        "WorkingDirectory",
        "EnvironmentVariables",
        "ProcessType",
        "RunAtLoad",
        "KeepAlive",
        "ThrottleInterval",
        "ExitTimeOut",
        "StandardOutPath",
        "StandardErrorPath",
    }
    assert payload["Label"] == LAUNCH_AGENT_LABEL
    assert payload["WorkingDirectory"] == str(repository / "backend")
    assert payload["EnvironmentVariables"] == {
        "PYTHONUNBUFFERED": "1",
        "LANG": "en_US.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    assert payload["ProcessType"] == "Standard"
    assert payload["RunAtLoad"] is True
    # Unconditional: restart after any exit, not only after a failure.
    assert payload["KeepAlive"] is True
    assert payload["ThrottleInterval"] == 30
    assert payload["ExitTimeOut"] == 45
    assert payload["StandardOutPath"] == str(user_paths.launchd_stdout_file)
    assert payload["StandardErrorPath"] == str(user_paths.launchd_stderr_file)


def test_current_manager_targets_this_repository(tmp_path: Path) -> None:
    current = LaunchdManager.current(user_paths=UserPaths(home=tmp_path / "home"))
    repository = Path(__file__).resolve().parents[2]

    assert current.repository_root == repository
    arguments = current.payload()["ProgramArguments"]
    assert arguments[3] == str(repository / "backend" / ".venv" / "bin" / "python")
    assert Path(arguments[4]).is_file()
    assert current.payload()["WorkingDirectory"] == str(repository / "backend")


def test_current_manager_points_at_a_real_virtualenv(tmp_path: Path) -> None:
    current = LaunchdManager.current(user_paths=UserPaths(home=tmp_path / "home"))
    if not (current.virtualenv_directory / "pyvenv.cfg").is_file():
        pytest.skip("backend/.venv is not present in this checkout")

    current.check_runtime()


def test_repository_root_must_be_absolute(user_paths: UserPaths, launchctl: FakeLaunchctl) -> None:
    with pytest.raises(ValueError, match="absolute"):
        LaunchdManager(user_paths=user_paths, repository_root=Path("repo"), command_runner=launchctl)


def test_install_writes_a_private_plist_without_touching_launchctl(
    manager: LaunchdManager, user_paths: UserPaths, launchctl: FakeLaunchctl
) -> None:
    written = manager.install()

    assert written == user_paths.home / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert not written.is_symlink()
    assert plistlib.loads(written.read_bytes()) == manager.payload()
    assert manager.is_installed()
    assert manager.is_current()
    assert launchctl.calls == []


def _remove_pyvenv_cfg(repository: Path) -> None:
    (repository / "backend" / ".venv" / "pyvenv.cfg").unlink()


def _dangle_interpreter(repository: Path) -> None:
    (repository / "backend" / ".venv" / "bin" / "python").resolve().unlink()


def _remove_entrypoint(repository: Path) -> None:
    (repository / "backend" / "autotraderctl.py").unlink()


@pytest.mark.parametrize(
    ("breakage", "message"),
    [
        (_remove_pyvenv_cfg, "not a virtual environment"),
        (_dangle_interpreter, "missing or not executable"),
        (_remove_entrypoint, "autotraderctl.py is missing"),
    ],
)
def test_install_refuses_a_command_that_can_only_crash_loop(
    manager: LaunchdManager,
    repository: Path,
    user_paths: UserPaths,
    breakage: Callable[[Path], None],
    message: str,
) -> None:
    breakage(repository)

    with pytest.raises(LaunchdInstallError, match=message):
        manager.install()
    assert not user_paths.launch_agent_file.exists()


def test_enable_requires_an_installed_plist(manager: LaunchdManager, launchctl: FakeLaunchctl) -> None:
    with pytest.raises(LaunchdInstallError, match="installed first"):
        manager.enable()
    assert launchctl.calls == []


def test_enable_turns_the_service_on_then_bootstraps_the_plist(
    manager: LaunchdManager, user_paths: UserPaths, launchctl: FakeLaunchctl
) -> None:
    manager.install()

    manager.enable()

    assert launchctl.calls == [
        ("/bin/launchctl", "enable", _service()),
        ("/bin/launchctl", "print", _service()),
        ("/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(user_paths.launch_agent_file)),
    ]
    assert [plistlib.loads(data) for data in launchctl.bootstrapped_plists] == [manager.payload()]


def test_enable_rewrites_a_stale_plist_before_bootstrapping(
    manager: LaunchdManager, repository: Path, user_paths: UserPaths, launchctl: FakeLaunchctl
) -> None:
    _write_pre_fix_plist(repository, user_paths)
    assert manager.is_installed()
    assert not manager.is_current()

    manager.enable()

    assert launchctl.verbs == ["enable", "print", "bootstrap"]
    assert [plistlib.loads(data) for data in launchctl.bootstrapped_plists] == [manager.payload()]
    assert manager.is_current()
    assert stat.S_IMODE(user_paths.launch_agent_file.stat().st_mode) == 0o600


def test_enable_refuses_to_report_success_while_a_stale_definition_is_loaded(
    repository: Path, user_paths: UserPaths
) -> None:
    launchctl = FakeLaunchctl(loaded=True)
    manager = LaunchdManager(user_paths=user_paths, repository_root=repository, command_runner=launchctl)
    _write_pre_fix_plist(repository, user_paths)

    with pytest.raises(LaunchdInstallError, match="outdated plist"):
        manager.enable()

    # The file is ready for the reload, but the running supervisor is never bounced here.
    assert manager.is_current()
    assert launchctl.verbs == ["enable", "print"]


def test_enable_leaves_a_current_loaded_agent_running(repository: Path, user_paths: UserPaths) -> None:
    launchctl = FakeLaunchctl(loaded=True)
    manager = LaunchdManager(user_paths=user_paths, repository_root=repository, command_runner=launchctl)
    manager.install()

    manager.enable()

    assert launchctl.verbs == ["enable", "print"]


def test_enable_disables_the_service_again_when_bootstrap_fails(repository: Path, user_paths: UserPaths) -> None:
    launchctl = FakeLaunchctl(failing_verbs=frozenset({"bootstrap"}))
    manager = LaunchdManager(user_paths=user_paths, repository_root=repository, command_runner=launchctl)
    manager.install()

    with pytest.raises(LaunchdInstallError, match="unable to load") as raised:
        manager.enable()

    assert "secret-detail" not in str(raised.value)
    assert launchctl.calls[-1] == ("/bin/launchctl", "disable", _service())
    assert launchctl.verbs == ["enable", "print", "bootstrap", "disable"]


def test_enable_stops_when_launchctl_enable_fails(repository: Path, user_paths: UserPaths) -> None:
    launchctl = FakeLaunchctl(failing_verbs=frozenset({"enable"}))
    manager = LaunchdManager(user_paths=user_paths, repository_root=repository, command_runner=launchctl)
    manager.install()

    with pytest.raises(LaunchdInstallError, match="unable to enable"):
        manager.enable()
    assert launchctl.verbs == ["enable"]


def test_disable_turns_the_service_off_then_boots_it_out(repository: Path, user_paths: UserPaths) -> None:
    launchctl = FakeLaunchctl(loaded=True)
    manager = LaunchdManager(user_paths=user_paths, repository_root=repository, command_runner=launchctl)

    manager.disable()

    assert launchctl.calls == [
        ("/bin/launchctl", "disable", _service()),
        ("/bin/launchctl", "print", _service()),
        ("/bin/launchctl", "bootout", _service()),
    ]
    assert manager.is_loaded() is False


def test_disable_skips_bootout_when_the_service_is_not_loaded(
    manager: LaunchdManager, launchctl: FakeLaunchctl
) -> None:
    manager.disable()

    assert launchctl.verbs == ["disable", "print"]


def test_disable_reports_a_failed_bootout(repository: Path, user_paths: UserPaths) -> None:
    launchctl = FakeLaunchctl(loaded=True, failing_verbs=frozenset({"bootout"}))
    manager = LaunchdManager(user_paths=user_paths, repository_root=repository, command_runner=launchctl)

    with pytest.raises(LaunchdInstallError, match="unable to unload") as raised:
        manager.disable()
    assert "secret-detail" not in str(raised.value)


def test_launchctl_spawn_errors_become_install_errors(repository: Path, user_paths: UserPaths) -> None:
    def unavailable(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(command), timeout=10)

    manager = LaunchdManager(user_paths=user_paths, repository_root=repository, command_runner=unavailable)

    with pytest.raises(LaunchdInstallError, match="unable to control"):
        manager.is_loaded()


@pytest.mark.skipif(not PLUTIL.is_file(), reason="plutil is only available on macOS")
def test_installed_plist_passes_plutil_lint(manager: LaunchdManager) -> None:
    path = manager.install()

    lint = subprocess.run(
        [str(PLUTIL), "-lint", str(path)], capture_output=True, text=True, timeout=10, check=False
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr
    # Apple's own parser reads back exactly the payload we generated.
    converted = subprocess.run(
        [str(PLUTIL), "-convert", "json", "-o", "-", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert converted.returncode == 0, converted.stderr
    assert json.loads(converted.stdout) == manager.payload()


@pytest.mark.skipif(sys.platform != "darwin", reason="caffeinate is a macOS tool")
def test_caffeinate_wrapper_exists_on_this_host() -> None:
    assert os.access(CAFFEINATE, os.X_OK)
