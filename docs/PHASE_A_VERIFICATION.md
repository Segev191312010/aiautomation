# Phase A Verification Manual

Date: 2026-07-10

Phase: A - Truth, Safety, and Product Consolidation

Source of truth:

- `docs/release-evidence/2026-07-phase-a-tracker.md`
- `docs/release-evidence/2026-07-phase-a-complete.md`
- Stage-specific evidence files under `docs/release-evidence/`

Mission: given a fresh checkout of `master`, re-prove every Phase A invariant
and catch regressions before Phase B starts.

This is an execution checklist, not a design note. Each stage includes the
commands to run, the files to inspect, the expected result, and what to do when
something fails.

The `PASS` values in the completion tracker are signed historical results from
2026-07-10. They are not automatic results for a later checkout. A verifier must
run this manual against one recorded commit and produce a new dated result.

## Resolved Regressions And Scope Boundary

The first execution of this checker on 2026-07-10 found and reopened two Phase
A safety regressions. Clean committed verification and same-source Ubuntu CI
confirmed the technical corrections. The dated report remains at A12 pending
renewed owner/lead sign-off for the material safety changes:

- A5/A6 replaced the stale read/check/unlink algorithm with a persistent,
  descriptor-held v2 lock: Windows uses a non-blocking byte-range lock and
  POSIX uses `flock(LOCK_EX | LOCK_NB)`. The implementation never unlinks an
  owned v2 path.
- A6 now covers synchronized contenders, real subprocess collision before app
  side effects, crash release, interrupted metadata, malformed JSON, metadata
  write/diagnostic failures, and lifespan failure cleanup.
- A8 now validates the persisted database mode before runtime services. An
  unsafe mode or a failed strict database read forces all autopilot authority
  to `OFF`.

The supported ownership invariant is exactly one v2 runtime per shared lock
path in one OS/filesystem lock namespace. Native defaults coordinate all native
clones for the same OS user. Compose stacks coordinate through the named
`tradebot-runtime-lock` volume. Native and container launches, different OS
users, distinct volumes, or distinct configured paths do not automatically
coordinate; this is not a machine-global mutex.

Rolling v1/v2 coexistence is unsupported. Stop every pre-v2 native and Compose
runtime before upgrading because old binaries use different default paths and
do not honor the v2 OS lock. The A5 recovery section gives the fail-closed
procedure for unknown or interrupted metadata.

## Contents

