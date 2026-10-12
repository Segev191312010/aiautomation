"""Repository-backed shadow-run execution with lease and replay safety."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
import json
from typing import Any

import aiosqlite

from db.core import get_db, transaction
from trading.contracts import (
    Candidate,
    Decision,
    EngineMode,
    PortfolioSnapshot,
    RiskPolicy,
    Run,
    ScannerRun,
    StrategyRecord,
)
from trading.data import (
    CandidateRepository,
    DuplicateRecordError,
    RunRepository,
    ScannerRunRepository,
)
from trading.orchestration.pipeline import CandidateInput, ShadowPipeline
from trading.scheduler import (
    DEFAULT_LEASE_RENEW_INTERVAL,
    DEFAULT_LEASE_TTL,
    LeaseClaim,
    LeaseHeartbeat,
    LeaseLostError,
    acquire_lease,
    market_session,
    release_lease,
)
from trading.storage.repository import record_decision


class ShadowLeaseUnavailableError(RuntimeError):
    """Raised when another scheduler holder owns the shadow-run lease."""


class ShadowPersistenceConflictError(RuntimeError):
    """Raised when immutable run, scanner, candidate, or decision data differs."""


def _utc(value: datetime | None = None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("shadow runner timestamps must be timezone-aware")
    return observed.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_name(run: Run) -> str:
    return f"shadow:{run.strategy_id}:{run.strategy_version}:{run.session_date.isoformat()}"


def _same_run(expected: Run, stored: Run) -> bool:
    return (
        expected.id == stored.id
        and expected.owner_id == stored.owner_id
        and expected.source_sha == stored.source_sha
        and expected.strategy_id == stored.strategy_id
        and expected.strategy_version == stored.strategy_version
        and expected.session_date == stored.session_date
        and expected.mode == stored.mode
        and expected.policy_fingerprints == stored.policy_fingerprints
    )


def _same_scanner(expected: ScannerRun, stored: ScannerRun) -> bool:
    return (
        expected.id == stored.id
        and expected.run_id == stored.run_id
        and expected.parameters == stored.parameters
        and expected.parameters_fingerprint == stored.parameters_fingerprint
    )


async def _mark_run_status(
    *,
    run_id: str,
    owner_id: str,
    status: str,
    allowed_statuses: tuple[str, ...],
    closed_at: datetime | None = None,
) -> None:
    if status not in {"running", "completed", "failed"}:
        raise ValueError("unsupported shadow run status")
    placeholders = ",".join("?" for _ in allowed_statuses)
    values: list[Any] = [status]
    values.append(_utc(closed_at).isoformat() if closed_at is not None else None)
    values.extend((run_id, owner_id, *allowed_statuses))
    async with transaction() as db:
        cursor = await db.execute(
            f"""
            UPDATE autotrader_runs
            SET status=?, closed_at=COALESCE(?, closed_at)
            WHERE id=? AND owner_id=? AND status IN ({placeholders})
            """,
            values,
        )
        if cursor.rowcount != 1:
            raise ShadowPersistenceConflictError("shadow run status transition conflict")


async def _record_decision_idempotent(*, decision: Decision, owner_id: str) -> None:
    """Persist once, accepting only an exact duplicate on a retry race."""
    try:
        await record_decision(decision=decision, owner_id=owner_id)
        return
    except aiosqlite.IntegrityError as exc:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT payload_json
                FROM autotrader_candidate_decisions
                WHERE decision_key=?
                """,
                (decision.decision_key,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise
        try:
            stored = Decision.model_validate(json.loads(str(row[0])))
        except (TypeError, ValueError, json.JSONDecodeError) as parse_error:
            raise ShadowPersistenceConflictError(
                "existing decision payload is invalid"
            ) from parse_error
        if (
            stored.run_id != decision.run_id
            or stored.candidate_id != decision.candidate_id
            or stored.strategy_id != decision.strategy_id
            or stored.strategy_version != decision.strategy_version
            or stored.decision_key != decision.decision_key
        ):
            raise ShadowPersistenceConflictError("decision key collision has different ownership")
        return


class RepositoryBackedShadowRunner:
    """Materialize and execute a shadow run without broker or authority effects."""

    def __init__(
        self,
        *,
        pipeline: ShadowPipeline,
        run_repository: RunRepository | None = None,
        scanner_repository: ScannerRunRepository | None = None,
        candidate_repository: CandidateRepository | None = None,
        lease_ttl: timedelta = DEFAULT_LEASE_TTL,
        lease_renew_interval: timedelta = DEFAULT_LEASE_RENEW_INTERVAL,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        if lease_renew_interval <= timedelta(0) or lease_renew_interval >= lease_ttl:
            raise ValueError(
                "lease_renew_interval must be positive and shorter than lease_ttl"
            )
        _utc(clock())
        self._pipeline = pipeline
        self._runs = run_repository or RunRepository()
        self._scanners = scanner_repository or ScannerRunRepository()
        self._candidates = candidate_repository or CandidateRepository()
        self._lease_ttl = lease_ttl
        self._lease_renew_interval = lease_renew_interval
        self._clock = clock

    async def execute(
        self,
        *,
        run: Run,
        scanner_run: ScannerRun,
        strategy: StrategyRecord,
        candidates: Sequence[CandidateInput],
        portfolio: PortfolioSnapshot,
        policy: RiskPolicy,
        holder_id: str,
        now: datetime | None = None,
        lease_name: str | None = None,
    ) -> tuple[Decision, ...]:
        """Run candidates after durable materialization and lease acquisition."""
        observed_at = _utc(now or self._clock())
        self._validate_inputs(
            run=run,
            scanner_run=scanner_run,
            strategy=strategy,
            candidates=candidates,
            policy=policy,
            holder_id=holder_id,
            lease_name=lease_name,
        )
        name = lease_name or _lease_name(run)
        async with get_db() as lease_db:
            claim = await acquire_lease(
                lease_db,
                name=name,
                holder_id=holder_id,
                ttl=self._lease_ttl,
                now=observed_at,
            )
            if claim is None:
                raise ShadowLeaseUnavailableError(f"shadow lease is busy: {name}")
            try:
                async with LeaseHeartbeat(
                    lease_db,
                    claim=claim,
                    ttl=self._lease_ttl,
                    renew_interval=self._lease_renew_interval,
                    clock=self._clock,
                ) as heartbeat:
                    await self._materialize(
                        run=run,
                        scanner_run=scanner_run,
                        candidates=tuple(item.candidate for item in candidates),
                        observed_at=observed_at,
                    )
                    stored_decisions = await self._existing_decisions(
                        run=run,
                        candidates=candidates,
                    )
                    already_completed = await self._set_running(
                        run=run,
                        stored_status=stored_decisions,
                        candidate_count=len(candidates),
                    )

                    async def guarded_recorder(*, decision: Decision, owner_id: str) -> None:
                        await heartbeat.require(now=_utc(self._clock()))
                        await _record_decision_idempotent(
                            decision=decision, owner_id=owner_id
                        )

                    pipeline = ShadowPipeline(
                        proposal_provider=self._pipeline.proposal_provider,
                        decision_recorder=guarded_recorder,
                        orb_config=self._pipeline.orb_config,
                    )
                    decisions = await pipeline.run(
                        run_id=run.id,
                        strategy=strategy,
                        candidates=candidates,
                        portfolio=portfolio,
                        policy=policy,
                        session_date=run.session_date,
                        now=observed_at,
                        existing_decisions=stored_decisions,
                    )
                    await heartbeat.require(now=_utc(self._clock()))
                    complete = self._is_complete(
                        candidates=candidates, decisions=decisions
                    )
                    session = market_session(run.session_date)
                    if session is None:
                        raise ShadowPersistenceConflictError(
                            "shadow run session date is not an XNYS trading session"
                        )
                    cutoff_reached = observed_at >= session.entry_cutoff_at.astimezone(
                        timezone.utc
                    )
                    if cutoff_reached and not complete:
                        self._assert_complete(candidates=candidates, decisions=decisions)
                    if complete and not already_completed:
                        await _mark_run_status(
                            run_id=run.id,
                            owner_id=run.owner_id,
                            status="completed",
                            allowed_statuses=("running",),
                            closed_at=_utc(self._clock()),
                        )
                    return decisions
            except BaseException:
                try:
                    heartbeat_claim = claim
                    if "heartbeat" in locals():
                        heartbeat_claim = heartbeat.claim
                    from trading.scheduler import require_lease

                    await require_lease(
                        lease_db,
                        claim=heartbeat_claim,
                        now=_utc(self._clock()),
                    )
                except LeaseLostError:
                    pass
                else:
                    try:
                        await _mark_run_status(
                            run_id=run.id,
                            owner_id=run.owner_id,
                            status="failed",
                            allowed_statuses=("pending", "running"),
                            closed_at=_utc(self._clock()),
                        )
                    except ShadowPersistenceConflictError:
                        pass
                raise
            finally:
                release_claim = claim
                if "heartbeat" in locals():
                    release_claim = heartbeat.claim
                await release_lease(
                    lease_db, claim=release_claim, now=_utc(self._clock())
                )

    async def _materialize(
        self,
        *,
        run: Run,
        scanner_run: ScannerRun,
        candidates: tuple[Candidate, ...],
        observed_at: datetime,
    ) -> None:
        stored_run = await self._runs.get(run_id=run.id, owner_id=run.owner_id)
        if stored_run is None:
            initial = run.model_copy(
                update={
                    "status": "running",
                    "started_at": run.started_at or observed_at,
                    "closed_at": None,
                }
            )
            try:
                await self._runs.save(initial)
            except DuplicateRecordError as exc:
                raise ShadowPersistenceConflictError("shadow run was concurrently created") from exc
        elif not _same_run(run, stored_run):
            raise ShadowPersistenceConflictError("stored run differs from immutable request")

        stored_scanner = await self._scanners.get(scanner_run.id)
        if stored_scanner is None:
            try:
                await self._scanners.save(scanner_run)
            except DuplicateRecordError as exc:
                raise ShadowPersistenceConflictError(
                    "scanner run was concurrently created"
                ) from exc
        elif not _same_scanner(scanner_run, stored_scanner):
            raise ShadowPersistenceConflictError("stored scanner run differs from request")

        stored_candidates = {
            candidate.id: candidate
            for candidate in await self._candidates.list_for_scanner(scanner_run.id)
        }
        for candidate in candidates:
            stored = stored_candidates.get(candidate.id)
            if stored is None:
                try:
                    await self._candidates.save(candidate)
                except DuplicateRecordError as exc:
                    raise ShadowPersistenceConflictError(
                        "candidate was concurrently created"
                    ) from exc
            elif stored != candidate:
                raise ShadowPersistenceConflictError(
                    f"stored candidate differs from request: {candidate.id}"
                )

    async def _existing_decisions(
        self,
        *,
        run: Run,
        candidates: Sequence[CandidateInput],
    ) -> dict[str, Decision]:
        from trading.storage.repository import list_decisions

        payloads = await list_decisions(owner_id=run.owner_id, run_id=run.id, limit=500)
        requested = {item.candidate.id for item in candidates}
        result: dict[str, Decision] = {}
        for payload in payloads:
            try:
                decision = Decision.model_validate(payload)
            except (TypeError, ValueError) as exc:
                raise ShadowPersistenceConflictError(
                    "stored decision payload is invalid"
                ) from exc
            if decision.candidate_id not in requested:
                continue
            if decision.candidate_id in result:
                raise ShadowPersistenceConflictError("multiple decisions exist for a candidate")
            result[decision.candidate_id] = decision
        return result

    async def _set_running(
        self,
        *,
        run: Run,
        stored_status: dict[str, Decision],
        candidate_count: int,
    ) -> bool:
        stored = await self._runs.get(run_id=run.id, owner_id=run.owner_id)
        if stored is None:
            raise ShadowPersistenceConflictError("shadow run disappeared")
        if stored.status == "completed":
            if len(stored_status) != candidate_count:
                raise ShadowPersistenceConflictError(
                    "completed shadow run is missing terminal candidate decisions"
                )
            return True
        if stored.status == "running":
            return False
        await _mark_run_status(
            run_id=run.id,
            owner_id=run.owner_id,
            status="running",
            allowed_statuses=("pending", "failed"),
        )
        return False

    @staticmethod
    def _assert_complete(
        *,
        candidates: Sequence[CandidateInput],
        decisions: Sequence[Decision],
    ) -> None:
        expected = {item.candidate.id for item in candidates}
        actual = {decision.candidate_id for decision in decisions}
        if actual != expected or len(decisions) != len(expected):
            raise ShadowPersistenceConflictError(
                "shadow pipeline did not produce one terminal decision per candidate"
            )

    @staticmethod
    def _is_complete(
        *,
        candidates: Sequence[CandidateInput],
        decisions: Sequence[Decision],
    ) -> bool:
        expected = {item.candidate.id for item in candidates}
        actual = {decision.candidate_id for decision in decisions}
        return actual == expected and len(decisions) == len(expected)

    @staticmethod
    def _validate_inputs(
        *,
        run: Run,
        scanner_run: ScannerRun,
        strategy: StrategyRecord,
        candidates: Sequence[CandidateInput],
        policy: RiskPolicy,
        holder_id: str,
        lease_name: str | None,
    ) -> None:
        del policy
        if run.mode != EngineMode.SHADOW:
            raise ValueError("repository-backed runner only supports shadow runs")
        if run.owner_id != strategy.owner_id:
            raise ValueError("run owner does not own the strategy")
        if run.strategy_id != strategy.strategy_id or run.strategy_version != strategy.version:
            raise ValueError("run strategy does not match the immutable strategy record")
        if scanner_run.run_id != run.id:
            raise ValueError("scanner run does not belong to run")
        if not holder_id.strip():
            raise ValueError("holder_id must be non-empty")
        if lease_name is not None and not lease_name.strip():
            raise ValueError("lease_name must be non-empty")
        candidate_ids: set[str] = set()
        for item in candidates:
            if item.candidate.id in candidate_ids:
                raise ValueError("a shadow run cannot assess one candidate more than once")
            candidate_ids.add(item.candidate.id)
            if item.candidate.scanner_run_id != scanner_run.id:
                raise ValueError("candidate does not belong to scanner run")


__all__ = [
    "RepositoryBackedShadowRunner",
    "ShadowLeaseUnavailableError",
    "ShadowPersistenceConflictError",
]
