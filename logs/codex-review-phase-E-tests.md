# Phase E — Testing & Validation — Codex Review

**Reviewed:** 2026-04-19
**Diff range:** a701d04, 59fba4d, af74ecb, 1c211a4 + fix d9287e7
**Reviewer:** codex (mcp__codex-review__codex)

## Commits reviewed

- a701d04 test(dashboard): comprehensive indicator unit tests (E1)
- 59fba4d test(dashboard): auth token revocation on 401 (E2)
- af74ecb test(dashboard): WebSocket reconnection behavior (E3)
- 1c211a4 test(backend): integration smoke for order lifecycle + watchlist persistence (E4)
- d9287e7 fix+test(dashboard): close codex findings on Phase E test suite

## Verdict

- CRITICAL: none
- MAJOR (2 flagged, both FIXED in d9287e7):
  - **Stale bearer token could be re-sent after a 401 cleared localStorage** — this was an actual client.ts bug, not a test gap. Root cause: `_bootstrapPromise` resolved once with the initial token, and `_waitForToken` continued to race against that resolved value even when storage was empty. FIX: added `_bootstrapDone` flag; post-bootstrap-with-empty-storage returns null immediately. New test covers the second-request path and fails against the previous code.
  - **Ping-interval test only checked readyState, trivially true** — FIX: count sockets[0].sent across three ping windows before and after disconnect; assert no further sends.
- MINOR (1 flagged, FIXED in d9287e7):
  - **EMA/RSI/MACD were mostly sanity checks** — FIX: added pinned EMA(period=4) sequence, Wilder textbook RSI fixture (≈61.83), RSI first-time alignment, MACD constant-series = zero invariant, MACD first-point time = slow-EMA start.
- LGTM:
  - E1: deterministic fixtures, hand-computed SMA/BB/VWAP values, registry + interval coverage.
  - E2: module-reset hygiene, useful branch coverage.
  - E3: fake timers + mock socket make reconnect tests deterministic.
  - E4: smoke tests exercise real route wiring, auth bootstrap, SQLite persistence, and the sim engine; mocks are narrow.

## Test counts after Phase E closure

- Dashboard: 355 tests passing (was 286 at start of Phase E)
- Backend: 542 tests passing (was 538 at start)

## Follow-up

None required. The stale-token bug fix is a real security win surfaced by the test suite — exactly what the phase was intended to achieve.
