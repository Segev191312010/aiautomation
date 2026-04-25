# Phase A — Quick Wins — Codex Review

**Reviewed:** 2026-04-19
**Diff range:** cbc8c0f..HEAD (post-fix: includes cancel-guard commit)
**Reviewer:** codex (mcp__codex-review__codex)

## Commits reviewed

1. 355c70c refactor(dashboard): deduplicate fmtUSD in 4 files
2. 63c1fb4 perf(dashboard): memoize WatchlistGrid sort chain
3. bd9e3ee chore(dashboard): remove dead addTrade import in useWebSocket
4. 2314346 security(dashboard): encode symbol path params in API clients
5. 00534df feat(dashboard): add connected getter to MarketDataWsService
6. cc716d0 perf(dashboard): adaptive polling based on WS connection state

## Verdict

- CRITICAL: none
- MAJOR (2 flagged):
  - **Adaptive polling not reactive to mid-sleep WS state changes** — when a 30s (SLOW) timer is scheduled and the socket drops mid-sleep, polling stays quiet for up to 30s before falling back to FAST. `useMarketData.ts:163`.
    - **Decision:** NOT FIXED. The plan `sessions/phase5-adaptive-polling.md` explicitly calls this tradeoff out under "Worst-case latency" and accepts it. No action.
  - **Recursive setTimeout lacks cancellation guard** — cleanup clears the current timer but a callback already mid-flight could install a new one post-unmount, leaking a detached poller. `useMarketData.ts:163`.
    - **Decision:** FIXED in follow-up commit (cancel-guard). Added a `let cancelled = false` closure flag checked on entry to both `scheduleNext` and the timeout callback; cleanup now sets the flag before clearing the timer.
- MINOR (1 flagged):
  - **No hook-level test for reschedule on WS open/close** — only the `connected` getter is covered directly. Integration coverage deferred; the getter is the unit primitive and is tested.
    - **Decision:** NOT FIXED this phase. Hook integration tests require renderHook + fetch mock setup that is out-of-scope for A5; revisit in Phase E if needed.

## LGTM items

- 355c70c — clean import swap; shared fmtUSD matches Intl-based call sites.
- 63c1fb4 — memo preserves behavior; quotes/watchlist updates are immutable so memo does not stale.
- bd9e3ee — safe; hook still uses useAccountStore.getState() for trade/account paths.
- 2314346 — encoding is correct for the affected FastAPI routes.
- 00534df — getter is correct; unit test covers OPEN/CLOSED transitions.

## Follow-up recommendation

Add a renderHook-based integration test during Phase E (Testing & Validation) that asserts `setTimeout` is scheduled with the correct delay for each WS state. That closes the minor coverage gap.
