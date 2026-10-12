"""Repository-backed shadow-run execution with lease and replay safety."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
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
from trading.orchestration.pipeline import (
    Assessment,
    CandidateInput,
    ShadowPipeline,
    entry_cutoff_utc,
)
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
        """Assess every candidate once at ``now``; pending candidates are omitted."""
        observed_at = _utc(now or self._clock())
        async with self.open_session(
            run=run,
            scanner_run=scanner_run,
            strategy=strategy,
            candidates=tuple(item.candidate for item in candidates),
            policy=policy,
            holder_id=holder_id,
            now=observed_at,
            lease_name=lease_name,
        ) as session:
            decisions: list[Decision] = []
            for item in candidates:
                assessment = await session.assess(item, portfolio=portfolio, now=observed_at)
                if assessment.decision is not None:
                    decisions.append(assessment.decision)
            if observed_at >= session.cutoff and not session.complete:
                raise ShadowPersistenceConflictError(
                    "shadow pipeline did not produce one terminal decision per candidate"
                )
            return tuple(decisions)

    @asynccontextmanager
    async def open_session(
        self,
        *,
        run: Run,
        scanner_run: ScannerRun,
        strategy: StrategyRecord,
        candidates: Sequence[Candidate],
        policy: RiskPolicy,
        holder_id: str,
        now: datetime | None = None,
        lease_name: str | None = None,
    ) -> AsyncIterator["ShadowRunSession"]:
        """Hold the run lease for a whole session of repeated assessments.

        The scanner universe is materialized once; every later poll assesses
        only these candidates. A failure while the lease is held marks the run
        failed so a restart resumes from the recorded decisions.
        """
        observed_at = _utc(now or self._clock())
        candidate_tuple = tuple(candidates)
        self._validate_inputs(
            run=run,
            scanner_run=scanner_run,
            strategy=strategy,
            candidates=candidate_tuple,
            policy=policy,
            holder_id=holder_id,
            lease_name=lease_name,
        )
        session_calendar = market_session(run.session_date)
        if session_calendar is None:
            raise ShadowPersistenceConflictError(
                "shadow run session date is not an XNYS trading session"
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
            heartbeat: LeaseHeartbeat | None = None
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
                        candidates=candidate_tuple,
                        observed_at=observed_at,
                    )
                    stored_decisions = await self._existing_decisions(
                        run=run,
                        candidate_ids=frozenset(item.id for item in candidate_tuple),
                    )
                    already_completed = await self._set_running(
                        run=run,
                        stored_status=stored_decisions,
                        candidate_count=len(candidate_tuple),
                    )
                    active_heartbeat = heartbeat

                    async def guarded_recorder(*, decision: Decision, owner_id: str) -> None:
                        await active_heartbeat.require(now=_utc(self._clock()))
                        await _record_decision_idempotent(
                            decision=decision, owner_id=owner_id
                        )

                    pipeline = ShadowPipeline(
                        proposal_provider=self._pipeline.proposal_provider,
                        decision_recorder=guarded_recorder,
                        orb_config=self._pipeline.orb_config,
                        mode=self._pipeline.mode,
                    )
                    yield ShadowRunSession(
                        run=run,
                        strategy=strategy,
                        policy=policy,
                        candidates=candidate_tuple,
                        pipeline=pipeline,
                        decisions=stored_decisions,
                        completed=already_completed,
                        cutoff=entry_cutoff_utc(run.session_date, self._pipeline.orb_config),
                        heartbeat=heartbeat,
                        clock=self._clock,
                    )
            except BaseException:
                try:
                    from trading.scheduler import require_lease

                    await require_lease(
                        lease_db,
                        claim=heartbeat.claim if heartbeat is not None else claim,
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
                await release_lease(
                    lease_db,
                    claim=heartbeat.claim if heartbeat is not None else claim,
                    now=_utc(self._clock()),
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
        candidate_ids: frozenset[str],
    ) -> dict[str, Decision]:
        from trading.storage.repository import list_decisions

        payloads = await list_decisions(owner_id=run.owner_id, run_id=run.id, limit=500)
        requested = candidate_ids
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
    def _validate_inputs(
        *,
        run: Run,
        scanner_run: ScannerRun,
        strategy: StrategyRecord,
        candidates: Sequence[Candidate],
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
        for candidate in candidates:
            if candidate.id in candidate_ids:
                raise ValueError("a shadow run cannot assess one candidate more than once")
            candidate_ids.add(candidate.id)
            if candidate.scanner_run_id != scanner_run.id:
                raise ValueError("candidate does not belong to scanner run")


class ShadowRunSession:
    """One leased run: assess candidates repeatedly until each is terminal."""

    def __init__(
        self,
        *,
        run: Run,
        strategy: StrategyRecord,
        policy: RiskPolicy,
        candidates: tuple[Candidate, ...],
        pipeline: ShadowPipeline,
        decisions: dict[str, Decision],
        completed: bool,
        cutoff: datetime,
        heartbeat: LeaseHeartbeat,
        clock: Callable[[], datetime],
    ) -> None:
        self.run = run
        self.cutoff = cutoff
        self._strategy = strategy
        self._policy = policy
        self._candidates = {candidate.id: candidate for candidate in candidates}
        self._admitted_symbols = frozenset(
            candidate.symbol
            for candidate in candidates
            if candidate.admission_status == "admitted"
        )
        self._pipeline = pipeline
        self._decisions = dict(decisions)
        self._completed = completed
        self._heartbeat = heartbeat
        self._clock = clock

    @property
    def complete(self) -> bool:
        return len(self._decisions) == len(self._candidates)

    @property
    def decisions(self) -> dict[str, Decision]:
        return dict(self._decisions)

    def pending(self) -> tuple[Candidate, ...]:
        return tuple(
            candidate
            for candidate_id, candidate in self._candidates.items()
            if candidate_id not in self._decisions
        )

    async def assess(
        self,
        item: CandidateInput,
        *,
        portfolio: PortfolioSnapshot,
        now: datetime | None = None,
    ) -> Assessment:
        candidate = self._candidates.get(item.candidate.id)
        if candidate is None or candidate != item.candidate:
            raise ShadowPersistenceConflictError(
                "assessed candidate is not part of the materialized scanner run"
            )
        observed = _utc(now or self._clock())
        await self._heartbeat.require(now=observed)
        assessment = await self._pipeline.assess_one(
            run_id=self.run.id,
            strategy=self._strategy,
            item=item,
            portfolio=portfolio,
            policy=self._policy,
            session_date=self.run.session_date,
            admitted_symbols=self._admitted_symbols,
            now=observed,
            prior=self._decisions.get(candidate.id),
        )
        if assessment.decision is not None:
            self._decisions[candidate.id] = assessment.decision
            await self._complete_if_done()
        return assessment

    async def finalize(
        self,
        inputs: Sequence[CandidateInput],
        *,
        portfolio: PortfolioSnapshot,
        now: datetime | None = None,
    ) -> dict[str, Decision]:
        """Cutoff sweep: every remaining candidate must become terminal."""
        observed = _utc(now or self._clock())
        if observed < self.cutoff:
            raise ValueError("finalize requires the entry cutoff to have passed")
        by_id = {item.candidate.id: item for item in inputs}
        for candidate in self.pending():
            item = by_id.get(candidate.id)
            if item is not None:
                await self.assess(item, portfolio=portfolio, now=observed)
                continue
            await self._heartbeat.require(now=observed)
            decision = await self._pipeline.record_data_blocked(
                run_id=self.run.id, strategy=self._strategy, candidate=candidate
            )
            self._decisions[candidate.id] = decision
            await self._complete_if_done()
        if not self.complete:
            raise ShadowPersistenceConflictError(
                "shadow pipeline did not produce one terminal decision per candidate"
            )
        return self.decisions

    async def _complete_if_done(self) -> None:
        if self._completed or not self.complete:
            return
        await _mark_run_status(
            run_id=self.run.id,
            owner_id=self.run.owner_id,
            status="completed",
            allowed_statuses=("running",),
            closed_at=_utc(self._clock()),
        )
        self._completed = True


__all__ = [
    "RepositoryBackedShadowRunner",
    "ShadowRunSession",
    "ShadowLeaseUnavailableError",
    "ShadowPersistenceConflictError",
]
