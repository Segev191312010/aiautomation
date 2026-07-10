# Phase A Re-verification

Date: 2026-07-10

Overall result: **TECHNICAL AND ADMINISTRATIVE PASS** for the Phase A
repository invariants on the tested source below. Phase B is deliberately
deferred pending a jointly agreed plan.

This is not approval for unattended live-money trading. The paper soak,
desktop packaging, and later release gates remain open in the application
readiness roadmap.

## Verification Identity

- Verifier: Codex, executing `docs/PHASE_A_VERIFICATION.md`
- Independent reviews: runtime-lock design review, safety review, quality-gate
  review, and final manual/document review
- Renewed owner/lead acceptance: approved by the owner in-thread on
  `2026-07-10`
- Initial audit session started UTC: `2026-07-10T10:36:01Z`
- Clean-source gate finished UTC: `2026-07-10T12:08:37Z`
- Raw detached clean-source replay: `2026-07-10T12:18:21Z` through
  `2026-07-10T12:22:36Z`
- Raw PowerShell/native-output record:
  `docs/release-evidence/2026-07-10-phase-a-reverification-raw.log`
- Condensed durable transcript:
  `docs/release-evidence/2026-07-10-phase-a-reverification-transcript.txt`
- Runtime-remediation GitHub Actions run:
  https://github.com/Segev191312010/aiautomation/actions/runs/29091445438
- Late hygiene-policy GitHub Actions run:
  https://github.com/Segev191312010/aiautomation/actions/runs/29099407063

## Immutable Source Model

The report intentionally distinguishes the runtime-remediation source, the
later hygiene-policy source, and the final documentation-only addendum commit.
A commit cannot embed its own hash.

- Original signed Phase A tip:
  `e91be61ae89a107fa0fb20b6d12530202d8b6df7`
- Tested remediation source:
  `e9ea6de6f43c6deffa0e7284ab9c00cfe2418df1`
- First documentation evidence commit:
  `5bc95e43b08253c30c3be981351e989267c219b1`
- Tested late hygiene-policy source:
  `2b4db50101b6202eb7ac0a1d631264a122ea961d`
- Branch: `master`
- `HEAD` at formal local start/end: tested remediation source above
- `origin/master` at formal local start/end: tested remediation source above
- Clean at formal local start: yes
- Clean at formal local end: yes
- Final addendum commit: the later `master` commit containing this revision;
  resolve it
  with `git log -1 --format=%H -- docs/release-evidence/2026-07-10-phase-a-reverification.md`

The final addendum commit changes documentation/handoff records only. The
tested runtime, lock, tests, manifests, and dashboard harness are in the tested
remediation source. The expanded 11-suffix hygiene policy and executable manual
are in the separately tested late hygiene-policy source.

## Environment

- OS: Microsoft Windows `10.0.26200`, x64
- Time zone: Asia/Jerusalem (`UTC+03:00` during this run)
- PowerShell: `5.1.26100.8655`
- Python: `3.12.10`
- Node: `24.13.1`
- npm: `11.8.0`
- Git: `2.53.0.windows.1`
- ripgrep: `15.1.0`
- Docker CLI: unavailable locally
- WSL: installed, but no Linux distribution is installed

The local Windows run exercised `msvcrt` byte-range ownership. GitHub's
`ubuntu-latest` backend job exercised the POSIX `fcntl.flock` branch on the same
tested commit.

The raw replay used a clean detached worktree because `master` was checked out
by the evidence-writing worktree. Its first `Start-Transcript` section records
the complete command script and PowerShell-visible output. Because this host
does not mirror all native stdout into `Start-Transcript`, the appended native
output supplement captures dependency resolution and command output explicitly.
That supplement was once interrupted when PowerShell promoted intentional
Vitest WebSocket stderr to `NativeCommandError`; the log states the capture
harness issue and contains the successful continuation with exit code `0`.
It also contains `pip install -r backend/requirements.txt` and `pip freeze`
output. The committed copy is privacy-sanitized: user/host/home paths were
redacted and NUL/ANSI/line-ending noise was normalized; commands, dependency
versions, results, exit codes, hashes, and timestamps were preserved.

## Regressions Found And Corrected

### F1 - A5/A6 stale-lock double ownership

The v1 lock read stale PID metadata and unlinked the path before creating a new
file. Two reclaimers could both classify the same file as stale, one could
unlink the other's new pathname, and both could report ownership.

Correction:

- keep one persistent lock pathname;
- retain an OS-locked descriptor for the full FastAPI lifespan;
- use `msvcrt.LK_NBLCK` on Windows and
  `flock(LOCK_EX | LOCK_NB)` on POSIX;
- never unlink or replace an owned v2 path;
- make JSON/PID metadata diagnostic rather than v2 ownership authority;
- close/unlock on initialization, startup, shutdown, and diagnostic failures.

### F2 - A5 lock scope overstated

