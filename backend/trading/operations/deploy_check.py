"""``autotraderctl deploy check``: runbook 6.2 STEP 0 as code, strictly read-only.

The read-only half of a one-command deploy. It answers "may this candidate be
fast-forwarded now?" and changes nothing: no merge, no launchctl, no database
write (one ``mode=ro`` handle, the schema status helper of ``db_admin``), and
every git call passes ``--no-optional-locks`` so not even the index's stat
cache is refreshed in the live checkout. The write half (``deploy apply`` and
``deploy undo``) is ``trading.operations.deploy_apply``, with its own review.

Each check prints one line, ``OK <token>``, ``WARN <token>: <detail>`` or
``REFUSE <token>: <detail>``, followed by indented evidence lines; the run ends
with ``deploy check: <n> ok, <w> warn, <m> refused`` and exits 0 when nothing
refused, else 78 (EX_CONFIG, the bridge's refusal code). A check that cannot
decide (git error, unreadable database, no clock reading) refuses: a pre-check
that fails open is no pre-check.

Tokens, in the runbook's order:

* ``verdict`` (I-11): a ledger verdict line (``<time> | [<label>] <sha>
  verdict=GO ... | ...``, the verdict at the start of the check field) in a
  ``docs/reviews`` ledger of the candidate itself names the candidate head, or
  names an ancestor followed only by ``docs/reviews/`` changes (the addendum
  commit).
* ``tree_clean``: the live checkout has nothing but known-benign entries.
* ``parent``: its HEAD is an ancestor of the candidate (a fast-forward) and
  not the candidate itself (``--repo`` is the live checkout, never the
  candidate's own worktree), and equals ``--expect-parent`` when given.
* ``files`` (warn only): the candidate changes nothing outside the deployable
  top-level paths.
* ``schema``: the database is at exactly this code's schema; never migrates.
  With ``--expect-migration N`` (P2-3) the one pending migration must be
  exactly ``N`` (``migration_unexpected``), and the verdict line must carry
  ``migration=N`` (``verdict_migration_missing``).
* ``fence``: the global ``paper`` kill switch is not stopped.
* ``clock``: the ``sntp`` offset is at most 2 s (``--skip-clock``: WARN).
* ``window``: now is inside the deploy window 18:30 -> 07:20 ET (I-4).
* ``pin`` (I-5): the candidate's bridge hashes to the candidate's own
  ``REVIEWED_BRIDGE_SHA256``.

The readers ``match_verdict`` (the matched verdict lines), ``verdict_tokens``
(their ``bridge_sha=``/``migration=``/``imports_sha=``) and ``reviewed_pins``
(every ``REVIEWED_*`` constant of a supervisor.py) serve ``deploy apply``
(``trading.operations.deploy_apply``), which compares the pins three ways.
"""
from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import sys
from typing import TextIO
from urllib.parse import quote

from trading.scheduler.sessions import MARKET_TIMEZONE
from trading.storage.runtime_fence import GLOBAL_FENCE, IDENTITY_FENCE_PREFIX

UTC = timezone.utc
EXIT_OK = 0
EXIT_REFUSED = 78  # EX_CONFIG, as the bridge's own refusals

OK = "OK"
WARN = "WARN"
REFUSE = "REFUSE"

VERDICT = "verdict"
TREE_CLEAN = "tree_clean"
PARENT = "parent"
FILES = "files"
SCHEMA = "schema"
FENCE = "fence"
CLOCK = "clock"
WINDOW = "window"
PIN = "pin"
TOKENS = (VERDICT, TREE_CLEAN, PARENT, FILES, SCHEMA, FENCE, CLOCK, WINDOW, PIN)

REVIEWS_DIRECTORY = "docs/reviews"
DEFAULT_LEDGER_GLOB = f"{REVIEWS_DIRECTORY}/*ledger*.md"
# I-11: the only commits allowed between a verdict sha and the deployed head
# add review text (the addendum line itself). Nothing else under docs/.
REVIEW_ONLY_PREFIX = f"{REVIEWS_DIRECTORY}/"
# The operator's local Claude settings; never part of a deploy.
DEFAULT_BENIGN_PATHS = (".claude/settings.local.json",)
DEPLOYABLE_PREFIXES = ("backend/", "dashboard/", "docs/", "plans/", "scripts/")
DEPLOYABLE_FILES = ("AGENTS.md", "CLAUDE.md", "LEARNED.md", "README.md")
BRIDGE_PATH = "backend/scripts_local/paper_bridge.py"
SUPERVISOR_PATH = "backend/trading/operations/supervisor.py"
PIN_NAME = "REVIEWED_BRIDGE_SHA256"
# Every reviewed pin constant in supervisor.py starts with this (P3-B0 adds
# REVIEWED_BRIDGE_IMPORTS_SHA256); deploy apply reads them all generically.
REVIEWED_PREFIX = "REVIEWED_"
# P2-3 refusal tokens, the first word of the refusing check's detail.
MIGRATION_UNEXPECTED = "migration_unexpected"
VERDICT_MIGRATION_MISSING = "verdict_migration_missing"

