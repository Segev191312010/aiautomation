# Autotrader V1 Specification

## Purpose

Autotrader V1 is a new, modular engine for strategies the operator defines and
AI can propose. It replaces the legacy bot as the only future path to
automated execution. The legacy bot remains disabled and is not migrated by
copying its authority into this engine.

## Initial Product Contract

- Assets: US listed common stocks only; long entries only.
- Trading session: regular US market hours only (09:30–16:00 ET). Each strategy
  has its own entry window inside it; nothing trades in extended hours.
- Universe: the owner's watchlist (TradingView export, resolved to IBKR
  contracts and qualified daily: $10–$300, ≥ $20M average dollar volume,
  common stock only) ranked by an always-on screener into a hot list, plus
  allowlisted IBKR scans. Scanner movers held for the day (same qualification
  rules) form a separate day universe. It feeds the AI day manager and the
  shadow strategies only, and it never changes the hot list a sendable
  strategy trades without a reviewed change.
- Bot entries: limit orders only, as one native bracket (parent DAY limit, GTC
  target and GTC stop in one OCA group) so protection survives an engine crash.
  Engine-driven exits are session-end safety actions only: unfilled entries
  are cancelled at the strategy cutoff (or after a 10-minute TTL), and bot
  positions are flattened 15 minutes before the session close (half-day aware)
  by repricing the existing target order — never a second sell order.
- Pilot limits (bot): at most three open bot positions across all strategies,
  one per symbol, at most 0.5% of net liquidation at risk per trade, per-order
  caps of 25% notional and 1% risk, and a 2% daily-loss circuit breaker.
- Risk target (owner decision "Paper = go wide", 2026-10-13):
  - at most 8 open bot positions;
  - 0.5% risk per trade;
  - a 4% daily stop;
  - gross exposure at most 200% of net liquidation, intraday only.

  These apply only through the reviewed execution engine that replaces the
  bridge; until then the pilot limits above bind.
- Market regime: the screener classifies risk_on / neutral / risk_off from
  index ETFs and watchlist breadth; momentum entries pause while risk_off.
- Engine modes: `backtest`, `shadow`, and `paper`.
- Release authority (owner decisions 2026-10-12, widened 2026-10-14 18:38 and
  2026-10-15 01:30): PAPER order submission is allowed only for a strategy
  whose catalogue name is exactly one of the sendable identities
  (`trading/sendable_identities.SENDABLE_IDENTITIES`, pinned equal to the
  bridge's allowlist): MOMO_HOD_V2 at the base policy (0.5%/15%, 3 positions),
  ORB_V2 at quarter size (0.125%/3.75%, 1 position, 1 entry a day), VWAP_PB_V2
  and GAP_GO_V2 at half size (0.25%/7.5%, 1 position, 2 and 1 entries a day).
  Sizes are fixed per-identity multipliers of the base policy, caps are per
  identity in the bridge, and each identity has its own fence row
  (`paper:<IDENTITY>`). The three new identities send only from bridge v7b
  (independent review, deploy planned 2026-10-19 after one observed v7a
  session). ORB_V1, VWAP_PB_V1 and GAP_GO_V1 are shadow-only (they stay
  registered for the CLI and the replay), as are the managed AP_MOMO_HOD_V1
  and every SH_ research identity.
  The PAPER account is a sandbox: its gate proves mechanical safety, not
  strategy profitability. Every change to the order sender needs an
  independent execution-safety review before it runs unattended.
- Supervised children (phase 2): the `manager` child (`autotraderctl manager
  run`) plans the day around its slots for the shadow strategies; it opens no
  TWS connection and has no order authority. The managed strategy child
  (`momo-ap`, AP_MOMO_HOD_V1) is shadow-only. The Telegram `/autopilot
  on|off|status` switch is shadow-only in phase 2: it latches the manager's
  plans for AP_MOMO_HOD_V1 for the day and never changes real MOMO.
- Schema (applied in production since the deploy after the 2026-10-13 close,
  frozen by `tests/test_migration_freeze.py`): migration 14 holds the PAPER
  execution engine's ledger (`autotrader_exec_*`, empty until the reviewed
  engine runs a supervised drill), 15 phase 1's tables (the day universe, the
  IBKR contract facts, the signal outcomes) and 16 phase 2's (plans, verdicts,
  managed decisions, the autopilot switch with its day latch, spend). Every
  database child, the `manager` included, refuses a pending migration (exit
  75); only `autotraderctl db migrate` changes the schema.
- Owner manual orders: the owner may place any order (including selling
  existing holdings) from the pinned Telegram chat, sent without confirmation.
  Orders come only from the deterministic parser and validator in
  `trading/execution/manual_orders.py` (never from an AI model) and are sent by
  the single order sender. They never short, never touch shares held by a bot
  bracket except through `close`, and are refused outside regular hours, when
  stale, or when the order sender is offline.
