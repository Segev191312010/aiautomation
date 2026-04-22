"""Router registration — includes all extracted domain routers into the FastAPI app."""
from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    """Register all extracted domain routers."""
    # Batch A — zero shared state
    from routers.auth import router as auth_router
    from routers.settings_routes import router as settings_router
    from routers.backtest_routes import router as backtest_router
    from routers.sectors import router as sectors_router
    from routers.events import router as events_router

    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(backtest_router)
    app.include_router(sectors_router)
    app.include_router(events_router)

    # Batch B — light DB state + shared screener cache
    from routers.rules_routes import router as rules_router
    from routers.bot_routes import router as bot_router
    from routers.orders import router as orders_router
    from routers.screener_routes import router as screener_router
    from routers.alerts_routes import router as alerts_router
    from routers.swing_routes import router as swing_router

    app.include_router(rules_router)
    app.include_router(bot_router)
    app.include_router(orders_router)
    app.include_router(screener_router)
    app.include_router(alerts_router)
    app.include_router(swing_router)

    # Batch C — IBKR/broker state
    from routers.status import router as status_router
    from routers.positions import router as positions_router

    app.include_router(status_router)
    app.include_router(positions_router)

    # Batch D — Simulation
    from routers.simulation_routes import router as simulation_router

    app.include_router(simulation_router)

    # Batch E — Market data (HTTP routes only; WebSocket stays in main.py)
    from routers.market_routes import router as market_router

    app.include_router(market_router)