# I-4: start a deploy only 18:30 -> 07:20 ET; the session, the recorder and
# the replay are children of the supervisor a deploy restarts.
WINDOW_OPENS = time(18, 30)
WINDOW_CLOSES = time(7, 20)
MAX_CLOCK_OFFSET_SECONDS = Decimal("2")
SNTP_SERVER = "time.apple.com"
SNTP_TIMEOUT_SECONDS = 20
GIT_TIMEOUT_SECONDS = 60
FENCE_READ_TIMEOUT_SECONDS = 5
NOTE_LIMIT = 12

# A ledger line is `<time> | <check> | <result> | <evidence>`, and a verdict is
# a check field that starts `[<label> ...] <sha> verdict=GO`, e.g.
#   2026-10-16T00:53:35+03:00 | P1-A 299d6e1 verdict=GO scope=T-1,T-2,T-5,T-7 bridge_sha=... | ... | ...
#   05:15 | 904af38 verdict=GO scope=A6,A7,A8,A10 | GO; ... | ...
# Nothing else counts (review P1-A F-9): prose that mentions a verdict ("not
# yet: <sha> verdict=GO", "(<sha> verdict=GO)", a backticked line a note is
# waiting for) in any other field, or a line outside the ledger grammar.
# The time is HH:MM[:SS], a date, or an ISO date-time with its offset.
LEDGER_TIME_PATTERN = re.compile(
    r"\d{1,2}:\d{2}(?::\d{2})?"
    r"|\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
# Up to two release labels, each with a digit (v7a, v7a.1, P1-A; never a plain
# English word), then a 7-40 hex sha not glued to a word or a backtick, and GO
# not followed by more of a word (no GO-PENDING).
VERDICT_PATTERN = re.compile(
    r"(?:(?=[\w.-]*\d)[A-Za-z][\w.-]* ){0,2}(?<![\w`])([0-9a-f]{7,40}) verdict=GO(?![\w-])"
)
SCOPE_PATTERN = re.compile(r"\bscope=([^\s|`]+)")
# The machine-read tokens of a verdict's check field, after the verdict itself.
VERDICT_TOKEN_PATTERN = re.compile(r"\b(bridge_sha|migration|imports_sha)=([^\s|`]+)")
PIN_PATTERN = re.compile(
    rf"^{PIN_NAME}\s*(?::\s*[\w\[\]]+\s*)?=\s*[\"']([0-9a-fA-F]{{64}})[\"']", re.MULTILINE
)
SNTP_OFFSET_PATTERN = re.compile(r"(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*\+/-\s*\d+(?:\.\d+)?")
# Variables that would point git somewhere other than --repo.
_GIT_LOCATION_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
)

SntpProbe = Callable[[], str]


class GitError(RuntimeError):
    """A git command failed; the check that asked refuses."""


class ClockUnavailable(RuntimeError):
    """``sntp`` gave no reading."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One named check: ``OK`` carries evidence notes only, never a detail."""

    token: str
    status: str
    detail: str = ""
    notes: tuple[str, ...] = ()

    def lines(self) -> list[str]:
        head = f"{self.status} {self.token}"
        if self.status != OK:
            head = f"{head}: {self.detail}"
        return [head, *(f"  {note}" for note in self.notes)]


@dataclass(frozen=True, slots=True)
class DeployCheckRequest:
    candidate: str
    repo: Path
    db_path: Path
    scope: tuple[str, ...] = ()
    ledger_glob: str = DEFAULT_LEDGER_GLOB
    expect_parent: str | None = None
    now: datetime | None = None
    skip_clock: bool = False
    benign_paths: tuple[str, ...] = DEFAULT_BENIGN_PATHS
    # P2-3: the one migration this deploy applies (None: nothing may be pending).
    expect_migration: int | None = None


@dataclass(frozen=True, slots=True)
class VerdictLine:
    path: str
    line_number: int
    text: str
    sha: str

    def describe(self) -> str:
        return f"{self.path}:{self.line_number}: {self.text.strip()}"


