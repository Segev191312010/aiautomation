# Learned Rules

## Repository truth beats planning assumptions
- Inspect imports and runtime entry points before deleting or preserving a file.
- A test is retained only when it protects surviving production behavior.

## Authority belongs in code
- Environment variables configure infrastructure; they do not grant PAPER or LIVE execution authority.
- New authority requires a reviewed code change and evidence for the exact source SHA.

## Rebuilds need one path
- Do not keep parallel legacy and rebuild entry points after the replacement becomes canonical.
- Delete compatibility exports instead of letting removed architecture leak back through imports.

## The trading day follows the session calendar
- A child's planned finish comes from the session's close (half days close at 13:00), never a fixed clock time.
- A finished session is never re-entered: run-once children (recorder, replay) usually end before their windows do.
- Same-user IBKR logins elsewhere (phone app, web) take the market data: keep them logged out during the session (error 10197).
- The IB Gateway's daily restart must fall outside the session and the 16:20-18:30 ET research windows.
- Opus 5.5 always thinks: control cost with `output_config` effort, never by disabling thinking or forcing `tool_choice`.
