"""Repository-backed shadow-run execution with lease and replay safety."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
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

logger = logging.getLogger("autotrader.shadow.persistence")

# autotrader_candidates.rank CHECK(rank BETWEEN 1 AND 50), mirrored by
# Candidate.rank: one scanner run holds at most this many candidates.
MAX_CANDIDATE_RANK = 50


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
        dynamic: bool = False,
    ) -> AsyncIterator["ShadowRunSession"]:
        """Hold the run lease for a whole session of repeated assessments.

        By default the scanner universe is materialized once; every later poll
        assesses only these candidates, and the run completes as soon as each
        one is terminal. A ``dynamic`` session keeps its scanner run open until
        the entry cutoff instead: ``add_candidates`` admits symbols that heat
        up during the day, a reopen resumes every stored candidate (the scanner
        parameters stay those of the first selection and are never compared
        again), and only the cutoff sweep completes the run.

        A failure while the lease is held marks the run failed so a restart
        resumes from the recorded decisions.
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
                    session_candidates = await self._materialize(
                        run=run,
                        scanner_run=scanner_run,
                        candidates=candidate_tuple,
                        observed_at=observed_at,
                        dynamic=dynamic,
                    )
                    stored_decisions = await self._existing_decisions(
                        run=run,
                        candidate_ids=frozenset(item.id for item in session_candidates),
                    )
                    already_completed = await self._set_running(
                        run=run,
                        stored_status=stored_decisions,
                        candidate_count=len(session_candidates),
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
                        strategy=self._pipeline.strategy,
                        mode=self._pipeline.mode,
                    )
                    session = ShadowRunSession(
                        run=run,
                        strategy=strategy,
                        policy=policy,
                        scanner_run_id=scanner_run.id,
                        candidates=session_candidates,
                        pipeline=pipeline,
                        decisions=stored_decisions,
                        completed=already_completed,
                        cutoff=entry_cutoff_utc(run.session_date, pipeline.strategy),
                        heartbeat=heartbeat,
                        clock=self._clock,
                        candidate_repository=self._candidates,
                        dynamic=dynamic,
                    )
                    if dynamic:
                        # The stored candidates are the session's base; this
                        # selection only adds the conIds it does not hold yet.
                        await session.add_candidates(candidate_tuple, now=observed_at)
                    yield session
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
        dynamic: bool,
    ) -> tuple[Candidate, ...]:
        """Persist the run and its scanner run; return the session's starting candidates."""
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
        elif dynamic:
            # Every selection of a dynamic run reads a newer snapshot; the
            # first one's parameters stay the record, so a restart resumes.
            if stored_scanner.run_id != scanner_run.run_id:
                raise ShadowPersistenceConflictError("stored scanner run belongs to another run")
        elif not _same_scanner(scanner_run, stored_scanner):
            raise ShadowPersistenceConflictError("stored scanner run differs from request")

        stored_rows = await self._candidates.list_for_scanner(scanner_run.id)
        if dynamic:
            # The session admits the requested candidates itself (next free
            # rank, idempotent by conId), so a restart keeps every stored one.
            return tuple(stored_rows)
        stored_candidates = {candidate.id: candidate for candidate in stored_rows}
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
        return candidates

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
        scanner_run_id: str,
        candidates: tuple[Candidate, ...],
        pipeline: ShadowPipeline,
        decisions: dict[str, Decision],
        completed: bool,
        cutoff: datetime,
        heartbeat: LeaseHeartbeat,
        clock: Callable[[], datetime],
        candidate_repository: CandidateRepository,
        dynamic: bool = False,
    ) -> None:
        self.run = run
        self.cutoff = cutoff
        self._strategy = strategy
        self._policy = policy
        self._scanner_run_id = scanner_run_id
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
        self._candidate_repository = candidate_repository
        self._dynamic = dynamic
        self._rank_cap_logged = False

    @property
    def dynamic(self) -> bool:
        return self._dynamic

    @property
    def complete(self) -> bool:
        """Every current candidate is terminal (a dynamic run may still grow)."""
        return len(self._decisions) == len(self._candidates)

    @property
    def decisions(self) -> dict[str, Decision]:
        return dict(self._decisions)

    @property
    def candidates(self) -> tuple[Candidate, ...]:
        return tuple(self._candidates.values())

    def pending(self) -> tuple[Candidate, ...]:
        return tuple(
            candidate
            for candidate_id, candidate in self._candidates.items()
            if candidate_id not in self._decisions
        )

    async def add_candidates(
        self,
        candidates: Sequence[Candidate],
        *,
        now: datetime | None = None,
    ) -> tuple[Candidate, ...]:
        """Admit candidates into this dynamic run's scanner run; return those added.

        Each new conId takes the scanner run's next free rank; a conId the run
        already holds is a no-op, so repeating a selection is idempotent. Once
        the rank cap is full, the entry cutoff has passed, or the run has
        completed, nothing more is admitted: no decision could follow.
        """
        if not self._dynamic:
            raise ValueError("only a dynamic session admits candidates after it opens")
        batch = tuple(candidates)
        batch_ids: set[str] = set()
        for candidate in batch:
            if candidate.scanner_run_id != self._scanner_run_id:
                raise ValueError("candidate does not belong to scanner run")
            if candidate.id in batch_ids:
                raise ValueError("a shadow run cannot assess one candidate more than once")
            batch_ids.add(candidate.id)
        held = {candidate.con_id for candidate in self._candidates.values()}
        fresh = [candidate for candidate in batch if candidate.con_id not in held]
        if not fresh:
            return ()
        observed = _utc(now or self._clock())
        if self._completed or observed >= self.cutoff:
            logger.info(
                "run %s is closed to new candidates; %d not admitted", self.run.id, len(fresh)
            )
            return ()
        await self._heartbeat.require(now=observed)
        added: list[Candidate] = []
        for candidate in fresh:
            if candidate.con_id in held:
                continue
            if candidate.id in self._candidates:
                raise ShadowPersistenceConflictError(
                    f"candidate id already names another contract: {candidate.id}"
                )
            rank = self._next_free_rank()
            if rank is None:
                if not self._rank_cap_logged:
                    self._rank_cap_logged = True
                    logger.warning(
                        "run %s holds %d candidates (the cap); newly hot symbols are "
                        "no longer added",
                        self.run.id,
                        MAX_CANDIDATE_RANK,
                    )
                break
            ranked = Candidate.model_validate({**candidate.model_dump(), "rank": rank})
            try:
                await self._candidate_repository.save(ranked)
            except DuplicateRecordError as exc:
                # The lease makes this session the run's only writer, so a
                # collision means another writer exists: never guess, fail.
                raise ShadowPersistenceConflictError(
                    f"candidate was concurrently created: {ranked.id}"
                ) from exc
            self._candidates[ranked.id] = ranked
            held.add(ranked.con_id)
            if ranked.admission_status == "admitted":
                self._admitted_symbols = self._admitted_symbols | {ranked.symbol}
            added.append(ranked)
        return tuple(added)

    def _next_free_rank(self) -> int | None:
        used = {candidate.rank for candidate in self._candidates.values()}
        return next(
            (rank for rank in range(1, MAX_CANDIDATE_RANK + 1) if rank not in used), None
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
        """Cutoff sweep: every remaining candidate becomes terminal, then the run completes."""
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
        await self._complete_if_done(sweep=True)
        return self.decisions

    async def _complete_if_done(self, *, sweep: bool = False) -> None:
        # A dynamic run can still admit candidates until its cutoff, so a
        # momentarily empty pending set is not the end; only the sweep is.
        if self._completed or not self.complete or (self._dynamic and not sweep):
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
    "MAX_CANDIDATE_RANK",
    "RepositoryBackedShadowRunner",
    "ShadowRunSession",
    "ShadowLeaseUnavailableError",
    "ShadowPersistenceConflictError",
]
