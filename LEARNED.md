# Learned Rules

Rules are added here when mistakes are corrected during sessions.
Format: `## [Category] Rule (YYYY-MM-DD)`

Read this file before editing code. If a rule applies to your current task, follow it.

<!-- Entries will be added below this line -->

## [Prompting] Phase-by-phase execution means phase-by-phase (2026-04-17)
- Mistake: User said "start only phase one" but I later launched agents for all 5 phases when they said "call all agents"
- Correction: "Call all agents" means multiple agents for the CURRENT phase, not jumping ahead. When per-phase session files exist, the user controls the pace.

## [Claude-Code] Ultraplan analyzes a remote stale copy (2026-04-17)
- Mistake: Ultraplan claimed files like QuickOrderForm.tsx and formatters.ts didn't exist — they did locally
- Correction: Always cross-reference ultraplan findings against the local codebase before executing. The remote copy may be outdated.

## [Backtester] Column ordering with reset_index (2026-03-03)
- Mistake: Lowercased column names before `reset_index()`, so the DatetimeIndex 'Date' kept its uppercase name and the check `"date" in raw.columns` failed, causing `time = range(len(raw))` — all trade dates showed as 1970-01-01
- Correction: Always call `reset_index()` first, then lowercase all columns. Order matters when index names differ from column names.

## [Safety] Unawaited async fence is a silent no-op (2026-07-28)
- Mistake: `if not validate_fencing_token(token):` returned a coroutine, which is truthy, so the fence was bypassed.
- Correction: Always `await validate_fencing_token(token)`. For shared broker mutations, centralize the fence inside guarded wrappers (`place_order_guarded`, `cancel_order_guarded`) so callers can't forget.

## [Audit] Handoff caveats are not tracked to closure (2026-08-01)
- Mistake: The 2026-05-15 deep-review handoff listed 7 known caveats. As of 2026-08-01, 3 are still open (advisor user_id filter, cycle_degraded event, market-data GET auth) with no tracking issue.
- Correction: Every handoff caveat needs a tracking ID and owner. Handoff files should not be treated as closure — they are a snapshot of known debt.

## [Audit] Session files and handoffs drift from code (2026-08-01)
- Mistake: Multiple session files claimed work was "NOT STARTED" when it was fully implemented (swing screener, TV webhook). Other handoffs claimed issues were open when they were fixed (rate-cap cross-process). The paper-soak runbook references a `remaining_work_2026_04_08.md` file that doesn't exist.
- Correction: After every stage, cross-reference session claims against actual code. Delete completed session files. Update handoff caveats with current status.

## [Shell] `path` is a special zsh variable (2026-08-02)
- Mistake: A read-only audit loop assigned a filename to `path`, which overwrote zsh's command-search `PATH` and made subsequent `git` calls fail.
- Correction: Use task-specific shell variable names such as `audit_file`; never assign to zsh special variables while orchestrating repository checks.

## [Git] Verify tree-path revisions before comparing blobs (2026-08-02)
- Mistake: A provenance audit used `git rev-parse commit:path` without `--verify`; missing tree paths could be echoed as unresolved expressions and produce unreliable classifications.
- Correction: Resolve tree-path blobs with `git rev-parse --verify "commit:path"` (or an equivalent checked command), treat a nonzero result as absent, and discard classifications from unchecked revisions.

## [Shell] Interpolate orchestrator paths before execution (2026-08-02)
- Mistake: A generated shell comparison referenced unset `$clean` and `$root` variables even though those paths existed only as JavaScript variables, producing misleading zero-file diff summaries.
- Correction: Put validated absolute paths directly into the generated command (or explicitly define task-specific shell variables first), and sanity-check comparisons with file hashes before trusting an empty diff.

## [Evidence] Verify producers before hashing derived artifacts (2026-08-02)
- Mistake: A schema-fingerprint command invoked the backend initializer from the repository root, where `database` was not importable, and a later SQLite command could still hash the resulting empty file.
- Correction: Run backend entry points from `backend/` (or set an explicit `PYTHONPATH`), require the producer command to exit successfully, and discard every downstream count or digest when artifact creation fails.

## [Shell] Separate commands that require different module roots (2026-08-02)
- Mistake: After recording the backend-cwd rule, a combined inspection still ran repository-root file reads and `import main` under one root working directory, repeating the same module-resolution failure.
- Correction: Never combine operations that require different working directories. Run repository inspection from the repository root and Python backend imports in a separate call whose `workdir` is explicitly `backend/`.

## [Shell] Rebase every path against the declared working directory (2026-08-02)
- Mistake: A dashboard inspection set `workdir` to `dashboard/` but still prefixed the manifest path with `dashboard/`, so the read resolved to the nonexistent `dashboard/dashboard/package.json`.
- Correction: Before executing a command with a non-root `workdir`, resolve each relative path mentally against that directory. Use subproject-relative paths only, and split commands when their targets require different roots.

## [Shell] Do not pass optional unmatched globs to zsh commands (2026-08-02)
- Mistake: A documentation search included the optional glob `backend/README*`; zsh's `nomatch` behavior aborted the command because no file matched.
- Correction: Discover optional targets with `rg --files` first, then pass only explicit existing paths. If a shell glob is truly necessary, use a safely quoted pattern inside the search tool rather than relying on shell expansion.

## [FastAPI] Included routers may be lazy (2026-08-02)
- Mistake: A route-ownership regression test scanned `app.routes` for concrete paths, but the pinned FastAPI version stores `include_router()` registrations as lazy `_IncludedRouter` entries whose direct `path` is `None`.
- Correction: Verify included-route uniqueness through the generated OpenAPI path map (or explicitly expand included routers), not a flat `app.routes` path scan.

## [API] Test the full application's error envelope (2026-08-02)
- Mistake: A focused router test expected FastAPI's default `{"detail": ...}` error, while the assembled app's global `HTTPException` middleware deliberately normalizes it to `{"error": ..., "detail": ...}`.
- Correction: For public contract assertions, mount or launch the real application and assert its final middleware-normalized response. Router-only tests may verify internal behavior but are not wire-contract evidence.

## [State] Durable provenance does not prove process-local activation (2026-08-02)
- Mistake: The dashboard initially inferred that a deterministic fixture was active from persisted order/account provenance, even though the fixture itself exists only in backend process memory after a restart.
- Correction: Expose and poll explicit process-local activation state. Keep historical provenance separate, and fail controls closed whenever the active-state check is absent, inactive, or unavailable.

## [Tooling] Do not probe executable scripts with unsupported flags (2026-08-02)
- Mistake: Running a quality script with `--help` to inspect usage launched the full gate because the script did not parse that flag.
- Correction: Read an executable script before invoking it. Use `--help` only when argument parsing is verified; otherwise treat the documented command as executable behavior.

## [Python] Use the repository interpreter explicitly (2026-08-02)
- Mistake: A backend checkpoint invoked bare `python`, which was not installed on the shell PATH even though the project virtual environment was valid.
- Correction: Use `backend/.venv/bin/python` (or `.venv/bin/python` from `backend/`) for repository Python commands so interpreter and dependency identity stay explicit.
