"""Event system routes — /api/events/*"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/metrics")
async def get_event_metrics():
    """Live metrics from the event system."""
    from bot_runner import event_bus, event_logger, metrics
    return {
        "event_bus": {"total_events": event_bus.event_count, "handlers": event_bus.handler_count()},
        "event_logger": {"events_logged": event_logger.event_count, "log_file": str(event_logger.log_path)},
        "metrics": metrics.summary(),
    }


@router.get("/log")
async def get_event_log(last_n: int = 50):
    """Recent events from the JSONL log."""
    from bot_runner import event_logger
    from event_logger import EventLogger
    events = EventLogger.replay(event_logger.log_path)
    return {"events": events[-last_n:], "total": len(events)}


@router.get("/sessions")
async def get_event_sessions():
    """List all event log sessions."""
    from event_logger import EventLogger
    return EventLogger.list_sessions()
