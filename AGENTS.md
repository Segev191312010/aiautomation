# Trading Platform — Workflow & Agents

## Stage Protocol
Each development stage follows this cycle:
1. Read session prompt (sessions/stage-N-*.md)
2. Implement in order — quality gates every 5 edits
3. Run full test suite before committing
4. Commit with conventional format
5. Generate handoff: /handoff → handoffs/YYYY-MM-DD-stage-N-*.md
6. Update learning-log.md with session summary
7. Run /wrap-up to close the session

## Commit Convention
Format: `type(scope): short summary`

Types: feat, fix, refactor, test, docs, chore, perf
Scopes: backend, dashboard, chart, screener, backtest, alerts, rules, analytics, infra

Example: `feat(chart): add Fibonacci retracement drawing tool`
- Summary under 72 characters
- Body explains WHY, not WHAT
- Reference stage in body: "Part of Stage 2b"

## Branch Strategy
- master: main branch (single developer, all stages committed here)
- Worktrees: for parallel exploration only, merge back to master
- Tag after each stage: v0.1.0 (Stage 1), v0.2.0 (Stage 2), etc.

## File Organization
- Backend: one module per concern (auth.py, settings.py, screener.py, backtest.py)
- Dashboard components: dashboard/src/components/{feature}/*.tsx
- Shared types: dashboard/src/types/index.ts (extend, don't create new files unless >200 lines)
- API service: dashboard/src/services/api/ (16 domain modules, barrel re-export in api.ts)
- Store: dashboard/src/store/ (15 domain stores, barrel re-export in index.ts)
- Tests: colocate with source (backend/tests/, dashboard/src/**/__tests__/)
- Session prompts: sessions/stage-N-*-prompt.md
- Handoffs: handoffs/YYYY-MM-DD-stage-N-*.md

## Testing Requirements
- Backend: 490+ tests across 41 files (pytest) — covers order lifecycle, exits, safety kernel, AI optimizer, decision ledger, replay, rules, alerts, screener, bot health, WS auth
- Frontend: 259+ tests across 17 files (vitest) — covers pages, hooks, components
- Run all: `cd backend && python -m pytest tests/ -v` and `cd dashboard && npx vitest run`
- Quality gates (run before every commit): typecheck, build, pytest, vitest

## Agent Definitions (future .claude/agents/)

### Explorer Agent
- Model: Haiku (fast, cheap)
- Purpose: read-only codebase exploration, finding patterns, locating imports
- Tools: Read, Glob, Grep, Bash (read-only)
- Use when: finding where something is defined, tracing imports, understanding call chains

### Quality Agent
- Purpose: run quality gates and report results
- Tools: Bash
- Commands: `cd dashboard && npm run typecheck && npm run build && npx vitest run` then `cd backend && python -m pytest tests/ -v`
- Use when: before every commit, after every 5 file edits (80/20 checkpoints)

### Code Reviewer Agent
- Model: Sonnet (fast, thorough enough for review)
- Purpose: review staged changes for bugs, anti-patterns, and style violations
- Tools: Read, Glob, Grep, Bash (git diff only)
- Checklist:
  - Logic errors and off-by-one mistakes
  - Missing error handling at system boundaries (API endpoints, external calls)
  - Type safety (no `any` in TypeScript, proper Python type hints)
  - Zustand store mutations follow immutable patterns
  - FastAPI endpoints return proper HTTP status codes
  - No hardcoded secrets, API keys, or credentials
- Use when: before committing, after significant refactors
- Severity levels: CRITICAL (block commit), HIGH (fix before merge), LOW (suggestion)

### Security Auditor Agent
- Model: Opus (correctness critical for financial platform)
- Purpose: scan for vulnerabilities in a trading platform context
- Tools: Read, Glob, Grep
- Checklist:
  - OWASP Top 10: injection, XSS, broken auth, SSRF
  - API key / secret exposure (IBKR credentials, .env leaks)
  - SQL injection in raw queries (aiosqlite parameterization)
  - WebSocket authentication and message validation
  - CORS configuration review
  - Rate limiting on public endpoints
  - Input validation on trading-related endpoints (order sizes, symbols)
  - No sensitive data in logs or error responses
- Use when: before committing auth/API changes, before Stage 8 production hardening

### Python Backend Expert Agent
- Model: Sonnet
- Purpose: FastAPI + async Python specialist
- Tools: Read, Glob, Grep, Bash
- Expertise:
  - FastAPI route design, dependency injection, middleware
  - async/await patterns with aiosqlite and ib_insync
  - pandas data pipeline optimization
  - yfinance rate limiting and caching strategies
  - Pydantic model validation and serialization
  - Background tasks (asyncio, FastAPI lifespan events)
- Use when: designing new backend modules, debugging async issues, optimizing data pipelines