The old repository-local path did not coordinate clones/worktrees, and no file
lock can automatically coordinate unrelated native/container namespaces.

Correction:

- native defaults now use per-user OS state and coordinate native clones for
  that user;
- Compose uses the globally named `tradebot-runtime-lock` volume;
- both Dockerfiles prepare `/runtime` for `appuser`;
- the accepted invariant is one owner per shared underlying local path and OS
  lock namespace, not one machine-global owner.

### F3 - v1/v2 and interrupted-metadata boundary

Old v1 binaries ignore the v2 OS primitive, used different default paths, and
can unlink a locked inode on POSIX. A kill during the first v2 metadata write
can also leave an unknown partial record.

Correction:

- rolling v1/v2 coexistence is explicitly unsupported;
- operators must stop every v1 native process and Compose stack before v2;
- unknown/partial metadata fails closed without overwrite;
- the exception and manual require verified process/container emptiness before
  operator cleanup;
- a complete abandoned v2 record recovers automatically after OS ownership is
  released.

### F4 - A8 persisted mode bypass

The original persisted mode was loaded after simulation, IBKR, heartbeat, and
alert services had begun. Capability was not rechecked, and the loader's
environment fallback could preserve PAPER authority after a DB read failure.

Correction:

- validate environment mode before DB initialization;
- load persisted guardrails strictly immediately after DB initialization;
- validate matrix and AI capability before all runtime services;
- force mode `OFF`, autonomy false, and shadow mode true on invalid modes,
  missing capability, retired models, or DB/JSON/Pydantic read failure.

### F5 - passing Vitest run emitted chart exceptions

Analytics tests deleted their local `ResizeObserver` while asynchronous chart
effects could still run, producing post-summary `ReferenceError` stacks even
though Vitest exited zero.

Correction: install one stable no-op `ResizeObserver` in the global test setup.
The final full run has no chart exception, React warning, or unhandled-error
stack. Eight intentional WebSocket disconnect diagnostics remain.

## Stage Results

| Stage | Result | Executed proof | Manual evidence/disposition |
| --- | --- | --- | --- |
| A0 | PASS | clean `master`; `HEAD == origin/master`; full gates `640/372` | baseline remains dated history |
| A1 | PASS - REMEDIATED | late ignored TWS installer quarantined; tracked and hidden/ignored 11-suffix scans empty | dated artifact metadata and disposition recorded |
| A2 | PASS | all 11 isolated suffix probes produced required exit `1`; clean scan passed | quarantine policy and docs present |
| A3 | PASS | launch-path `rg` inventory returned only documented paths | launch inventory matches source |
| A4 | PASS | `test_launch_manifests.py`: `4 passed` | one worker plus named-volume/permission checks |
| A5 | PASS UNDER DOCUMENTED SCOPE; ACCEPTANCE PENDING | compile pass; Compose YAML parse pass; OS-lock source/review pass | shared-path/namespace v2 scope replaces the original literal machine-global wording; stop-v1 boundary documented |
| A6 | PASS | runtime lock `22 passed -W error`; lifespan lock `4 passed -W error`; Ubuntu backend job passed | deterministic contenders, real app subprocess, crash/error cleanup |
| A7 | PASS | targeted backend `41 passed`; active-runtime retired-ID scan empty | current Anthropic defaults reconfirmed |
| A8 | PASS | targeted backend `73 passed`; dashboard typecheck; Autopilot UI `14 passed` | strict persisted-mode fail-closed ordering proved |
| A9 | PASS | `aiautomation/` and `frontend/` absent; tracked dist empty | ADR/canonical surface consistent |
| A10 | PASS | archive tag target verified locally/remotely; backend `2 passed`; UI `14 passed`; global gates | keepers remain in canonical dashboard |
| A11 | PASS | dashboard build leaves tree clean; stale surface scan empty; counts/doc truth updated | old evidence labeled historical/superseded |
| A12 | PASS | final clean-source gates; immutable commit/remote equality; this dated record; owner approval recorded | Phase B deliberately deferred pending joint planning |

## Exact Targeted Results

```text
A4 launch manifests                         4 passed
A6 runtime lock (-W error)                 22 passed
A6 startup/lifespan lock (-W error)         4 passed
A7 startup/learning/replay                  41 passed
A8 capability/startup/contracts/mode        73 passed
A10 product surface                          2 passed
A8/A10 Autopilot UI                         14 passed
PowerShell manual blocks             33, 0 parse errors
```

## Final Local Global Gates

Formal clean-source run on
`e9ea6de6f43c6deffa0e7284ab9c00cfe2418df1`:

