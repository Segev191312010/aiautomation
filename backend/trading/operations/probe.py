"""Read-only real-TWS probe: show exactly what IBKR returns for each data path.

It verifies assumptions that unit tests cannot: scanner row shape, contract
details, bar timestamps, the daily volume unit, the real-time quote callback,
and the account download. It never places, modifies, or cancels orders.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time
from typing import Any

from trading.contracts import ShadowRuntimeConfig
from trading.market.ibkr_boundary import (
    PaperIBKRMarketData,
    liquidity_profile,
    validate_completed_trade_bars,
)
from trading.market.tws_actor import (
    PAPER_TWS_HOST,
    TWSActorConfig,
    TWSPaperActor,
    documented_line_allocation,
)
from trading.operations.scanner_probe import PROBE_CLIENT_ID, child_client_ids


def _mask(account: str) -> str:
    return account[:2] + "*" * max(0, len(account) - 5) + account[-3:] if len(account) > 5 else "***"


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _default_actor(config: TWSActorConfig) -> TWSPaperActor:
    return TWSPaperActor(config, capacity_checker=documented_line_allocation)


async def probe_tws(
    config: ShadowRuntimeConfig,
    *,
    con_id: int | None = None,
    include_ai: bool = False,
    actor_factory: Callable[[TWSActorConfig], TWSPaperActor] = _default_actor,
) -> dict[str, Any]:
    report: dict[str, Any] = {"orders": "none — this probe is read-only"}
    # Never a child's client id: a second login with it would knock that
    # child's data connection over mid-session.
    if PROBE_CLIENT_ID in child_client_ids(config.data_client_id, config.order_client_id):
        report["connection"] = {
            "ok": False,
            "client_id": PROBE_CLIENT_ID,
            "error": f"probe client id {PROBE_CLIENT_ID} collides with a configured child client id",
        }
        return report
    actor = actor_factory(
        TWSActorConfig(account=config.paper_account_id, client_id=PROBE_CLIENT_ID)
    )
    market = PaperIBKRMarketData(actor=actor, host=PAPER_TWS_HOST)
    try:
        await actor.connect()
    except Exception as exc:  # noqa: BLE001 - reported to the operator
        report["connection"] = {"ok": False, "client_id": PROBE_CLIENT_ID, "error": _error(exc)}
        return report
    try:
        report["connection"] = {
            "ok": True,
            "client_id": PROBE_CLIENT_ID,
            "managed_accounts": sorted(_mask(account) for account in actor.managed_accounts),
            "all_paper_prefixed": all(
                account.startswith("DU") for account in actor.managed_accounts
            ),
        }
        report["scanner"], first_con_id = await _probe_scanner(market)
        target = con_id or first_con_id
        if target is not None:
            report["instrument"] = await _probe_instrument(actor, target)
        report["account"] = await _probe_account(actor)
    finally:
        await actor.disconnect()
    if include_ai:
        report["claude"] = await _probe_claude(config)
    return report


async def _probe_scanner(market: PaperIBKRMarketData) -> tuple[dict[str, Any], int | None]:
    started = time.monotonic()
    try:
        snapshot = await market.scanner_snapshot(scanner_run_id="probe")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _error(exc)}, None
    rows = [
        {
            "rank": record.source.rank,
            "raw_rank": record.source.raw_rank,
            "symbol": record.source.symbol,
            "con_id": record.source.con_id,
            "primary_exchange": record.source.primary_exchange,
            "stock_type": record.source.stock_type,
            "admitted": record.admitted,
            "rejection": record.rejection_reason,
        }
        for record in snapshot.records[:8]
    ]
    first = snapshot.admitted_candidates[0].con_id if snapshot.admitted_candidates else None
    return {
        "ok": True,
        "seconds": round(time.monotonic() - started, 1),
        "raw_rows": len(snapshot.records),
        "admitted": len(snapshot.admitted_candidates),
        "rejections": dict(
            Counter(r.rejection_reason for r in snapshot.records if r.rejection_reason)
        ),
        "first_rows": rows,
    }, first


async def _probe_instrument(actor: TWSPaperActor, con_id: int) -> dict[str, Any]:
    result: dict[str, Any] = {"con_id": con_id}
    min_tick = Decimal("0.01")
    try:
        details = await actor.contract_details(con_id)
        min_tick = details.min_tick
        result["contract_details"] = {
            "symbol": details.symbol,
            "primary_exchange": details.primary_exchange,
            "stock_type": details.stock_type or "(not reported)",
            "min_tick": str(details.min_tick),
        }
    except Exception as exc:  # noqa: BLE001
        result["contract_details"] = {"error": _error(exc)}

    now = datetime.now(timezone.utc)
    try:
        batch = await actor.completed_trade_bars(con_id)
        bars = validate_completed_trade_bars(batch, now=now, max_age=timedelta(days=7))
        result["intraday_bars"] = {
            "raw_bars": len(batch.bars),
            "completed": len(bars),
            "first_start_utc": bars[0].interval_start_utc.isoformat() if bars else None,
            "last_end_utc": bars[-1].interval_end_utc.isoformat() if bars else None,
            "last_raw_volume": str(batch.bars[-1].volume) if batch.bars else None,
        }
    except Exception as exc:  # noqa: BLE001
        result["intraday_bars"] = {"error": _error(exc)}

    try:
        daily = await actor.daily_trade_bars(con_id)
        last = daily.bars[-3:]
        result["daily_bars"] = {
            "bars": len(daily.bars),
            "last_three": [
                {"date": bar.session_date.isoformat(), "close": str(bar.close), "raw_volume": str(bar.volume)}
                for bar in last
            ],
            "check": (
                "Compare raw_volume with the daily volume TWS shows for this stock. "
                "If TWS shows ~100x more, the API reports round lots and "
                "IBKR_API_VOLUME_MULTIPLIER must be 100."
            ),
        }
        for multiplier in (Decimal("1"), Decimal("100")):
            try:
                profile = liquidity_profile(
                    daily,
                    session_date=now.date() + timedelta(days=1),
                    volume_multiplier=multiplier,
                )
                result["daily_bars"][f"avg_dollar_volume_x{multiplier}"] = (
                    f"{profile.average_dollar_volume:,.0f}"
                )
            except Exception as exc:  # noqa: BLE001
                result["daily_bars"][f"avg_dollar_volume_x{multiplier}"] = _error(exc)
    except Exception as exc:  # noqa: BLE001
        result["daily_bars"] = {"error": _error(exc)}

    try:
        started = time.monotonic()
        quote = await actor.quote_snapshot(con_id, min_tick=min_tick)
        result["quote"] = {
            "ok": True,
            "bid": str(quote.bid),
            "ask": str(quote.ask),
            "spread_bps": str(round(quote.spread_bps, 2)),
            "seconds_to_resolve": round(time.monotonic() - started, 2),
        }
    except Exception as exc:  # noqa: BLE001
        result["quote"] = {
            "ok": False,
            "error": _error(exc),
            "hint": "Outside regular hours or without a real-time subscription this is expected.",
        }
    return result


async def _probe_account(actor: TWSPaperActor) -> dict[str, Any]:
    try:
        account = await actor.account_snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _error(exc)}
    return {
        "ok": True,
        "net_liquidation": f"{account.net_liquidation:,.2f}",
        "available_funds": f"{account.available_funds:,.2f}",
        "realized_day_pnl": str(account.realized_day_pnl),
        "unrealized_pnl": str(account.unrealized_pnl),
        "positions": [
            {"symbol": position.symbol, "quantity": str(position.quantity)}
            for position in account.positions
        ],
    }


async def _probe_claude(config: ShadowRuntimeConfig) -> dict[str, Any]:
    """One real review of a synthetic setup; costs about two cents."""
    from trading.contracts import RuleSetup
    from trading.intelligence.anthropic_transport import (
        AnthropicMessagesTransport,
        build_anthropic_client,
    )
    from trading.intelligence.prompts import review_prompt
    from trading.intelligence.review_provider import StructuredAIReviewProvider
    from trading.operations.keychain import read_anthropic_api_key

    setup = RuleSetup(
        run_id="probe",
        candidate_id="probe-candidate",
        strategy_id="probe",
        strategy_version=1,
        symbol="AAPL",
        opening_range_high="230.00",
        opening_range_low="227.50",
        atr="0.85",
        entry_limit="230.40",
        stop_price="229.55",
        target_price="232.10",
        bar_fingerprints=("0" * 64,),
        quote_fingerprint="0" * 64,
        detected_at=datetime.now(timezone.utc),
    )
    try:
        provider = StructuredAIReviewProvider(
            transport=AnthropicMessagesTransport(
                client=build_anthropic_client(
                    api_key=read_anthropic_api_key(), timeout_seconds=30
                ),
                system_prompt=review_prompt(config.prompt_id),
            ),
            model_id=config.model_id,
            prompt_version=config.prompt_id,
        )
        started = time.monotonic()
        review = await provider.propose_trade(setup=setup)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _error(exc)}
    return {
        "ok": review.failure_reason is None,
        "decision": review.decision,
        "confidence": str(review.confidence),
        "rationale": review.rationale,
        "failure_reason": review.failure_reason,
        "cost_usd": str(review.cost_usd),
        "seconds": round(time.monotonic() - started, 1),
    }


__all__ = ["probe_tws"]