- LIVE has no V1 runtime type, transition, approval, route, or configuration.

## Strategy Lifecycle

`draft` → `backtesting` → `shadow` → `paper` is the complete V1 forward
promotion path. `paused` and `retired` are terminal for execution until an
explicit review creates a new strategy version.

Each strategy version has immutable canonical content and provenance. An AI
review can only `PROPOSE` or `ABSTAIN`; it cannot set order fields, risk limits,
authority, or submit an order. Risk owns quantity and the final immutable trade
intent.

## Data and Decision Contract

Every engine decision records the strategy ID and version, mode, candidate,
setup fingerprint, AI review, deterministic risk decision, and final terminal
outcome. No missing record is a valid outcome.

The broker remains the execution and position source of truth. Execution
adapters must submit IBKR's staged bracket transmit sequence, reconcile broker
events, and never retry or repair an unknown order state automatically.

## Authority Boundaries

- AI may review an immutable deterministic setup only. The V1 reviewer is the
  Anthropic Claude API; any failure, refusal, or budget exhaustion is ABSTAIN.
  The Telegram assistant's model answers questions with read-only tools and
  has no ability to create, change or cancel orders.
- The deterministic risk engine owns all size, exposure, loss, price, and
  universe limits. The AI day manager may only narrow inside them (see below).
- The lifecycle service owns optimistic, version-bound status transitions.
- PAPER submission authority is code-owned (the order sender's exact-match
  strategy-name gate) and was granted by the owner on 2026-10-12 for
  MOMO_HOD_V2 and owner manual orders, and on 2026-10-14/15 for ORB_V2,
  VWAP_PB_V2 and GAP_GO_V2 at reduced size. Environment variables cannot
  enable it.
- The PAPER account must be an IBKR paper account (`DU` prefix), and every
  account TWS reports as managed must also be a paper account.
- The execution adapter owns order submission only after lifecycle and risk
  checks approve the exact intent.

## AI Day Manager (owner decisions 2026-10-13)

The owner chose a "full manager" AI. Its authority only ever narrows.

**What the manager may do:**
- Pick a focus list that is a subset of the deterministic candidates.
- Switch running strategies off for the day.
- Scale size down by the fixed steps 0, 0.25, 0.5, 0.75 and 1.
- Say PROPOSE or ABSTAIN on each setup, as the reviewer does.

**What it may never do:**
- Add a symbol.
- Switch on a strategy that is not running.
- Size up.
- Set or change order fields.
- Touch orders.

**How this is enforced:**
- Every managed decision records its eligibility snapshot, plan, policy
  quantity and size steps, so the narrowing is checked per decision.
- If the manager is unavailable (no valid plan, an error, its budget spent), a
  managed strategy falls back to its unmanaged behaviour. A test proves the two
  identical, and each such decision is marked `manager_fallback_neutral`.
- The manager's on/off state is an append-only database record, set by the
  owner through the CLI or the deterministic Telegram command parser. No
  environment variable, route or config key can enable it.

**Real orders under the manager:**
- Orders still come only from deterministic code and the single order sender.
- A managed sendable identity needs:
  1. an independent review of the managed path;
  2. the owner's review of its shadow decisions against the unmanaged strategy;
  3. a supervised first session.

**Hours (owner decision "Both"):** shadow-only strategies may look for setups
for the whole session (entries 09:35–15:30 ET) to collect evidence. Sendable
strategies keep their reviewed windows.

## Evidence Required Before Authority Increases

A shadow strategy becomes eligible for PAPER orders only after its own replay
on recorded real bars (`trading/research/`) and at least one paper day of
shadow decisions reviewed by the owner, plus an owner decision.

**Paper promotion rule (owner decision "earn it by results", 2026-10-13).** The
rule is fixed before any data is seen.

The evidence must come from one evidence identity: the rules fingerprint, the
entry window and the risk policy, plus the manager version and prompt when
managed. Any change to these resets the window. Over at least 20 sessions:
- the replay has at least 100 trades;
- the live shadow setups number at least 30;
- the day-clustered bootstrap 90% lower bound of expectancy after costs is above
  zero, on both the replay and the live setups;
- the profit factor is at least 1.2;
- the replay drawdown is at most 15R.

**The steps to real orders:**
1. An independent safety review.
2. The owner's review of the shadow decisions.
3. An owner decision.
4. 5 probation sessions at half size.

Demotion is automatic and only removes risk. Promotion is never automatic. Strategy-quality
evidence (replay, profit factor, drawdown, expectancy) is collected continuously
and is a prerequisite for any future real-money authority, which remains outside
V1. LIVE has no implementation path in this codebase.
