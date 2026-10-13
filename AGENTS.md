# Autotrader V1 — Engineering Rules

## Product Boundary
- `backend/trading/` is the trading-domain implementation. The temporary PAPER
  order sender `backend/scripts_local/paper_bridge.py` is the ONLY process that
  sends orders (TWS client 18) until the reviewed execution engine replaces it.
- PAPER order authority (owner, 2026-10-12): MOMO_HOD strategies and owner manual
  orders from the pinned Telegram chat. ORB_V1, VWAP_PB_V1 and GAP_GO_V1 are
  shadow-only. LIVE has no path. See `plans/AUTOTRADER_V1_SPEC.md`.
- No environment variable may enable PAPER or LIVE execution.
- AI models may only PROPOSE/ABSTAIN on deterministic setups or answer questions
  with read-only tools; orders come only from deterministic code.
- Every change to the order sender needs an independent execution-safety
  review before it runs unattended.

## Execution Engine (C2): built, not wired
- `backend/trading/execution/live_executor.py` is the execution engine (not yet
  independently reviewed) meant to replace the bridge. It is NOT wired: no
  supervisor child, LaunchAgent or CLI command runs it, and it must not be
  wired until it has passed an
  independent execution-safety review and a supervised drill on the paper
  account, and the owner explicitly decides to switch the order sender. Only
  one sender may ever run: the bridge stays the only one until the switch, and
  the bridge is retired after the engine's first clean paper week.
- Its PAPER authority comes only from an append-only promotion record in the
  ledger (`autotrader_exec_promotions`, migration 13), written by
  `autotraderctl execution promote --confirm` for a `MOMO_HOD` strategy and a DU
  account. A grant binds the SHA-256 of the engine's source files
  (`trading/execution/promotion.py`), so any change to them needs a new grant
  after its review. `LiveExecutor.start()` refuses to run without one.
  Configuration can only switch entries off; no setting or environment variable
  can grant authority. `revoke` is always allowed.
- Engine invariants live in code and in migration-13 triggers: the outbox is
  written before any order, a crash never resends, orderRefs are
  `at1:<bracket>:<leg>`, fills are keyed by execId, the shared risk book
  (`trading/risk/book.py`) is re-checked inside the staging transaction, and a
  reconciliation mismatch pauses entries but never exits. Do not weaken a
  trigger to make a test pass; change the engine instead.
- The old transport stack (`ibkr_paper.py`, `tws_paper_transport.py`, `ports.py`,
  `reconciliation.py` and their ledgers) stays until governance retires it after
  the clean paper week.

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
- Do not edit `.env`, `*.db`, or `docs/archive/` without explicit owner instruction.
- Do not add LIVE execution, broker credentials, or model authority without explicit human approval.
- Never commit personal identifiers (account numbers, chat ids, tokens); the
  repository is public. Tests use fake ids such as `DU1234567`.
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
