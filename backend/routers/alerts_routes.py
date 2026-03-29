"""Alert routes — /api/alerts/*"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from database import get_alerts, get_alert, save_alert, delete_alert, get_alert_history
from market_data import get_latest_price
from models import Alert, AlertCreate, AlertUpdate

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _alert_condition_summary(alert: Alert | AlertCreate) -> str:
    params = getattr(alert.condition, "params", {}) or {}
    params_str = ", ".join(str(value) for value in params.values())
    indicator = f"{alert.condition.indicator}({params_str})" if params_str else alert.condition.indicator
    return f"{indicator} {alert.condition.operator} {alert.condition.value}"


async def _resolve_alert_test_price(symbol: str, fallback: float | None = None) -> float:
    sym = symbol.upper()
    price = await get_latest_price(sym)
    if price is not None:
        return float(price)
    try:
        from yahoo_data import yf_quotes
        quotes = await yf_quotes(sym, source="price_fallback")
        if quotes and isinstance(quotes[0].get("price"), (int, float)):
            return float(quotes[0]["price"])
    except Exception:
        pass
    if fallback is not None:
        return float(fallback)
    return 0.0


@router.get("")
async def api_alerts_list(user=Depends(get_current_user)):
    alerts = await get_alerts(user.id)
    return [alert.model_dump() for alert in alerts]


@router.post("", status_code=201)
async def api_alerts_create(body: AlertCreate, user=Depends(get_current_user)):
    alert = Alert(
        user_id=user.id, name=body.name, symbol=body.symbol.upper(),
        condition=body.condition, alert_type=body.alert_type,
        cooldown_minutes=body.cooldown_minutes, enabled=body.enabled,
    )
    await save_alert(alert, user.id)
    return alert.model_dump()


@router.get("/history")
async def api_alerts_history(limit: int = 100, alert_id: str | None = None, user=Depends(get_current_user)):
    history = await get_alert_history(user.id, limit=limit, alert_id=alert_id)
    return [entry.model_dump() for entry in history]


@router.post("/test")
async def api_alerts_test(body: AlertCreate, user=Depends(get_current_user)):
    fallback = None
    if isinstance(body.condition.value, (int, float)):
        fallback = float(body.condition.value)
    price = await _resolve_alert_test_price(body.symbol, fallback=fallback)
    temp_alert = Alert(
        user_id=user.id, name=body.name, symbol=body.symbol.upper(),
        condition=body.condition, alert_type=body.alert_type,
        cooldown_minutes=body.cooldown_minutes, enabled=body.enabled,
    )
    summary = _alert_condition_summary(temp_alert)
    now = datetime.now(timezone.utc).isoformat()

    # Broadcast via runtime state
    from runtime_state import get_ws_manager
    mgr = get_ws_manager()
    if mgr:
        await mgr.broadcast({
            "type": "alert_fired",
            "alert_id": temp_alert.id,
            "name": temp_alert.name,
            "symbol": temp_alert.symbol,
            "condition_summary": summary,
            "price": price,
            "timestamp": now,
        })

    return {
        "alert_id": temp_alert.id,
        "symbol": temp_alert.symbol,
        "price": price,
        "triggered": True,
        "condition_summary": summary,
    }


@router.get("/{alert_id}")
async def api_alerts_get(alert_id: str, user=Depends(get_current_user)):
    alert = await get_alert(alert_id, user.id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    return alert.model_dump()


@router.put("/{alert_id}")
async def api_alerts_update(alert_id: str, body: AlertUpdate, user=Depends(get_current_user)):
    alert = await get_alert(alert_id, user.id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    patch = body.model_dump(exclude_unset=True, exclude_none=True)
    if "symbol" in patch:
        patch["symbol"] = str(patch["symbol"]).upper()
    updated = alert.model_copy(update=patch)
    await save_alert(updated, user.id)
    return updated.model_dump()


@router.delete("/{alert_id}")
async def api_alerts_delete(alert_id: str, user=Depends(get_current_user)):
    deleted = await delete_alert(alert_id, user.id)
    if not deleted:
        raise HTTPException(404, "Alert not found")
    return {"deleted": True}


@router.post("/{alert_id}/toggle")
async def api_alerts_toggle(alert_id: str, user=Depends(get_current_user)):
    alert = await get_alert(alert_id, user.id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    alert.enabled = not alert.enabled
    await save_alert(alert, user.id)
    return {"id": alert.id, "enabled": alert.enabled}
