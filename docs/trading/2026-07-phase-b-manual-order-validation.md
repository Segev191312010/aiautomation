# Phase B Manual Order Validation Policy

Date: 2026-07-11

Manual orders are validated at the backend boundary before simulator or broker side effects.

## Accepted boundary

- Symbol: uppercase ASCII matching `[A-Z0-9][A-Z0-9.-]{0,9}`.
- Action: `BUY` or `SELL`.
- Quantity: strict integer from 1 through `MANUAL_ORDER_MAX_QUANTITY`; default maximum 10,000.
- Absolute unit notional: quantity multiplied by the validated limit or market reference price, capped by `MANUAL_ORDER_MAX_NOTIONAL`; default $100,000.
- Market orders: no limit price is accepted and a finite positive quote must be available before execution.
- Limit orders: a finite positive limit price is required.
- Asset type: `STK` only for Phase B manual orders.
- Unknown request fields are rejected.

## Fail-closed behavior

Malformed requests return validation errors. Missing market data returns `503`. Orders above the configured quantity or notional policy return `422`, and no simulator or broker call occurs.

Broker-backed manual market requests are converted to a protective limit with a
0.5% allowance and that actual limit is checked against the notional cap before
placement. A manual SELL is accepted only after the backend verifies a sufficient
long stock position; it is then classified as an exit for the safety kernel.
Short-selling through this endpoint is unsupported.

Simulation limit orders execute only when the current quote satisfies the
submitted limit. The simulator does not yet maintain resting limit orders, so a
non-marketable limit returns `409` without a fill.

Options and futures are unavailable at this boundary until contract-multiplier-aware notional, asset-specific symbol, and price validation are implemented. This avoids understating derivative exposure.

The same request/reference cap applies to buys and verified position-reducing
sells. Any future reduction exception requires a separately approved policy.
