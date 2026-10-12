# Autotrader V1 Specification

## Purpose

Autotrader V1 is a new, modular engine for strategies the operator defines and
AI can propose. It replaces the legacy bot as the only future path to
automated execution. The legacy bot remains disabled and is not migrated by
copying its authority into this engine.

## Initial Product Contract

- Assets: US listed common stocks only; long entries only.
- Trading session: regular US market hours only.
- Entries: limit orders only.
- Protection: every accepted entry has a broker-native GTC stop and target in
  one OCA group, so protection survives an engine crash. The only engine-driven
  exits are session-end safety actions: unfilled entries are cancelled at the
  entry cutoff, and open positions are flattened 15 minutes before the session
  close by repricing the existing target order (never a second sell order).
- Moderate pilot limits: at most three open positions, at most 0.5% of net
  liquidation value at risk per trade, and a 2% daily-loss circuit breaker.
- Engine modes: `backtest`, `shadow`, and `paper`.
- Current release authority: `backtest` and `shadow`. `paper` requires the
  reviewed adapter, a passed supervised PAPER drill, and an owner-signed
  authority receipt. The PAPER account is a sandbox: its gate proves mechanical
  safety, not strategy profitability.
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
- The deterministic risk engine owns all size, exposure, loss, price, and
  universe limits.
- The lifecycle service owns optimistic, version-bound status transitions.
- PAPER activation requires a receipt bound to the build SHA, evidence digest,
  strategy and policy versions, PAPER account, data and order client IDs,
  expiry (at most 30 days), and owner approval. Environment variables alone
  cannot enable it.
- The PAPER account must be an IBKR paper account (`DU` prefix), and every
  account TWS reports as managed must also be a paper account.
- The execution adapter owns order submission only after lifecycle and risk
  checks approve the exact intent.

## Evidence Required Before Authority Increases

PAPER authority needs a SHA-bound supervised drill record proving order IDs,
permIds, orderRefs, OCA linkage, acknowledgement matching, a fill, the
session-end flatten, and restart reconciliation, plus the owner-signed receipt.
Strategy-quality evidence (replay, walk-forward folds, profit factor, drawdown)
is collected during PAPER and is a prerequisite for any future real-money
authority, which remains outside V1. LIVE has no implementation path in this
codebase.