```text
cd backend; python -m pytest tests/ -q
640 passed in 43.29s

cd dashboard; npm run typecheck
PASS

cd dashboard; npm run build
PASS - 610 modules transformed (Vite 2.38s)

cd dashboard; npx vitest run
27 files passed, 372 tests passed in 6.26s

python scripts/check_workspace_hygiene.py
Workspace hygiene OK: no forbidden binary artifacts found.

final HEAD:          e9ea6de6f43c6deffa0e7284ab9c00cfe2418df1
final origin/master: e9ea6de6f43c6deffa0e7284ab9c00cfe2418df1
final porcelain:     empty
```

## GitHub Ubuntu Evidence

GitHub Actions CI run `29091445438` completed successfully in `1m 11s` on the
tested source:

- Backend (Python + pytest): success in `1m 09s`
- Dashboard (TypeScript + Vite + Vitest): success in `56s`
- Backend job:
  https://github.com/Segev191312010/aiautomation/actions/runs/29091445438/job/86357489634
- Dashboard job:
  https://github.com/Segev191312010/aiautomation/actions/runs/29091445438/job/86357489643

The run emitted two non-blocking GitHub action-runtime warnings: the Node
20-based JavaScript runtimes in `actions/checkout@v4`, `actions/setup-node@v4`,
and `actions/setup-python@v5` are being forced onto Node 24 by the runner. Both
jobs passed. Updating those action majors is tracked as CI maintenance and is
not a Phase A functional regression.

## Static And Manual Invariants

- tracked forbidden-binary scan: no hits;
- isolated fake-DLL hygiene probe: detected and rejected;
- retired model scan outside lifecycle/negative-test allowlists: no hits;
- multi-worker supported-launch scan: no hits;
- retired product-surface operational-path scan: no hits;
- `dashboard/dist` tracked paths: none;
- archive tag target:
  `16280057ab04bee97904e9c59b9a5143a58bb673`, local and remote;
- Compose YAML: backend mounts `tradebot-runtime-lock:/runtime`, stable volume
  name present;
- `runtime_lock.py` and `main.py`: compile pass;
- manual: 33 PowerShell blocks, zero parser errors.

## Post-report A1/A2 Workspace Addendum

After this report's clean detached replay, a completion audit found the ignored
Interactive Brokers installer `ntws-latest-standalone-windows-x64.exe` in the
primary repository root. An ignored executable still violates A1/A2 policy.
The checker did not execute or delete it: it recorded the size, SHA-256,
Authenticode identity, Git state, and timestamps, then moved the file with its
hash intact to a dated quarantine directory under
`$env:USERPROFILE\Downloads`.

After quarantine, the policy checker, tracked scan, hidden/ignored all-file
scan, backend `640`, dashboard `372`, typecheck, and build all passed again on
clean source commit `5bc95e43b08253c30c3be981351e989267c219b1`.

The checker policy was then expanded to enforce the original brief's `.bin`,
`.so`, and `.dylib` suffixes. All 11 isolated suffix probes, the global gates,
and set-equality checks passed locally. Immutable policy commit
`2b4db50101b6202eb7ac0a1d631264a122ea961d` then passed both Ubuntu jobs in
GitHub Actions run `29099407063`. Full details are in
`docs/release-evidence/2026-07-10-a1-a2-late-binary-quarantine.md`.

The owner approval recorded in this thread accepts the disposition and closes
the A1/A2 follow-up for A12.

## Limitations And Required Operations

1. Docker was unavailable on this verifier, so no live Compose container/volume
   smoke was run. YAML, Dockerfile ownership, and manifest tests passed. This is
   a transparent environment limitation, not claimed container runtime proof.
2. Stop every v1 native runtime and old Compose stack before deploying v2.
   Rolling coexistence is unsupported.
3. Never delete unknown/partial lock metadata until process and container
   inventories prove that the scope has no owner.
4. POSIX proof comes from the same-source Ubuntu CI run; local execution was
   Windows only.
5. Network filesystem lock behavior is outside the supported local-filesystem
   invariant.
6. GitHub action major-version warnings should be removed in a later infra
   maintenance commit.
7. Fresh `npm ci` repeated the roadmap's known dependency audit result: 10
   findings (`1 critical`, `4 high`, `4 moderate`, `1 low`). They were not
   introduced by this remediation, but they remain a production-release
   blocker and must not be mistaken for a clean dependency audit.

## Review Outcome

The final safety review reported no remaining CRITICAL or HIGH finding under
the stop-all-v1/no-rolling-coexistence premise. The final documentation review
confirmed the recorded A4/A6/A7/A8 counts and PowerShell syntax; its history,
self-reference, count, scope, inventory, and transcript findings were corrected
before this report.

## Final Decision

Phase A regression re-verification is a **TECHNICAL AND ADMINISTRATIVE PASS**
for the tested source. The owner approval recorded in-thread accepts the
shared-lock-path and OS/filesystem-namespace scope, stop-all-v1 boundary, A8
fail-closed behavior, and late A1 quarantine disposition. Phase B is explicitly
not started; it will begin only after a jointly agreed plan. The paper-soak,
packaging, auth, contract, and live-release gates remain independently
mandatory.
