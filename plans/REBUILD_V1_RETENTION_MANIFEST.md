# Autotrader V1 Retention Manifest

## Canonical Source
- `backend/main.py`
- `backend/auth.py`, `backend/config.py`, `backend/startup.py`, `backend/middleware.py`
- `backend/db/`
- `backend/routers/auth.py`, `backend/routers/autotrader_routes.py`
- `backend/trading/`
- `dashboard/src/App.tsx`, `dashboard/src/main.tsx`, `dashboard/src/index.css`
- package manifests, lockfiles, Docker/Compose definitions, and quality scripts

## Contract Tests
- authentication and startup validation
- entry-point route boundary
- strategy lifecycle and authority
- scanner, deterministic risk, and conservative backtest behavior
- repository persistence and owner isolation through V1 routes
- shadow workflow decision recording

## Preserved Local Artifacts
- `.env` files and SQLite databases remain outside source control.
- `docs/paper_review_protocol.md` remains untouched because it contains owner changes; it is not a current V1 contract.
- Removed dirty worktrees are recoverable from `/Users/salomon/aiautomation-rebuild-freeze-20260826`.

## Authority
- Backtest and shadow are enabled.
- PAPER execution is absent.
- LIVE execution is absent.
- No environment variable can increase authority.
