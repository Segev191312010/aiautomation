"""``autotraderctl deploy apply`` and ``deploy undo``: the deploy runbook
(OPERATOR_PROMPT 6.2 STEP 0-5 and 6.3) as one owner-run command (P2).

The write half of the one-command deploy; ``deploy check``
(``trading.operations.deploy_check``) stays the read-only half and is run here
as the first pre-check, as it is.

Who runs it. Only the owner, at a terminal: stdin must be a TTY
(``not_interactive``), the full plan is printed, and nothing changes until the
owner types the candidate's 7-character short sha back (``confirm_mismatch``).
Agents never run it; they read its output.

Where it runs from. The candidate's worktree, with ``--repo`` the live
checkout; it refuses to run from the live checkout itself
(``runs_from_live_checkout``) and from any checkout whose HEAD is not the
candidate (``not_candidate_worktree``), because its own expectations (the
schema, the sendable identities) are the running code's. Everything that
derives paths from its own file runs as a subprocess of the live checkout:
``<repo>/backend/.venv/bin/python <repo>/backend/autotraderctl.py`` with
``shadow agent disable|enable`` (the plist's WorkingDirectory),
``run --dry-run --date D`` (REPO_ROOT and VENV_PYTHON), ``db migrate`` and
``db status``, each with cwd ``<repo>/backend``.

What it does, in order (exit codes below):

1. pre-checks (any refusal: exit 78, nothing written, no live call);
2. the plan and the typed confirmation. The confirmation goes stale: when the
   sha is typed, the window, live HEAD == PREV_SHA and the upcoming session
   are read again, and an answer older than ``CONFIRM_TIMEOUT_S`` is refused
   (exit 78, nothing written; N17 MEDIUM-1);
3. ``<iso> <label> PREV_SHA=<40hex> schema=v<n>`` into ``deploys.log``;
4. stop: ``shadow agent disable``, then wait on observed facts (C11: a
   disable that errors is logged and the wait goes on) for no supervisor, no
   stray trading process, a free ``supervisor.lock`` and, when a supervisor
   was running, its ``SUPERVISOR_STOP reason=SIGTERM`` line;
5. the window again, then ``git merge --ff-only <resolved sha>`` (never the
   branch name), HEAD, the bridge (and every pinned file) on disk, the tree;
6. only with ``--expect-migration N``: ``db migrate`` and ``db status``;
7. the live dry run, every required line checked (``DryRunCheck``);
8. the window again, then ``shadow agent enable`` and the start checks, which
   end only after telegram and heartbeat ran ``POST_START_SETTLE_S`` with no
   exit (rehearsal F-1);
9. ``<iso> DEPLOY <label> <short> PREV_SHA=<40hex> pin=<12> result=PASS
   schema=v<n>`` and the ledger-ready line.

A failure in steps 5-8 of a code-only deploy undoes it by itself
(``undo_to``: stop, ``git reset --keep PREV``, HEAD re-read, the dry run shows
the old pin, the fences and the heartbeat, start, ``DEPLOY rollback to <sha>
result=ROLLBACK reason=<token>``, exit 3). When git never moved (live HEAD is
still PREV, e.g. the window closed before the fast-forward), nothing is reset:
the unchanged code is checked and started again and ``DEPLOY <label> <short>
... result=ABORTED reason=<token>`` is written (exit 3 too: either way the
previous code runs again). The undo also accepts the previous code's
mid-session start form (no ``SESSION_WAIT``; ``SESSION_START`` at once). After
step 6 ran, a failure halts instead: the robot is left disabled,
``result=HALTED reason=<token> backup=<path>`` is written and the database
restore is printed, never executed (exit 4). A failure inside the undo,
expected or not, leaves the robot disabled and prints runbook 6.3 with literal
values (exit 5). After the confirmation, SIGHUP (the terminal closed) and
SIGTERM halt the run where it stands like Ctrl-C (exit 4), and the HALTED line
in ``deploys.log`` carries the next action. Every failure path has one literal
token, used identically in stdout, the ``deploys.log`` ``reason=`` and the
final summary (``TOKENS``).

Paths. The robot under launchd uses the account's own home: ``deploy apply``
and ``deploy undo`` refuse (``paths_not_canonical``) when ``HOME`` is not the
account's home directory or the database is not ``<data>/autotrader.db``
(``DB_PATH`` set elsewhere; ``--db-path`` is refused by the command line).

Git writes go through ``GitWriter`` only: ``merge --ff-only`` and
``reset --keep``, with ``deploy_check``'s scrubbed git environment; this module
holds no other git write (an AST test pins it).
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import time as time_module
from typing import Protocol, TextIO
from urllib.parse import quote

from trading.operations.deploy_check import (
    BRIDGE_PATH,
    DEFAULT_BENIGN_PATHS,
    DEFAULT_LEDGER_GLOB,
    MIGRATION_UNEXPECTED,
    PIN_NAME,
    REFUSE,
    SUPERVISOR_PATH,
    VERDICT_MIGRATION_MISSING,
    CheckResult,
    DeployCheckRequest,
    Git,
    GitError,
    SntpProbe,
    VerdictLine,
    check_tree_clean,
    git_environment,
    match_verdict,
    resolve_candidate,
    reviewed_pins,
    run_checks,
    verdict_label,
    verdict_tokens,
)
from trading.operations.heartbeat import HEARTBEAT_CHILD, presence_detail
from trading.operations.launchd import LaunchdCommandRunner, LaunchdInstallError, LaunchdManager
from trading.operations.log_tailer import line_time
from trading.operations.logging import archive_path, latest_rotation_boundary
from trading.operations.paths import FILE_MODE, UserPaths
from trading.operations.session_check import mask_accounts
from trading.operations.supervisor import (
    LOCK_FILE_NAME,
    SUPERVISOR_LOG_NAME,
    TELEGRAM_CHILD,
    _SUPERVISOR_PATTERN,
    _UNSUPERVISED_PATTERN,
    ProcessEntry,
    plan_session,
    process_table,
    registry_lines,
    upcoming_session,
)
from trading.research.live_ledger import BRIDGE_STATE_DIRECTORY
from trading.scheduler.sessions import MARKET_TIMEZONE, MarketSession
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage.migrations import inspect_schema
from trading.storage.runtime_fence import IDENTITY_FENCE_PREFIX

UTC = timezone.utc

# --- design invariants (test_deploy_apply_constants_are_pinned) ---------------------

# O6: a deploy starts 18:40 -> 06:50 ET, clear of the 18:35 ET log rotation and
# an hour before the first child (07:55), whatever the weekday.
FIRST_START = time(18, 40)
LAST_START = time(6, 50)
# launchd gives the supervisor 45 s (ExitTimeOut) to stop its children (20 s).
STOP_WAIT_TIMEOUT_S = 75
POST_START_TIMEOUT_S = 60
# Rehearsal F-1: the manual L3-9 look came 20 s after the enable; a telegram
# child without its token exits 78 one or two seconds after its CHILD_START.
POST_START_SETTLE_S = 20
POLL_S = 1.0
# N17 MEDIUM-1: a typed sha older than this is a stale confirmation.
CONFIRM_TIMEOUT_S = 300
DEPLOYS_LOG_NAME = "deploys.log"
DEPLOY_LOCK_NAME = "deploy.lock"

EXIT_OK = 0
EXIT_REFUSED = 78  # nothing changed
EXIT_ROLLED_BACK = 3  # a failure after the stop, undone: the previous code runs again
EXIT_HALTED = 4  # the robot is left disabled
EXIT_UNDO_FAILED = 5  # the undo itself failed: the robot is left disabled

SHORT_SHA_LENGTH = 7
LIVE_COMMAND_TIMEOUT_S = 120.0
DRY_RUN_TIMEOUT_S = 300.0
# A migration is never killed half way for being slow.
MIGRATE_TIMEOUT_S = 3600.0
# The files that make up this tool: a candidate that changes one must be
# reviewed with scope P2 (the self-change guard).
DEPLOY_TOOL_PATHS = (
    "backend/trading/operations/deploy_apply.py",
    "backend/trading/operations/deploy_check.py",
    "backend/trading/operations/launchd.py",
    "backend/trading/operations/db_admin.py",
)
LAUNCHD_PATH = "backend/trading/operations/launchd.py"
DEPLOY_TOOL_SCOPE = "P2"
LIVE_PYTHON = "backend/.venv/bin/python"
LIVE_ENTRYPOINT = "backend/autotraderctl.py"
# The checkout this code runs from (the candidate's worktree, never --repo).
OWN_ROOT = Path(__file__).resolve().parents[3]

# --- named outcome tokens ----------------------------------------------------------

# Pre-checks and confirmation (exit 78, nothing changed).
NOT_INTERACTIVE = "not_interactive"
RUNS_FROM_LIVE_CHECKOUT = "runs_from_live_checkout"
NOT_CANDIDATE_WORKTREE = "not_candidate_worktree"
CANDIDATE_WORKTREE_DIRTY = "candidate_worktree_dirty"
PATHS_NOT_CANONICAL = "paths_not_canonical"
LIVE_PYTHON_MISSING = "live_python_missing"
DEPLOY_LOCK_BUSY = "deploy_lock_busy"
START_CUTOFF = "start_cutoff"
DEPLOY_CHECK_PREFIX = "deploy_check_"
PIN_VERDICT_MISSING = "pin_verdict_missing"
PIN_EXPECTED = "pin_expected"
PREV_SHA_INVALID = "prev_sha_invalid"
CHANGED_PATHS_UNREADABLE = "changed_paths_unreadable"
DEPLOY_TOOL_SCOPE_MISSING = "deploy_tool_scope_missing"
FENCED_UNEXPECTED = "fenced_unexpected"
MIGRATE_LOCK_BUSY = "migrate_lock_busy"
DEPLOYS_LOG_UNUSABLE = "deploys_log_unusable"
LABEL_MISSING = "label_missing"
CONFIRM_MISMATCH = "confirm_mismatch"
# The plan went stale while the prompt waited (N17 MEDIUM-1; with START_CUTOFF).
CONFIRM_TIMEOUT = "confirm_timeout"
LIVE_HEAD_MOVED = "live_head_moved"
SESSION_DATE_CHANGED = "session_date_changed"
# The stop (exit 4 before the fast-forward, git untouched).
STOP_WAIT_TIMEOUT = "stop_wait_timeout"
# Steps 5-8 (code-only: undone, exit 3; after a migration: halted, exit 4).
MERGE_FAILED = "merge_failed"
HEAD_MISMATCH = "head_mismatch"
BRIDGE_SHA_MISMATCH = "bridge_sha_mismatch"
TREE_DIRTY_AFTER_FF = "tree_dirty_after_ff"
MIGRATE_FAILED = "migrate_failed"
MIGRATE_UNPARSED = "migrate_unparsed"
MIGRATE_STATUS_NOT_CURRENT = "migrate_status_not_current"
DRY_RUN_EXIT = "dry_run_exit"
DRY_RUN_BANG = "dry_run_bang"
DRY_RUN_WARNING = "dry_run_warning"
DRY_RUN_PIN = "dry_run_pin"
DRY_RUN_FENCED = "dry_run_fenced"
DRY_RUN_SENDABLE = "dry_run_sendable"
DRY_RUN_REGISTRY = "dry_run_registry"
DRY_RUN_SCHEMA = "dry_run_schema"
DRY_RUN_SESSION = "dry_run_session"
DRY_RUN_HEARTBEAT = "dry_run_heartbeat"
DRY_RUN_BUDGET = "dry_run_budget"
POST_START_NO_START = "post_start_no_start"
POST_START_SESSION_WAIT = "post_start_session_wait"
POST_START_LAUNCH_REFUSED = "post_start_launch_refused"
POST_START_ROTATION_FAILED = "post_start_rotation_failed"
POST_START_PREFLIGHT_WARNING = "post_start_preflight_warning"
POST_START_TELEGRAM_MISSING = "post_start_telegram_missing"
POST_START_HEARTBEAT_MISSING = "post_start_heartbeat_missing"
POST_START_CHILD_EXITED = "post_start_child_exited"
POST_START_PLIST = "post_start_plist"
POST_START_HEAD = "post_start_head"
POST_START_TIMEOUT = "post_start_timeout"
# Anything else after the confirmation: halted where it stood (exit 4).
INTERRUPTED = "interrupted"
INTERNAL_ERROR = "internal_error"
# The undo (failures: exit 5, the robot left disabled).
UNDO_STOP_TIMEOUT = "undo_stop_timeout"
UNDO_PREV_INVALID = "undo_prev_invalid"
UNDO_RESET_FAILED = "undo_reset_failed"
UNDO_RESET_NOOP = "undo_reset_noop"
UNDO_DRY_RUN = "undo_dry_run"
UNDO_POST_START = "undo_post_start"
# `deploy undo` refusals (exit 78).
UNDO_PREV_MISMATCH = "undo_prev_mismatch"
UNDO_NEEDS_DB_RESTORE = "undo_needs_db_restore"
UNDO_SCHEMA_UNKNOWN = "undo_schema_unknown"
UNDO_NOT_FLAT = "undo_not_flat"

# deploy_check refusals, by its check token (``files`` refuses only an unknown candidate).
DEPLOY_CHECK_TOKENS = tuple(
    f"{DEPLOY_CHECK_PREFIX}{token}"
    for token in ("verdict", "tree_clean", "parent", "files", "schema", "fence", "clock", "window", "pin")
)
TOKENS = (
    NOT_INTERACTIVE,
    RUNS_FROM_LIVE_CHECKOUT,
    NOT_CANDIDATE_WORKTREE,
    CANDIDATE_WORKTREE_DIRTY,
    PATHS_NOT_CANONICAL,
    LIVE_PYTHON_MISSING,
    DEPLOY_LOCK_BUSY,
    START_CUTOFF,
    *DEPLOY_CHECK_TOKENS,
    MIGRATION_UNEXPECTED,
    VERDICT_MIGRATION_MISSING,
    PIN_VERDICT_MISSING,
    PIN_EXPECTED,
    PREV_SHA_INVALID,
    CHANGED_PATHS_UNREADABLE,
    DEPLOY_TOOL_SCOPE_MISSING,
    FENCED_UNEXPECTED,
    MIGRATE_LOCK_BUSY,
    DEPLOYS_LOG_UNUSABLE,
    LABEL_MISSING,
    CONFIRM_MISMATCH,
    CONFIRM_TIMEOUT,
    LIVE_HEAD_MOVED,
    SESSION_DATE_CHANGED,
    STOP_WAIT_TIMEOUT,
    MERGE_FAILED,
    HEAD_MISMATCH,
    BRIDGE_SHA_MISMATCH,
    TREE_DIRTY_AFTER_FF,
    MIGRATE_FAILED,
    MIGRATE_UNPARSED,
    MIGRATE_STATUS_NOT_CURRENT,
    DRY_RUN_EXIT,
    DRY_RUN_BANG,
    DRY_RUN_WARNING,
    DRY_RUN_PIN,
    DRY_RUN_FENCED,
    DRY_RUN_SENDABLE,
    DRY_RUN_REGISTRY,
    DRY_RUN_SCHEMA,
    DRY_RUN_SESSION,
    DRY_RUN_HEARTBEAT,
    DRY_RUN_BUDGET,
    POST_START_NO_START,
    POST_START_SESSION_WAIT,
    POST_START_LAUNCH_REFUSED,
    POST_START_ROTATION_FAILED,
    POST_START_PREFLIGHT_WARNING,
    POST_START_TELEGRAM_MISSING,
    POST_START_HEARTBEAT_MISSING,
    POST_START_CHILD_EXITED,
    POST_START_PLIST,
    POST_START_HEAD,
    POST_START_TIMEOUT,
    INTERRUPTED,
    INTERNAL_ERROR,
    UNDO_STOP_TIMEOUT,
    UNDO_PREV_INVALID,
    UNDO_RESET_FAILED,
    UNDO_RESET_NOOP,
    UNDO_DRY_RUN,
    UNDO_POST_START,
    UNDO_PREV_MISMATCH,
    UNDO_NEEDS_DB_RESTORE,
    UNDO_SCHEMA_UNKNOWN,
    UNDO_NOT_FLAT,
)

FULL_SHA = re.compile(r"[0-9a-f]{40}")
PIN_SHA = re.compile(r"[0-9a-f]{64}")
LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
REASON_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,63}")
# db_admin's own lines (db_admin.py: `backup    <path>`, `migrated  v<a> -> v<b>`).
MIGRATE_BACKUP_LINE = re.compile(r"^backup    (?P<path>/\S.*)$")
MIGRATE_DONE_LINE = re.compile(r"^migrated  v(?P<before>\d+) -> v(?P<after>\d+)$")
# The dry run's own line prefixes (supervisor.dry_run_report).
BRIDGE_PREFIX = "bridge       "
SENDABLE_LINE_PREFIX = f"{BRIDGE_PREFIX}INFO sendable identities = "
SESSION_PREFIX = "session      "
DATABASE_PREFIX = "database     schema "
BUDGET_TEXT = "day risk budget 3% of net liquidation"
_SESSION_WAIT_EVENT = re.compile(r"^\d{2}:\d{2}:\d{2}  SESSION_WAIT date=(?P<day>\S+) ")
# A dry run from after 07:55 ET on the session day simulates from now: the
# session starts at once, with no SESSION_WAIT (supervisor.run).
_SESSION_START_EVENT = re.compile(r"^\d{2}:\d{2}:\d{2}  SESSION_START date=(?P<day>\S+)(?:\s|$)")
_PID = re.compile(r"\bpid=(?P<pid>\d+)")
_ARCHIVE_NAME = re.compile(rf"^{re.escape(SUPERVISOR_LOG_NAME)}\.(?P<day>\d{{4}}-\d{{2}}-\d{{2}})$")
# A DEPLOY line written by this tool or by hand on 2026-10-16/18: `DEPLOY <label>
# [<label>] <short> PREV_SHA=<40> pin=<12> result=PASS [schema=v<n>]`, or 4.10's
# `DEPLOY <candidate>@<short> ...`.
_DEPLOY_PASS = re.compile(
    r"\bDEPLOY (?:\S+ ){0,2}?(?:\S+@)?(?P<short>[0-9a-f]{7,40}) PREV_SHA=(?P<prev>[0-9a-f]{40})"
    r" pin=(?P<pin>[0-9a-f]{12}) result=PASS(?: schema=v(?P<schema>\d+))?(?:\s|$)"
)
_PREV_LINE = re.compile(
    r"^\S+ (?!DEPLOY )(?:\S+ ){1,2}?PREV_SHA=(?P<prev>[0-9a-f]{40})(?: schema=v(?P<schema>\d+))?\s*$"
)
# A sent or open bracket is a working order or a position (runbook 6.3: only flat).
_NOT_FLAT_STATUSES = frozenset({"sent", "open", "needs_attention"})
_ROTATION_DOUBLE = (time(18, 30), time(18, 35))

Clock = Callable[[], datetime]
Sleep = Callable[[float], None]
ProcessTable = Callable[[], Sequence[ProcessEntry]]


def _local_now() -> datetime:
    """The wall clock with its own offset, as `date -Iseconds` writes it."""
    return datetime.now().astimezone()


# --- errors --------------------------------------------------------------------------


class DeployFailure(Exception):
    """One step failed with its named token."""

    def __init__(self, token: str, detail: str, *, notes: Sequence[str] = ()) -> None:
        super().__init__(f"{token}: {detail}")
        self.token = token
        self.detail = detail
        self.notes = tuple(notes)


class DeploysLogError(RuntimeError):
    """``deploys.log`` is a symlink, not a regular file, or cannot be written."""


class DeploySignal(KeyboardInterrupt):
    """SIGHUP (the terminal closed) or SIGTERM after the confirmation: handled like
    Ctrl-C, so the run halts where it stands with its line and manual block (N17 LOW-2)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


