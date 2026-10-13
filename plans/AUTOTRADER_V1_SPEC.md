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
  allowlisted IBKR scans.
- Bot entries: limit orders only, as one native bracket (parent DAY limit, GTC
  target and GTC stop in one OCA group) so protection survives an engine crash.
  Engine-driven exits are session-end safety actions only: unfilled entries
  are cancelled at the strategy cutoff (or after a 10-minute TTL), and bot
  positions are flattened 15 minutes before the session close (half-day aware)
  by repricing the existing target order — never a second sell order.
- Pilot limits (bot): at most three open bot positions across all strategies,
  one per symbol, at most 0.5% of net liquidation at risk per trade, per-order
  caps of 25% notional and 1% risk, and a 2% daily-loss circuit breaker.
- Market regime: the screener classifies risk_on / neutral / risk_off from
  index ETFs and watchlist breadth; momentum entries pause while risk_off.
- Engine modes: `backtest`, `shadow`, and `paper`.
- Release authority (owner decisions 2026-10-12): PAPER order submission is
  allowed only for strategies whose catalogue name starts with `MOMO_HOD`
  (currently MOMO_HOD_V2). ORB_V1, VWAP_PB_V1 and GAP_GO_V1 are shadow-only.
  The PAPER account is a sandbox: its gate proves mechanical safety, not
  strategy profitability. Every change to the order sender needs an
  independent execution-safety review before it runs unattended.
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
  universe limits.
- The lifecycle service owns optimistic, version-bound status transitions.
- PAPER submission authority is code-owned (the order sender's strategy-name
  gate) and was granted by the owner on 2026-10-12 for MOMO_HOD strategies and
  for owner manual orders. Environment variables cannot enable it.
- The PAPER account must be an IBKR paper account (`DU` prefix), and every
  account TWS reports as managed must also be a paper account.
- The execution adapter owns order submission only after lifecycle and risk
  checks approve the exact intent.

## Evidence Required Before Authority Increases

A shadow strategy becomes eligible for PAPER orders only after its own replay
on recorded real bars (`trading/research/`) and at least one paper day of
shadow decisions reviewed by the owner, plus an owner decision. Strategy-quality
evidence (replay, profit factor, drawdown, expectancy) is collected continuously
and is a prerequisite for any future real-money authority, which remains outside
V1. LIVE has no implementation path in this codebase.