### React/TypeScript Expert Agent
- Model: Sonnet
- Purpose: React 18 + TypeScript + Vite frontend specialist
- Tools: Read, Glob, Grep, Bash
- Expertise:
  - React 18 hooks, Suspense, concurrent features
  - TypeScript strict mode, generics, discriminated unions
  - Zustand store design (slices, selectors, subscriptions)
  - lightweight-charts API and custom overlays
  - Tailwind CSS utility patterns and responsive design
  - Vite configuration, HMR, build optimization
  - Component composition over inheritance
- Use when: building complex UI components, fixing type errors, optimizing renders

### Database Expert Agent
- Model: Sonnet
- Purpose: schema design, query optimization, migrations
- Tools: Read, Grep, Bash
- Expertise:
  - aiosqlite async query patterns and connection pooling
  - Schema design for trading data (OHLCV, positions, orders, alerts)
  - Index strategy for time-series queries
  - Migration scripts (schema versioning without Alembic)
  - Data integrity constraints for financial records
  - Query optimization for screener bulk scans
- Use when: adding new tables, optimizing slow queries, planning schema changes

### Performance Engineer Agent
- Model: Sonnet
- Purpose: identify and fix performance bottlenecks
- Tools: Read, Glob, Grep, Bash
- Checklist:
  - Frontend: bundle size analysis, React render profiling, memoization audit
  - Backend: async bottleneck detection, N+1 query patterns, connection pool sizing
  - Data: pandas vs. raw SQL for bulk operations, caching strategy (in-memory vs. disk)
  - WebSocket: message frequency throttling, payload size optimization
  - Charts: lightweight-charts render performance with large datasets
- Use when: screener feels slow, charts lag with many data points, API response times degrade

### Test Automator Agent
- Model: Sonnet
- Purpose: write comprehensive test suites (pytest + vitest)
- Tools: Read, Glob, Grep, Bash
- Patterns:
  - pytest: fixtures, parametrize, async test patterns, mock IBKR responses
  - vitest: component testing with React Testing Library, MSW for API mocking
  - Test data factories for trading domain (mock candles, orders, positions)
  - Edge cases: market hours, weekends, holidays, empty datasets, rate limits
  - Integration tests: API contract validation between frontend and backend
- Use when: adding new features, after fixing bugs (regression test), Stage 4 backtest engine

### API Designer Agent
- Model: Sonnet
- Purpose: design consistent REST endpoints and data contracts
- Tools: Read, Glob, Grep
- Standards:
  - RESTful resource naming (`/api/screener/scans`, not `/api/runScreener`)
  - Consistent response envelope: `{ data, error, meta }`
  - Pagination for list endpoints (offset/limit with total count)
  - Proper HTTP methods and status codes (201 Created, 204 No Content, 422 Unprocessable)
  - Request/response type alignment between Pydantic models and TypeScript interfaces
  - Versioning strategy for breaking changes
- Use when: adding new endpoints, refactoring existing API surface

### Debugger Agent
- Model: Sonnet
- Purpose: investigate errors, test failures, and unexpected behavior
- Tools: Read, Glob, Grep, Bash
- Approach:
  1. Reproduce: read error message/stack trace, identify the failing code path
  2. Isolate: trace data flow from entry point to failure
  3. Hypothesize: form 2-3 theories ranked by likelihood
  4. Verify: add targeted logging or read state at failure point
  5. Fix: minimal change that addresses root cause, not symptoms
- Specialties: async race conditions, WebSocket disconnects, IBKR API timeouts, chart rendering glitches
- Use when: something broke and the cause isn't obvious

### Build Error Resolver Agent
- Model: Haiku (fast iteration on type/build errors)
- Purpose: fix TypeScript compilation errors and Python import issues
- Tools: Read, Edit, Bash
- Approach:
  - Parse error output, fix incrementally (one error at a time)
  - TypeScript: module resolution, missing types, strict null checks
  - Python: circular imports, missing dependencies, version conflicts
  - Vite: build failures, chunk size warnings, dependency pre-bundling
- Use when: `npm run typecheck` or `npm run build` fails, Python import errors

### Backtest Validator Agent (Stage 4)
- Model: Opus (correctness is critical — financial calculations)
- Purpose: validate backtesting engine for correctness and integrity
- Tools: Read, Glob, Grep, Bash
- Checklist:
  - No look-ahead bias: strategy only sees data up to current bar
  - Indicator warmup: skip bars until all indicators have enough history
  - Event ordering: market data → indicator calc → signal → order → fill
  - Fill simulation: realistic slippage, partial fills, spread modeling
  - Position tracking: correct PnL calculation, commission accounting
  - Time handling: timezone-aware, market hours respected
  - Reproducibility: same input → same output (deterministic)
  - Edge cases: gaps, halts, splits, dividends, thin liquidity
- Use when: any change to the backtest engine, validating strategy results