# --- deploys.log -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeploysLog:
    """Append-only, private evidence outside the tree: one write and one fsync per line."""

    path: Path

    def problem(self) -> str | None:
        """Why a line could not be appended now, without writing anything."""
        parent = self.path.parent
        if not parent.is_dir() or parent.is_symlink():
            return f"{parent} is not a directory"
        try:
            status = os.lstat(self.path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            return f"{self.path}: {type(exc).__name__}"
        if not stat.S_ISREG(status.st_mode):
            return f"{self.path} is a symbolic link or not a regular file"
        return None

    def append(self, line: str) -> None:
        if "\n" in line or "\r" in line:
            raise ValueError("a deploys.log line is one line")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptor = os.open(self.path, flags, FILE_MODE)
        except OSError as exc:
            raise DeploysLogError(f"{self.path}: {type(exc).__name__}: {exc.strerror}") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise DeploysLogError(f"{self.path} is not a regular file")
            os.fchmod(descriptor, FILE_MODE)
            data = f"{line}\n".encode("utf-8")
            if os.write(descriptor, data) != len(data):
                raise DeploysLogError(f"{self.path}: short write")
            os.fsync(descriptor)
        except OSError as exc:
            raise DeploysLogError(f"{self.path}: {type(exc).__name__}: {exc.strerror}") from exc
        finally:
            os.close(descriptor)

    def lines(self) -> list[str]:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise DeploysLogError(f"{self.path}: {type(exc).__name__}: {exc.strerror}") from exc
        with os.fdopen(descriptor, "r", encoding="utf-8", errors="replace") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise DeploysLogError(f"{self.path} is not a regular file")
            return stream.read().splitlines()


def iso_seconds(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def prev_line(moment: datetime, label: str, prev: str, schema: int) -> str:
    return f"{iso_seconds(moment)} {label} PREV_SHA={prev} schema=v{schema}"


def pass_line(moment: datetime, label: str, head: str, prev: str, pin: str, schema: int) -> str:
    return (
        f"{iso_seconds(moment)} DEPLOY {label} {head[:SHORT_SHA_LENGTH]} PREV_SHA={prev}"
        f" pin={pin[:12]} result=PASS schema=v{schema}"
    )


def halted_line(
    moment: datetime, label: str, head: str, prev: str, pin: str, token: str, backup: str | None
) -> str:
    return (
        f"{iso_seconds(moment)} DEPLOY {label} {head[:SHORT_SHA_LENGTH]} PREV_SHA={prev}"
        f" pin={pin[:12]} result=HALTED reason={token}" + (f" backup={backup}" if backup else "")
    )


def aborted_line(moment: datetime, label: str, head: str, prev: str, pin: str, token: str) -> str:
    """Git never moved: the deploy stopped before its fast-forward took, and PREV runs again."""
    return (
        f"{iso_seconds(moment)} DEPLOY {label} {head[:SHORT_SHA_LENGTH]} PREV_SHA={prev}"
        f" pin={pin[:12]} result=ABORTED reason={token}"
    )


def rollback_line(moment: datetime, prev: str, token: str) -> str:
    return f"{iso_seconds(moment)} DEPLOY rollback to {prev} result=ROLLBACK reason={token}"


def undo_failed_line(moment: datetime, prev: str, token: str) -> str:
    return f"{iso_seconds(moment)} DEPLOY rollback to {prev} result=UNDO_FAILED reason={token}"


# --- locks ------------------------------------------------------------------------------


def lock_is_free(path: Path) -> bool | None:
    """Whether an exclusive flock on ``path`` could be taken now: probed through an
    O_RDONLY descriptor and released at once; never creates the file. None: unknown."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return True
    except OSError:
        return None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except OSError:
            return None
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return True
    finally:
        os.close(descriptor)


class LockBusy(RuntimeError):
    pass


@contextmanager
def hold_lock(path: Path) -> Iterator[None]:
    """An exclusive lock for this run: one deploy at a time."""
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, FILE_MODE)
    except OSError as exc:
        raise LockBusy(f"cannot open {path}: {type(exc).__name__}") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder = os.pread(descriptor, 32, 0).decode("ascii", "replace").strip() or "unknown"
            raise LockBusy(f"another deploy holds {path} (pid {holder})") from exc
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, f"{os.getpid()}\n".encode("ascii"), 0)
        yield
    finally:
        os.close(descriptor)


# --- signals ----------------------------------------------------------------------------

# Without a handler either signal kills the run wherever it stands, between the
# stop and the start too, and leaves no instructions (N17 LOW-2).
HALT_SIGNALS = (signal.SIGHUP, signal.SIGTERM)
_DEFERRED_SIGNALS = (signal.SIGINT, *HALT_SIGNALS)


def _raise_deploy_signal(signum: int, _frame: object) -> None:
    raise DeploySignal(signal.Signals(signum).name)


@contextmanager
def _signal_handlers(handler: object, numbers: Sequence[int]) -> Iterator[None]:
    previous: dict[int, object] = {}
    try:
        for number in numbers:
            previous[number] = signal.signal(number, handler)  # type: ignore[arg-type]
    except ValueError:
        pass  # not the main thread: the default handling stays
    try:
        yield
    finally:
        for number, old in previous.items():
            signal.signal(number, old)  # type: ignore[arg-type]


def signals_interrupt() -> AbstractContextManager[None]:
    """SIGHUP and SIGTERM raise ``DeploySignal`` (a ``KeyboardInterrupt``) while this holds."""
    return _signal_handlers(_raise_deploy_signal, HALT_SIGNALS)


def signals_deferred() -> AbstractContextManager[None]:
    """While a halt writes its line and prints its block, a second signal is ignored."""
    return _signal_handlers(signal.SIG_IGN, _DEFERRED_SIGNALS)


# --- git writes -------------------------------------------------------------------------


class GitWriter:
    """The only git writes of a deploy: a fast-forward and ``reset --keep``.

    ``--keep`` refuses to discard local modifications (LEARNED 2026-10-14);
    both take a full 40-hex sha, never a branch name or an option.
    """

    def __init__(self, repo: Path) -> None:
        self._repo = repo

    def _run(self, *args: str) -> None:
        argv = ["git", "-C", str(self._repo), "-c", "core.fsmonitor=false", *args]
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                env=git_environment(self._repo),
                stdin=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(f"git {args[0]} failed: {type(exc).__name__}: {exc}") from exc
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", "replace").strip().splitlines()
            raise GitError(f"git {args[0]} failed: {message[-1] if message else f'exit {completed.returncode}'}")

    def merge_ff_only(self, sha: str) -> None:
        if not FULL_SHA.fullmatch(sha):
            raise ValueError(f"merge_ff_only needs a 40-hex sha, got {sha!r}")
        self._run("merge", "--ff-only", sha)

    def reset_keep(self, sha: str) -> None:
        if not FULL_SHA.fullmatch(sha):
            raise ValueError(f"reset_keep needs a 40-hex sha, got {sha!r}")
        self._run("reset", "--keep", sha)


# --- the live checkout's own commands -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def last_error(self) -> str:
        lines = [line for line in (self.stderr or self.stdout).splitlines() if line.strip()]
        return mask_accounts(lines[-1].strip()) if lines else f"exit {self.returncode}"


class LiveRunner(Protocol):
    def __call__(self, argv: Sequence[str], *, cwd: Path, timeout: float) -> LiveResult: ...


def subprocess_runner(argv: Sequence[str], *, cwd: Path, timeout: float) -> LiveResult:
    """One live-checkout command; its Python finds only the live checkout's packages."""
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME")}
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return LiveResult(tuple(argv), 124, partial, f"timed out after {timeout:.0f} s")
    except OSError as exc:
        return LiveResult(tuple(argv), 127, "", f"could not run: {type(exc).__name__}: {exc}")
    return LiveResult(tuple(argv), completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True)
class LiveCheckout:
    """``<repo>/backend/.venv/bin/python <repo>/backend/autotraderctl.py ...`` with cwd
    ``<repo>/backend``: the plist, REPO_ROOT and VENV_PYTHON all derive from the
    running file, so these commands must be the live checkout's own."""

    repo: Path
    runner: LiveRunner

    @property
    def backend(self) -> Path:
        return self.repo / "backend"

    @property
    def python(self) -> Path:
        return self.repo / LIVE_PYTHON

    @property
    def entrypoint(self) -> Path:
        return self.repo / LIVE_ENTRYPOINT

    def argv(self, *args: str) -> tuple[str, ...]:
        return (str(self.python), str(self.entrypoint), *args)

    def run(self, *args: str, timeout: float = LIVE_COMMAND_TIMEOUT_S) -> LiveResult:
        return self.runner(self.argv(*args), cwd=self.backend, timeout=timeout)

    def disable(self) -> LiveResult:
        return self.run("shadow", "agent", "disable")

    def enable(self) -> LiveResult:
        return self.run("shadow", "agent", "enable")

    def dry_run(self, session_date: date) -> LiveResult:
        return self.run("run", "--dry-run", "--date", session_date.isoformat(), timeout=DRY_RUN_TIMEOUT_S)

    def migrate(self, database: Path) -> LiveResult:
        return self.run("db", "migrate", "--db-path", str(database), timeout=MIGRATE_TIMEOUT_S)

    def status(self, database: Path) -> LiveResult:
        return self.run("db", "status", "--db-path", str(database))

    def command_line(self, *args: str) -> str:
        """The same command for the owner's zsh, as one line."""
        return " ".join(shlex.quote(part) for part in self.argv(*args))


# --- pure checks ---------------------------------------------------------------------------


def in_start_window(now: datetime) -> bool:
    """O6 on the ET wall clock: 18:40 inclusive to 06:50 exclusive, DST-aware, every day."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    wall = now.astimezone(MARKET_TIMEZONE).time()
    return wall >= FIRST_START or wall < LAST_START


def start_window_problem(now: datetime) -> str | None:
    if in_start_window(now):
        return None
    local = now.astimezone(MARKET_TIMEZONE)
    return (
        f"now {local:%Y-%m-%d %H:%M:%S} {local.tzname()} is outside the deploy start window"
        f" {FIRST_START:%H:%M} -> {LAST_START:%H:%M} ET (O6)"
    )


def prev_sha_problem(git: Git, prev: str | None, head: str | None = None) -> str | None:
    """PREV_SHA must be 40 hex, a commit, and (when ``head`` is given) its ancestor:
    an empty PREV makes ``reset --keep`` a silent no-op (LEARNED, P2-4)."""
    if not isinstance(prev, str) or not FULL_SHA.fullmatch(prev):
        return f"PREV_SHA {prev!r} is not a 40-hex sha"
    try:
        resolved = git.resolve(prev)
    except GitError as exc:
        return f"PREV_SHA {prev} cannot be resolved: {exc}"
    if resolved != prev:
        return f"PREV_SHA {prev} is not a commit in {git.repo}"
    if head is not None:
        try:
            ancestor = git.is_ancestor(prev, head)
        except GitError as exc:
            return f"PREV_SHA {prev}: {exc}"
        if not ancestor:
            return f"PREV_SHA {prev} is not an ancestor of the candidate {head[:12]}"
    return None


def bridge_pin_at(git: Git, commit: str) -> str:
    """The ``REVIEWED_BRIDGE_SHA256`` of ``commit``'s supervisor.py (64 hex)."""
    blob = git.blob(commit, SUPERVISOR_PATH)
    if blob is None:
        raise ValueError(f"{commit[:12]} has no {SUPERVISOR_PATH}")
    pin = reviewed_pins(blob.decode("utf-8", "replace")).get(PIN_NAME)
    if not isinstance(pin, str) or not PIN_SHA.fullmatch(pin):
        raise ValueError(f"{commit[:12]}:{SUPERVISOR_PATH} has no 64-hex {PIN_NAME}")
    return pin


def pinned_files(pins: dict[str, object]) -> dict[str, str]:
    """Every file a ``REVIEWED_*`` pin covers -> its sha256: the bridge, and each
    entry of a ``{path under backend/: sha256}`` map (P3-B0). Raises ValueError
    for a pin this tool does not know how to verify."""
    files: dict[str, str] = {}
    for name, value in sorted(pins.items()):
        if name == PIN_NAME and isinstance(value, str) and PIN_SHA.fullmatch(value):
            files[BRIDGE_PATH] = value
        elif isinstance(value, dict) and value and all(
            isinstance(path, str) and isinstance(digest, str) and PIN_SHA.fullmatch(digest)
            for path, digest in value.items()
        ):
            for path, digest in value.items():
                relative = f"backend/{path}"
                if ".." in Path(path).parts or Path(path).is_absolute():
                    raise ValueError(f"{name} names a path outside backend/: {path!r}")
                files[relative] = digest
        else:
            raise ValueError(f"{name} is neither a 64-hex sha nor a {{path: sha256}} map")
    if BRIDGE_PATH not in files:
        raise ValueError(f"no 64-hex {PIN_NAME}")
    return files


def pin_problems(
    git: Git, head: str, expect_pin: str, verdicts: Sequence[VerdictLine]
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """The three-way pin check: ``--expect-pin`` == every matched verdict's
    ``bridge_sha=`` == the candidate's ``REVIEWED_BRIDGE_SHA256`` == the
    candidate's bridge blob; and every other ``REVIEWED_*`` pin == its blobs."""
    problems: list[tuple[str, str]] = []
    if not verdicts:
        problems.append((PIN_VERDICT_MISSING, "no matched verdict line to read bridge_sha= from"))
    for line in verdicts:
        shas = verdict_tokens(line.text).get("bridge_sha", ())
        if not shas:
            problems.append((PIN_VERDICT_MISSING, f"the verdict line has no bridge_sha=: {line.describe()}"))
        elif shas != (expect_pin,):
            problems.append(
                (
                    PIN_EXPECTED,
                    f"verdict bridge_sha={','.join(shas)} is not --expect-pin {expect_pin}: {line.describe()}",
                )
            )
    supervisor = git.blob(head, SUPERVISOR_PATH)
    if supervisor is None:
        problems.append((PIN_EXPECTED, f"{head[:12]} has no {SUPERVISOR_PATH}"))
        return problems, {}
    try:
        files = pinned_files(reviewed_pins(supervisor.decode("utf-8", "replace")))
    except (SyntaxError, ValueError) as exc:
        problems.append((PIN_EXPECTED, f"{head[:12]}:{SUPERVISOR_PATH}: {exc}"))
        return problems, {}
    if files[BRIDGE_PATH] != expect_pin:
        problems.append(
            (PIN_EXPECTED, f"the candidate's {PIN_NAME} {files[BRIDGE_PATH]} is not --expect-pin {expect_pin}")
        )
    for path, digest in sorted(files.items()):
        blob = git.blob(head, path)
        actual = None if blob is None else hashlib.sha256(blob).hexdigest()
        if actual != digest:
            problems.append(
                (PIN_EXPECTED, f"{head[:12]}:{path} sha256 {actual or 'missing'} is not its pin {digest}")
            )
    return problems, files


def stopped_identity_fences(database: Path) -> tuple[str, ...]:
    """Every identity whose own fence is stopped, sendable or not (read-only)."""
    uri = f"file:{quote(str(database.expanduser().resolve()))}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        rows = db.execute(
            "SELECT name FROM autotrader_execution_fences WHERE name LIKE ? AND stopped=1",
            (f"{IDENTITY_FENCE_PREFIX}%",),
        ).fetchall()
    return tuple(sorted({str(name)[len(IDENTITY_FENCE_PREFIX):] for (name,) in rows}))


def fenced_sendable(database: Path) -> tuple[str, ...]:
    """The sendable identities whose own fence is stopped (read-only, as the bridge reads them)."""
    return tuple(sorted(set(stopped_identity_fences(database)) & set(SENDABLE_IDENTITIES)))


def account_home() -> Path:
    """The account's own home directory (the password database), whatever HOME says."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _ere_literal(text: str) -> str:
    """``text`` as a POSIX extended regular expression that matches only itself (pgrep)."""
    return re.sub(r"([\\.^$*+?()\[\]{}|])", r"\\\1", text)


def next_session_date(now: datetime) -> date:
    """``supervisor.upcoming_session(now)``'s date. The windows' end does not depend
    on the strategy cutoffs (the replay ends last), so they are planned at the close,
    as ``db_admin.trading_day`` does, without importing the TWS and Claude stack."""

    def planner(session: MarketSession) -> Sequence[object]:
        return plan_session(session, cutoff_for=lambda _name, _day: session.closes_at)

    session, _plans = upcoming_session(now, planner)  # type: ignore[arg-type]
    return session.session_date


def parse_migrate_output(output: str) -> tuple[str | None, tuple[int, int] | None]:
    """``backup    <path>`` and ``migrated  v<a> -> v<b>`` from ``db migrate`` (db_admin.py)."""
    backup: str | None = None
    migrated: tuple[int, int] | None = None
    for line in output.splitlines():
        found = MIGRATE_BACKUP_LINE.match(line)
        if found:
            backup = found.group("path").strip()
        done = MIGRATE_DONE_LINE.match(line)
        if done:
            migrated = (int(done.group("before")), int(done.group("after")))
    return backup, migrated


# --- the dry run ------------------------------------------------------------------------------


def reviewed_line(pin: str) -> str:
    return f"{BRIDGE_PREFIX}reviewed (sha256 {pin[:12]})"


def pin_info_line(pin: str) -> str:
    return f"{BRIDGE_PREFIX}INFO bridge sha256={pin}"


def fenced_line(fenced: Sequence[str]) -> str:
    names = sorted(fenced)
    level = "CRITICAL" if names else "INFO"
    return f"{BRIDGE_PREFIX}{level} fenced identities = {', '.join(names) or 'none'}"


def sendable_line(names: Sequence[str]) -> str:
    return f"{BRIDGE_PREFIX}INFO sendable identities = {', '.join(sorted(names)) or 'none'}"


def schema_ok_line(version: int, database: str) -> str:
    return f"{DATABASE_PREFIX}v{version} current ({database})"


def registry_ok_line() -> str:
    return registry_lines(())[0]


def heartbeat_ok_line() -> str:
    return f"{HEARTBEAT_CHILD:<13}{presence_detail(lambda: True)[1]}"


@dataclass(frozen=True, slots=True)
class DryRunExpectation:
    session_date: date
    pin: str
    schema_version: int
    database: str
    # The bridge's own `INFO bridge sha256=` line (the undo's older code is
    # only asked for its reviewed pin).
    pin_info: bool = True
    fenced: tuple[str, ...] | None = ()
    # The undo's older bridge prints the fences of its own sendable set: with this,
    # ``fenced`` holds every stopped identity fence and the expected line keeps
    # those the dry run's one sendable line names, as that bridge does.
    fenced_by_printed_sendable: bool = False
    sendable: tuple[str, ...] | None = SENDABLE_IDENTITIES
    registry: bool = True
    heartbeat: bool = True
    budget: bool = False
    # The undo only (N17 MEDIUM-1): after 07:55 ET on the session day the dry run
    # simulates from now, and the session starts at once with no SESSION_WAIT.
    mid_session_ok: bool = False


@dataclass(frozen=True, slots=True)
class DryRunCheck:
    """The live dry run read line by line: one token per failed condition."""

    failures: tuple[tuple[str, str], ...]
    evidence: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures

    @classmethod
    def parse(cls, output: str, exit_code: int, expect: DryRunExpectation) -> "DryRunCheck":
        lines = output.splitlines()
        failures: list[tuple[str, str]] = []
        evidence: list[str] = []

        def exactly_one(token: str, wanted: str, *, family: Callable[[str], bool]) -> None:
            members = [line for line in lines if family(line)]
            if members == [wanted]:
                evidence.append(wanted)
                return
            shown = "; ".join(mask_accounts(line) for line in members[:3]) or "none"
            failures.append((token, f"expected exactly `{wanted}`, found {len(members)}: {shown}"))

        if exit_code != 0:
            failures.append((DRY_RUN_EXIT, f"the dry run exited {exit_code}"))
        bangs = [line for line in lines if "!!!" in line]
        if bangs:
            failures.append((DRY_RUN_BANG, f"{len(bangs)} line(s) with !!!: {mask_accounts(bangs[0])}"))
        warnings = [line for line in lines if line.startswith("WARNING:")]
        if warnings:
            failures.append((DRY_RUN_WARNING, f"{len(warnings)} WARNING: line(s): {mask_accounts(warnings[0])}"))
        exactly_one(
            DRY_RUN_PIN, reviewed_line(expect.pin), family=lambda line: line.startswith(f"{BRIDGE_PREFIX}reviewed ")
        )
        if expect.pin_info:
            exactly_one(
                DRY_RUN_PIN,
                pin_info_line(expect.pin),
                family=lambda line: line.startswith(f"{BRIDGE_PREFIX}INFO bridge sha256="),
            )
        fenced = expect.fenced
        if fenced is not None and expect.fenced_by_printed_sendable:
            printed = [line for line in lines if line.startswith(SENDABLE_LINE_PREFIX)]
            if len(printed) == 1:
                names = set(printed[0][len(SENDABLE_LINE_PREFIX):].split(", "))
                fenced = tuple(sorted(set(fenced) & names))
            else:
                fenced = None
                failures.append(
                    (
                        DRY_RUN_SENDABLE,
                        f"expected one `{SENDABLE_LINE_PREFIX}...` line to read the bridge's own sendable set,"
                        f" found {len(printed)}",
                    )
                )
        if fenced is not None:
            exactly_one(
                DRY_RUN_FENCED,
                fenced_line(fenced),
                family=lambda line: line.startswith(BRIDGE_PREFIX) and " fenced identities = " in line,
            )
        if expect.sendable is not None:
            exactly_one(
                DRY_RUN_SENDABLE,
                sendable_line(expect.sendable),
                family=lambda line: line.startswith(SENDABLE_LINE_PREFIX),
            )
        if expect.registry:
            exactly_one(
                DRY_RUN_REGISTRY, registry_ok_line(), family=lambda line: line.startswith("registry ")
            )
        exactly_one(
            DRY_RUN_SCHEMA,
            schema_ok_line(expect.schema_version, expect.database),
            family=lambda line: line.startswith(DATABASE_PREFIX),
        )
        day = expect.session_date.isoformat()
        sessions = [line for line in lines if line.startswith(SESSION_PREFIX)]
        waits = [found.group("day") for found in map(_SESSION_WAIT_EVENT.match, lines) if found]
        started = [found.group("day") for found in map(_SESSION_START_EVENT.match, lines) if found]
        session_ok = len(sessions) == 1 and sessions[0].startswith(f"{SESSION_PREFIX}{day} (")
        if session_ok and waits == [day]:
            evidence.append(sessions[0])
            evidence.append(f"SESSION_WAIT date={day}")
        elif session_ok and expect.mid_session_ok and not waits and started == [day]:
            evidence.append(sessions[0])
            evidence.append(f"SESSION_START date={day} (mid-session: no SESSION_WAIT)")
        else:
            also = f" (or, mid-session, one SESSION_START date={day})" if expect.mid_session_ok else ""
            failures.append(
                (
                    DRY_RUN_SESSION,
                    f"expected `{SESSION_PREFIX}{day} (...` and one SESSION_WAIT date={day}{also};"
                    f" found {'; '.join(sessions) or 'no session line'}, SESSION_WAIT {waits or 'none'}",
                )
            )
        if expect.heartbeat:
            # The status column only (`heartbeat    ...`), not the always-on plan rows.
            exactly_one(
                DRY_RUN_HEARTBEAT, heartbeat_ok_line(), family=lambda line: line.startswith(f"{HEARTBEAT_CHILD:<13}")
            )
        if expect.budget:
            budget = [line for line in lines if BUDGET_TEXT in line]
            if len(budget) == 1:
                evidence.append(budget[0])
            else:
                failures.append((DRY_RUN_BUDGET, f"expected one `{BUDGET_TEXT}` line, found {len(budget)}"))
        return cls(failures=tuple(failures), evidence=tuple(evidence))


# --- processes and the supervisor log -------------------------------------------------------------


def supervisors(table: Sequence[ProcessEntry]) -> list[ProcessEntry]:
    return [entry for entry in table if re.search(_SUPERVISOR_PATTERN, entry[2])]


def trading_processes(table: Sequence[ProcessEntry]) -> list[ProcessEntry]:
    """Every screener, strategy, notifier, telegram, bridge or heartbeat process,
    supervised or not (after a stop there must be none)."""
    own = os.getpid()
    return [entry for entry in table if entry[0] != own and re.search(_UNSUPERVISED_PATTERN, entry[2])]


def descends_from(table: Sequence[ProcessEntry], pid: int, ancestor: int) -> bool:
    parents = {entry[0]: entry[1] for entry in table}
    seen: set[int] = set()
    while pid in parents and pid not in seen:
        seen.add(pid)
        pid = parents[pid]
        if pid == ancestor:
            return True
    return False


@dataclass(frozen=True, slots=True)
class LogLine:
    at: datetime
    kind: str
    text: str

    @property
    def fields(self) -> list[str]:
        return self.text.split()


def latest_archive(log_directory: Path) -> Path | None:
    """``supervisor.log.<latest rotation date>``: where a rotation at start moved
    the lines written before it (rehearsal finding L3-A)."""
    try:
        names = [entry.name for entry in log_directory.iterdir()]
    except OSError:
        return None
    dated = sorted(found.group("day") for found in map(_ARCHIVE_NAME.match, names) if found)
    return log_directory / f"{SUPERVISOR_LOG_NAME}.{dated[-1]}" if dated else None


def _read_lines(path: Path) -> list[str]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return []
    with os.fdopen(descriptor, "r", encoding="utf-8", errors="replace") as stream:
        return stream.read().splitlines()


def supervisor_lines(log_directory: Path, since: datetime) -> list[LogLine]:
    """``supervisor.log`` and its latest archive, lines stamped at or after
    ``since``; a line copied by a rotation (same text) counts once."""
    paths = [path for path in (latest_archive(log_directory), log_directory / SUPERVISOR_LOG_NAME) if path]
    seen: set[str] = set()
    found: list[LogLine] = []
    for path in paths:
        for text in _read_lines(path):
            at = line_time(text)
            fields = text.split()
            if at is None or len(fields) < 2 or at < since or text in seen:
                continue
            seen.add(text)
            found.append(LogLine(at=at, kind=fields[1], text=text))
    found.sort(key=lambda line: line.at)
    return found


def floor_second(moment: datetime) -> datetime:
    return moment.replace(microsecond=0)


def rotation_due_at(log_directory: Path, moment: datetime) -> bool:
    """A start now rotates at once: the newest 18:35 boundary has no archive yet
    (``LogRotation.due_at``)."""
    boundary = latest_rotation_boundary(moment)
    return not archive_path(log_directory / SUPERVISOR_LOG_NAME, boundary.date()).exists()


def rotations_allowed(since: datetime) -> int:
    """One LOG_ROTATED line; two for a start within 18:30-18:35 ET (the previous
    day's rotation at once, then the 18:35 one)."""
    wall = since.astimezone(MARKET_TIMEZONE).time()
    return 2 if _ROTATION_DOUBLE[0] <= wall < _ROTATION_DOUBLE[1] else 1


# --- requests and environment -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeployApplyRequest:
    candidate: str
    repo: Path
    expect_parent: str
    expect_pin: str
    scope: tuple[str, ...]
    expect_migration: int | None = None
    expect_fenced: tuple[str, ...] = ()
    label: str | None = None
    ledger_glob: str = DEFAULT_LEDGER_GLOB


@dataclass(frozen=True, slots=True)
class DeployUndoRequest:
    repo: Path
    to: str
    reason: str


@dataclass(slots=True)
class DeployEnvironment:
    """Everything a deploy touches outside git, injectable for tests."""

    user_paths: UserPaths
    db_path: Path
    stdin: TextIO
    out: TextIO
    clock: Clock = _local_now
    sleep: Sleep = time_module.sleep
    process_table: ProcessTable = process_table
    live_runner: LiveRunner = subprocess_runner
    launchctl: LaunchdCommandRunner | None = None
    sntp: SntpProbe | None = None
    own_root: Path = OWN_ROOT
    writer_factory: Callable[[Path], GitWriter] = GitWriter
    account_home: Callable[[], Path] = account_home


@dataclass(frozen=True, slots=True)
class ApplyPlan:
    request: DeployApplyRequest
    head: str
    prev: str
    label: str
    pin: str
    pinned: dict[str, str]
    old_pin: str
    schema_before: int
    schema_after: int
    session_date: date
    budget: bool
    tool_sha: str


@dataclass(slots=True)
class _State:
    """Where a run stands, for the failure paths."""

    enabled: bool = False
    migrated: bool = False
    backup: str | None = None
    migrate_started: datetime | None = None
    dry_run_passed: bool = False


# --- the operator -------------------------------------------------------------------------------


class DeployOperator:
    """One ``deploy apply`` or ``deploy undo`` run against the live checkout ``repo``."""

    def __init__(self, env: DeployEnvironment, repo: Path) -> None:
        self.env = env
        self.repo = repo.expanduser().resolve()
        self.git = Git(self.repo)
        self.live = LiveCheckout(self.repo, env.live_runner)
        self.writer = env.writer_factory(self.repo)
        self.deploys_log = DeploysLog(env.user_paths.log_directory / DEPLOYS_LOG_NAME)
        self.command = "deploy apply"
        self._last_dry_run: DryRunCheck | None = None
        # Set by undo_to: git never moved, so nothing was reset (result=ABORTED).
        self.undo_aborted = False

    # -- output --

    def say(self, line: str = "") -> None:
        try:
            print(line, file=self.env.out, flush=True)
        except (OSError, ValueError):
            pass  # the terminal went away; deploys.log keeps the record

    def now(self) -> datetime:
        return self.env.clock()

    def _refuse(self, refusals: Sequence[tuple[str, str]]) -> int:
        for token, detail in refusals:
            self.say(f"REFUSE {token}: {detail}")
        tokens = ",".join(dict.fromkeys(token for token, _detail in refusals))
        self.say(f"{self.command}: REFUSED {tokens} (exit {EXIT_REFUSED}; nothing changed)")
        return EXIT_REFUSED

    def _append(self, line: str) -> bool:
        try:
            self.deploys_log.append(line)
            return True
        except (DeploysLogError, ValueError) as exc:
            self.say(f"WARNING {DEPLOYS_LOG_UNUSABLE}: {exc}; append this line by hand: {line}")
            return False

    # -- apply --

    def apply(self, request: DeployApplyRequest) -> int:
        local = self.now().astimezone(MARKET_TIMEZONE)
        self.say(
            f"deploy apply: candidate {request.candidate} into {self.repo}"
            f" at {local:%Y-%m-%d %H:%M:%S} {local.tzname()} scope={','.join(request.scope)}"
            + (f" expect-migration={request.expect_migration}" if request.expect_migration is not None else "")
        )
        if not self.env.stdin.isatty():
            return self._refuse([(NOT_INTERACTIVE, "stdin is not a terminal: deploy apply is owner-run only")])
        lock = self.env.user_paths.data_directory / DEPLOY_LOCK_NAME
        try:
            with hold_lock(lock):
                plan, refusals = self._prechecks(request)
                if refusals or plan is None:
                    return self._refuse(refusals)
                self._print_plan(plan)
                shown_at = self.now()
                if not self._confirmed(plan.head, "the candidate's"):
                    return self._refuse([(CONFIRM_MISMATCH, "the typed sha is not the candidate's short sha")])
                stale = self._confirmation_problems(
                    shown_at=shown_at, head=plan.prev, session_date=plan.session_date
                )
                if stale:
                    return self._refuse(stale)
                return self._run(plan)
        except LockBusy as exc:
            return self._refuse([(DEPLOY_LOCK_BUSY, str(exc))])

    def _confirmation_problems(
        self, *, shown_at: datetime, head: str, session_date: date
    ) -> list[tuple[str, str]]:
        """N17 MEDIUM-1: the plan the owner confirmed must still hold when the sha
        is typed (the prompt waits for ever); else nothing is written and nothing runs."""
        now = self.now()
        problems: list[tuple[str, str]] = []
        waited = (now - shown_at).total_seconds()
        if waited > CONFIRM_TIMEOUT_S:
            problems.append(
                (
                    CONFIRM_TIMEOUT,
                    f"the sha was typed {waited:.0f} s after the plan was printed (at most {CONFIRM_TIMEOUT_S} s):"
                    " run the command again for a fresh plan",
                )
            )
        window = start_window_problem(now)
        if window:
            problems.append((START_CUTOFF, f"when the sha was typed: {window}"))
        live = self._live_head()
        if live != head:
            problems.append(
                (LIVE_HEAD_MOVED, f"live HEAD is {live}, not {head} as the plan showed: run the command again")
            )
        upcoming = next_session_date(now)
        if upcoming != session_date:
            problems.append(
                (
                    SESSION_DATE_CHANGED,
                    f"the upcoming session is {upcoming.isoformat()}, not {session_date.isoformat()} as the plan"
                    " showed: run the command again",
                )
            )
        return problems

    def _path_problems(self) -> list[tuple[str, str]]:
        """N17 LOW-4: the robot under launchd uses the account's canonical paths (the
        plist sets neither HOME nor DB_PATH); a shell pointed at a rehearsal copy
        would check one database and log directory while launchctl acts on the real robot."""
        account = self.env.account_home()
        canonical = UserPaths(home=account)
        problems: list[tuple[str, str]] = []
        if not _same_path(self.env.user_paths.home, account):
            problems.append(
                (
                    PATHS_NOT_CANONICAL,
                    f"HOME is {self.env.user_paths.home}, not this account's home {account}: run from a shell"
                    " whose HOME is the account's own (`echo $HOME`)",
                )
            )
        if not _same_path(self.env.db_path, canonical.database_file):
            problems.append(
                (
                    PATHS_NOT_CANONICAL,
                    f"the database is {self.env.db_path}, not the robot's {canonical.database_file}:"
                    " unset DB_PATH (`unset DB_PATH`)",
                )
            )
        return problems

    def _prechecks(self, request: DeployApplyRequest) -> tuple[ApplyPlan | None, list[tuple[str, str]]]:
        refusals: list[tuple[str, str]] = []
        now = self.now()
        if _same_path(self.env.own_root, self.repo):
            refusals.append(
                (
                    RUNS_FROM_LIVE_CHECKOUT,
                    f"this code runs from {self.repo} itself: run it from the candidate's worktree",
                )
            )
        window = start_window_problem(now)
        if window:
            refusals.append((START_CUTOFF, window))
        refusals.extend(self._path_problems())
        head = resolve_candidate(self.git, request.candidate)
        check_request = DeployCheckRequest(
            candidate=request.candidate,
            repo=self.repo,
            db_path=self.env.db_path,
            scope=request.scope,
            ledger_glob=request.ledger_glob,
            expect_parent=request.expect_parent,
            now=now,
            skip_clock=False,
            expect_migration=request.expect_migration,
        )
        for result in run_checks(check_request, sntp=self.env.sntp, git=self.git, head=head):
            for line in result.lines():
                self.say(f"check {line}")
            if result.status == REFUSE:
                refusals.append((_check_token(result), f"{result.token}: {result.detail}"))
        own_head = None
        try:
            own_head = Git(self.env.own_root).resolve("HEAD")
        except GitError:
            pass
        if head is None or own_head != head:
            refusals.append(
                (
                    NOT_CANDIDATE_WORKTREE,
                    f"this code runs from {self.env.own_root} at {(own_head or '?')[:12]}, not the candidate"
                    f" {request.candidate} {(head or '?')[:12]}: run it from the candidate's worktree",
                )
            )
        dirty = self._own_checkout_changes()
        if dirty:
            refusals.append(
                (
                    CANDIDATE_WORKTREE_DIRTY,
                    f"{self.env.own_root} has {len(dirty)} change(s) the review did not see"
                    f" ({'; '.join(dirty[:5])}): `git -C {self.env.own_root} status --short` must print nothing",
                )
            )
        if not os.access(self.live.python, os.X_OK) or not self.live.entrypoint.is_file():
            refusals.append(
                (LIVE_PYTHON_MISSING, f"{self.live.python} or {self.live.entrypoint} is missing or not executable")
            )
        prev = self._live_head()
        problem = prev_sha_problem(self.git, prev, head) if head else f"PREV_SHA {prev!r}: no candidate"
        if problem:
            refusals.append((PREV_SHA_INVALID, problem))
        if head is None:
            return None, refusals
        try:
            changed = self.git.changed_paths("HEAD", head)
        except GitError as exc:
            changed = []
            refusals.append((CHANGED_PATHS_UNREADABLE, f"cannot list the paths HEAD..candidate changes: {exc}"))
        tool_changes = sorted(set(changed) & set(DEPLOY_TOOL_PATHS))
        if tool_changes and DEPLOY_TOOL_SCOPE not in request.scope:
            refusals.append(
                (
                    DEPLOY_TOOL_SCOPE_MISSING,
                    f"the candidate changes the deploy tool ({', '.join(tool_changes)}): its verdict scope"
                    f" must list {DEPLOY_TOOL_SCOPE} (--scope ...,{DEPLOY_TOOL_SCOPE})",
                )
            )
        verdict, matched = match_verdict(
            self.git,
            candidate=request.candidate,
            head=head,
            ledger_glob=request.ledger_glob,
            scope=request.scope,
            expect_migration=request.expect_migration,
        )
        problems, pinned = pin_problems(self.git, head, request.expect_pin, matched)
        if verdict.status == REFUSE and not matched:
            problems = [item for item in problems if item[0] != PIN_VERDICT_MISSING]
        refusals.extend(problems)
        old_pin = ""
        if prev is not None and problem is None:
            try:
                old_pin = bridge_pin_at(self.git, prev)
            except (GitError, SyntaxError, ValueError) as exc:
                refusals.append((PREV_SHA_INVALID, f"the live code's pin cannot be read: {exc}"))
        try:
            fenced = fenced_sendable(self.env.db_path)
        except (OSError, sqlite3.Error) as exc:
            fenced = ()
            refusals.append((FENCED_UNEXPECTED, f"identity fences unreadable: {type(exc).__name__}: {exc}"))
        else:
            if fenced != tuple(sorted(request.expect_fenced)):
                refusals.append(
                    (
                        FENCED_UNEXPECTED,
                        f"fenced sendable identities are {', '.join(fenced) or 'none'}, --expect-fenced"
                        f" {', '.join(sorted(request.expect_fenced)) or 'none'}",
                    )
                )
        migrate_lock = self.env.db_path.with_name(self.env.db_path.name + ".migrate.lock")
        if lock_is_free(migrate_lock) is not True:
            refusals.append((MIGRATE_LOCK_BUSY, f"{migrate_lock} is held: a `db migrate` is running"))
        log_problem = self.deploys_log.problem()
        if log_problem:
            refusals.append((DEPLOYS_LOG_UNUSABLE, log_problem))
        label = request.label or (verdict_label(matched[0].text) if matched else "")
        if matched and not _valid_label(label):
            refusals.append(
                (LABEL_MISSING, f"the verdict's release label {label!r} is not one or two release words: pass --label")
            )
        status = inspect_schema(self.env.db_path)
        bridge_blob = self.git.blob(head, BRIDGE_PATH) or b""
        if refusals or prev is None:
            return None, refusals
        return (
            ApplyPlan(
                request=request,
                head=head,
                prev=prev,
                label=label,
                pin=request.expect_pin,
                pinned=pinned,
                old_pin=old_pin,
                schema_before=status.applied_version,
                schema_after=status.code_version,
                session_date=next_session_date(now),
                budget=b"\nDAY_RISK_BUDGET_PCT" in bridge_blob,
                tool_sha=(own_head or "?")[:SHORT_SHA_LENGTH],
            ),
            [],
        )

    def _live_head(self) -> str | None:
        try:
            return self.git.resolve("HEAD")
        except GitError:
            return None

    def _own_checkout_changes(self) -> list[str]:
        """N17 LOW-3: what the tool's own checkout holds beyond its HEAD: tracked
        changes anywhere, and untracked files under backend/ (first on sys.path,
        so importable). The review saw HEAD only."""
        try:
            entries = Git(self.env.own_root).status_entries()
        except GitError as exc:
            return [f"git status failed: {exc}"]
        return [entry for entry in entries if not entry.startswith("??") or entry[3:].startswith("backend/")]

    def _print_paths(self) -> None:
        self.say(f"  home           {self.env.user_paths.home} (the account's: {self.env.account_home()})")
        self.say(f"  logs           {self.env.user_paths.log_directory} (supervisor.log, {DEPLOYS_LOG_NAME})")
        self.say(f"  data           {self.env.user_paths.data_directory}")

    def _print_plan(self, plan: ApplyPlan) -> None:
        request = plan.request
        migration = request.expect_migration is not None
        self.say("")
        self.say("PLAN (nothing has changed yet):")
        self.say(f"  live checkout  {self.repo}  HEAD {plan.prev} (PREV_SHA)")
        self.say(f"  candidate      {request.candidate} = {plan.head}  label {plan.label}")
        self.say(f"  bridge pin     {plan.pin} (was {plan.old_pin[:12]})")
        self.say(
            f"  database       {self.env.db_path}  schema v{plan.schema_before}"
            + (f" -> v{plan.schema_after} (db migrate)" if migration else " (unchanged)")
        )
        self._print_paths()
        fenced = ", ".join(request.expect_fenced) or "none"
        self.say(f"  session        {plan.session_date.isoformat()}  fenced {fenced}")
        first = prev_line(self.now(), plan.label, plan.prev, plan.schema_before)
        steps = [
            f"append to {self.deploys_log.path}: {first}",
            f"stop: {self.live.command_line('shadow', 'agent', 'disable')}; wait up to {STOP_WAIT_TIMEOUT_S} s"
            " for no supervisor, no trading process, a free supervisor.lock and SUPERVISOR_STOP reason=SIGTERM",
            f"window again, then git -C {self.repo} merge --ff-only {plan.head};"
            " then HEAD, the pinned files' sha256 and the tree",
        ]
        if migration:
            steps.append(
                f"{self.live.command_line('db', 'migrate', '--db-path', str(self.env.db_path))}"
                f" (expect migrated  v{plan.schema_before} -> v{plan.schema_after}), then db status"
            )
        steps += [
            f"{self.live.command_line('run', '--dry-run', '--date', plan.session_date.isoformat())}:"
            " every required line, no !!!, no WARNING:",
            f"window again, then {self.live.command_line('shadow', 'agent', 'enable')}; wait up to"
            f" {POST_START_TIMEOUT_S} s for the start lines, the children, the plist and HEAD, and"
            f" {POST_START_SETTLE_S} s with no telegram or heartbeat exit",
            f"append the DEPLOY {plan.label} {plan.head[:SHORT_SHA_LENGTH]} ... result=PASS line",
        ]
        # Rehearsal F-3: numbered consecutively, with or without the migration.
        for number, step in enumerate(steps, start=1):
            self.say(f"  {number}. {step}")
        if migration:
            self.say(
                "  A failure after the migration HALTS: the robot stays disabled and the restore is printed"
                " (never run by this command)."
            )
        else:
            self.say(f"  A failure after the stop undoes the deploy by itself: back to {plan.prev}.")
        self._print_confirmation_rule()

    def _print_confirmation_rule(self) -> None:
        self.say(
            f"  Type the sha within {CONFIRM_TIMEOUT_S} s: the window, live HEAD and the session are read again"
            " when you type, and a changed fact refuses (nothing changed). Do not close this window while it runs."
        )

    def _confirmed(self, sha: str, whose: str) -> bool:
        short = sha[:SHORT_SHA_LENGTH]
        try:
            self.env.out.write(
                f"Type {whose} short sha ({SHORT_SHA_LENGTH} characters) to go on; anything else aborts: "
            )
            self.env.out.flush()
        except (OSError, ValueError):
            pass
        try:
            typed = self.env.stdin.readline()
        except (OSError, ValueError, EOFError, KeyboardInterrupt):
            return False  # Ctrl-C at the prompt aborts: nothing has changed yet
        return typed.strip() == short

    def _run(self, plan: ApplyPlan) -> int:
        first = prev_line(self.now(), plan.label, plan.prev, plan.schema_before)
        try:
            self.deploys_log.append(first)
        except (DeploysLogError, ValueError) as exc:
            return self._refuse([(DEPLOYS_LOG_UNUSABLE, f"the PREV line could not be written: {exc}")])
        self.say(f"OK deploys.log: {first}")
        state = _State()
        with signals_interrupt():
            try:
                try:
                    self.stop()
                except DeployFailure as failure:
                    return self._halt_before_ff(plan, failure)
                try:
                    self._require_window("fast-forward")
                    self.fast_forward(plan)
                    if plan.request.expect_migration is not None:
                        state.migrated = True
                        state.migrate_started = self.now()
                        state.backup = self.migrate(plan)
                    self.dry_run(self._expectation(plan))
                    state.dry_run_passed = True
                    self._require_window("start")
                    since = self.enable(state)
                    self.wait_started(since, expected_head=plan.head, session_date=plan.session_date)
                except DeployFailure as failure:
                    if state.migrated:
                        return self._halt_after_migration(plan, failure, state)
                    return self._auto_undo(plan, failure, state)
            except KeyboardInterrupt as interrupt:
                return self._halt_unexpected(plan, INTERRUPTED, _interrupted(interrupt), state)
            except Exception as exc:  # noqa: BLE001 - every unexpected error halts with the manual blocks
                return self._halt_unexpected(plan, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}", state)
            with signals_deferred():
                line = pass_line(self.now(), plan.label, plan.head, plan.prev, plan.pin, plan.schema_after)
                self._append(line)
                checked = len(self._last_dry_run.evidence) if self._last_dry_run else 0
                self.say(
                    f"{iso_seconds(self.now())} | DEPLOY {plan.label} {plan.head[:SHORT_SHA_LENGTH]}"
                    f" PREV_SHA={plan.prev} pin={plan.pin[:12]} result=PASS | owner-run via autotraderctl deploy"
                    f" apply ({plan.tool_sha}); {checked} dry-run lines ok; post-checks ok"
                    f" | {_display(self.deploys_log.path)}"
                )
                self.say(f"deploy apply: PASS {plan.label} {plan.head[:SHORT_SHA_LENGTH]} (exit {EXIT_OK})")
        return EXIT_OK

    def _require_window(self, step: str) -> None:
        problem = start_window_problem(self.now())
        if problem:
            raise DeployFailure(START_CUTOFF, f"before the {step}: {problem}")
        self.say(f"OK window before the {step}")

    def _expectation(self, plan: ApplyPlan) -> DryRunExpectation:
        return DryRunExpectation(
            session_date=plan.session_date,
            pin=plan.pin,
            schema_version=plan.schema_after,
            database=str(self.env.db_path.expanduser()),
            fenced=tuple(sorted(plan.request.expect_fenced)),
            sendable=SENDABLE_IDENTITIES,
            budget=plan.budget,
        )

    # -- steps --

    def stop(self) -> None:
        """Disable the LaunchAgent, then wait on what is observed (C11)."""
        self.say(f"STEP stop: {self.live.command_line('shadow', 'agent', 'disable')}")
        lock = self.env.user_paths.data_directory / LOCK_FILE_NAME
        table = list(self.env.process_table())
        running = bool(supervisors(table)) or lock_is_free(lock) is False
        since = floor_second(self.now())
        result = self.live.disable()
        if result.returncode != 0:
            # launchd's 10 s launchctl timeout can fire while the 45 s stop goes on.
            self.say(
                f"note: shadow agent disable exited {result.returncode} ({result.last_error()});"
                " waiting on what is observed"
            )
        if not running:
            self.say("note: no supervisor was running before the stop: no SUPERVISOR_STOP line is awaited")
        self.wait_stopped(since, require_stop_line=running)

    def _stop_state(self, since: datetime, require_stop_line: bool) -> tuple[list[str], list[ProcessEntry]]:
        table = list(self.env.process_table())
        missing: list[str] = []
        running = supervisors(table)
        strays = [entry for entry in trading_processes(table) if entry not in running]
        left = running + strays
        if not table:
            missing.append("the process table is empty (ps failed)")
        if running:
            missing.append(f"supervisor still running: pid {', '.join(str(entry[0]) for entry in running)}")
        if strays:
            missing.append(f"trading process(es) still running: pid {', '.join(str(entry[0]) for entry in strays)}")
        lock = self.env.user_paths.data_directory / LOCK_FILE_NAME
        free = lock_is_free(lock)
        if free is not True:
            missing.append(f"{lock} is {'held' if free is False else 'unreadable'}")
        if require_stop_line:
            stopped = [
                line
                for line in supervisor_lines(self.env.user_paths.log_directory, since)
                if line.kind == "SUPERVISOR_STOP" and "reason=SIGTERM" in line.fields
            ]
            if not stopped:
                missing.append(f"no SUPERVISOR_STOP reason=SIGTERM line since {since.astimezone(UTC).isoformat()}")
        return missing, left

    def wait_stopped(self, since: datetime, *, require_stop_line: bool) -> None:
        since = floor_second(since)
        deadline = self.now() + timedelta(seconds=STOP_WAIT_TIMEOUT_S)
        while True:
            missing, left = self._stop_state(since, require_stop_line)
            if not missing:
                self.say("OK stopped: no supervisor, no trading process, supervisor.lock free" + (
                    ", SUPERVISOR_STOP reason=SIGTERM" if require_stop_line else ""
                ))
                return
            if self.now() >= deadline:
                raise DeployFailure(
                    STOP_WAIT_TIMEOUT,
                    f"not stopped after {STOP_WAIT_TIMEOUT_S} s: " + "; ".join(missing),
                    notes=[f"pid {pid} ppid {ppid}: {mask_accounts(command)}" for pid, ppid, command in left],
                )
            self.env.sleep(POLL_S)

    def fast_forward(self, plan: ApplyPlan) -> None:
        self.say(f"STEP git -C {self.repo} merge --ff-only {plan.head}")
        try:
            self.writer.merge_ff_only(plan.head)
        except (GitError, ValueError) as exc:
            raise DeployFailure(MERGE_FAILED, str(exc)) from exc
        head = self._live_head()
        if head != plan.head:
            raise DeployFailure(HEAD_MISMATCH, f"HEAD is {head}, not the candidate {plan.head}")
        for path, digest in sorted(plan.pinned.items()):
            try:
                actual = hashlib.sha256((self.repo / path).read_bytes()).hexdigest()
            except OSError as exc:
                raise DeployFailure(BRIDGE_SHA_MISMATCH, f"{self.repo / path}: {type(exc).__name__}") from exc
            if actual != digest:
                raise DeployFailure(BRIDGE_SHA_MISMATCH, f"{self.repo / path} sha256 {actual} is not the pin {digest}")
        tree = check_tree_clean(self.git, benign_paths=DEFAULT_BENIGN_PATHS)
        if tree.status == REFUSE:
            raise DeployFailure(TREE_DIRTY_AFTER_FF, tree.detail, notes=tree.notes)
        self.say(f"OK fast-forward: HEAD {plan.head}, bridge sha256 {plan.pin[:12]}, tree only benign")

    def migrate(self, plan: ApplyPlan) -> str | None:
        expected = (plan.schema_before, plan.schema_after)
        self.say(f"STEP {self.live.command_line('db', 'migrate', '--db-path', str(self.env.db_path))}")
        result = self.live.migrate(self.env.db_path)
        for line in result.stdout.splitlines():
            self.say(f"  | {mask_accounts(line)}")
        backup, migrated = parse_migrate_output(result.stdout)
        if result.returncode != 0:
            raise DeployFailure(MIGRATE_FAILED, f"db migrate exited {result.returncode}: {result.last_error()}")
        if backup is None or migrated != expected:
            raise DeployFailure(
                MIGRATE_UNPARSED,
                f"expected `backup    <path>` and `migrated  v{expected[0]} -> v{expected[1]}`,"
                f" read backup={backup} migrated={migrated}",
            )
        status = self.live.status(self.env.db_path)
        current = f"state     schema v{plan.schema_after} is current"
        if status.returncode != 0 or current not in status.stdout.splitlines():
            raise DeployFailure(
                MIGRATE_STATUS_NOT_CURRENT, f"db status exited {status.returncode}: {status.last_error()}"
            )
        self.say(f"OK migrated v{expected[0]} -> v{expected[1]}; backup {backup}")
        return backup

    def dry_run(self, expect: DryRunExpectation) -> DryRunCheck:
        self.say(f"STEP {self.live.command_line('run', '--dry-run', '--date', expect.session_date.isoformat())}")
        result = self.live.dry_run(expect.session_date)
        check = DryRunCheck.parse(result.stdout, result.returncode, expect)
        for line in check.evidence:
            self.say(f"  | {mask_accounts(line)}")
        if not check.ok:
            token, detail = check.failures[0]
            notes = [f"also {other}: {text}" for other, text in check.failures[1:]]
            if result.returncode != 0:
                notes.append(f"dry run stderr: {result.last_error()}")
            raise DeployFailure(token, detail, notes=notes)
        self._last_dry_run = check
        self.say(f"OK dry run: {len(check.evidence)} required lines, no !!!, no WARNING:")
        return check

    def enable(self, state: _State) -> datetime:
        since = floor_second(self.now())
        self.say(f"STEP {self.live.command_line('shadow', 'agent', 'enable')}")
        state.enabled = True
        result = self.live.enable()
        if result.returncode != 0:
            self.say(
                f"note: shadow agent enable exited {result.returncode} ({result.last_error()});"
                " waiting on what is observed"
            )
        return since

    def _launchd(self) -> LaunchdManager:
        if self.env.launchctl is None:
            return LaunchdManager(user_paths=self.env.user_paths, repository_root=self.repo)
        return LaunchdManager(
            user_paths=self.env.user_paths, repository_root=self.repo, command_runner=self.env.launchctl
        )

    def _start_state(
        self,
        since: datetime,
        session_date: date,
        *,
        rotation_due: bool,
        mid_session_ok: bool = False,
    ) -> tuple[list[tuple[str, str]], list[str], list[str], datetime | None]:
        """``(failures, still missing, evidence, settle from)`` of the start, from the
        logs and ps; ``settle from`` is the last required CHILD_START's time."""
        lines = supervisor_lines(self.env.user_paths.log_directory, since)
        table = list(self.env.process_table())
        failures: list[tuple[str, str]] = []
        missing: list[str] = []
        evidence: list[str] = []
        loud = {
            "LAUNCH_REFUSED": POST_START_LAUNCH_REFUSED,
            "LOG_ROTATION_FAILED": POST_START_ROTATION_FAILED,
            "PREFLIGHT_WARNING": POST_START_PREFLIGHT_WARNING,
        }
        for line in lines:
            if line.kind in loud:
                failures.append((loud[line.kind], mask_accounts(line.text)))
        starts = [line for line in lines if line.kind == "SUPERVISOR_START"]
        pid: int | None = None
        if len(starts) > 1:
            failures.append((POST_START_NO_START, f"{len(starts)} SUPERVISOR_START lines: the supervisor restarted"))
        elif not starts:
            missing.append("no SUPERVISOR_START line")
        else:
            found = _PID.search(starts[0].text)
            if found is None or "mode=daily" not in starts[0].fields:
                failures.append((POST_START_NO_START, f"not a daily start: {starts[0].text}"))
            else:
                pid = int(found.group("pid"))
                evidence.append(starts[0].text)
        # Only a stop after the new start is the new supervisor's: log times have
        # whole seconds, and the previous supervisor's stop can share the enable's.
        first_start = next((index for index, line in enumerate(lines) if line.kind == "SUPERVISOR_START"), None)
        stops = [] if first_start is None else [
            line for line in lines[first_start + 1:] if line.kind == "SUPERVISOR_STOP"
        ]
        if stops:
            failures.append((POST_START_NO_START, f"the supervisor stopped: {stops[-1].text}"))
        after_start = [] if first_start is None else lines[first_start + 1:]
        if pid is not None:
            running = supervisors(table)
            others = [entry for entry in running if entry[0] != pid]
            mine = [entry for entry in running if entry[0] == pid]
            if others:
                failures.append((POST_START_NO_START, f"another supervisor runs: pid {others[0][0]}"))
            if not mine:
                missing.append(f"supervisor pid {pid} in the process table")
            elif f"{self.live.entrypoint} run" not in mine[0][2]:
                failures.append(
                    (
                        POST_START_NO_START,
                        f"pid {pid} is not the live checkout's supervisor: {mask_accounts(mine[0][2])}",
                    )
                )
        waits = [line for line in lines if line.kind == "SESSION_WAIT"]
        wanted = f"date={session_date.isoformat()}"
        wrong = [line for line in waits if wanted not in line.fields]
        # The undo's previous code may start mid-session (N17 MEDIUM-1, the real
        # 2026-10-13 09:46 ET restart): SESSION_START at once, no SESSION_WAIT.
        sessions = [line for line in after_start if line.kind == "SESSION_START"] if mid_session_ok else []
        if wrong:
            failures.append((POST_START_SESSION_WAIT, f"{wrong[0].text} is not {wanted}"))
        elif waits:
            evidence.append(waits[0].text)
        elif sessions and wanted not in sessions[0].fields:
            failures.append((POST_START_SESSION_WAIT, f"{sessions[0].text} is not {wanted}"))
        elif sessions:
            evidence.append(sessions[0].text)
        else:
            missing.append(f"SESSION_WAIT {wanted}" + (f" (or SESSION_START {wanted})" if mid_session_ok else ""))
        always_on = ((TELEGRAM_CHILD, POST_START_TELEGRAM_MISSING), (HEARTBEAT_CHILD, POST_START_HEARTBEAT_MISSING))
        settle_from: datetime | None = None
        for child, token in always_on:
            # Rehearsal F-1: a required child that exited (or restarted) after the
            # new start is a failure, even when its restart is running again.
            exits = [line for line in after_start if line.kind == "CHILD_EXIT" and line.fields[2:3] == [child]]
            restarts = [line for line in after_start if line.kind == "CHILD_START" and line.fields[2:3] == [child]]
            if exits:
                failures.append((POST_START_CHILD_EXITED, f"{mask_accounts(exits[0].text)} after the start"))
                continue
            if len(restarts) > 1:
                failures.append((POST_START_CHILD_EXITED, f"{len(restarts)} CHILD_START {child} lines: restarted"))
                continue
            events = [
                line
                for line in lines
                if line.kind in ("CHILD_START", "CHILD_EXIT") and line.fields[2:3] == [child]
            ]
            if not events or events[-1].kind != "CHILD_START":
                missing.append(f"a running CHILD_START {child}")
                continue
            found = _PID.search(events[-1].text)
            child_pid = int(found.group("pid")) if found else -1
            if not any(entry[0] == child_pid for entry in table):
                missing.append(f"{child} pid {child_pid} in the process table")
            elif pid is None or not descends_from(table, child_pid, pid):
                failures.append(
                    (token, f"{child} pid {child_pid} does not descend from the new supervisor (pid {pid})")
                )
            else:
                evidence.append(events[-1].text)
                settle_from = events[-1].at if settle_from is None else max(settle_from, events[-1].at)
        rotated = [line for line in lines if line.kind == "LOG_ROTATED"]
        allowed = rotations_allowed(since)
        if len(rotated) > allowed:
            failures.append(
                (POST_START_ROTATION_FAILED, f"{len(rotated)} LOG_ROTATED lines since the start, at most {allowed}")
            )
        elif rotation_due and not rotated:
            missing.append("the LOG_ROTATED line of the rotation due at this start")
        evidence.extend(line.text for line in rotated)
        return failures, missing, evidence, settle_from

    def _plist_problem(self, *, check_current: bool) -> str | None:
        path = self.env.user_paths.launch_agent_file
        if path.is_symlink() or not path.is_file():
            return f"{path} is missing or not a regular file"
        try:
            payload = plistlib.loads(path.read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException) as exc:
            return f"{path} unreadable: {type(exc).__name__}"
        directory = payload.get("WorkingDirectory") if isinstance(payload, dict) else None
        arguments = payload.get("ProgramArguments") if isinstance(payload, dict) else None
        if directory != str(self.live.backend):
            return f"plist WorkingDirectory {directory!r} is not {self.live.backend}"
        if not isinstance(arguments, list) or arguments[3:5] != [str(self.live.python), str(self.live.entrypoint)]:
            shown = arguments[3:5] if isinstance(arguments, list) else arguments
            return f"plist ProgramArguments[3:5] {shown!r} are not the live checkout's"
        manager = self._launchd()
        if check_current and not manager.is_current():
            return f"{path} is not the plist the live checkout writes"
        try:
            loaded = manager.is_loaded()
        except LaunchdInstallError as exc:
            return f"launchctl print failed: {exc}"
        if not loaded:
            return "the LaunchAgent is not loaded"
        return None

    def wait_started(
        self,
        since: datetime,
        *,
        expected_head: str,
        session_date: date,
        check_current: bool = True,
        mid_session_ok: bool = False,
    ) -> None:
        since = floor_second(since)
        rotation_due = rotation_due_at(self.env.user_paths.log_directory, since)
        deadline = self.now() + timedelta(seconds=POST_START_TIMEOUT_S)
        settled = False
        while True:
            failures, missing, evidence, settle_from = self._start_state(
                since, session_date, rotation_due=rotation_due, mid_session_ok=mid_session_ok
            )
            if failures:
                token, detail = failures[0]
                raise DeployFailure(token, detail, notes=[f"{t}: {d}" for t, d in failures[1:]])
            if missing:
                settled = False
                if self.now() >= deadline:
                    raise DeployFailure(
                        POST_START_TIMEOUT, f"still missing after {POST_START_TIMEOUT_S} s: " + "; ".join(missing)
                    )
            elif settle_from is not None and self.now() < settle_from + timedelta(seconds=POST_START_SETTLE_S):
                # Rehearsal F-1: every start line is there; watch the children for
                # POST_START_SETTLE_S after their CHILD_START before trusting them.
                settled = False
            elif settled:
                break
            else:
                # One more look: a LOG_ROTATION_FAILED follows its marker.
                settled = True
            self.env.sleep(POLL_S)
        for line in evidence:
            self.say(f"  | {line}")
        self.say(f"OK settled: telegram and heartbeat ran {POST_START_SETTLE_S} s after their CHILD_START, no exit")
        problem = self._plist_problem(check_current=check_current)
        if problem:
            raise DeployFailure(POST_START_PLIST, problem)
        head = self._live_head()
        if head != expected_head:
            raise DeployFailure(POST_START_HEAD, f"HEAD is {head}, expected {expected_head}")
        mid_session = not any(" SESSION_WAIT " in line for line in evidence)
        session = (
            f"SESSION_START date={session_date} (mid-session)" if mid_session else f"SESSION_WAIT date={session_date}"
        )
        self.say(
            f"OK started: one supervisor, telegram and heartbeat under it, {session},"
            f" plist WorkingDirectory {self.live.backend}, loaded, HEAD {expected_head[:12]}"
        )

    # -- failure paths --

    def _halt_before_ff(self, plan: ApplyPlan, failure: DeployFailure) -> int:
        self.say(f"FAIL {failure.token}: {failure.detail}")
        for note in failure.notes:
            self.say(f"  {note}")
        self._append(halted_line(self.now(), plan.label, plan.head, plan.prev, plan.pin, failure.token, None))
        self.say("Git is untouched. When the processes above are gone, the one re-enable command is:")
        enable = self.live.command_line("shadow", "agent", "enable")
        self.say(f"  ( cd {shlex.quote(str(self.live.backend))} && {enable} )")
        self.say(f"{self.command}: HALTED reason={failure.token} (exit {EXIT_HALTED}; the robot is disabled)")
        return EXIT_HALTED

    def _halt_after_migration(self, plan: ApplyPlan, failure: DeployFailure, state: _State) -> int:
        self.say(f"FAIL {failure.token}: {failure.detail}")
        for note in failure.notes:
            self.say(f"  {note}")
        self.say("The database was migrated (or may have been): HALT, never an automatic undo.")
        self._leave_disabled()
        self._append(
            halted_line(self.now(), plan.label, plan.head, plan.prev, plan.pin, failure.token, state.backup or "none")
        )
        if failure.token == START_CUTOFF and state.dry_run_passed and not state.enabled:
            # Rehearsal R5c: only the start came too late; the migrated candidate
            # passed its dry run. D4 holds: this command never starts it itself.
            self._print_start_migrated(plan)
            self.say("OPTION 2: go back to PREV with the database restore below.")
        self._print_restore(
            plan.prev,
            plan.old_pin,
            plan.schema_before,
            plan.schema_after,
            state.backup,
            plan.session_date,
            made_after=state.migrate_started,
        )
        self.say(f"{self.command}: HALTED reason={failure.token} (exit {EXIT_HALTED}; the robot is disabled)")
        return EXIT_HALTED

    def _print_start_migrated(self, plan: ApplyPlan) -> None:
        self.say(
            f"OPTION 1: start the new code. The database is at v{plan.schema_after} and"
            f" {plan.head[:SHORT_SHA_LENGTH]}'s dry run passed; only the start came after"
            f" {LAST_START:%H:%M} ET. One line at a time:"
        )
        self.say(f"  cd {shlex.quote(str(self.live.backend))}")
        self.say(f"  {self.live.command_line('shadow', 'agent', 'enable')}")
        self._print_start_expectation(plan.session_date)
        line = (
            f"DEPLOY {plan.label} {plan.head[:SHORT_SHA_LENGTH]} PREV_SHA={plan.prev} pin={plan.pin[:12]}"
            f" result=PASS schema=v{plan.schema_after}"
        )
        self.say(f"  echo \"$(date -Iseconds) {line}\" >> {shlex.quote(str(self.deploys_log.path))}")
        self.say("  (the echo only after both looks showed the expected lines)")

    def _print_start_expectation(self, session_date: date) -> None:
        log = shlex.quote(str(self.env.user_paths.log_directory / SUPERVISOR_LOG_NAME))
        self.say(f"  tail -n 8 {log}")
        self.say(
            f"  Expect: SUPERVISOR_START pid=<n> mode=daily, CHILD_START {TELEGRAM_CHILD}, CHILD_START"
            f" {HEARTBEAT_CHILD}, then SESSION_WAIT date={session_date} (SESSION_START date={session_date} after"
            " 07:55 ET); no PREFLIGHT_WARNING, LAUNCH_REFUSED or CHILD_EXIT"
        )
        self.say(f"  after {POST_START_SETTLE_S} s, tail -n 8 {log} again: still no CHILD_EXIT {TELEGRAM_CHILD} or"
                 f" {HEARTBEAT_CHILD}")

    def _halt_unexpected(self, plan: ApplyPlan, token: str, detail: str, state: _State) -> int:
        with signals_deferred():
            self.say(f"FAIL {token}: {detail}")
            head = self._live_head()
            # The terminal may be gone (SIGHUP): deploys.log carries the next action too.
            if state.migrated:
                after = (
                    f"restore the database and the code to PREV_SHA={plan.prev} (backup={state.backup or 'none'});"
                    " never enable the old code on the migrated database"
                )
            elif head == plan.prev:
                after = (
                    f"git never moved (HEAD {plan.prev}): ( cd {self.live.backend} &&"
                    f" {self.live.command_line('shadow', 'agent', 'enable')} ), then the start lines"
                )
            else:
                after = f"runbook 6.3 back to PREV_SHA={plan.prev} (HEAD {head or 'unknown'}): only when flat"
            self._append(
                halted_line(self.now(), plan.label, plan.head, plan.prev, plan.pin, token, state.backup)
                + f" next: {after}"
            )
            self.say(f"Nothing more was run. Live HEAD is {head or 'unknown'}; the robot may be stopped or running.")
            if state.migrated:
                self._print_restore(
                    plan.prev,
                    plan.old_pin,
                    plan.schema_before,
                    plan.schema_after,
                    state.backup,
                    plan.session_date,
                    made_after=state.migrate_started,
                )
            else:
                self._print_manual_rollback(plan.prev, plan.old_pin, plan.session_date, token)
            self.say(f"{self.command}: HALTED reason={token} (exit {EXIT_HALTED})")
        return EXIT_HALTED

    def _leave_disabled(self) -> None:
        """Disable and wait; best effort (the wait's outcome is printed, never raised)."""
        try:
            self.stop()
        except DeployFailure as failure:
            self.say(f"WARNING {failure.token}: {failure.detail}")
            for note in failure.notes:
                self.say(f"  {note}")
        except Exception as exc:  # noqa: BLE001 - a failure path must reach its manual block
            self.say(f"WARNING the disable could not be confirmed: {type(exc).__name__}: {exc}")

    def _auto_undo(self, plan: ApplyPlan, failure: DeployFailure, state: _State) -> int:
        self.say(f"FAIL {failure.token}: {failure.detail}")
        for note in failure.notes:
            self.say(f"  {note}")
        self.say(f"UNDO automatic (code-only deploy): back to {plan.prev}")
        code = self.undo_to(
            plan.prev,
            failure.token,
            plan.old_pin,
            session_date=plan.session_date,
            launchd_changed=LAUNCHD_PATH in _changed(self.git, plan.prev, plan.head),
            fenced=plan.request.expect_fenced,
            aborted=lambda moment, token: aborted_line(moment, plan.label, plan.head, plan.prev, plan.pin, token),
        )
        if code == EXIT_ROLLED_BACK and self.undo_aborted:
            self.say(
                f"{self.command}: ABORTED reason={failure.token} (exit {EXIT_ROLLED_BACK}; git never moved;"
                f" {plan.prev[:SHORT_SHA_LENGTH]} runs again)"
            )
        elif code == EXIT_ROLLED_BACK:
            self.say(
                f"{self.command}: ROLLED BACK reason={failure.token} (exit {EXIT_ROLLED_BACK};"
                f" {plan.prev[:SHORT_SHA_LENGTH]} runs again)"
            )
        return code

    def undo_to(
        self,
        prev: str,
        reason: str,
        expected_old_pin: str,
        *,
        session_date: date,
        launchd_changed: bool = False,
        fenced: Sequence[str] | None = None,
        aborted: Callable[[datetime, str], str] | None = None,
    ) -> int:
        """P2-4: stop, ``reset --keep`` to PREV, re-read HEAD, dry run with the old
        pin, start; ``result=ROLLBACK`` and exit 3, or exit 5 with the robot disabled.

        With ``aborted`` (the automatic undo) and live HEAD still PREV, git never
        moved: nothing is reset, and ``aborted(now, reason)`` is the line written.
        ``fenced`` is the identity fence set seen before the deploy (default: read now).
        """
        state = _State()
        self.undo_aborted = False
        try:
            current = next_session_date(self.now())
            if current != session_date:
                # N17 MEDIUM-1: the session the previous code will meet is the one now.
                self.say(f"note: the upcoming session is now {current}, not {session_date}: the undo checks {current}")
                session_date = current
            try:
                self.stop()
            except DeployFailure as failure:
                raise DeployFailure(UNDO_STOP_TIMEOUT, failure.detail, notes=failure.notes) from failure
            problem = prev_sha_problem(self.git, prev)
            if problem:
                raise DeployFailure(UNDO_PREV_INVALID, problem)
            if aborted is not None and self._live_head() == prev:
                self.undo_aborted = True
                self.say(f"OK HEAD is still {prev}: git never moved, nothing to reset")
            else:
                self.say(f"STEP git -C {self.repo} reset --keep {prev}")
                try:
                    self.writer.reset_keep(prev)
                except (GitError, ValueError) as exc:
                    raise DeployFailure(UNDO_RESET_FAILED, str(exc)) from exc
                head = self._live_head()
                if head != prev:
                    raise DeployFailure(UNDO_RESET_NOOP, f"HEAD is {head} after reset --keep {prev}")
                self.say(f"OK HEAD {prev}")
            status = inspect_schema(self.env.db_path)
            fences: tuple[str, ...] | None
            if fenced is not None:
                fences = tuple(sorted(fenced))
            else:
                try:
                    fences = stopped_identity_fences(self.env.db_path)
                except (OSError, sqlite3.Error) as exc:
                    fences = None
                    self.say(f"note: identity fences unreadable ({type(exc).__name__}): the fenced line is not checked")
            expect = DryRunExpectation(
                session_date=session_date,
                pin=expected_old_pin,
                schema_version=status.applied_version,
                database=str(self.env.db_path.expanduser()),
                pin_info=False,
                fenced=fences,
                fenced_by_printed_sendable=True,
                sendable=None,
                registry=False,
                heartbeat=True,
                mid_session_ok=True,
            )
            try:
                self.dry_run(expect)
            except DeployFailure as failure:
                detail = f"{failure.token}: {failure.detail}"
                raise DeployFailure(UNDO_DRY_RUN, detail, notes=failure.notes) from failure
            since = self.enable(state)
            try:
                self.wait_started(
                    since,
                    expected_head=prev,
                    session_date=session_date,
                    check_current=not launchd_changed,
                    mid_session_ok=True,
                )
            except DeployFailure as failure:
                detail = f"{failure.token}: {failure.detail}"
                raise DeployFailure(UNDO_POST_START, detail, notes=failure.notes) from failure
        except Exception as exc:  # noqa: BLE001 - N17 LOW-6: every failure inside the undo
            failure = exc if isinstance(exc, DeployFailure) else DeployFailure(
                INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
            )
            return self._undo_failed(prev, expected_old_pin, session_date, reason, failure, state)
        if self.undo_aborted and aborted is not None:
            self._append(aborted(self.now(), reason))
            self.say(f"OK aborted: {prev} runs again; reason={reason}")
        else:
            self._append(rollback_line(self.now(), prev, reason))
            self.say(f"OK rollback to {prev}: reason={reason}")
        return EXIT_ROLLED_BACK

    def _undo_failed(
        self, prev: str, old_pin: str, session_date: date, reason: str, failure: DeployFailure, state: _State
    ) -> int:
        """Any failure inside the undo: disabled, UNDO_FAILED, runbook 6.3, exit 5."""
        with signals_deferred():
            self.say(f"FAIL {failure.token}: {failure.detail}")
            for note in failure.notes:
                self.say(f"  {note}")
            if state.enabled or failure.token == INTERNAL_ERROR:
                self._leave_disabled()
            self._append(undo_failed_line(self.now(), prev, failure.token))
            if failure.token in (UNDO_DRY_RUN, UNDO_POST_START):
                # N17 LOW-5: the previous code itself showed this; it may have run so before.
                step = "dry run" if failure.token == UNDO_DRY_RUN else "start"
                condition = failure.detail.split(":", 1)[0]
                self.say(
                    f"The condition above is {prev[:SHORT_SHA_LENGTH]}'s own {step} ({condition})."
                    " If the robot ran with it before this deploy (for example the same PREFLIGHT_WARNING heartbeat"
                    " line), the block's last enable restores that state."
                )
            self._print_manual_rollback(prev, old_pin, session_date, reason)
            self.say(
                f"{self.command}: UNDO FAILED reason={failure.token} after {reason}"
                f" (exit {EXIT_UNDO_FAILED}; the robot is disabled)"
            )
        return EXIT_UNDO_FAILED

    def _print_manual_rollback(self, prev: str, old_pin: str, session_date: date, reason: str) -> None:
        """Runbook 6.3 with literal values, for the owner's zsh: one line at a time."""
        self.say("MANUAL ROLLBACK (runbook 6.3), only when flat; run one line at a time:")
        self.say(f"  cd {shlex.quote(str(self.live.backend))}")
        self.say(f"  {self.live.command_line('shadow', 'agent', 'disable')}")
        self.say(f"  git -C {shlex.quote(str(self.repo))} reset --keep {prev}")
        self.say(f"  git -C {shlex.quote(str(self.repo))} rev-parse HEAD")
        self.say(f"  {self.live.command_line('run', '--dry-run', '--date', session_date.isoformat())}")
        self.say(f"  {self.live.command_line('shadow', 'agent', 'enable')}")
        self.say(
            f"  echo \"$(date -Iseconds) DEPLOY rollback to {prev} result=ROLLBACK reason={reason}\""
            f" >> {shlex.quote(str(self.deploys_log.path))}"
        )
        self.say(f"Expect: rev-parse prints {prev}; the dry run shows `{reviewed_line(old_pin)}` and no !!!.")
        self.say("After the enable:")
        self._print_start_expectation(session_date)

    def _pgrep_line(self) -> str:
        """Rehearsal O-e: only the live checkout's processes (its venv python and
        scripts), never another checkout's or worktree's."""
        return f"pgrep -fl {shlex.quote(_ere_literal(f'{self.live.backend}/'))} || echo none"

    def _print_restore(
        self,
        prev: str,
        old_pin: str,
        before: int,
        after: int,
        backup: str | None,
        session_date: date,
        *,
        made_after: datetime | None = None,
    ) -> None:
        """P2-3: the database restore, printed with literal values and never executed."""
        database = self.env.db_path.expanduser()
        stamp = self.now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        quoted = shlex.quote(str(database))
        self.say("RESTORE (never run by this command); one line at a time, each only after the one before passed:")
        if not backup:
            # N17 MEDIUM-2: no `backup` line means `db migrate` refused before it
            # changed anything. "The newest" same-pair backup can be an older
            # attempt's file: copied over the live data it loses every row since.
            self.say("  No `backup    <path>` line was read from `db migrate`: no backup made by this run is known.")
            self.say(f"  {self.live.command_line('db', 'status', '--db-path', str(database))}")
            try:
                status = inspect_schema(database)
                state = status.describe()
                unchanged = not status.problems and status.applied_version == before
            except Exception as exc:  # noqa: BLE001 - unknown is never "unchanged"
                state, unchanged = f"unreadable: {type(exc).__name__}: {exc}", False
            self.say(f"  state now (read-only, as db status prints it): {state}")
            if not unchanged:
                self.say(
                    f"  The database is not at v{before}, or its state is unknown: STOP and ask before copying"
                    " anything. Copy no file you did not see this run make."
                )
                when = (
                    f"stamped at or after {made_after.astimezone(UTC):%Y%m%dT%H%M%S}Z (this run's `db migrate`"
                    f" started {iso_seconds(made_after)})"
                    if made_after is not None
                    else "made by that deploy (between its PREV line and its DEPLOY line in deploys.log)"
                )
                self.say(
                    f"  This run's backup, if one exists: {shlex.quote(str(database.parent / 'backups'))}/"
                    f"{database.stem}.<YYYYMMDDTHHMMSS.ffffff>Z.v{before}-to-v{after}.db {when}; never an older one."
                )
                return
            self.say(f"  The database is unchanged at v{before}: no database restore is needed. Run only:")
        numbers = iter(range(1, 10))

        def step(*lines: str, each: bool = False) -> None:
            number = next(numbers)
            for index, line in enumerate(lines):
                self.say(f"  {number}. {line}" if index == 0 or each else f"     {line}")

        step(self._pgrep_line(), "Expect: none")
        if backup:
            moves = []
            for suffix in ("", "-wal", "-shm"):
                source = shlex.quote(f"{database}{suffix}")
                target = shlex.quote(f"{database}{suffix}.v{after}-failed-{stamp}")
                moves.append(f"[ -e {source} ] && mv {source} {target}")
            step(*moves, each=True)
            step(f"cp {shlex.quote(backup)} {quoted}")
        step(
            f"git -C {shlex.quote(str(self.repo))} reset --keep {prev}",
            f"git -C {shlex.quote(str(self.repo))} rev-parse HEAD",
            f"Expect: {prev}",
        )
        step(
            f"cd {shlex.quote(str(self.live.backend))}",
            self.live.command_line("run", "--dry-run", "--date", session_date.isoformat()),
            f"Expect: `{reviewed_line(old_pin)}` and `{DATABASE_PREFIX}v{before} current`, no !!!",
        )
        step(self.live.command_line("shadow", "agent", "enable"))

    # -- undo --

    def undo(self, request: DeployUndoRequest) -> int:
        self.command = "deploy undo"
        local = self.now().astimezone(MARKET_TIMEZONE)
        self.say(
            f"deploy undo: {self.repo} to {request.to} reason={request.reason}"
            f" at {local:%Y-%m-%d %H:%M:%S} {local.tzname()}"
        )
        if not self.env.stdin.isatty():
            return self._refuse([(NOT_INTERACTIVE, "stdin is not a terminal: deploy undo is owner-run only")])
        lock = self.env.user_paths.data_directory / DEPLOY_LOCK_NAME
        try:
            with hold_lock(lock):
                refusals, old_pin, head = self._undo_prechecks(request)
                if refusals or head is None:
                    return self._refuse(refusals)
                session_date = next_session_date(self.now())
                self.say("")
                self.say("PLAN (nothing has changed yet):")
                self.say(f"  live checkout  {self.repo}  HEAD {head}")
                self.say(f"  back to        {request.to} (bridge pin {old_pin[:12]})  reason={request.reason}")
                self.say(f"  database       {self.env.db_path}")
                self._print_paths()
                self.say(f"  then the dry run for {session_date.isoformat()} and the start checks")
                self._print_confirmation_rule()
                shown_at = self.now()
                if not self._confirmed(request.to, "the --to"):
                    return self._refuse([(CONFIRM_MISMATCH, "the typed sha is not the --to short sha")])
                stale = self._confirmation_problems(shown_at=shown_at, head=head, session_date=session_date)
                if stale:
                    return self._refuse(stale)
                with signals_interrupt():
                    try:
                        code = self.undo_to(
                            request.to,
                            request.reason,
                            old_pin,
                            session_date=session_date,
                            launchd_changed=LAUNCHD_PATH in _changed(self.git, request.to, head),
                        )
                    except KeyboardInterrupt as interrupt:
                        with signals_deferred():
                            self.say(f"FAIL {INTERRUPTED}: {_interrupted(interrupt)}; nothing more was run")
                            self._append(
                                undo_failed_line(self.now(), request.to, INTERRUPTED)
                                + f" next: runbook 6.3 back to {request.to} (HEAD {self._live_head() or 'unknown'})"
                            )
                            self._print_manual_rollback(request.to, old_pin, session_date, request.reason)
                            self.say(f"{self.command}: HALTED reason={INTERRUPTED} (exit {EXIT_HALTED})")
                        return EXIT_HALTED
                if code == EXIT_ROLLED_BACK:
                    self.say(f"{self.command}: ROLLED BACK reason={request.reason} (exit {EXIT_ROLLED_BACK})")
                return code
        except LockBusy as exc:
            return self._refuse([(DEPLOY_LOCK_BUSY, str(exc))])

    def _undo_prechecks(self, request: DeployUndoRequest) -> tuple[list[tuple[str, str]], str, str | None]:
        refusals: list[tuple[str, str]] = []
        if _same_path(self.env.own_root, self.repo):
            refusals.append((RUNS_FROM_LIVE_CHECKOUT, f"this code runs from {self.repo} itself"))
        window = start_window_problem(self.now())
        if window:
            refusals.append((START_CUTOFF, window))
        refusals.extend(self._path_problems())
        head = self._live_head()
        problem = prev_sha_problem(self.git, request.to)
        if problem:
            refusals.append((PREV_SHA_INVALID, problem))
        old_pin = ""
        if problem is None:
            try:
                old_pin = bridge_pin_at(self.git, request.to)
            except (GitError, SyntaxError, ValueError) as exc:
                refusals.append((PREV_SHA_INVALID, f"--to's pin cannot be read: {exc}"))
        if not os.access(self.live.python, os.X_OK) or not self.live.entrypoint.is_file():
            refusals.append((LIVE_PYTHON_MISSING, f"{self.live.python} or {self.live.entrypoint} is missing"))
        try:
            log = self.deploys_log.lines()
        except DeploysLogError as exc:
            log = []
            refusals.append((DEPLOYS_LOG_UNUSABLE, str(exc)))
        deploy = _deploy_line_for(log, head) if head else None
        if deploy is None:
            refusals.append(
                (
                    UNDO_PREV_MISMATCH,
                    f"no `DEPLOY ... result=PASS` line for HEAD {(head or '?')[:12]} in {self.deploys_log.path}",
                )
            )
        elif deploy[1].group("prev") != request.to:
            refusals.append(
                (
                    UNDO_PREV_MISMATCH,
                    f"--to {request.to} is not the PREV_SHA {deploy[1].group('prev')} of the DEPLOY line for HEAD"
                    f" {(head or '?')[:12]}: {deploy[1].string.strip()}",
                )
            )
        else:
            index, found = deploy
            after = found.group("schema")
            before = _prev_schema(log[:index], request.to)
            if after is None or before is None:
                refusals.append(
                    (
                        UNDO_SCHEMA_UNKNOWN,
                        "the PREV or DEPLOY line carries no schema=v<n> (written before P2): check `db status`"
                        " and use the manual runbook 6.3",
                    )
                )
            elif int(after) != before:
                refusals.append(
                    (
                        UNDO_NEEDS_DB_RESTORE,
                        f"the deploy migrated v{before} -> v{after}: the database must be restored first",
                    )
                )
                # The deploy migrated and passed, so its backup is the newest of the
                # pair; without one, _print_restore copies nothing (N17 MEDIUM-2).
                self._print_restore(
                    request.to, old_pin or "?" * 12, before, int(after),
                    _newest_backup(self.env.db_path, before, int(after)), next_session_date(self.now()),
                )
        flat = _not_flat(self.env.user_paths.data_directory / BRIDGE_STATE_DIRECTORY)
        if flat:
            refusals.append((UNDO_NOT_FLAT, flat))
        return refusals, old_pin, head


# --- helpers ------------------------------------------------------------------------------------


def _interrupted(interrupt: KeyboardInterrupt) -> str:
    if isinstance(interrupt, DeploySignal):
        return f"interrupted ({interrupt.name}: the terminal closed or the process was told to stop)"
    return "interrupted (Ctrl-C)"


def _valid_label(label: str) -> bool:
    words = label.split(" ")
    return 1 <= len(words) <= 2 and all(LABEL_PATTERN.fullmatch(word) for word in words)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return False


def _check_token(result: CheckResult) -> str:
    """A deploy_check refusal's token here: the P2-3 tokens lead their detail."""
    for token in (MIGRATION_UNEXPECTED, VERDICT_MIGRATION_MISSING):
        if result.detail.startswith(f"{token}:"):
            return token
    return f"{DEPLOY_CHECK_PREFIX}{result.token}"


def _changed(git: Git, base: str, head: str) -> list[str]:
    try:
        return git.changed_paths(base, head)
    except GitError:
        return [LAUNCHD_PATH]  # unknown: do not compare the plist bytes


def _display(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _deploy_line_for(lines: Sequence[str], head: str) -> tuple[int, re.Match[str]] | None:
    """The newest ``DEPLOY ... result=PASS`` line whose short sha is HEAD's."""
    for index in range(len(lines) - 1, -1, -1):
        found = _DEPLOY_PASS.search(lines[index])
        if found and head.startswith(found.group("short")):
            return index, found
    return None


def _prev_schema(lines: Sequence[str], prev: str) -> int | None:
    """``schema=v<n>`` of the newest PREV line for ``prev`` before the DEPLOY line."""
    for line in reversed(lines):
        found = _PREV_LINE.match(line.strip())
        if found and found.group("prev") == prev:
            schema = found.group("schema")
            return None if schema is None else int(schema)
    return None


def _newest_backup(database: Path, before: int, after: int) -> str | None:
    directory = database.expanduser().parent / "backups"
    try:
        found = sorted(
            entry for entry in directory.iterdir()
            if entry.name.startswith(f"{database.stem}.") and entry.name.endswith(f".v{before}-to-v{after}.db")
        )
    except OSError:
        return None
    return str(found[-1]) if found else None


def _not_flat(state_directory: Path) -> str | None:
    """Why the newest bridge state is not flat (a working order or shares held), else None."""
    try:
        files = sorted(
            entry for entry in state_directory.iterdir()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", entry.name)
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"{state_directory} unreadable: {type(exc).__name__}"
    if not files:
        return None
    newest = files[-1]
    try:
        if newest.is_symlink():
            raise ValueError("a symbolic link")
        document = json.loads(newest.read_text(encoding="utf-8"))
        submitted = document["submitted"]
        if not isinstance(submitted, dict):
            raise ValueError("no submitted map")
        held: list[str] = []
        for key, bracket in submitted.items():
            if not isinstance(bracket, dict):
                raise ValueError(f"entry {key} is not an object")
            status = str(bracket.get("status", ""))
            net = int(bracket.get("filled", 0) or 0) - int(bracket.get("exit_filled", 0) or 0)
            if status in _NOT_FLAT_STATUSES and (status == "sent" or net != 0):
                held.append(f"{bracket.get('symbol', '?')} {status} {net} sh")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"{newest} unreadable ({type(exc).__name__}: {exc}): flatness unknown"
    if held:
        return f"{newest.name}: {', '.join(held[:5])}: not flat (runbook 6.3: only when flat, no working orders)"
    return None


def run_deploy_apply(request: DeployApplyRequest, env: DeployEnvironment) -> int:
    """``autotraderctl deploy apply``: 0 PASS, 78 refused, 3 rolled back, 4 halted, 5 undo failed."""
    return DeployOperator(env, request.repo).apply(request)


def run_deploy_undo(request: DeployUndoRequest, env: DeployEnvironment) -> int:
    """``autotraderctl deploy undo``: 3 rolled back, 78 refused, 4 halted, 5 undo failed."""
    return DeployOperator(env, request.repo).undo(request)


def default_environment(db_path: Path) -> DeployEnvironment:
    return DeployEnvironment(
        user_paths=UserPaths.current(),
        db_path=db_path.expanduser(),
        stdin=sys.stdin,
        out=sys.stdout,
    )


__all__ = [
    "CONFIRM_TIMEOUT_S",
    "DEPLOYS_LOG_NAME",
    "DEPLOY_LOCK_NAME",
    "DEPLOY_TOOL_PATHS",
    "DeployApplyRequest",
    "DeployEnvironment",
    "DeployFailure",
    "DeployOperator",
    "DeploySignal",
    "DeployUndoRequest",
    "DeploysLog",
    "DeploysLogError",
    "DryRunCheck",
    "DryRunExpectation",
    "EXIT_HALTED",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_ROLLED_BACK",
    "EXIT_UNDO_FAILED",
    "FIRST_START",
    "GitWriter",
    "HALT_SIGNALS",
    "LAST_START",
    "LiveCheckout",
    "LiveResult",
    "POLL_S",
    "POST_START_SETTLE_S",
    "POST_START_TIMEOUT_S",
    "STOP_WAIT_TIMEOUT_S",
    "TOKENS",
    "account_home",
    "default_environment",
    "in_start_window",
    "lock_is_free",
    "parse_migrate_output",
    "prev_sha_problem",
    "run_deploy_apply",
    "run_deploy_undo",
    "subprocess_runner",
    "supervisor_lines",
]
