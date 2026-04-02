"""Screener routes — /api/screener/*"""
import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from database import get_screener_presets, save_screener_preset, delete_screener_preset
from models import ScanRequest, ScanFilter, ScreenerPreset, EnrichRequest
from screener import run_scan, list_universes, validate_timeframe, enrich_symbols

try:
    from ibkr_scanner import get_available_scans, run_scan as ibkr_run_scan, run_multi_scan, SCAN_TEMPLATES
    _IBKR_AVAILABLE = True
except ImportError:
    _IBKR_AVAILABLE = False

router = APIRouter(prefix="/api/screener", tags=["screener"])


class SavePresetRequest(BaseModel):
    name: str
    filters: list[ScanFilter]


class MultiScanRequest(BaseModel):
    scans: list[str] | None = Field(default=None, max_length=10)


@router.post("/scan")
async def screener_scan(body: ScanRequest):
    if len(body.filters) > 15:
        raise HTTPException(400, "Maximum 15 filters allowed")
    if body.symbols and len(body.symbols) > 600:
        raise HTTPException(400, "Maximum 600 symbols allowed")
    if not validate_timeframe(body.interval, body.period):
        raise HTTPException(400, f"Invalid interval/period combination: {body.interval}/{body.period}")
    response = await run_scan(body)
    return response.model_dump()


@router.get("/universes")
async def screener_universes():
    return list_universes()


@router.get("/presets")
async def screener_list_presets():
    presets = await get_screener_presets()
    return [p.model_dump() for p in presets]


@router.post("/presets", status_code=201)
async def screener_save_preset(body: SavePresetRequest):
    preset = ScreenerPreset(
        name=body.name, filters=body.filters, built_in=False,
        user_id="demo", created_at=datetime.now(timezone.utc).isoformat(),
    )
    await save_screener_preset(preset)
    return preset.model_dump()


@router.delete("/presets/{preset_id}")
async def screener_delete_preset(preset_id: str):
    if not await delete_screener_preset(preset_id):
        raise HTTPException(404, "Preset not found or is built-in")
    return {"deleted": True}


@router.post("/enrich")
async def screener_enrich(body: EnrichRequest):
    results = await enrich_symbols(body.symbols)
    return [r.model_dump() for r in results]


# ── IBKR Scanner integration ─────────────────────────────────────────────────

@router.get("/ibkr-scans")
async def screener_ibkr_available_scans():
    """List available IBKR scanner templates."""
    if not _IBKR_AVAILABLE:
        raise HTTPException(503, "IBKR scanner module not available")
    return get_available_scans()


@router.get("/ibkr-scan/{scan_name}")
async def screener_ibkr_run_scan(scan_name: str, max_results: int = 50):
    """Run an IBKR server-side scanner and return results."""
    if not _IBKR_AVAILABLE:
        raise HTTPException(503, "IBKR scanner module not available")
    if scan_name not in SCAN_TEMPLATES:
        raise HTTPException(404, f"Unknown scan template: {scan_name}")
    results = await ibkr_run_scan(scan_name, min(max_results, 100))
    return {"scan": scan_name, "results": results, "count": len(results)}


@router.post("/ibkr-multi-scan")
async def screener_ibkr_multi_scan(body: MultiScanRequest):
    """Run multiple IBKR scans concurrently."""
    if not _IBKR_AVAILABLE:
        raise HTTPException(503, "IBKR scanner module not available")
    results = await run_multi_scan(body.scans)
    return {
        name: {"results": items, "count": len(items)}
        for name, items in results.items()
    }


# ── CSV export ────────────────────────────────────────────────────────────────

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Prevent CSV formula injection by prefixing dangerous values with apostrophe."""
    s = str(value)
    if s and s[0] in _CSV_FORMULA_PREFIXES:
        return "'" + s
    return s


@router.post("/export-csv")
async def screener_export_csv(body: ScanRequest):
    """Run a scan and return results as a downloadable CSV file.

    Columns: Symbol, Price, Change%, Volume, Score, Setup, RVOL, Mom20D,
    Trend, Notes, then one column per indicator key (sorted alphabetically).
    """
    if len(body.filters) > 15:
        raise HTTPException(400, "Maximum 15 filters allowed")
    if body.symbols and len(body.symbols) > 600:
        raise HTTPException(400, "Maximum 600 symbols allowed")
    if not validate_timeframe(body.interval, body.period):
        raise HTTPException(400, f"Invalid interval/period combination: {body.interval}/{body.period}")

    # Cap result size to prevent unbounded memory allocation
    body.limit = min(body.limit, 600)

    scan_response = await run_scan(body)
    rows = scan_response.results

    # Collect all indicator keys across every result row, then sort for a
    # deterministic column order.
    all_indicator_keys: set[str] = set()
    for row in rows:
        all_indicator_keys.update(row.indicators.keys())
    indicator_cols = sorted(all_indicator_keys)

    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM for Excel auto-detection
    writer = csv.writer(output)

    # Header
    fixed_cols = ["Symbol", "Price", "Change%", "Volume", "Score", "Setup",
                  "RVOL", "Mom20D", "Trend", "Notes"]
    writer.writerow(fixed_cols + [_csv_safe(col) for col in indicator_cols])

    # Data rows — sanitize all string fields against formula injection
    for row in rows:
        writer.writerow([
            _csv_safe(row.symbol),
            f"{row.price:.2f}",
            f"{row.change_pct:.2f}",
            row.volume,
            f"{row.screener_score:.1f}",
            _csv_safe(row.setup),
            f"{row.relative_volume:.2f}",
            f"{row.momentum_20d:.2f}",
            f"{row.trend_strength:.1f}",
            _csv_safe("; ".join(row.notes)),
        ] + [f"{row.indicators.get(col, 0):.4f}" for col in indicator_cols])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=screener_results.csv"},
    )
