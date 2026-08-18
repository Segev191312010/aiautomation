# Backend Scripts

DEV TOOLS - not production code.

This folder is for one-off backend utilities, migration helpers, or local inspection scripts.

## Offline screener benchmark

Run `python backend/scripts/screener_benchmark.py --symbols 20 --concurrency 3` from
the repository root. The command evaluates deterministic OHLCV fixtures only and
prints JSON containing latency, fixture size, concurrency, and result-integrity
checks. It makes zero network or broker calls; its timings must not be interpreted
as end-to-end market-data scan latency.

Rules:
- Do not import these files from production runtime modules.
- Keep temporary appenders and patch helpers out of the backend root.
- If a script becomes part of a regular workflow, promote it into a documented tool instead of leaving it ad hoc.