### Refactor Agent
- Model: Sonnet
- Purpose: clean up technical debt without changing behavior
- Tools: Read, Glob, Grep, Edit, Bash
- Guidelines:
  - Dead code removal (unused imports, unreachable branches, orphan components)
  - Extract repeated patterns into shared utilities (only if 3+ occurrences)
  - Simplify complex conditionals and deeply nested callbacks
  - Ensure tests pass before AND after refactoring (behavior preservation)
  - Keep changes small and reviewable — one concern per refactor pass
- Use when: accumulated tech debt, before starting a new stage, code smells flagged by reviewer

### Trading Strategy Agent
- Model: Opus (correctness critical for financial decisions)
- Purpose: design, review, and debug trading strategies, indicators, signals, and entry/exit logic
- Tools: Read, Glob, Grep, Bash
- Expertise: technical indicators (SMA, EMA, RSI, MACD, BB, ATR, VWAP), strategy patterns (mean reversion, trend following, momentum), position sizing (fixed fractional, ATR-based, Kelly), stop-loss design
- Use when: building strategies, reviewing indicator calculations, checking for overfitting

### Risk Manager Agent
- Model: Opus (financial calculations must be exact)
- Purpose: portfolio risk management, position sizing, and risk metrics
- Tools: Read, Glob, Grep, Bash
- Metrics: Sharpe, Sortino, max drawdown, win rate, profit factor, expectancy, Calmar ratio
- Use when: implementing risk controls, Stage 7 analytics, reviewing position sizing logic

### Market Data Agent
- Model: Sonnet
- Purpose: market data pipelines — IBKR feeds, yfinance downloads, OHLCV processing, caching
- Tools: Read, Glob, Grep, Bash
- Expertise: yfinance rate limits, IBKR historical data API, pandas OHLCV processing, data validation, caching strategies
- Use when: building data fetchers, debugging missing/bad data, optimizing download speed

### Order Execution Agent
- Model: Opus (real money operations — correctness non-negotiable)
- Purpose: order placement, fill handling, position management via ib_insync
- Tools: Read, Glob, Grep, Bash
- Safety: symbol validation, quantity bounds, price sanity, duplicate detection, buying power check
- Use when: implementing order flow, IBKR integration, position tracking

### Screener Specialist Agent
- Model: Sonnet
- Purpose: stock screener/scanner — bulk scanning, filtering, ranking, caching
- Tools: Read, Glob, Grep, Bash
- Use when: Stage 3 implementation, optimizing scan performance, designing filter schemas

### Alert Engine Agent
- Model: Sonnet
- Purpose: alert system — price/technical alerts, background evaluation loop, WebSocket notifications
- Tools: Read, Glob, Grep, Bash
- Use when: Stage 5 implementation, designing alert conditions, WebSocket notification delivery

### Rule Builder Agent
- Model: Sonnet
- Purpose: visual condition builder — composable rule engine, serialization, drag-and-drop UI
- Tools: Read, Glob, Grep, Bash
- Use when: Stage 6 implementation, designing the condition tree format, rule serialization

### WebSocket Specialist Agent
- Model: Sonnet
- Purpose: real-time data streaming — live quotes, alerts, order updates
- Tools: Read, Glob, Grep, Bash
- Use when: implementing WebSocket endpoints, React hooks for real-time data, connection management

### UX Reviewer Agent
- Model: Sonnet
- Purpose: review UI/UX for usability, consistency, and trading-specific patterns
- Tools: Read, Glob, Grep
- Use when: building new pages/components, reviewing dashboard layout, checking data density

### State Manager Agent
- Model: Sonnet
- Purpose: Zustand store architecture — slice design, selectors, real-time update patterns
- Tools: Read, Glob, Grep
- Use when: designing new store slices, debugging state bugs, optimizing subscriptions

### Error Handler Agent
- Model: Sonnet
- Purpose: error handling, logging, and recovery strategies across full stack
- Tools: Read, Glob, Grep, Bash
- Use when: setting up error boundaries, API error responses, retry logic, IBKR connection resilience

### Data Migration Agent
- Model: Sonnet
- Purpose: database migration scripts, schema versioning, data transformations
- Tools: Read, Glob, Grep, Bash, Edit
- Use when: changing schemas, adding tables, planning SQLite → PostgreSQL migration

### Dependency Auditor Agent
- Model: Haiku (quick scan)
- Purpose: audit npm/pip dependencies for vulnerabilities, outdated packages, bundle size impact
- Tools: Bash, Read, Grep, Glob
- Use when: periodically, before production deployment, after adding new dependencies

### Git Historian Agent
- Model: Haiku (fast lookups)
- Purpose: analyze git history, track code evolution, hunt regressions
- Tools: Bash, Read, Grep, Glob
- Use when: investigating regressions, understanding why code was written a certain way, reviewing stage progress

### Deployment Agent
- Model: Sonnet
- Purpose: Docker, environment config, health checks, production hardening
- Tools: Read, Glob, Grep, Bash
- Use when: Stage 8 production hardening, containerization, security headers, monitoring setup
