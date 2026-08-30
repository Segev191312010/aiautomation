# Autotrader V1 — Style and Conventions

## Principles
- Clarity over cleverness.
- Deterministic decisions over hidden side effects.
- Fail closed at data, risk, authority, and execution boundaries.
- One module owns each concern.

## Python
- Python 3.11+, complete type hints, Pydantic v2 boundary models.
- `Decimal` for prices, quantities, risk, and PnL.
- Timezone-aware timestamps only.
- `aiosqlite` for runtime persistence.
- External providers implement narrow protocols and are mocked in tests.

## Dashboard
- Strict TypeScript and functional React components.
- Keep the status shell dependency-light until a real product workflow is implemented.
- Never display controls for authority the backend does not expose.

## Safety
- AI proposes; deterministic code decides.
- The broker is the execution and position source of truth.
- Unknown broker state requires reconciliation, never blind retry.
- PAPER and LIVE gates are release events, not operator toggles.