- [Run Protocol](#0-run-protocol)
- [Global Gates](#global-gate-commands)
- [A0 through A12](#a0---freeze-and-baseline)
- [Final Deterministic Sweeps](#final-deterministic-sweeps)
- [Re-verification Record](#re-verification-record-template)

## 0. Run Protocol

### Safety Boundary

- Use a fresh clone with no production `.env`, broker credentials, or live
  account configuration. A detached worktree requires a different source-prep
  flow because `master` may already be checked out elsewhere; the exact block
  below is intentionally for a fresh clone.
- Do not start Uvicorn, IB Gateway, Autopilot, or any order-capable process.
  This manual runs static checks and automated tests only.
- Never use `git reset --hard` to prepare a verification checkout. Abort on a
  dirty tree and preserve the developer's work.
- Run all commands from the repository root in one PowerShell session unless a
  section explicitly says otherwise.

### Prepare And Pin The Source

In a fresh clone, fast-forward `master`, prove it is clean, and record the exact
revision. The signed Phase A evidence commit must be an ancestor; current
`HEAD` is expected to be newer once this manual is committed.

```powershell
$verificationStartedUtc = (Get-Date).ToUniversalTime().ToString("o")
$transcript = Join-Path $env:TEMP "phase-a-verification-$((Get-Date).ToString('yyyyMMdd-HHmmss')).log"
$durableTranscript = $env:PHASE_A_TRANSCRIPT_PATH
if ([string]::IsNullOrWhiteSpace($durableTranscript)) {
    throw "Set PHASE_A_TRANSCRIPT_PATH to a durable team-controlled output file"
}
$durableParent = Split-Path -Parent $durableTranscript
if (-not (Test-Path -LiteralPath $durableParent -PathType Container)) {
    throw "Durable transcript directory does not exist: $durableParent"
}
Start-Transcript -Path $transcript -ErrorAction Stop

function Assert-NativeSuccess([string] $Label) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with native exit code $LASTEXITCODE"
    }
}

$initialDirty = @(git status --porcelain=v1 --untracked-files=all)
Assert-NativeSuccess "initial git status"
if ($initialDirty.Count -ne 0) {
    $initialDirty
    throw "Preserve local work and use a fresh clean clone"
}

git fetch --prune origin
Assert-NativeSuccess "git fetch"
git switch master
Assert-NativeSuccess "git switch master"
git pull --ff-only
Assert-NativeSuccess "git pull --ff-only"

$dirty = @(git status --porcelain=v1 --untracked-files=all)
Assert-NativeSuccess "git status"
if ($dirty.Count -ne 0) {
    $dirty
    throw "Verification requires a clean checkout"
}

$head = (git rev-parse HEAD).Trim()
Assert-NativeSuccess "resolve HEAD"
$originHead = (git rev-parse origin/master).Trim()
Assert-NativeSuccess "resolve origin/master"
if ($head -ne $originHead) {
    throw "HEAD ($head) does not equal origin/master ($originHead)"
}

git merge-base --is-ancestor e91be61ae89a107fa0fb20b6d12530202d8b6df7 HEAD
Assert-NativeSuccess "Phase A sign-off ancestry"
git status --short --branch
Assert-NativeSuccess "record branch status"
git show -s --format="%H %cI %s" HEAD
Assert-NativeSuccess "record source commit"
"commit=$head"
"started_utc=$verificationStartedUtc"
"transcript=$transcript"
"durable_transcript=$durableTranscript"
```

Expected:

```text
## master...origin/master
<full HEAD SHA> <commit timestamp> <commit subject>
```

There should be no modified, staged, or untracked files.

### Prepare And Record The Environment

Phase A CI uses Python 3.12 and Node 20. The product documentation supports
Python 3.11+ and Node 18+, but CI parity is the verification target. Dashboard
dependencies are npm-lockfile controlled; backend requirements use lower
bounds rather than an exact lock, so capture the resolved Python environment.

```powershell
python --version
Assert-NativeSuccess "Python version"
node --version
Assert-NativeSuccess "Node version"
npm --version
Assert-NativeSuccess "npm version"
rg --version | Select-Object -First 1
Assert-NativeSuccess "ripgrep version"
git --version
Assert-NativeSuccess "Git version"

if (-not $env:VIRTUAL_ENV) {
    python -m venv .venv
    Assert-NativeSuccess "create verification virtual environment"
    & .\.venv\Scripts\Activate.ps1
    if (-not $env:VIRTUAL_ENV) {
        throw "Verification virtual environment did not activate"
    }
}

Push-Location backend
try {
    python -m pip install -r requirements.txt
    Assert-NativeSuccess "backend dependency install"
    python -m pip freeze
    Assert-NativeSuccess "record resolved backend dependencies"
} finally {
    Pop-Location
}

Push-Location dashboard
try {
    npm ci
    Assert-NativeSuccess "dashboard npm ci"
    npm ls --depth=0
    Assert-NativeSuccess "record resolved dashboard dependencies"
} finally {
    Pop-Location
}
```

The transcript started before source preparation and now contains the exact
versions, dependency resolution, commit, and all subsequent command output.

Policy:

- If a test command fails, treat it as a Phase A regression.
- Do not defer a Phase A regression to Phase B.
- Fix the regression, update evidence only after the fix is real, then rerun
  the affected stage and all downstream/global checks from a new clean commit.
- Test counts may increase later, but a lower count or any failure needs an
  explanation and updated evidence.
- Never edit the 2026-07-09/10 evidence to make a new run look green. Create a
  new dated re-verification record and link its issue/fix commits.

## Global Gate Commands

Several stages refer to the full gate set. Run this fail-fast block from the
repository root. Every native exit code is checked before the next gate starts.

```powershell
Push-Location backend
try {
    python -m pytest tests/ -q
    Assert-NativeSuccess "backend pytest"
} finally {
    Pop-Location
}

Push-Location dashboard
try {
    npm run typecheck
    Assert-NativeSuccess "dashboard typecheck"
    npm run build
    Assert-NativeSuccess "dashboard build"
    npx vitest run
    Assert-NativeSuccess "dashboard Vitest"
} finally {
    Pop-Location
}

python scripts/check_workspace_hygiene.py
Assert-NativeSuccess "workspace hygiene"
```

Signed Phase A expectations and the latest dated re-verification comparison:

```text
signed backend pytest: 620 passed
2026-07-10 re-verification backend pytest: 640 passed
dashboard typecheck: PASS
dashboard build: PASS, Vite builds 610 modules
dashboard Vitest: 27 files passed, 372 tests passed
workspace hygiene: Workspace hygiene OK
```

All gates must exit `0`. The exact counts above are historical comparison
points, not permission to ignore a higher count. Any lower count must be
explained as a collection/scope change and treated as failed until reviewed.

## A0 - Freeze And Baseline

Goal: baseline evidence exists and remains clearly historical.

Files:

- `docs/release-evidence/2026-07-phase-a-baseline.md`

Steps:

1. Open `docs/release-evidence/2026-07-phase-a-baseline.md`.
2. Confirm it records the parent and nested repository commits at Phase A
   start.
3. Confirm it records the Phase A starting tree status, including the original
   generated `dashboard/dist` artifact state.
4. Confirm it records the baseline gate results from that date.
5. Verify the parent baseline commit still exists and is an ancestor of the
   verification target:

```powershell
git cat-file -e "68119ce63e3e5d257167a94ce0f2823e0b308170^{commit}"
Assert-NativeSuccess "A0 baseline commit exists"
git merge-base --is-ancestor 68119ce63e3e5d257167a94ce0f2823e0b308170 HEAD
Assert-NativeSuccess "A0 baseline is an ancestor"
```

6. Confirm the baseline's four tracked `dashboard/dist` paths are visible at
   that historical commit. Do not restore them into the working tree:

```powershell
$baselineDist = @(git ls-tree -r --name-only 68119ce63e3e5d257167a94ce0f2823e0b308170 -- dashboard/dist)
Assert-NativeSuccess "A0 historical dist listing"
if ($baselineDist.Count -ne 4) {
    $baselineDist
    throw "Expected four historically tracked dashboard/dist files"
}
$baselineDist
```

7. Run the global gate commands.

The nested dashboard's historical typecheck/build/11-test result cannot be
rerun from the canonical tree because A10 removed the nested working repo. A0
re-verification proves that the result is clearly historical; A10 separately
proves the archived commit/tag remains retrievable.

Pass criteria:

- Global gates pass.
- The baseline file is obviously a dated historical snapshot.
- The baseline file does not claim to describe the final Phase A source state.

If failed:

- If a gate fails, stop and log a Phase A regression at A0.
- If the baseline doc is ambiguous, add a clarification note such as:
  `Historical baseline as of 2026-07-09; see 2026-07-phase-a-complete.md for final state.`
- Do not rewrite the baseline as if it were a current-state document.

## A1 - Inventory Workspace Binaries

Goal: no unknown executable or binary artifacts are active in the source tree.

Files:

- `docs/release-evidence/2026-07-workspace-inventory.md`

Steps:

1. Open `docs/release-evidence/2026-07-workspace-inventory.md`.
2. Confirm it lists binary paths, sizes, hashes, signature status where
   applicable, and dispositions.
3. Run the tracked-file scan from the A1 evidence. Exit `1` from `rg` means
   correctly finding no matches; any output is a failure:

```powershell
$trackedFiles = @(git ls-files)
Assert-NativeSuccess "A1 tracked-file listing"
$trackedBinaryHits = @($trackedFiles | rg -i '\.(dll|exe|msi|zip|rar|7z|dmg|pkg)$')
$trackedSearchExit = $LASTEXITCODE
if ($trackedSearchExit -notin 0, 1) {
    throw "Tracked binary search failed with exit code $trackedSearchExit"
}
if ($trackedBinaryHits.Count -ne 0) {
    $trackedBinaryHits
    throw "Tracked forbidden binary artifacts found"
}
```

4. Run the policy checker. Unlike `rg --files`, it deliberately sees ignored
   root artifacts:

```powershell
python scripts/check_workspace_hygiene.py
Assert-NativeSuccess "A1 workspace hygiene"
```

5. Independently scan ignored and hidden files using the exact A1/A2 suffix
   policy. This also covers active directories not named in the Python
   checker's `SCAN_DIRS` while pruning dependency/cache/build trees:

```powershell
$binaryFindings = @(rg --files --hidden --no-ignore `
    --iglob '*.7z' --iglob '*.dll' --iglob '*.dmg' --iglob '*.exe' `
    --iglob '*.msi' --iglob '*.pkg' --iglob '*.rar' --iglob '*.zip' `
    --glob '!.git/**' --glob '!**/.git/**' `
    --glob '!**/.mypy_cache/**' --glob '!**/.pytest_cache/**' `
    --glob '!**/.ruff_cache/**' --glob '!**/.tmp/**' `
    --glob '!**/.venv/**' --glob '!**/__pycache__/**' `
    --glob '!**/coverage/**' --glob '!**/dist/**' `
    --glob '!**/node_modules/**')
$allFileSearchExit = $LASTEXITCODE
if ($allFileSearchExit -notin 0, 1) {
    throw "All-file binary search failed with exit code $allFileSearchExit"
}
if ($binaryFindings.Count -ne 0) {
    $binaryFindings
    throw "Forbidden binary artifacts found outside skipped dependency/build directories"
}
```

Expected:

- Both scans produce no findings.
- The hygiene script prints
  `Workspace hygiene OK: no forbidden binary artifacts found.`
- Dependency/cache/build directories listed above are out of policy scope; do
  not classify a hit as safe merely because Git ignores it.

Pass criteria:

- Every discovered binary is documented and safe, or absent because it was
  quarantined or removed.
- No new undocumented binary appears in active source paths.

If failed:

- Do not execute or delete an unexpected binary.
- Record its relative path, byte size, `Get-FileHash -Algorithm SHA256`, Git
  state, and `Get-AuthenticodeSignature` result on Windows.
- After owner review, quarantine it outside the repository and record the
  destination in a new dated evidence file.
- Update `scripts/check_workspace_hygiene.py` only when policy intentionally
  changes.
- Rerun the scan and hygiene script.

## A2 - Quarantine And Hygiene Policy

Goal: quarantine policy is documented and automatically enforced.

Files:

- `.gitignore`
- `docs/DEVELOPMENT.md`
- `scripts/check_workspace_hygiene.py`

Steps:

1. Open `.gitignore`.
2. Confirm it ignores the actual documented categories: `dashboard/dist/`,
   dependency folders, virtual environments, logs, secrets/local config,
   database files, and `.runtime/`. Do not require a general OS/editor rule that
   the current file does not define.
3. Open `docs/DEVELOPMENT.md`.
4. Confirm it states that local tools and binaries do not belong in the repo
   tree.
5. Confirm it states that the workspace hygiene script must pass before Phase A
   safety or release commits. A stronger handoff/merge rule requires a separate
   policy change.
6. Run:

```powershell
python scripts/check_workspace_hygiene.py
Assert-NativeSuccess "A2 workspace hygiene"
```

Expected:

```text
Workspace hygiene OK: no forbidden binary artifacts found.
```

Required negative probe using an isolated temporary root. Cleanup happens even
if an assertion fails, and the expected checker exit is explicitly tested:

```powershell
$probeRoot = Join-Path $env:TEMP "phase-a-hygiene-$([guid]::NewGuid().ToString('N'))"
New-Item -Path $probeRoot -ItemType Directory | Out-Null
try {
    New-Item -Path (Join-Path $probeRoot 'phase-a-hygiene-probe.dll') -ItemType File | Out-Null
    $probeOutput = @(python scripts/check_workspace_hygiene.py --root $probeRoot 2>&1)
    $probeExit = $LASTEXITCODE
    $probeOutput
    if ($probeExit -ne 1) {
        throw "Expected hygiene probe exit 1; got $probeExit"
    }
    if (($probeOutput -join "`n") -notmatch 'phase-a-hygiene-probe\.dll') {
        throw "Hygiene probe did not report the fake DLL"
    }
} finally {
    Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
}
```

Expected: exit `1`, a `Workspace hygiene failed` message, and the probe's
relative path. Then rerun the checker against the real repository and require
exit `0`.

```powershell
python scripts/check_workspace_hygiene.py
Assert-NativeSuccess "A2 post-probe workspace hygiene"
```

Pass criteria:

- Hygiene script passes on a clean workspace.
- Isolated fake-DLL probe fails as expected and is removed.
- `.gitignore`, `docs/DEVELOPMENT.md`, and the script agree on policy.

If failed:

- Fix the script or documentation to match the intended policy.
- Remove any temporary probe files.
- Rerun the hygiene script before proceeding.

## A3 - Inventory Backend Launch Paths

Goal: every supported backend startup path is documented.

Files:

- `docs/release-evidence/2026-07-launch-path-inventory.md`

Steps:

1. Run the launch-path search:

```powershell
$launchHits = @(rg -n -e "--workers" -e "WORKERS" -e "uvicorn main:app" -e "uvicorn.run" -e "gunicorn" Dockerfile backend/Dockerfile docker-compose.yml README.md docs/DEPLOYMENT.md sessions/phase2-paper-soak-runbook.md backend/main.py .github/workflows/ci.yml dashboard/nginx.conf)
Assert-NativeSuccess "A3 fixed-scope launch search"
$launchHits
```

2. Open `docs/release-evidence/2026-07-launch-path-inventory.md`.
3. For each search hit, confirm the inventory records:
   - path;
   - worker count;
   - host bind;
   - reload flag;
   - intended environment.
4. Search tracked executable/manifest formats for a new launch mechanism. This
   intentionally excludes Markdown evidence and tests so the checker does not
   match its own examples:

```powershell
$operationalLaunchHits = @(rg --hidden -n `
    -g '*.py' -g '*.ps1' -g '*.sh' -g '*.cmd' -g '*.bat' `
    -g 'Dockerfile*' -g 'Makefile' -g 'Procfile' -g '*.yml' -g '*.yaml' `
    -e 'uvicorn' -e 'gunicorn' -e 'fastapi run' -e 'main:app' . `
    --glob '!.git/**' --glob '!backend/tests/**' `
    --glob '!dashboard/node_modules/**' --glob '!dashboard/dist/**')
Assert-NativeSuccess "A3 broad operational launch search"
$operationalLaunchHits
```

Classify every broad-search hit as one of: supported launch path, import/call
inside the supported entry point, logging-only reference, or false positive.
The supported paths remain the two Dockerfiles, compose through those images,
`README.md`, `backend/main.py`, the paper-soak runbook, and the two deployment
examples. A newly supported wrapper is a failure until the inventory and launch
regression tests include it.

Pass criteria:

- Search hits map to documented launch paths.
- No hidden or undocumented backend startup path remains.
- Any new launch path added after Phase A is inventoried before it is used.

If failed:

- Add missing launch paths to the inventory.
- If a path starts more than one worker, fix it before proceeding to A4/A5.
- If a path binds unexpectedly to `0.0.0.0`, document why or change it.

## A4 - Force One-Worker Runtime

Goal: all supported backend launch paths start exactly one backend worker.

Files:

- `Dockerfile`
- `backend/Dockerfile`
- `docker-compose.yml`
- `README.md`
- `docs/DEPLOYMENT.md`
- `sessions/phase2-paper-soak-runbook.md`
- `backend/main.py`
- `backend/startup.py`
- `backend/tests/test_launch_manifests.py`

Steps:

1. Inspect `Dockerfile` and `backend/Dockerfile`.
2. Confirm each backend launch command pins one worker.
3. Inspect `docker-compose.yml`.
4. Confirm it does not expose worker-count overrides such as `WORKERS`,
   `WEB_CONCURRENCY`, or `UVICORN_WORKERS`.
5. Inspect `README.md` and `docs/DEPLOYMENT.md`.
6. Confirm they do not recommend `--workers 2`, `--workers 4`, or any other
   multi-worker backend command.
7. Inspect `sessions/phase2-paper-soak-runbook.md` and `backend/main.py`.
8. Confirm both use Uvicorn's single-worker default and neither introduces a
   worker-count override.
9. Inspect `backend/startup.py` and confirm startup logging still reports the
   single-process policy.
10. Run:

```powershell
Push-Location backend
try {
    python -m pytest tests/test_launch_manifests.py -q
    Assert-NativeSuccess "A4 launch-manifest tests"
} finally {
    Pop-Location
}
```

Expected:

```text
4 passed
```

Pass criteria:

- Docker, compose, docs, and startup policy enforce one worker.
- Launch-manifest regression tests pass.

If failed:

- Fix every multi-worker manifest, environment variable, or doc command.
- Update tests only to make them stricter or to reflect an intentional Phase A
  policy change.
- Rerun `tests/test_launch_manifests.py`.

## A5 - Runtime Process Lock

Goal: exactly one v2 backend runtime owns each supported shared lock scope, and
the lock is held before any stateful startup side effect.

Scope warning: the lock is authoritative only among processes that open the
same underlying local file in the same OS/filesystem lock namespace. Do not
describe it as a machine-global mutex.

Files:

- `Dockerfile`
- `backend/Dockerfile`
- `backend/config.py`
- `backend/main.py`
- `backend/runtime_lock.py`
- `backend/tests/test_launch_manifests.py`
- `docker-compose.yml`
- `docs/release-evidence/2026-07-a5-a6-runtime-lock.md`
- `docs/release-evidence/2026-07-10-phase-a-reverification.md`

Steps:

1. Inspect `backend/runtime_lock.py`.
2. Confirm v2 ownership comes from a retained OS lock, not JSON/PID state:
   - the file is opened read/write and the descriptor is non-inheritable;
   - Windows locks one reserved byte with `msvcrt.LK_NBLCK`;
   - POSIX locks the descriptor with `flock(LOCK_EX | LOCK_NB)`;
   - the descriptor remains open for the complete FastAPI lifespan;
   - release unlocks and closes in unconditional cleanup;
   - the implementation never unlinks or replaces an owned v2 path.
3. Confirm persistent JSON metadata includes `lock_version: 2`, state, PID,
   hostname, start time, executable, working directory, mode, and token.
   Metadata is operator diagnostics; it does not grant v2 ownership.
4. Confirm a valid abandoned v2 record is overwritten only after the caller
   acquires the OS lock. A normal crash therefore releases ownership through
   the operating system even though the metadata file remains.
5. Confirm the v1 bridge fails closed for a live legacy PID, migrates a fully
   written dead legacy record, and refuses unknown or partially written pre-v2
   metadata without overwriting it.
6. Inspect `backend/main.py`.
7. Confirm `lifespan()` acquires the lock before `_run_lifespan()` and therefore
   before startup validation, DB initialization, simulation, IBKR, alerts,
   heartbeats, reconciliation, runtime mutation, or AI loops.
8. Confirm release happens in `finally`, including when startup raises.
9. Inspect the supported scope mapping:

| Launch surface | Shared scope | Does not coordinate with |
| --- | --- | --- |
| Native Windows | `%LOCALAPPDATA%\TradeBot\runtime\tradebot-runtime.lock` | other OS users or containers |
| Native POSIX without `/data` | XDG state/runtime or `~/.local/state/TradeBot/runtime/` | other OS users or containers |
| Docker Compose | named volume `tradebot-runtime-lock` at `/runtime` | native launches or another volume |
| Bare container | `/data/tradebot-runtime.lock` when `/data` exists | containers that do not share `/data` |
| Explicit override | resolved `RUNTIME_LOCK_PATH` | processes using another path/filesystem |

10. Inspect both Dockerfiles and `docker-compose.yml`.
11. Confirm Compose sets `/runtime/tradebot-runtime.lock`, uses the stable named
    volume, and both images create and `chown` `/runtime` before `USER appuser`.
12. Enforce the upgrade boundary: stop all pre-v2 native processes and old
    Compose stacks before the first v2 start. Rolling coexistence is unsafe
    because v1 used different defaults, ignores the OS lock, and can unlink a
    locked inode on POSIX.
13. If startup reports unknown or incomplete metadata:
    - do not delete the file while any backend may be active;
    - stop all native TradeBot/Uvicorn processes and Compose stacks;
    - verify the process/container inventory is empty;
    - preserve the metadata for diagnosis if needed;
    - only then remove the affected native lock or the dedicated Compose lock
      volume and restart one v2 runtime.

Useful Windows inventory commands for this exceptional recovery path:

```powershell
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'uvicorn.+main:app|python.+main\.py' } |
    Select-Object ProcessId, ExecutablePath, CommandLine

docker compose ps --all
docker compose ls --all
docker ps --all --format '{{.ID}} {{.Names}} {{.Image}} {{.Command}}' |
    Select-String -Pattern 'tradebot|uvicorn|main:app'

$nativeLock = Join-Path $env:LOCALAPPDATA 'TradeBot\runtime\tradebot-runtime.lock'
if (Test-Path -LiteralPath $nativeLock) {
    Get-Content -LiteralPath $nativeLock
}
```

`docker` may be absent on a native-only verifier. Do not interpret that as an
empty container inventory on a deployment host. The deletion step is
intentionally not automated by this checker.

14. Compile the lock and startup modules:

```powershell
Push-Location backend
try {
    python -m py_compile runtime_lock.py main.py
    Assert-NativeSuccess "A5 compile check"
} finally {
    Pop-Location
}
```

Expected: no output and exit code `0`.

15. Run the launch-manifest tests:

```powershell
Push-Location backend
try {
    python -m pytest tests/test_launch_manifests.py -q
    Assert-NativeSuccess "A5 launch-manifest lock scope"
} finally {
    Pop-Location
}
```

Expected: `4 passed` for the 2026-07-10 re-verification source.

16. Treat `docs/release-evidence/2026-07-a5-a6-runtime-lock.md` as signed
    v1-era evidence. Its supersession note must point to the dated v2 report.

Pass criteria:

- One and only one contender can hold the same supported v2 scope.
- Ownership is OS-held and the persistent path is never unlinked by v2.
- Acquisition is before every side effect and release is exception-safe.
- Native/Compose scope and the stop-all-v1 boundary are explicit.
- Interrupted or unknown metadata fails closed with verified operator cleanup.
- Compile and launch-manifest tests pass.

If failed:

- Stop at A5. Do not weaken fail-closed behavior or restore PID/unlink as v2
  ownership authority.
- Capture the lock path, process IDs, platform, logs, and metadata.
- Define and test the shared underlying path before supporting a new launcher.

## A6 - Lock Tests And Failure UX

Goal: ownership, duplicate refusal, crash/release behavior, migration failure
UX, and the pre-side-effect application boundary are executable regressions.

Files:

- `backend/tests/test_runtime_lock.py`
- `backend/tests/test_startup_runtime_lock.py`
- `docs/release-evidence/2026-07-10-phase-a-reverification.md`

Steps:

1. Run unit and real-process tests with warnings promoted to errors:

```powershell
Push-Location backend
try {
    python -m pytest tests/test_runtime_lock.py -q -W error
    Assert-NativeSuccess "A6 runtime-lock tests"
} finally {
    Pop-Location
}
```

Expected:

```text
22 passed
```

2. Run startup/lifespan tests:

```powershell
Push-Location backend
try {
    python -m pytest tests/test_startup_runtime_lock.py -q -W error
    Assert-NativeSuccess "A6 startup runtime-lock tests"
} finally {
    Pop-Location
}
```

Expected:

```text
4 passed
```

3. Inspect `backend/tests/test_runtime_lock.py` and require coverage for:
   - stable native default across working directories;
   - readable owned metadata and persistent released metadata;
   - clear owner/path details on duplicate refusal;
   - v1 dead-record migration and live-record refusal;
   - v2 PID diagnostics never overriding OS authority;
   - unknown, non-object, partial-v1, and interrupted-v2 metadata failing
     closed with actionable recovery text;
   - a held OS lock refusing a contender even when metadata is corrupt;
   - two barrier-synchronized stale contenders producing exactly one winner;
   - metadata-write and diagnostic exceptions releasing the OS lock;
   - a real holder blocking an actual FastAPI contender before its lifespan
     side-effect marker is written;
   - abrupt process death followed by successful reacquisition;
   - configured relative-path resolution.
4. Inspect `backend/tests/test_startup_runtime_lock.py` and require first-start,
   duplicate-before-body, orderly-release, and startup-exception coverage.
5. Treat `ResourceWarning`, leaked pipes/handles, hung helpers, or timing-only
   assertions as test failures.
6. Windows-local execution proves `msvcrt`; require the same commit's Ubuntu CI
   backend job to pass before claiming POSIX `flock` execution evidence.

Pass criteria:

- Both commands pass at or above the recorded counts.
- The synchronized race has exactly one owner.
- The real app contender never enters `_run_lifespan`.
- Crash, error, and release paths leave no held descriptor.
- Windows-local and Ubuntu-CI evidence refer to the same commit.

If failed:

- Preserve the lock file and process output for diagnosis.
- Never relax the one-owner assertion to hide a race.
- Rerun A5, both A6 modules, the full suite, and Ubuntu CI.

## A7 - Replace Retired Anthropic Defaults

Goal: no retired Anthropic model is used as a runtime default.

Files:

- `backend/config.py`
- `backend/ai_advisor.py`
- `backend/ai_learning.py`
- `backend/ai_model_router.py`
- `backend/tests/test_startup_config.py`
- `backend/tests/test_ai_learning.py`
- `backend/tests/test_ai_replay.py`
- `backend/ARCHITECTURE.md`
- `docs/APPLICATION_READINESS_ROADMAP.md`
- `docs/release-evidence/2026-07-a7-anthropic-defaults.md`

Steps:

1. Run targeted tests:

```powershell
Push-Location backend
try {
    python -m pytest tests/test_startup_config.py tests/test_ai_learning.py tests/test_ai_replay.py -q
    Assert-NativeSuccess "A7 targeted backend tests"
} finally {
    Pop-Location
}
```

Expected:

```text
signed A7 evidence: 33 passed
2026-07-10 re-verification: 41 passed
```

The higher current count is expected because later Phase A work added tests to
one of the same modules. Any failure or count below the signed `33` is a
regression; record the actual count.

2. Search for retired IDs outside their intentional lifecycle-registry and
   negative-fixture locations:

```powershell
$retiredIdHits = @(rg -n "claude-sonnet-4-20250514|claude-3-5-sonnet-20240620|claude-3-haiku-20240307" `
    backend dashboard/src `
    --glob "!backend/ai_capability.py" `
    --glob "!backend/tests/**" `
    --glob "!dashboard/src/**/__tests__/**")
$retiredSearchExit = $LASTEXITCODE
if ($retiredSearchExit -notin 0, 1) {
    throw "A7 retired-ID search failed with exit code $retiredSearchExit"
}
if ($retiredIdHits.Count -ne 0) {
    $retiredIdHits
    throw "Retired model ID found outside the approved lifecycle/test allowlist"
}
```

Expected:

- No output.
- Retired IDs are intentionally allowed in `backend/ai_capability.py` so the
  runtime can reject them, in negative tests, and in dated historical evidence.

3. Inspect `backend/config.py`.
4. Confirm `DEFAULT_AI_PRIMARY_MODEL` and `DEFAULT_AI_FALLBACK_MODEL` use
   current intended model IDs.
5. Inspect `backend/ARCHITECTURE.md` and
   `docs/APPLICATION_READINESS_ROADMAP.md`.
6. Confirm current-model descriptions match A7 evidence.
7. Recheck the two official provider sources linked in
   `docs/release-evidence/2026-07-a7-anthropic-defaults.md`. Model lifecycle is
   time-sensitive. If either configured default is no longer active or is
   inside the repository's 30-day retirement block window, fail A7 even when
   the historical tests remain green.

Pass criteria:

- Targeted tests pass.
- No retired IDs are active runtime defaults.
- Runtime config, tests, and docs agree.

If failed:

- Update defaults and tests together.
- Update architecture/evidence docs after the runtime behavior is correct.
- Re-run the A7 targeted tests and retired-ID search.

## A8 - AI Capability Validation

Goal: AI capability is explicit and enforced at startup, status payloads, and
mode transitions.

Files:

- `backend/ai_capability.py`
- `backend/startup.py`
- `backend/ai_guardrails.py`
- `backend/autopilot_api.py`
- `backend/api_contracts.py`
- `backend/tests/test_ai_capability.py`
- `backend/tests/test_startup_config.py`
- `backend/tests/test_api_contracts.py`
- `backend/tests/test_autopilot_mode_semantics.py`
- `dashboard/src/types/advisor.ts`
- `dashboard/src/components/autopilot/__tests__/autopilot.test.tsx`
- `docs/release-evidence/2026-07-a8-ai-capability.md`

Steps:

1. Run A8 backend tests:

```powershell
Push-Location backend
try {
    python -m pytest tests/test_ai_capability.py tests/test_startup_config.py tests/test_api_contracts.py tests/test_autopilot_mode_semantics.py -q
    Assert-NativeSuccess "A8 targeted backend tests"
} finally {
    Pop-Location
}
```

Expected:

```text
signed A8 evidence: 67 passed
2026-07-10 re-verification: 73 passed
```

2. Run dashboard typecheck:

```powershell
Push-Location dashboard
try {
    npm run typecheck
    Assert-NativeSuccess "A8 dashboard typecheck"
    npx vitest run src/components/autopilot/__tests__/autopilot.test.tsx
    Assert-NativeSuccess "A8 Autopilot contract/UI test"
} finally {
    Pop-Location
}
```

Expected: typecheck exit `0`; current targeted Vitest result is one file and
`14 passed`.

3. Inspect `backend/ai_capability.py`.
4. Confirm `AICapabilityState` contains:
   - `disabled`;
   - `unconfigured`;
   - `invalid_model`;
   - `ready`;
   - `degraded`.
5. Confirm `MODEL_LIFECYCLE` lists active, deprecated, and retired Anthropic
   IDs with retirement dates where applicable.
6. Confirm `resolve_ai_capability()` behavior:
   - AI off returns `disabled` and lifecycle issues are warnings.
   - missing key in paper/live returns `unconfigured`;
   - lifecycle errors return `invalid_model`;
   - fallback disabled or breaker tripped returns `degraded`;
   - healthy config returns `ready`.
7. Inspect `backend/startup.py` and `backend/main.py`.
8. Confirm environment PAPER/LIVE is checked before DB initialization and is
   forced `OFF` if matrix or capability checks fail, regardless of whether
   `STRICT_CONFIG` converts validation findings into a process exit.
9. Confirm `_run_lifespan()` performs a strict persisted guardrail read after
   DB initialization but before simulation, IBKR, heartbeats, alerts, or AI
   loops. Missing capability, a retired model, an invalid persisted mode, or a
   DB/JSON/Pydantic read failure must force every authority flag to `OFF`.
10. Inspect `backend/ai_guardrails.py` and `backend/autopilot_api.py`.
11. Confirm status payloads expose capability, provider configured status,
    primary/fallback model IDs, errors, and warnings.
12. Inspect `backend/api_contracts.py`.
13. Confirm `AIStatusResponse` includes the new fields.
14. Inspect `dashboard/src/types/advisor.ts` and
    `dashboard/src/components/autopilot/__tests__/autopilot.test.tsx`.
15. Confirm frontend types and tests match the status contract.
16. Open `docs/release-evidence/2026-07-a8-ai-capability.md`.
17. Confirm it records all five state semantics and the tested status contract.
    The evidence does not contain a literal JSON example, so do not require one
    unless the evidence is intentionally extended.

Pass criteria:

- A8 backend tests pass.
- Dashboard typecheck passes.
- Capability helper, startup wiring, status contract, and frontend types agree.
- Environment and persisted mode changes fail closed before side effects.
- A persisted-state read failure leaves mode `OFF`, autonomy false, and shadow
  mode true.

If failed:

- Fix implementation and tests to match the capability contract.
- Update evidence only after tests prove the corrected behavior.

## A9 - Canonical Product Surface

Goal: only one backend and one dashboard are active.

Files:

- `docs/adr/0006-canonical-product-surface.md`
- `docs/release-evidence/2026-07-a9-canonical-product-decision.md`
- `docs/APPLICATION_READINESS_ROADMAP.md`
- `docs/release-evidence/2026-07-phase-a-tracker.md`

Steps:

1. Check workspace shape:

```powershell
if (Test-Path -LiteralPath aiautomation) { throw "A9: aiautomation/ is present" }
if (Test-Path -LiteralPath frontend) { throw "A9: frontend/ is present" }
$trackedDist = @(git ls-files dashboard/dist)
Assert-NativeSuccess "A9 tracked dist query"
if ($trackedDist.Count -ne 0) {
    $trackedDist
    throw "A9: dashboard/dist contains tracked files"
}
"aiautomation/ absent (OK)"
"frontend/ absent (OK)"
"dashboard/dist tracked files: 0 (OK)"
```

Expected:

```text
aiautomation/ absent (OK)
frontend/ absent (OK)
```

`git ls-files dashboard/dist` should print nothing.

2. Open `docs/adr/0006-canonical-product-surface.md`.
3. Confirm it declares `backend/` and `dashboard/` as canonical.
4. Confirm it assigns nested archive and legacy removal to A10. The ADR is a
   decision made before cleanup and should not be rewritten to pretend those
   actions had already happened.
5. Open `docs/release-evidence/2026-07-a9-canonical-product-decision.md`.
6. Confirm it agrees with the ADR.
7. Open `docs/APPLICATION_READINESS_ROADMAP.md`.
8. Confirm Phase A status matches the canonical surface decision.

Pass criteria:

- Only canonical active surfaces exist in the tree.
- ADR, evidence, and roadmap agree.
- `dashboard/dist` has no tracked files.

If failed:

- Remove reintroduced duplicate product surfaces.
- If a product-surface decision intentionally changed, create a new ADR rather
  than silently editing the old one.

## A10 - Migrate Keepers And Remove Duplicates

Goal: keeper features from the nested dashboard are migrated and duplicate
surfaces are removed.

Files:

- `dashboard/src/components/autopilot/AISystemPanel.tsx`
- `dashboard/src/pages/AutopilotPage.tsx`
- `dashboard/src/components/autopilot/__tests__/autopilot.test.tsx`
- `backend/main.py`
- `backend/tests/test_product_surface.py`
- `.github/workflows/ci.yml`
- `DOCUMENTATION.md`
- `docs/DEPLOYMENT.md`
- `docs/APPLICATION_READINESS_ROADMAP.md`
- `docs/release-evidence/2026-07-a10-product-migration.md`
- removed `frontend/trading.*`

Steps:

1. Verify the archive tag directly on the remote. The tag points into the
   former nested history and is not present in an ordinary parent checkout:

```powershell
$archiveRef = 'refs/tags/archive/aiautomation-v2-2026-07-a10'
$archiveExpected = '16280057ab04bee97904e9c59b9a5143a58bb673'
$archiveResult = @(git ls-remote --exit-code --tags origin $archiveRef)
Assert-NativeSuccess "A10 remote archive-tag lookup"
if ($archiveResult.Count -ne 1) {
    $archiveResult
    throw "A10: expected exactly one remote archive tag result"
}
$archiveActual = ($archiveResult[0] -split '\s+')[0]
if ($archiveActual -ne $archiveExpected) {
    throw "A10: archive tag is $archiveActual; expected $archiveExpected"
}
$archiveResult
```

Expected: the tag resolves to commit
`16280057ab04bee97904e9c59b9a5143a58bb673`.

2. Open `dashboard/src/components/autopilot/AISystemPanel.tsx`.
3. Open `dashboard/src/pages/AutopilotPage.tsx`.
4. Confirm `AutopilotPage.tsx` imports/renders `AISystemPanel` and exposes the
   `System` tab; confirm the panel consumes canonical Autopilot status/audit,
   learning, and economics data instead of nested-repo services.
5. Open `backend/tests/test_product_surface.py`.
6. Confirm it asserts a single canonical product surface.
7. Run:

```powershell
Push-Location backend
try {
    python -m pytest tests/test_product_surface.py -q
    Assert-NativeSuccess "A10 product-surface backend tests"
} finally {
    Pop-Location
}

Push-Location dashboard
try {
    npx vitest run src/components/autopilot/__tests__/autopilot.test.tsx
    Assert-NativeSuccess "A10 Autopilot targeted tests"
    npm run typecheck
    Assert-NativeSuccess "A10 dashboard typecheck"
} finally {
    Pop-Location
}
```

Expected: backend `2 passed`; dashboard one test file and `14 passed`;
typecheck exit `0`.

8. Inspect `.github/workflows/ci.yml`, `DOCUMENTATION.md`,
   `docs/DEPLOYMENT.md`, and `docs/APPLICATION_READINESS_ROADMAP.md`.
9. Confirm they no longer point users or CI at removed `frontend/` paths or the
   removed nested repo.
10. Open `docs/release-evidence/2026-07-a10-product-migration.md`.
11. Confirm it includes the keeper matrix and duplicate-surface removal notes.
12. Search active backend code for reintroduced legacy serving:

```powershell
$legacySurfaceHits = @(rg -n 'serve_legacy_frontend|StaticFiles|frontend/trading|["'']/trading["'']' backend `
    --glob '!backend/tests/**')
$legacySearchExit = $LASTEXITCODE
if ($legacySearchExit -notin 0, 1) {
    throw "A10 legacy-surface search failed with exit code $legacySearchExit"
}
if ($legacySurfaceHits.Count -ne 0) {
    $legacySurfaceHits
    throw "A10: legacy frontend serving was reintroduced"
}
```

13. Run the global gate commands.

Pass criteria:

- Archive tag exists and points to the expected nested repo commit.
- AI system keeper features exist in the canonical dashboard.
- `test_product_surface.py` passes.
- Docs and CI reference only the canonical backend/dashboard stack.
- Global gates pass.

If failed:

- Re-migrate missing keeper features.
- Fix stale CI/docs references.
- Rerun targeted and global gates.

## A11 - Generated Artifacts And Doc Truth

Goal: generated dashboard artifacts are untracked and top-level docs match the
actual product shape.

Files:

- `README.md`
- `DOCUMENTATION.md`
- `docs/baseline.md`
- `docs/APPLICATION_READINESS_ROADMAP.md`
- `docs/release-evidence/2026-07-a11-artifacts-doc-truth.md`
- removed tracked `dashboard/dist/assets/*`

Steps:

1. Confirm no generated dashboard output is tracked:

```powershell
$trackedDist = @(git ls-files dashboard/dist)
Assert-NativeSuccess "A11 tracked dist query"
if ($trackedDist.Count -ne 0) {
    $trackedDist
    throw "A11: dashboard/dist contains tracked files"
}
git check-ignore dashboard/dist/index.html | Out-Null
Assert-NativeSuccess "A11 dashboard/dist ignore rule"
```

Expected: no output.

2. Confirm a dashboard build does not create tracked changes:

```powershell
$preBuildTracked = @(git status --porcelain=v1 --untracked-files=no)
Assert-NativeSuccess "A11 pre-build status"
if ($preBuildTracked.Count -ne 0) {
    $preBuildTracked
    throw "A11 build check requires a clean tracked tree"
}
Push-Location dashboard
try {
    npm run build
    Assert-NativeSuccess "A11 dashboard build"
} finally {
    Pop-Location
}
$distChanges = @(git status --porcelain=v1 --untracked-files=no -- dashboard/dist)
Assert-NativeSuccess "A11 post-build dist status"
if ($distChanges.Count -ne 0) {
    $distChanges
    throw "A11: dashboard build changed tracked dist files"
}
```

Expected:

- no tracked changes from `dashboard/dist`;
- ignored local build output may exist, but it must not appear as tracked work.

3. Inspect `README.md`.
4. Confirm it describes current product status and entrypoints.
5. Inspect `DOCUMENTATION.md` and `docs/baseline.md`.
6. Confirm test counts are either current or clearly dated.
7. Inspect `docs/APPLICATION_READINESS_ROADMAP.md`.
8. Confirm pre-Phase-A findings about multi-worker defaults and the unsigned
   DLL are explicitly labeled historical/resolved, rather than written in the
   present tense beside a completed Phase A checklist.
9. Inspect `docs/release-evidence/2026-07-a11-artifacts-doc-truth.md`.
10. Confirm it describes the artifact cleanup and documentation truth pass.
11. Run the evidence's stale operational-path search:

```powershell
$stalePathHits = @(rg -n 'cd frontend|COPY frontend|frontend/package|frontend-build|serve_legacy_frontend|StaticFiles' `
    DOCUMENTATION.md docs README.md .github backend dashboard `
    --glob '!docs/release-evidence/2026-07-a11-artifacts-doc-truth.md' `
    --glob '!docs/PHASE_A_VERIFICATION.md' `
    --glob '!backend/tests/**' `
    --glob '!dashboard/node_modules/**' --glob '!dashboard/dist/**')
$stalePathExit = $LASTEXITCODE
if ($stalePathExit -notin 0, 1) {
    throw "A11 stale-path search failed with exit code $stalePathExit"
}
if ($stalePathHits.Count -ne 0) {
    $stalePathHits
    throw "A11: stale legacy operational path found"
}
```

12. Run the global gate commands. A11 is not re-verified by the build alone.

Pass criteria:

- `dashboard/dist` has no tracked files.
- Dashboard build does not dirty tracked files.
- High-level docs do not claim removed product surfaces still exist.
- Docs do not present aspirational Phase B work as current Phase A reality.

If failed:

- Remove generated artifacts from Git.
- Fix stale docs with current facts.
- Rerun build, status, and the relevant doc checks.

## A12 - Final Regression And Evidence Closeout

Goal: Phase A has a dated, signed, reproducible closeout.

Files:

- `docs/release-evidence/2026-07-phase-a-complete.md`
- `docs/release-evidence/2026-07-phase-a-tracker.md`
- `docs/release-evidence/2026-07-10-phase-a-reverification.md`

Steps:

1. Open `docs/release-evidence/2026-07-phase-a-complete.md`.
2. Confirm it remains a historical statement that records:
   - status `COMPLETE`;
   - owner/lead sign-off;
   - branch `master`;
   - tested source commit `7ed8f962a647c7afa1bd663c24f4086a2f759818`;
   - clean/synced source state at closeout;
   - final gate results;
   - archive tag for the nested dashboard;
   - workspace shape;
   - Phase B deferrals;
   - final done checklist.
3. Open the dated re-verification report.
4. Confirm it records a full tested-source commit, Windows environment, local
   gate outputs, A0-A12 results, lock scope, v1 stop boundary, and that tested
   source's Ubuntu CI result. The later evidence commit contains the report and
   need not embed its own hash; `HEAD`/`origin/master` identify it at run time.
5. Open `docs/release-evidence/2026-07-phase-a-tracker.md` and confirm its latest
   audit points to the new report while the signed historical table is intact.
6. Confirm A0 through A11 are `PASS` in the new report. A12 may become `PASS`
   only after renewed owner/lead acceptance is recorded; while it says
   `PENDING SIGN-OFF`, technical verification is complete but Phase A is not
   administratively re-signed.
7. Run the global gate commands.
8. Run the final shape checks:

```powershell
$testedSource = '<copy the 40-character tested-source commit from the report>'
if ($testedSource -notmatch '^[0-9a-f]{40}$') {
    throw "A12: tested-source commit must be a full lowercase SHA"
}
git cat-file -e "$testedSource^{commit}"
Assert-NativeSuccess "A12 tested source commit exists"
git merge-base --is-ancestor $testedSource HEAD
Assert-NativeSuccess "A12 tested source ancestry"

if (Test-Path -LiteralPath aiautomation) { throw "A12: aiautomation/ is present" }
if (Test-Path -LiteralPath frontend) { throw "A12: frontend/ is present" }
"aiautomation/ absent (OK)"
"frontend/ absent (OK)"
$trackedDist = @(git ls-files dashboard/dist)
Assert-NativeSuccess "A12 tracked dist query"
if ($trackedDist.Count -ne 0) {
    $trackedDist
    throw "A12: dashboard/dist contains tracked files"
}

$pendingSignoff = @(rg -ni 're-sign-off\s+pending|sign-off\s+pending|owner/lead.*pending' `
    docs/release-evidence/2026-07-phase-a-complete.md `
    docs/release-evidence/2026-07-phase-a-tracker.md `
    docs/release-evidence/2026-07-10-phase-a-reverification.md)
$pendingExit = $LASTEXITCODE
if ($pendingExit -notin 0, 1) {
    throw "A12 sign-off search failed with exit code $pendingExit"
}
if ($pendingSignoff.Count -ne 0) {
    $pendingSignoff
    throw "A12: pending sign-off marker found"
}

$endHead = (git rev-parse HEAD).Trim()
Assert-NativeSuccess "A12 final HEAD"
if ($endHead -ne $head) { throw "HEAD changed during verification" }

git fetch --prune origin
Assert-NativeSuccess "A12 final origin refresh"
$endOriginHead = (git rev-parse origin/master).Trim()
Assert-NativeSuccess "A12 final origin/master"
if ($endOriginHead -ne $originHead) {
    throw "origin/master moved during verification; rerun against $endOriginHead"
}
if ($endHead -ne $endOriginHead) {
    throw "Final HEAD no longer equals origin/master"
}

$endDirty = @(git status --porcelain=v1 --untracked-files=all)
Assert-NativeSuccess "A12 final status"
if ($endDirty.Count -ne 0) {
    $endDirty
    throw "Working tree changed during verification"
}
```

Expected:

- recorded `HEAD` is unchanged, clean, and still equals `origin/master`;
- `git ls-files dashboard/dist` prints nothing;
- `aiautomation/ absent (OK)`;
- `frontend/ absent (OK)`;
- the pending-signoff search prints nothing.

Pass criteria:

- Original closeout evidence remains historical and is not rewritten.
- The dated report and tracker match current reality.
- Global gates pass on the clean evidence checkout and Ubuntu CI passes on the
  recorded tested-source commit.
- Phase A sign-off is recorded.
- No duplicate product surfaces or tracked generated artifacts are present.
- No stage-specific blocker remains.

If failed:

- Fix the regression first.
- Update evidence/tracker only after verification passes.
- Reaffirm owner/lead sign-off if the fix materially changes Phase A behavior.

## Final Deterministic Sweeps

Do not use a repository-wide `TODO|PENDING` search as a gate: it matches valid
trading states, historical evidence, Phase B plans, and this manual. Run these
scoped invariant checks instead.

```powershell
$multiWorkerHits = @(rg --pcre2 -n `
    -e '--workers(?:=|\s+)(?:[2-9]|[1-9][0-9]+)\b' `
    -e '(?:^|\s)-w(?:=|\s+)(?:[2-9]|[1-9][0-9]+)\b' `
    -e 'WEB_CONCURRENCY|UVICORN_WORKERS' `
    Dockerfile backend/Dockerfile docker-compose.yml README.md `
    docs/DEPLOYMENT.md sessions/phase2-paper-soak-runbook.md)
$workerSearchExit = $LASTEXITCODE
if ($workerSearchExit -notin 0, 1) { throw "Worker sweep failed: $workerSearchExit" }
if ($multiWorkerHits.Count -ne 0) {
    $multiWorkerHits
    throw "Supported launch path contains a multi-worker setting"
}

$staleSurfaceHits = @(rg -n `
    'cd frontend|COPY frontend|frontend/package|frontend-build|serve_legacy_frontend|StaticFiles' `
    README.md DOCUMENTATION.md docs/DEPLOYMENT.md .github backend dashboard/src `
    --glob '!backend/tests/**' --glob '!dashboard/src/**/__tests__/**')
$surfaceSearchExit = $LASTEXITCODE
if ($surfaceSearchExit -notin 0, 1) { throw "Surface sweep failed: $surfaceSearchExit" }
if ($staleSurfaceHits.Count -ne 0) {
    $staleSurfaceHits
    throw "Active code/docs contain a retired product-surface path"
}
```

Expected:

- Both sweeps return no hits.
- Reuse the allowlisted retired-ID check from A7 and the sign-off check from
  A12; do not broaden them to historical evidence or negative fixtures.

Any unexpected hit is a failed stage. Record the path and line in the
re-verification report; do not dismiss it without a reviewed disposition.

## Re-verification Record Template

Create `docs/release-evidence/YYYY-MM-DD-phase-a-reverification.md` from this
template. Do not overwrite the signed Phase A completion evidence.

```text
# Phase A Re-verification

Verifier: <name/handle>
Reviewer: <name/handle>
Initial audit started UTC: <ISO-8601>
Formal clean-source replay started/finished UTC: <ISO-8601 values>
Raw transcript: <durable artifact path/link>
Condensed result record: <durable artifact path/link>

## Immutable Source

- branch: master
- tested-source full commit: <40-character SHA>
- tested-source origin/master at start/finish: <40-character SHA>
- tested source clean at start/end: yes/no
- evidence commit: do not self-embed; resolve the later containing commit with
  `git log -1 --format=%H -- <this report path>` during A12

## Environment

- OS/architecture: <value>
- Python: <exact version>
- Node: <exact version>
- npm: <exact version>
- Git: <exact version>
- rg: <exact version>

## Stage Results

| Stage | PASS/FAIL/BLOCKED | Commands/output location | Manual evidence checked | Issue/fix commit |
| --- | --- | --- | --- | --- |
| A0 | | | | |
| A1 | | | | |
| A2 | | | | |
| A3 | | | | |
| A4 | | | | |
| A5 | | | | |
| A6 | | | | |
| A7 | | | | |
| A8 | | | | |
| A9 | | | | |
| A10 | | | | |
| A11 | | | | |
| A12 | | | | |

## Global Gates

- backend pytest: <count>, exit <code>
- dashboard typecheck: exit <code>
- dashboard build: <module count>, exit <code>
- dashboard Vitest: <files/tests>, exit <code>
- workspace hygiene: exit <code>

## Overall Result

PASS/FAIL/BLOCKED

Notes:
- <every warning or count delta>
- <every intentionally historical/allowlisted reference>
- <open regression issue IDs and owners>
```

Before ending the PowerShell session, stop and preserve the transcript:

```powershell
$verificationFinishedUtc = (Get-Date).ToUniversalTime().ToString("o")
"finished_utc=$verificationFinishedUtc"
Stop-Transcript
Copy-Item -LiteralPath $transcript -Destination $durableTranscript -Force -ErrorAction Stop
Get-Item -LiteralPath $durableTranscript | Select-Object FullName, Length, LastWriteTimeUtc
```
