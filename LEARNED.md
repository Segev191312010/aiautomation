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