@dataclass(frozen=True, slots=True)
class Git:
    """Read-only git in one repository: every call passes ``--no-optional-locks``."""

    repo: Path

    def argv(self, *args: str) -> list[str]:
        return [
            "git",
            "--no-optional-locks",
            "-C",
            str(self.repo),
            # A configured fsmonitor would start a daemon in the live checkout.
            "-c",
            "core.fsmonitor=false",
            *args,
        ]

    def run(self, *args: str, accept: Sequence[int] = (0,)) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = subprocess.run(
                self.argv(*args),
                capture_output=True,
                env=git_environment(self.repo),
                stdin=subprocess.DEVNULL,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(f"git {args[0]} failed: {type(exc).__name__}: {exc}") from exc
        if completed.returncode not in accept:
            message = completed.stderr.decode("utf-8", "replace").strip().splitlines()
            reason = message[-1] if message else f"exit {completed.returncode}"
            raise GitError(f"git {args[0]} failed: {reason}")
        return completed

    def text(self, *args: str) -> str:
        return self.run(*args).stdout.decode("utf-8", "replace")

    def resolve(self, ref: str) -> str | None:
        """The full commit sha of ``ref``, or None (unknown, ambiguous, not a commit)."""
        completed = self.run(
            "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}", accept=(0, 1, 128)
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.decode("ascii", "replace").strip() or None

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        completed = self.run("merge-base", "--is-ancestor", ancestor, descendant, accept=(0, 1))
        return completed.returncode == 0

    def changed_paths(self, base: str, head: str) -> list[str]:
        """Every path whose content differs; renames as delete + add, so a file
        moved out of backend/ still shows its old path."""
        output = self.run("diff", "--name-only", "--no-renames", "-z", f"{base}..{head}").stdout
        return [item.decode("utf-8", "replace") for item in output.split(b"\0") if item]

    def commits_between(self, base: str, head: str) -> list[str]:
        """``<short sha> <subject>`` for each commit in ``base..head``, newest first."""
        output = self.text("log", "--no-show-signature", "--format=%h %s", f"{base}..{head}")
        return [line for line in output.splitlines() if line.strip()]

    def tree_paths(self, commit: str, pathspec: str | None) -> list[str]:
        args = ["ls-tree", "-r", "--name-only", "-z", commit]
        if pathspec:
            args += ["--", pathspec]
        output = self.run(*args).stdout
        return [item.decode("utf-8", "replace") for item in output.split(b"\0") if item]

    def blob(self, commit: str, path: str) -> bytes | None:
        completed = self.run("show", f"{commit}:{path}", accept=(0, 128))
        return completed.stdout if completed.returncode == 0 else None

    def status_entries(self) -> list[str]:
        """``git status --porcelain`` entries as ``XY path``; a rename or copy gives
        both its paths (``-z`` writes ``XY TO\\0FROM\\0``)."""
        output = self.run("status", "--porcelain=v1", "-z", "--untracked-files=normal").stdout
        items = iter(item.decode("utf-8", "replace") for item in output.split(b"\0"))
        entries: list[str] = []
        for item in items:
            if not item:
                continue
            entries.append(item)
            if "R" in item[:2] or "C" in item[:2]:
                origin = next(items, "")
                if origin:
                    entries.append(f"{item[:2]} {origin}")
        return entries


def git_environment(repo: Path) -> dict[str, str]:
    """The environment of every git call on ``repo`` (read-only here, and the
    two writes of ``deploy_apply.GitWriter``): nothing points git elsewhere."""
    env = {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION_VARIABLES}
    # --repo is the repository root: a plain directory inside another
    # repository must fail, never check the enclosing one.
    env["GIT_CEILING_DIRECTORIES"] = str(repo.expanduser().resolve().parent)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


# --- pure helpers ---------------------------------------------------------------


def parse_scope(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_now(value: str) -> datetime:
    """An ISO-8601 instant with its offset; a naive time is refused (no guessing a zone)."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"--now needs a UTC offset, e.g. 2026-10-19T02:00:00+03:00 (got {value!r})")
    return moment


def validate_ref(value: str, *, option: str) -> str:
    """A ref is passed to git as an argument: never an option, never blank."""
    ref = value.strip()
    if not ref or ref.startswith("-") or any(character.isspace() or character == "\0" for character in ref):
        raise ValueError(f"{option} must be a git ref or sha, got {value!r}")
    return ref


def glob_matches(path: str, pattern: str) -> bool:
    """Segment-wise glob: ``*`` never crosses a ``/``."""
    path_parts = PurePosixPath(path).parts
    pattern_parts = PurePosixPath(pattern).parts
    if len(path_parts) != len(pattern_parts):
        return False
    return all(fnmatchcase(part, glob) for part, glob in zip(path_parts, pattern_parts))


def static_prefix(pattern: str) -> str | None:
    """The leading directories of ``pattern`` without glob characters, for ls-tree."""
    parts: list[str] = []
    for part in PurePosixPath(pattern).parts[:-1]:
        if any(character in part for character in "*?["):
            break
        parts.append(part)
    return "/".join(parts) or None


def verdict_field(line: str) -> str | None:
    """The check field of a ledger line (``<time> | <check> | ...``), or None."""
    fields = line.split("|")
    if len(fields) < 2 or LEDGER_TIME_PATTERN.fullmatch(fields[0].strip()) is None:
        return None
    return fields[1].strip()


def verdict_lines(path: str, text: str, scope: Sequence[str] = ()) -> list[VerdictLine]:
    """Every ledger line whose check field is a ``<sha> verdict=GO`` verdict; with
    ``scope`` only those whose ``scope=`` (in that field, after the verdict) lists
    every requested slice."""
    wanted = set(scope)
    found: list[VerdictLine] = []
    for number, line in enumerate(text.splitlines(), start=1):
        field = verdict_field(line)
        match = VERDICT_PATTERN.match(field) if field is not None else None
        if field is None or match is None:
            continue
        if wanted:
            scoped = SCOPE_PATTERN.search(field, match.end())
            listed = set(parse_scope(scoped.group(1))) if scoped else set()
            if not wanted <= listed:
                continue
        found.append(VerdictLine(path=path, line_number=number, text=line, sha=match.group(1)))
    return found


def _verdict_match(line: str) -> tuple[str, re.Match[str]] | None:
    field = verdict_field(line)
    match = VERDICT_PATTERN.match(field) if field is not None else None
    return None if field is None or match is None else (field, match)


def verdict_tokens(line: str) -> dict[str, tuple[str, ...]]:
    """``bridge_sha=``, ``migration=`` and ``imports_sha=`` of a verdict line's
    check field (after the verdict, as ``scope=``); every value, in order."""
    found = _verdict_match(line)
    if found is None:
        return {}
    field, match = found
    tokens: dict[str, tuple[str, ...]] = {}
    for key, value in VERDICT_TOKEN_PATTERN.findall(field, match.end()):
        tokens[key] = (*tokens.get(key, ()), value)
    return tokens


def verdict_label(line: str) -> str:
    """The release label(s) before the sha in a verdict's check field (``P1-B``,
    ``v7a.1``), or ``""``."""
    found = _verdict_match(line)
    if found is None:
        return ""
    field, match = found
    return field[: match.start(1)].strip()


def reviewed_pins(source: str) -> dict[str, object]:
    """Every module-level ``REVIEWED_*`` constant of a supervisor.py, as a literal.

    Raises ``ValueError`` for a constant assigned twice or to anything but a
    literal (a pin that cannot be read is no pin).
    """
    tree = ast.parse(source)
    pins: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name) or not target.id.startswith(REVIEWED_PREFIX):
                continue
            if target.id in pins:
                raise ValueError(f"{target.id} is assigned more than once")
            try:
                pins[target.id] = ast.literal_eval(value)
            except ValueError as exc:
                raise ValueError(f"{target.id} is not a literal") from exc
    return pins


def is_review_path(path: str) -> bool:
    """I-11: the only path an addendum commit after a verdict may touch."""
    return path.startswith(REVIEW_ONLY_PREFIX)


def is_deployable(path: str) -> bool:
    return path in DEPLOYABLE_FILES or path.startswith(DEPLOYABLE_PREFIXES)


def parse_sntp_offset(output: str) -> Decimal | None:
    """The offset in seconds from ``sntp`` output (``+0.012345 +/- 0.023 time.apple.com ...``)."""
    match = SNTP_OFFSET_PATTERN.search(output)
    if match is None:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def in_deploy_window(now: datetime) -> bool:
    """I-4 on the ET wall clock: 18:30 inclusive to 07:20 exclusive, DST-aware."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    wall = now.astimezone(MARKET_TIMEZONE).time()
    return wall >= WINDOW_OPENS or wall < WINDOW_CLOSES


def system_sntp() -> str:
    """One ``sntp time.apple.com`` query (the only network use of the check)."""
    try:
        completed = subprocess.run(
            ["sntp", SNTP_SERVER],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=SNTP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClockUnavailable(f"sntp {SNTP_SERVER}: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout).strip().splitlines()
        raise ClockUnavailable(
            f"sntp {SNTP_SERVER} exited {completed.returncode}" + (f": {reason[-1]}" if reason else "")
        )
    return completed.stdout


def _short(sha: str | None) -> str:
    return sha[:12] if sha else "?"


def _limited(items: Sequence[str], limit: int = NOTE_LIMIT) -> list[str]:
    shown = list(items[:limit])
    if len(items) > limit:
        shown.append(f"... and {len(items) - limit} more")
    return shown


def _missing_candidate(token: str, candidate: str, repo: Path) -> CheckResult:
    return CheckResult(token, REFUSE, f"candidate {candidate!r} is not a commit in {repo}")


# --- checks -----------------------------------------------------------------------


def check_verdict(
    git: Git,
    *,
    candidate: str,
    head: str | None,
    ledger_glob: str,
    scope: Sequence[str],
    expect_migration: int | None = None,
) -> CheckResult:
    return match_verdict(
        git,
        candidate=candidate,
        head=head,
        ledger_glob=ledger_glob,
        scope=scope,
        expect_migration=expect_migration,
    )[0]


def match_verdict(
    git: Git,
    *,
    candidate: str,
    head: str | None,
    ledger_glob: str,
    scope: Sequence[str],
    expect_migration: int | None = None,
) -> tuple[CheckResult, tuple[VerdictLine, ...]]:
    """``check_verdict``'s result and, when it passes, the verdict lines it matched."""
    result, matched = _match_verdict(
        git, candidate=candidate, head=head, ledger_glob=ledger_glob, scope=scope
    )
    if result.status != OK or expect_migration is None:
        return result, matched
    wanted = str(expect_migration)
    missing = [line for line in matched if verdict_tokens(line.text).get("migration") != (wanted,)]
    if missing:
        return (
            CheckResult(
                VERDICT,
                REFUSE,
                f"{VERDICT_MIGRATION_MISSING}: --expect-migration {wanted} needs `migration={wanted}`"
                f" (and no other migration=) on every matched verdict line; {len(missing)} without it",
                tuple(line.describe() for line in missing[:3]),
            ),
            (),
        )
    return result, matched


def _match_verdict(
    git: Git, *, candidate: str, head: str | None, ledger_glob: str, scope: Sequence[str]
) -> tuple[CheckResult, tuple[VerdictLine, ...]]:
    if head is None:
        return _missing_candidate(VERDICT, candidate, git.repo), ()
    ledgers = sorted(
        path for path in git.tree_paths(head, static_prefix(ledger_glob)) if glob_matches(path, ledger_glob)
    )
    if not ledgers:
        return CheckResult(VERDICT, REFUSE, f"no ledger matches {ledger_glob} on {candidate}"), ()
    lines: list[VerdictLine] = []
    for path in ledgers:
        content = git.blob(head, path)
        if content is None:
            return CheckResult(VERDICT, REFUSE, f"cannot read {candidate}:{path}"), ()
        lines += verdict_lines(path, content.decode("utf-8", "replace"), scope)
    scope_text = f" scope={','.join(scope)}" if scope else ""
    searched = f"{len(ledgers)} ledger(s): {', '.join(ledgers)}"
    if not lines:
        return CheckResult(
            VERDICT,
            REFUSE,
            f"no `<sha> verdict=GO{scope_text}` line (ledger grammar"
            f" `<time> | [<label>] <sha> verdict=GO ... | ...`) in {searched}",
        ), ()
    by_sha: dict[str, list[VerdictLine]] = {}
    unresolved: list[VerdictLine] = []
    for line in lines:
        resolved = git.resolve(line.sha)
        if resolved is None:
            unresolved.append(line)
            continue
        by_sha.setdefault(resolved, []).append(line)
    if head in by_sha:
        return (
            CheckResult(VERDICT, OK, notes=tuple(line.describe() for line in by_sha[head])),
            tuple(by_sha[head]),
        )
    # I-11 exception: an ancestor verdict followed only by docs/reviews/ changes.
    qualifying: list[tuple[int, str, list[str]]] = []
    rejected: list[tuple[int, str, str]] = []
    for sha, named in by_sha.items():
        if not git.is_ancestor(sha, head):
            rejected.append(
                (sys.maxsize, sha, f"{named[0].describe()} -> {_short(sha)} is not an ancestor of {_short(head)}")
            )
            continue
        commits = git.commits_between(sha, head)
        outside = [path for path in git.changed_paths(sha, head) if not is_review_path(path)]
        if outside:
            sample = ", ".join(outside[:5]) + (", ..." if len(outside) > 5 else "")
            rejected.append(
                (
                    len(commits),
                    sha,
                    f"{named[0].describe()} -> {len(commits)} commit(s) since {_short(sha)} change"
                    f" {len(outside)} path(s) outside {REVIEW_ONLY_PREFIX}: {sample}",
                )
            )
            continue
        qualifying.append((len(commits), sha, commits))
    if qualifying:
        _count, sha, commits = min(qualifying, key=lambda item: item[0])
        notes = [line.describe() for line in by_sha[sha]]
        notes.append(
            f"head {_short(head)} = verdict {_short(sha)} + {len(commits)} commit(s)"
            f" touching only {REVIEW_ONLY_PREFIX}:"
        )
        notes += [f"+ {commit}" for commit in commits]
        return CheckResult(VERDICT, OK, notes=tuple(_limited(notes, NOTE_LIMIT * 2))), tuple(by_sha[sha])
    notes = [reason for _count, _sha, reason in sorted(rejected, key=lambda item: item[0])[:3]]
    notes += [f"{line.describe()} -> {line.sha} is not a commit in {git.repo}" for line in unresolved[:3]]
    return CheckResult(
        VERDICT,
        REFUSE,
        f"no GO verdict{scope_text} names {candidate} {_short(head)}, and none is followed only by"
        f" {REVIEW_ONLY_PREFIX} changes (I-11; {len(lines)} verdict line(s) in {searched})",
        tuple(notes),
    ), ()


def check_tree_clean(git: Git, *, benign_paths: Sequence[str]) -> CheckResult:
    entries = git.status_entries()
    benign = set(benign_paths)
    dirty = [entry for entry in entries if entry[3:] not in benign]
    allowed = [f"benign: {entry}" for entry in entries if entry[3:] in benign]
    if dirty:
        return CheckResult(
            TREE_CLEAN,
            REFUSE,
            f"{len(dirty)} status entr{'y' if len(dirty) == 1 else 'ies'} in {git.repo} outside the"
            f" benign list ({', '.join(benign_paths) or 'empty'})",
            tuple(_limited(dirty) + allowed),
        )
    return CheckResult(TREE_CLEAN, OK, notes=tuple(allowed))


def check_parent(
    git: Git, *, candidate: str, head: str | None, expect_parent: str | None
) -> CheckResult:
    current = git.resolve("HEAD")
    if current is None:
        return CheckResult(PARENT, REFUSE, f"{git.repo} has no HEAD commit")
    if expect_parent is not None:
        expected = git.resolve(expect_parent)
        if expected is None:
            return CheckResult(PARENT, REFUSE, f"--expect-parent {expect_parent} is not a commit in {git.repo}")
        if expected != current:
            return CheckResult(
                PARENT, REFUSE, f"HEAD is {_short(current)}, expected parent {expect_parent} ({_short(expected)})"
            )
    if head is None:
        return _missing_candidate(PARENT, candidate, git.repo)
    if current == head:
        # Run from the candidate's worktree with --repo pointing at itself, the
        # check compares the candidate with itself and proves nothing (review
        # P1-A F-8); after a deploy there is nothing left to fast-forward.
        return CheckResult(
            PARENT,
            REFUSE,
            f"HEAD of {git.repo} is the candidate {candidate} {_short(head)} itself: --repo must be"
            " the live checkout the candidate is deployed into (nothing to fast-forward)",
        )
    if not git.is_ancestor(current, head):
        return CheckResult(
            PARENT,
            REFUSE,
            f"HEAD {_short(current)} is not an ancestor of {candidate} {_short(head)}: no fast-forward",
        )
    ahead = len(git.commits_between(current, head))
    return CheckResult(
        PARENT, OK, notes=(f"HEAD {_short(current)} -> {candidate} {_short(head)}: {ahead} commit(s) ahead",)
    )


def check_files(git: Git, *, candidate: str, head: str | None) -> CheckResult:
    if head is None:
        return _missing_candidate(FILES, candidate, git.repo)
    paths = git.changed_paths("HEAD", head)
    tops = Counter(path.split("/", 1)[0] + ("/" if "/" in path else "") for path in paths)
    summary = f"{len(paths)} path(s) HEAD..{candidate}: " + (
        ", ".join(f"{top} {count}" for top, count in sorted(tops.items())) or "none"
    )
    outside = [path for path in paths if not is_deployable(path)]
    if outside:
        return CheckResult(
            FILES,
            WARN,
            f"{len(outside)} path(s) outside {', '.join(DEPLOYABLE_PREFIXES + DEPLOYABLE_FILES)}",
            tuple([summary, *_limited(outside)]),
        )
    return CheckResult(FILES, OK, notes=(summary,))


def check_schema(db_path: Path, *, now: datetime, expect_migration: int | None = None) -> CheckResult:
    from trading.operations.db_admin import run_status

    buffer = io.StringIO()
    exit_code = run_status(db_path, now=now, out=buffer)
    state = next(
        (line.split(None, 1)[1] for line in buffer.getvalue().splitlines() if line.startswith("state ")),
        "state unknown",
    )
    if expect_migration is not None:
        return _check_expected_migration(db_path, expect_migration, state)
    if exit_code == 0:
        return CheckResult(SCHEMA, OK, notes=(f"{db_path}: {state}",))
    return CheckResult(SCHEMA, REFUSE, f"{db_path}: {state} (never migrated here: `autotraderctl db migrate`)")


def _check_expected_migration(db_path: Path, expected: int, state: str) -> CheckResult:
    """P2-3: exactly ``(expected,)`` pending on an existing, sound database; still read-only."""
    from trading.storage.migrations import inspect_schema

    status = inspect_schema(db_path)
    if status.exists and not status.problems and status.pending == (expected,):
        return CheckResult(SCHEMA, OK, notes=(f"{db_path}: {state}; pending ({expected},) as expected",))
    pending = ", ".join(str(version) for version in status.pending) or "none"
    return CheckResult(
        SCHEMA,
        REFUSE,
        f"{MIGRATION_UNEXPECTED}: {db_path}: {state}; pending ({pending}) is not exactly"
        f" --expect-migration {expected}",
    )


def check_fence(db_path: Path) -> CheckResult:
    path = db_path.expanduser()
    if not path.is_file():
        return CheckResult(FENCE, REFUSE, f"no database at {path}")
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=FENCE_READ_TIMEOUT_SECONDS)) as db:
            row = db.execute(
                "SELECT stopped, reason, updated_at FROM autotrader_execution_fences WHERE name=?",
                (GLOBAL_FENCE,),
            ).fetchone()
            fenced = db.execute(
                "SELECT name, reason FROM autotrader_execution_fences"
                " WHERE name LIKE ? AND stopped=1 ORDER BY name",
                (f"{IDENTITY_FENCE_PREFIX}%",),
            ).fetchall()
    except sqlite3.Error as exc:
        return CheckResult(FENCE, REFUSE, f"fences unreadable at {path}: {type(exc).__name__}: {exc}")
    notes = tuple(
        f"identity {str(name)[len(IDENTITY_FENCE_PREFIX):]} STOPPED ({reason})" for name, reason in fenced
    )
    if row is not None and int(row[0]):
        return CheckResult(
            FENCE,
            REFUSE,
            f"the global PAPER kill switch is ACTIVE since {row[2]} ({row[1]}); it has no resume",
            notes,
        )
    return CheckResult(FENCE, OK, notes=("PAPER kill switch: INACTIVE", *notes))


def check_clock(*, skip: bool, sntp: SntpProbe) -> CheckResult:
    if skip:
        return CheckResult(CLOCK, WARN, "skipped (--skip-clock): offset not measured")
    try:
        output = sntp()
    except ClockUnavailable as exc:
        return CheckResult(CLOCK, REFUSE, f"{exc}; pass --skip-clock to deploy without a reading")
    offset = parse_sntp_offset(output)
    if offset is None:
        first = output.strip().splitlines()[:1]
        return CheckResult(
            CLOCK, REFUSE, f"no offset in sntp output {first[0] if first else '(empty)'!r}"
        )
    if abs(offset) > MAX_CLOCK_OFFSET_SECONDS:
        return CheckResult(
            CLOCK, REFUSE, f"clock offset {offset:+} s exceeds +/-{MAX_CLOCK_OFFSET_SECONDS} s ({SNTP_SERVER})"
        )
    return CheckResult(CLOCK, OK, notes=(f"offset {offset:+} s against {SNTP_SERVER}",))


def check_window(now: datetime) -> CheckResult:
    local = now.astimezone(MARKET_TIMEZONE)
    stamp = f"{local:%Y-%m-%d %H:%M} {local.tzname()}"
    if in_deploy_window(now):
        return CheckResult(
            WINDOW, OK, notes=(f"now {stamp}: inside {WINDOW_OPENS:%H:%M} -> {WINDOW_CLOSES:%H:%M} ET",)
        )
    return CheckResult(
        WINDOW,
        REFUSE,
        f"now {stamp} is outside the deploy window {WINDOW_OPENS:%H:%M} -> {WINDOW_CLOSES:%H:%M} ET"
        f" (I-4); it opens at {WINDOW_OPENS:%H:%M} ET",
    )


def check_pin(git: Git, *, candidate: str, head: str | None) -> CheckResult:
    if head is None:
        return _missing_candidate(PIN, candidate, git.repo)
    bridge = git.blob(head, BRIDGE_PATH)
    if bridge is None:
        return CheckResult(PIN, REFUSE, f"{candidate} has no {BRIDGE_PATH}")
    supervisor = git.blob(head, SUPERVISOR_PATH)
    if supervisor is None:
        return CheckResult(PIN, REFUSE, f"{candidate} has no {SUPERVISOR_PATH}")
    pins = PIN_PATTERN.findall(supervisor.decode("utf-8", "replace"))
    if len(pins) != 1:
        return CheckResult(
            PIN, REFUSE, f"expected one {PIN_NAME} literal in {candidate}:{SUPERVISOR_PATH}, found {len(pins)}"
        )
    pinned = pins[0].lower()
    actual = hashlib.sha256(bridge).hexdigest()
    if actual != pinned:
        return CheckResult(
            PIN,
            REFUSE,
            f"{candidate}:{BRIDGE_PATH} sha256 {actual[:12]} is not its {PIN_NAME} {pinned[:12]} (I-5)",
        )
    return CheckResult(PIN, OK, notes=(f"bridge sha256 {actual[:12]} == {PIN_NAME}",))


# --- the run ----------------------------------------------------------------------


def _guarded(token: str, check: Callable[[], CheckResult]) -> CheckResult:
    """A check that cannot decide refuses; it never crashes the others."""
    try:
        return check()
    except GitError as exc:
        return CheckResult(token, REFUSE, str(exc))
    except Exception as exc:  # noqa: BLE001 - fail closed with the reason
        return CheckResult(token, REFUSE, f"check failed: {type(exc).__name__}: {exc}")


def resolve_candidate(git: Git, candidate: str) -> str | None:
    try:
        return git.resolve(candidate)
    except GitError:
        return None


def run_checks(
    request: DeployCheckRequest,
    *,
    sntp: SntpProbe | None = None,
    git: Git | None = None,
    head: str | None = None,
) -> list[CheckResult]:
    """Every check, in order; ``head`` is the candidate's sha when already resolved."""
    now = request.now or datetime.now(UTC)
    repository = git or Git(request.repo)
    if head is None:
        head = resolve_candidate(repository, request.candidate)
    probe = sntp or system_sntp
    checks: list[tuple[str, Callable[[], CheckResult]]] = [
        (
            VERDICT,
            lambda: check_verdict(
                repository,
                candidate=request.candidate,
                head=head,
                ledger_glob=request.ledger_glob,
                scope=request.scope,
                expect_migration=request.expect_migration,
            ),
        ),
        (TREE_CLEAN, lambda: check_tree_clean(repository, benign_paths=request.benign_paths)),
        (
            PARENT,
            lambda: check_parent(
                repository, candidate=request.candidate, head=head, expect_parent=request.expect_parent
            ),
        ),
        (FILES, lambda: check_files(repository, candidate=request.candidate, head=head)),
        (SCHEMA, lambda: check_schema(request.db_path, now=now, expect_migration=request.expect_migration)),
        (FENCE, lambda: check_fence(request.db_path)),
        (CLOCK, lambda: check_clock(skip=request.skip_clock, sntp=probe)),
        (WINDOW, lambda: check_window(now)),
        (PIN, lambda: check_pin(repository, candidate=request.candidate, head=head)),
    ]
    return [_guarded(token, check) for token, check in checks]


def summary_line(results: Sequence[CheckResult]) -> str:
    counts = Counter(result.status for result in results)
    return f"deploy check: {counts[OK]} ok, {counts[WARN]} warn, {counts[REFUSE]} refused"


def exit_code(results: Sequence[CheckResult]) -> int:
    return EXIT_REFUSED if any(result.status == REFUSE for result in results) else EXIT_OK


def run_deploy_check(
    request: DeployCheckRequest,
    *,
    sntp: SntpProbe | None = None,
    out: TextIO | None = None,
) -> int:
    """``autotraderctl deploy check``: 0 when nothing refused, else 78."""
    stream = out or sys.stdout
    now = request.now or datetime.now(UTC)
    request = replace(request, now=now)
    git = Git(request.repo)
    head = resolve_candidate(git, request.candidate)
    local = now.astimezone(MARKET_TIMEZONE)
    print(
        f"deploy check: candidate {request.candidate} = {_short(head)} in {request.repo}"
        f" at {local:%Y-%m-%d %H:%M} {local.tzname()}"
        + (f" scope={','.join(request.scope)}" if request.scope else "")
        + (f" expect-migration={request.expect_migration}" if request.expect_migration is not None else ""),
        file=stream,
    )
    results = run_checks(request, sntp=sntp, git=git, head=head)
    for result in results:
        for line in result.lines():
            print(line, file=stream)
    print(summary_line(results), file=stream)
    return exit_code(results)


__all__ = [
    "BRIDGE_PATH",
    "CheckResult",
    "ClockUnavailable",
    "DEFAULT_BENIGN_PATHS",
    "DEFAULT_LEDGER_GLOB",
    "DeployCheckRequest",
    "EXIT_OK",
    "EXIT_REFUSED",
    "Git",
    "GitError",
    "MIGRATION_UNEXPECTED",
    "REVIEWED_PREFIX",
    "REVIEW_ONLY_PREFIX",
    "SUPERVISOR_PATH",
    "TOKENS",
    "VERDICT_MIGRATION_MISSING",
    "check_clock",
    "check_fence",
    "check_files",
    "check_parent",
    "check_pin",
    "check_schema",
    "check_tree_clean",
    "check_verdict",
    "check_window",
    "git_environment",
    "glob_matches",
    "in_deploy_window",
    "is_deployable",
    "is_review_path",
    "match_verdict",
    "parse_now",
    "parse_scope",
    "parse_sntp_offset",
    "resolve_candidate",
    "reviewed_pins",
    "run_checks",
    "run_deploy_check",
    "summary_line",
    "system_sntp",
    "validate_ref",
    "verdict_field",
    "verdict_label",
    "verdict_lines",
    "verdict_tokens",
]
