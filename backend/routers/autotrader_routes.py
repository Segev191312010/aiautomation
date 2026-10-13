"""Owner-scoped controls for immutable Autotrader V1 strategies."""
from __future__ import annotations

from datetime import date
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from auth import get_current_user
from trading.authority import EngineAuthority
from trading.contracts import EngineMode, StrategySpec, StrategyState
from trading.contracts import Run
from trading.data.repositories import CandidateRepository, RunRepository
from trading.sendable_identities import (
    API_RESERVED_NAME_PREFIXES,
    RESERVED_STRATEGY_NAME,
    is_api_reserved_name,
)
from trading.storage.repository import (
    create_strategy,
    get_strategy,
    list_decisions,
    list_strategies,
    list_strategy_versions,
    publish_strategy_version,
    transition_strategy,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/engine/v1",
    tags=["autotrader-v1"],
    dependencies=[Depends(get_current_user)],
)


class PublishStrategyVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version: int = Field(ge=1)
    spec: StrategySpec
    reason: str = Field(min_length=1, max_length=500)


class StrategyTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    target: StrategyState
    reason: str = Field(min_length=1, max_length=500)


class ShadowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_sha: str = Field(min_length=7, max_length=64, pattern=r"^[0-9a-f]+$")
    strategy_id: str = Field(min_length=1, max_length=200)
    strategy_version: int = Field(default=1, ge=1)
    session_date: date
    policy_fingerprints: dict[str, str] = Field(min_length=1)


_CURRENT_RELEASE_TRANSITIONS = {
    StrategyState.BACKTESTING,
    StrategyState.SHADOW,
    StrategyState.PAUSED,
    StrategyState.RETIRED,
}


def _refuse_reserved_name(spec: StrategySpec) -> None:
    """MOMO_HOD names reach the PAPER bridge and AP_ names are autopilot-managed:
    both are made only by reviewed code, never through this API."""
    if not is_api_reserved_name(spec.name):
        return
    log.warning("%s: API refused strategy name %r", RESERVED_STRATEGY_NAME, spec.name)
    raise HTTPException(
        status_code=422,
        detail={
            "code": RESERVED_STRATEGY_NAME,
            "message": "strategy names starting with "
            + " or ".join(API_RESERVED_NAME_PREFIXES)
            + " are created by reviewed code only",
        },
    )


@router.get("/status")
async def get_engine_status():
    authority = EngineAuthority()
    return {
        "engine": "autotrader-v1",
        "enabled_modes": sorted(mode.value for mode in authority.enabled_modes),
        "execution_enabled": False,
        "paper_authority": "blocked_pending_receipt",
        "live_authority": "unsupported",
    }


@router.get("/strategies")
async def get_strategies(user=Depends(get_current_user)):
    return [record.model_dump(mode="json") for record in await list_strategies(owner_id=user.id)]


@router.post("/strategies", status_code=201)
async def create_draft_strategy(spec: StrategySpec, user=Depends(get_current_user)):
    _refuse_reserved_name(spec)
    record = await create_strategy(spec=spec, owner_id=user.id, actor=user.id)
    return record.model_dump(mode="json")


@router.get("/strategies/{strategy_id}/versions")
async def get_versions(strategy_id: str, user=Depends(get_current_user)):
    records = await list_strategy_versions(strategy_id=strategy_id, owner_id=user.id)
    if not records:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return [record.model_dump(mode="json") for record in records]


@router.get("/strategies/{strategy_id}/versions/{version}")
async def get_version(strategy_id: str, version: int, user=Depends(get_current_user)):
    record = await get_strategy(strategy_id=strategy_id, owner_id=user.id, version=version)
    if record is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return record.model_dump(mode="json")


@router.post("/strategies/{strategy_id}/versions", status_code=201)
async def publish_version(
    strategy_id: str,
    request: PublishStrategyVersionRequest,
    user=Depends(get_current_user),
):
    _refuse_reserved_name(request.spec)
    try:
        record = await publish_strategy_version(
            strategy_id=strategy_id,
            owner_id=user.id,
            base_version=request.base_version,
            spec=request.spec,
            actor=user.id,
            reason=request.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Strategy not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record.model_dump(mode="json")


@router.post("/strategies/{strategy_id}/versions/{version}/transition")
async def move_strategy(
    strategy_id: str,
    version: int,
    request: StrategyTransitionRequest,
    user=Depends(get_current_user),
):
    if request.target not in _CURRENT_RELEASE_TRANSITIONS:
        raise HTTPException(
            status_code=409,
            detail="PAPER promotion requires a separate evidence-backed authority operation.",
        )
    try:
        updated = await transition_strategy(
            strategy_id=strategy_id,
            version=version,
            owner_id=user.id,
            expected_revision=request.expected_revision,
            target=request.target,
            actor=user.id,
            reason=request.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Strategy not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return updated.model_dump(mode="json")


@router.get("/strategies/{strategy_id}/decisions")
async def get_strategy_decisions(strategy_id: str, user=Depends(get_current_user)):
    record = await get_strategy(strategy_id=strategy_id, owner_id=user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return await list_decisions(strategy_id=strategy_id, owner_id=user.id)


@router.post("/runs/shadow", status_code=201)
async def create_shadow_run(request: ShadowRunRequest, user=Depends(get_current_user)):
    strategy = await get_strategy(
        strategy_id=request.strategy_id,
        owner_id=user.id,
        version=request.strategy_version,
    )
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if strategy.state != StrategyState.SHADOW:
        raise HTTPException(status_code=409, detail="shadow runs require a shadow strategy version")
    run = Run(
        owner_id=user.id,
        source_sha=request.source_sha,
        strategy_id=request.strategy_id,
        strategy_version=request.strategy_version,
        policy_fingerprints=request.policy_fingerprints,
        session_date=request.session_date,
        mode=EngineMode.SHADOW,
        status="pending",
    )
    try:
        await RunRepository().save(run)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="shadow run could not be created") from exc
    return {
        **run.model_dump(mode="json"),
        "execution_enabled": False,
        "next_step": "ingest scanner data before pipeline evaluation",
    }


@router.get("/runs")
async def get_runs(
    limit: int = Query(default=100, ge=1, le=500), user=Depends(get_current_user)
):
    return [run.model_dump(mode="json") for run in await RunRepository().list(owner_id=user.id, limit=limit)]


@router.get("/runs/{run_id}/candidates")
async def get_run_candidates(run_id: str, user=Depends(get_current_user)):
    run = await RunRepository().get(run_id=run_id, owner_id=user.id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return [candidate.model_dump(mode="json") for candidate in await CandidateRepository().list_for_run(run_id=run_id, owner_id=user.id)]


@router.get("/decisions")
async def get_decisions(
    strategy_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(get_current_user),
):
    return await list_decisions(
        owner_id=user.id, strategy_id=strategy_id, run_id=run_id, limit=limit
    )


@router.get("/evidence")
async def get_evidence(user=Depends(get_current_user)):
    # Evidence is file/SHA-bound and is not inferred from green tests or runs.
    return {"bundles": [], "status": "pending", "owner_id": user.id}
