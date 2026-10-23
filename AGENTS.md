# Autotrader V1 — Engineering Rules

## Product Boundary
- `backend/trading/` is the trading-domain implementation. The temporary PAPER
  order sender `backend/scripts_local/paper_bridge.py` is the ONLY process that
  sends orders (TWS client 18) until the reviewed execution engine replaces it.
- PAPER order authority (owner, 2026-10-12; widened 2026-10-14 18:38 and
  2026-10-15 01:30): exactly the sendable identities MOMO_HOD_V2 (0.5%/15%,
  up to 3 positions), ORB_V2 (quarter size: 0.125%/3.75%, 1 position, 1 entry
  a day), VWAP_PB_V2 and GAP_GO_V2 (half size: 0.25%/7.5%, 1 position, 2 and
  1 entries a day), sized and limited as in 'NO_LIMIT_TEST' below while it
  runs, plus owner manual orders from the pinned Telegram chat.
  The three new identities send only from bridge v7b, after its independent
  review and deploy (planned 2026-10-19) and one observed v7a session; until
  then only MOMO_HOD_V2 sends. Every other name is shadow-only: ORB_V1,
  VWAP_PB_V1 and GAP_GO_V1, the managed AP_MOMO_HOD_V1 and every SH_ research
  identity. Authority is the bridge's exact-match allowlist, pinned equal to
  `trading/sendable_identities.SENDABLE_IDENTITIES`. The `manager` child
  (`autotraderctl manager run`) plans the day for the shadow strategies: it
  opens no TWS connection and has no order authority. The Telegram
  `/autopilot on|off|status` switch is shadow-only in phase 2: it latches the
  manager's plans for AP_MOMO_HOD_V1 and never touches real MOMO. LIVE has no
  path. See `plans/AUTOTRADER_V1_SPEC.md`.
- NO_LIMIT_TEST (paper only, sessions of 2–13 Nov 2026). With the constant
  set, MOMO, ORB, VWAP and GAP run under the test risk policy: per-run,
  per-strategy, total-open, −2% day-loss and 3% risk-budget limits are
  computed and logged on every signal and do not block; sizes follow the
  Step 0 rule; the AI review cap is 100 reviews / $25 per run; the Sentinel
  logs instead of halting. The per-position stop rule with auto-repair, one
  trade per symbol, the per-order bug guard, the malfunction guards, the
  exchange calendar, the 15:45 ET close-out and paper-only remain hard blocks.
  The constant is set in code, never by configuration. Test trades carry the
  test tag and are excluded from the size-up track record. deploy undo
  restores the reviewed v7c build.
- Schema: migrations 14 (execution-engine ledger, empty until the reviewed
  engine runs a drill), 15 (day universe, contract facts, signal outcomes) and
  16 (manager plans, verdicts, managed decisions, the autopilot switch and its
  day latch, spend) are applied in production since the deploy after the
  2026-10-13 close and frozen like 1–13 (`tests/test_migration_freeze.py`);
  schema changes go into a new migration.
- No environment variable may enable PAPER or LIVE execution.
- AI models may only PROPOSE/ABSTAIN on deterministic setups or answer questions
  with read-only tools; orders come only from deterministic code.
- AI day manager (owner, 2026-10-13): the model may also narrow. It may pick a
  focus subset of the deterministic candidates, switch running strategies off
  for the day, and scale size down by fixed steps. It may never add symbols,
  risk or orders. If it fails, managed strategies fall back to their unmanaged
  behaviour. See "AI Day Manager" in the spec. "Autopilot" is only an owner-facing
  word; no environment variable, route or config key enables the manager.
- Every change to the order sender needs an independent execution-safety
  review before it runs unattended.

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
