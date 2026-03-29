# Stage 0 Exception Hotspots

Date: 2026-03-27
Purpose: inventory broad exception handling and silent degradation sites before Stage 1-3 hardening work.

## Severity Tags

- CRIT_TRADING: can directly weaken trade validation, sizing, concentration, or execution safety
- CRIT_IO: can hide broker, market-data, websocket, or external feed failures
- MEDIUM: affects status, AI telemetry, or operator visibility but is less likely to place a bad trade directly
- LOW: safe to defer after the critical paths are hardened

## Summary Counts

- CRIT_TRADING: 13 hotspots
- CRIT_IO: 15 hotspots
- MEDIUM: about 17 hotspots
- LOW: about 10 hotspots

These counts came from the Stage 0 sweep across `backend/main.py`, `backend/ai_optimizer.py`, `backend/ai_guardrails.py`, and `backend/bot_runner.py`.

## CRIT_TRADING

- `backend/bot_runner.py:380` - `_prescreen_symbols()` per-symbol multi-index parsing swallows exceptions and keeps going. Bad rows can skip liquidity validation and leave symbol screening under-signaled.
- `backend/bot_runner.py:389` - `_prescreen_symbols()` single-symbol parsing swallows exceptions and silently drops bad data. This can let the pre-screen return incomplete or misleading candidate sets.
- `backend/bot_runner.py:740` - account summary fetch failure zeroes out `cycle_net_liq` during concentration checks. That weakens net-liq-aware exposure limits for the rest of the cycle.
- `backend/bot_runner.py:757` - pending BUY order collection is wrapped in a silent `except Exception: pass`. Concentration checks can miss queued exposure and understate total position risk.
- `backend/bot_runner.py:816` - portfolio impact checks are skipped on exception for direct AI candidates. That can let direct trades continue without the intended concentration gate.
- `backend/bot_runner.py:930` - open-position fetch failures are swallowed before `check_trade_risk(...)`. Risk evaluation can run against an empty position set.
- `backend/bot_runner.py:940` - the outer trade-risk block logs a debug line and skips the risk gate entirely on exception. That is a direct trading-safety bypass.
- `backend/bot_runner.py:971` - pricing and sizing failures during dynamic quantity calculation fall into exception handling that keeps the cycle alive. This needs explicit fail-closed behavior later.
- `backend/bot_runner.py:1003` - bracket order planning continues after downstream exceptions with only log coverage. That can desync sizing from the intended execution envelope.
- `backend/bot_runner.py:1021` - order placement cleanup still treats some execution-path exceptions as recoverable without a strong fail-closed signal.
- `backend/bot_runner.py:1198` - exit-management exception handling can suppress operator visibility while position state changes continue.
- `backend/bot_runner.py:1294` - cycle-level exception recovery is broad enough to keep the bot alive after trade-path errors without classifying what was skipped.
- `backend/bot_runner.py:1405` - end-of-cycle cleanup and telemetry can fail without a structured degraded-state handoff.

## CRIT_IO

- `backend/main.py:684-685` - crypto quote fetch swallows exceptions and immediately falls through to secondary paths without surfacing the failure.
- `backend/main.py:699-700` - Coinbase spot fallback returns `None` on any exception. Downstream consumers cannot distinguish transport failure from missing data.
- `backend/main.py:739` - websocket broadcast paths catch broad exceptions while keeping the loop alive. Clients can silently miss updates.
- `backend/main.py:823` - per-client websocket send failures are swallowed during broadcast loops.
- `backend/main.py:920` - downstream route/helper broadcast failures are silently ignored.
- `backend/main.py:930` - cleanup logic swallows websocket close/send exceptions and reduces observability.
- `backend/main.py:955-956` - helper shutdown paths pass through broad exceptions with little context.
- `backend/main.py:999` - broad route/helper exception handling hides some degraded-state transitions.
- `backend/main.py:1141-1142` - fallback cleanup swallows exceptions during runtime fanout.
- `backend/main.py:1351-1352` - background task cleanup ignores send/close failures.
- `backend/main.py:1740-1741` - websocket/client teardown swallows exceptions.
- `backend/main.py:2072-2073` - another cleanup/fanout path uses silent pass.
- `backend/ai_guardrails.py:733` - AI status fetch can drop to partial data on broad exception.
- `backend/ai_guardrails.py:757-758` - bot health hydration returns `None` on exception, hiding why health data is absent.
- `backend/ai_optimizer.py:711-712` - ledger recording failures are treated as non-fatal and only logged, which is acceptable temporarily but still an I/O integrity gap.

## MEDIUM

- `backend/ai_optimizer.py:715-722` - decision runs can complete while ledger finalization silently fails. This weakens traceability and post-trade evaluation, but it does not directly bypass the trading gate itself.
- `backend/ai_guardrails.py:747-748` - AI status aggregation swallows database failures for open positions and active rules. This can mislead operators, but it is a telemetry/surface issue rather than a direct execution-safety bypass.
- `backend/ai_guardrails.py:40` - metric helper import/database path logs and continues.
- `backend/ai_guardrails.py:68` - similar non-fatal read path in guardrail telemetry.
- `backend/ai_guardrails.py:323` - broad exception inside audit helper path.
- `backend/ai_guardrails.py:599` - replay/status helper catches wide exceptions.
- `backend/ai_guardrails.py:669` - item-link read path degrades to logging.
- `backend/ai_optimizer.py:50` - import/boot helper exception path is broad.
- `backend/ai_optimizer.py:305-307` - context/helper fetch returns `None` on exception.
- `backend/ai_optimizer.py:586` - guardrail apply loop catches broad exceptions.
- `backend/ai_optimizer.py:620` - optimizer context build exception handling is broad.
- `backend/ai_optimizer.py:664` - config extraction degrades on exception.
- `backend/ai_optimizer.py:733` - optimization loop outer exception is broad.
- `backend/ai_optimizer.py:753` - background optimization wrapper is broad.
- `backend/ai_optimizer.py:786` - recurring task wrapper catches broad exceptions.
- `backend/main.py:237` - startup helper catches broad exceptions and logs.
- `backend/main.py:596` - helper route/fetch path catches broad exceptions and continues.

## LOW

- `backend/bot_runner.py:291-302` - logging/telemetry cleanup paths swallow exceptions.
- `backend/bot_runner.py:432` - cache cleanup pass.
- `backend/bot_runner.py:529-530` - IBKR connection probe fallback.
- `backend/bot_runner.py:580-581` - secondary monitoring/logging path.
- `backend/bot_runner.py:1093-1094` - notification-only swallow after trade event. This is visibility debt, not a trading-gate bypass.
- `backend/main.py:172` - optional startup cleanup pass.
- `backend/main.py:682` - timestamp parsing fallback when quote payload time is malformed.
- `backend/main.py:1767` - best-effort helper cleanup.
- `backend/ai_guardrails.py:712-713` - non-critical helper returns `None` on failed read.
- `backend/ai_optimizer.py:241` and `304` - soft `None` fallbacks for non-critical helper lookups.

## Stage Consumers

- Stage 1 will consume the monolith and router/repository hotspots from `main.py` and `database.py`.
- Stage 2 will consume the AI optimizer and guardrail telemetry hotspots.
- Stage 3 will consume the `bot_runner.py` trading-path hotspots and convert the most dangerous ones to fail-closed behavior.

