# Trading Platform — Style & Conventions

## General Principles
- Clarity over cleverness — this codebase must be readable months later
- Small functions — if a function exceeds 40 lines, split it
- No magic numbers — use named constants (OHLCV_COLUMNS, MAX_BARS_PER_REQUEST)
- Comments explain WHY, not WHAT — if the code needs a WHAT comment, refactor it

## Python (Backend)
- Python 3.11+ features OK (match, ExceptionGroup, TaskGroup)
- Type hints on all function signatures
- Pydantic v2 models for all API request/response shapes
- async def for all endpoint handlers and database calls
- snake_case for functions, variables, files
- PascalCase for Pydantic models and classes
- Constants: UPPER_SNAKE_CASE in config.py
- Imports: stdlib → third-party → local (separated by blank lines)
- f-strings over .format() or %
- aiosqlite for all database operations (no synchronous sqlite3)
- Error responses: always `{error: str, detail: str}` JSON format

## TypeScript (Dashboard)
- TypeScript strict mode (tsconfig.json)
- React 18 functional components only — no class components
- Zustand for global state, React state for component-local
- Named exports for components, default export only for pages
- Interface over type for object shapes (unless union/intersection needed)
- Destructure props in function signature
- File naming: PascalCase for components (ChartToolbar.tsx), camelCase for utilities (heikinAshi.ts)
- Path alias: @/* maps to src/*
- Tailwind for styling — no CSS modules, no styled-components
- Terminal dark theme: bg-gray-900/950, text-gray-100, border-gray-700, accent-blue-500
- Zod for runtime validation of API responses

## Testing
- pytest with asyncio_mode=auto (backend/pytest.ini)
- Vitest globals mode, node environment (dashboard/vite.config.ts)
- Test file naming: test_*.py (backend), *.test.ts (dashboard)
- Minimum: one test file per new module, test happy path + one error case
- Mock external services (yfinance, IBKR) — never hit real APIs in tests

## API Conventions
- All endpoints under /api/ prefix
- RESTful: GET reads, POST creates, PUT updates, DELETE deletes
- Pagination: ?page=1&per_page=50 (default 50, max 200)
- Error response: `{error: "ErrorType", detail: "Human-readable message"}`
- Success: return data directly (no wrapper object)
- Dates: ISO 8601 strings in API, Unix timestamps in lightweight-charts data

## Git
- Never commit: .env, *.db, node_modules/, __pycache__/
- Do commit: dashboard/dist/ (for deployment), requirements.txt, package.json
