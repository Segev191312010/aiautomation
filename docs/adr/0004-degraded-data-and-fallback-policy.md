# ADR 0004: Degraded Data and Fallback Policy

**Status:** Accepted
**Date:** 2026-04-11
**Supersedes:** N/A

## Context

A trading platform depends on live market data and AI outputs that can fail in subtle ways:
- The data provider (IBKR, yfinance, Coinbase) returns stale quotes without warning.
- A scheduled refresh job misses its window and the cache serves yesterday's values.
- An LLM response fails JSON parse and the code returns a neutral default.
- The WebSocket stream stops receiving updates but stays TCP-connected.

The dangerous failure mode is **silent degradation** — the system keeps returning plausible-looking numbers, the operator trusts them, and a trade fires on bad data. Early versions of the AnalyticsPage silently substituted `MOCK_*` values when the backend returned an error, which hid a real outage for multiple sessions.

Stage 0 of the review roadmap (2026-03) removed the analytics mock substitution. Stage 4 later attempted broader freshness normalization across data providers. This ADR codifies what "fresh / stale / degraded / unavailable" mean, how they must be surfaced, and what code is NOT allowed to do with degraded data.

## Decision

### Four canonical freshness states

Every data source that can stale must report one of these four states:

1. **`fresh`** — the data is within its expected latency window. Safe to use for trading decisions.
2. **`delayed`** — the data is slightly past its expected window but still within an acceptable bound. May be used for trading decisions; UI must label it as delayed.
3. **`stale`** — the data is past its acceptable bound. Must NOT be used for trading decisions that depend on recency (entries, exits, sizing). Read-only display is OK with a clear label.
4. **`unavailable`** — the data source is down, the cache is empty, or the request errored. Must NOT be used. UI must show a degraded state card, not fabricate numbers.

The states form an ordering: `fresh > delayed > stale > unavailable`. Code that consumes a data response must check the state before reading the payload and must fail closed on `stale` or `unavailable` for any decision path.

### Every data response carries its state

API responses that return market data, AI outputs, or diagnostics must include, at minimum:
- `status` — one of the four states above
- `source` — the provider identifier (`ibkr`, `yfinance`, `coinbase`, `cache`, `ai:claude-sonnet-4`, `ai:claude-haiku-4`)
- `fetched_at` — ISO timestamp when the data was obtained from the source
- Optional: `reason` when status is `stale` / `unavailable` to explain why

This is encoded in `api_contracts.py` for the affected endpoints. The data monitor at `/api/data/health` aggregates per-source state for operator display.

### No silent fallback in operator-facing surfaces

The rules, absolute:
1. **No `MOCK_*` substitution in production code paths.** Stage 0 removed the last known instances. A grep for `MOCK_` in `dashboard/src/pages/*` should return only test files or labeled dev-only flags.
2. **No `return None` or `return {}` when a fetch fails.** Return a degraded envelope (`{status: "unavailable", source, reason}`) and let the caller decide.
3. **No averaging between live and cached values.** If the live fetch fails, the cache value is `stale`, not "70% live 30% cache".
4. **No "best effort" magic numbers.** If the risk manager can't estimate a price for a trade, it returns `BLOCK` with `reason="Cannot estimate price for SYM — blocking for safety"`, not `price=100` as a default. (Verified in `risk_manager.py:200-201`.)

### TTL and retry policy

Each data source declares its TTL and retry policy in `DataFreshnessMonitor` registration (`data_health.py`). Per-source policy:
- **IBKR live quotes** (`watchlist_quotes`): `stale_after=30s`. Three consecutive failures → `consecutive_failures=3` warning; five consecutive → `critical`.
- **WebSocket quote stream** (`ws_quotes`): `stale_after=WS_STALE_WARN_SECONDS` (default 60s).
- **Bot cycle bars** (`bot_bars`): stale after 15 min (one full `BOT_INTERVAL_SECONDS`).
- **Diagnostic indicators** (`diag_indicator_values`): `stale_warn_s` / `stale_critical_s` per-indicator, stored in `diag_indicator_catalog`.
- **Sector prefetch cache** (`risk_manager._dynamic_sector_cache`): 24h TTL.
- **AI narrative output**: no retry on parse failure — logged as `bull_bear_parse_failed` and counter-incremented (P2-3, commit `cd1135b`). Threshold breach emits a `MetricEvent` for operator visibility.

Retry policy: **exponential backoff with a hard cap.** No infinite retry loops. After the cap, the source transitions to `unavailable` and the operator must intervene.

### Degraded mode UI contract

