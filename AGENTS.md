# Autotrader V1 — Engineering Rules

## Product Boundary
- `backend/trading/` is the only trading-domain implementation.
- Current authority is `backtest` and `shadow` only.
- No environment variable may enable PAPER or LIVE execution.
- AI providers may return typed proposals; deterministic code owns risk and lifecycle decisions.
- Broker execution remains a protocol boundary until evidence-backed PAPER work is approved.

## Repository Layout
- API entry point: `backend/main.py`
- Auth and startup boundary: `backend/auth.py`, `backend/config.py`, `backend/startup.py`
- Domain kernel: `backend/trading/`
- HTTP routers: `backend/routers/`
- Persistence: `backend/db/`, `backend/trading/storage/`
- Dashboard: `dashboard/src/`
- Governing specification: `plans/AUTOTRADER_V1_SPEC.md`

## Change Rules
- Remove superseded production code and its obsolete tests together.
- Do not reintroduce deleted legacy modules or compatibility exports.
- Keep one implementation per concern and one canonical application entry point.
- Do not edit `.env`, `*.db`, or `docs/paper_review_protocol.md` without explicit owner instruction.
- Do not add PAPER/LIVE execution, broker credentials, or model authority without explicit human approval.
- Do not commit, push, or rewrite Git history unless the owner asks.

## Collaboration
- For multi-agent work, partition by architecture, execution safety, and verification.
- Agents share the same worktree; assign non-overlapping files before concurrent edits.
- A reviewer reports defects before style suggestions.
- An execution-safety reviewer is mandatory before broker adapters or order submission are added.

## Quality Gates
Run after a meaningful slice and before any commit:

```bash
python3.11 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
(cd dashboard && npm ci)
SKIP_DOCKER=1 scripts/run_quality_gates.sh
```

Run Docker gates before shipping:

```bash
scripts/run_quality_gates.sh
```

Tests protect retained contracts only. Do not keep a test whose production owner was deleted.

## Code Style
- Python 3.11+, full type hints, Pydantic v2 at boundaries, async database access.
- TypeScript strict mode and functional React components.
- Comments explain safety rationale, not obvious mechanics.
- Prefer small pure functions and explicit state transitions.
- Financial values use `Decimal`; timestamps are timezone-aware.