The dashboard must visually distinguish the four states:
- `fresh` — no badge, normal render
- `delayed` — amber badge with latency value
- `stale` — amber card with "Data may be outdated as of X minutes ago"
- `unavailable` — red card with source + reason, no numbers

The `DegradedStateCard.tsx` component is the shared implementation. Pages that need degraded rendering must reuse it, not roll their own.

### AI-specific degraded handling

The AI layer has its own degraded-mode patterns:
- **Bull/bear debate JSON parse failure**: returns a `degraded: True` field on the result. Callers must check this and avoid treating degraded output as a strong signal. `should_trade` is forced to `False` when `any_degraded=True`.
- **AI call fallback chain** (`ai_model_router.py`): primary model fails → fallback model (usually Sonnet → Haiku). If all models fail, the wrapper returns a degraded envelope with `ok=False` and the caller must handle it.
- **Circuit breaker**: after `AI_CONSECUTIVE_FAILURE_THRESHOLD` failures, the AI call path is disabled for a cool-down window. Bot cycles continue without AI.

## Consequences

### Positive
- Operators see when data is degraded, so they don't treat stale numbers as live.
- Trading decisions fail closed on bad data — no accidental order fires on stale quotes.
- Post-mortem is easier: every data response carries its provenance.
- The same vocabulary (`fresh/delayed/stale/unavailable`) is used across backend and frontend, so cross-layer reasoning doesn't require translation.

### Negative
- Every data endpoint must return an envelope, not just the payload. This is additional surface area and a backward-compatibility hazard if we ever removed it.
- The freshness monitor has per-source state that can itself go stale (a source that's never been polled has `status='unknown'`). The data_health endpoint must treat `unknown` as degraded until the first successful fetch.
- The `unknown → fresh` transition during startup means the first ~30 seconds after a backend restart can show several sources as degraded even though they're about to come up. The soak runbook accounts for this (allow up to 90s post-restart for `overall_status` to clear).
- UI reviewers often push back on "too many warning cards". The policy is: any operator-facing metric that affects decisions MUST show degradation. Aesthetics is secondary to honesty.

### Rejected alternatives

**Alternative A: Use stale data silently if it's "close enough".** Rejected — "close enough" is how the MOCK fallback hid real outages. If it matters for a decision, the recency matters absolutely.

**Alternative B: Return the last-known-good value when a source fails.** Rejected because "last-known-good" and "fresh" are indistinguishable to the reader without a state field. The compromise allowed in some read-only contexts (historical charts) is to return the cache value explicitly labeled `status='stale'`.

**Alternative C: Per-endpoint vocabularies ("alive/down", "fresh/cached", "ok/error").** Rejected — inconsistency across endpoints was the original bug. One vocabulary across the platform.

**Alternative D: Fail hard on any stale data.** Considered; rejected as too aggressive for the UI. Hard-fail applies to decision paths (risk checks, sizing, exit logic) but not to display paths (a chart can show the last 10 stale bars with a banner).

## Compliance notes

- `data_health.py::DataFreshnessMonitor` is the canonical registration point. New data sources must register there with explicit TTL and failure thresholds.
- `api_contracts.py` defines the shared envelope types. Responses that return decision-relevant data must use one of these envelopes.
- Frontend pages must import `DegradedStateCard` rather than implementing their own error surfaces. Code review must enforce.
- The analytics page (`AnalyticsPage.tsx`) is the reference implementation for section-level degraded handling — see `sessions/review-stage-0-baseline-and-truth.md` Phase 0.2 for the rewrite history.
- The AI debate counter (`ai_debate_parse_failures_24h` in `bot_health.py`, P2-3) is the first per-subsystem degraded-rate surface on `/api/health/bot`. Future metrics should follow the same pattern: surface the count in the health endpoint, emit a MetricEvent at threshold.

## Links

- `backend/data_health.py` — `DataFreshnessMonitor`, freshness state machine
- `backend/api_contracts.py` — response envelope types
- `backend/risk_manager.py:200-201` — fail-closed price estimation
- `backend/ai_advisor.py:465-515` — bull/bear parse-failure telemetry (P2-3)
- `backend/ai_model_router.py` — AI fallback chain
- `dashboard/src/components/common/DegradedStateCard.tsx` — shared UI component
- `dashboard/src/pages/AnalyticsPage.tsx` — section-level degraded implementation
- `sessions/review-stage-0-baseline-and-truth.md` Phase 0.2 — Stage 0 analytics mock removal
- `sessions/review-stage-4-data-diagnostics-hardening.md` — Stage 4 provider boundary work (partially open)
- ADR 0001 (trade truth) — decision-relevant data must never fall back silently
