"""Receipt-gated local PAPER runtime state.

This controller is the only production execution gate.  It is intentionally
constructed with immutable runtime identity and trusted public keys rather than
environment switches, so configuration alone cannot activate PAPER trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from trading.authority import EngineAuthority, ReceiptValidation
from trading.contracts import AuthorityReceipt, TradeIntent
from trading.receipt_signing import ReceiptVerifier


class PaperRuntimeState(str, Enum):
    BLOCKED = "blocked"
    SHADOW_READY = "shadow_ready"
    PAPER_READY = "paper_ready"
    PAPER_ACTIVE = "paper_active"


class PaperRuntimeError(PermissionError):
    """PAPER entry is unavailable because a mandatory safety condition failed."""


class PaperKillStore(Protocol):
    async def is_stopped(self) -> bool:
        """Return whether a durable operator stop prevents all new entries."""

    async def stop(self, *, reason: str) -> None:
        """Persist a monotonic stop; no corresponding start operation exists."""


@dataclass(frozen=True, slots=True)
class PaperRuntimeIdentity:
    build_sha: str
    evidence_digest: str
    strategy_id: str
    strategy_version: int
    policy_hashes: tuple[str, ...]
    paper_account: str
    client_id: int


@dataclass(frozen=True, slots=True)
class PaperPreflight:
    tws_connected: bool
    paper_account_verified: bool
    client_id_verified: bool
    scanner_supported: bool
    real_time_data: bool
    data_line_capacity: bool
    reconciliation_clear: bool
    verified_at: datetime

    def is_healthy(self) -> bool:
        return all(
            (
                self.tws_connected,
                self.paper_account_verified,
                self.client_id_verified,
                self.scanner_supported,
                self.real_time_data,
                self.data_line_capacity,
                self.reconciliation_clear,
            )
        )


class PaperRuntimeController:
    """Non-bypassable runtime gate for a receipt-bound PAPER strategy version."""

    def __init__(
        self,
        *,
        identity: PaperRuntimeIdentity,
        trusted_receipt_verifiers: dict[str, ReceiptVerifier],
        kill_store: PaperKillStore,
    ) -> None:
        if not trusted_receipt_verifiers:
            raise ValueError("PAPER runtime requires at least one pinned receipt verifier")
        self._identity = identity
        self._trusted_receipt_verifiers = dict(trusted_receipt_verifiers)
        self._kill_store = kill_store
        self._authority = EngineAuthority()
        self._state = PaperRuntimeState.BLOCKED
        self._receipt: AuthorityReceipt | None = None
        self._last_preflight: PaperPreflight | None = None
        self._shadow_evidence_ready = False
        self._manual_drill_ready = False

    @property
    def state(self) -> PaperRuntimeState:
        return self._state

    @property
    def identity(self) -> PaperRuntimeIdentity:
        """Return the immutable receipt-bound identity expected by the executor."""

        return self._identity

    @property
    def receipt_validation(self) -> ReceiptValidation | None:
        if self._receipt is None:
            return None
        return self._validate_receipt()

    async def mark_shadow_ready(self, *, preflight: PaperPreflight) -> None:
        self._require_preflight(preflight)
        if await self._kill_store.is_stopped():
            self._block()
            raise PaperRuntimeError("PAPER runtime is stopped by the local operator")
        self._last_preflight = preflight
        self._state = PaperRuntimeState.SHADOW_READY

    async def mark_paper_ready(
        self,
        *,
        preflight: PaperPreflight,
        shadow_evidence_ready: bool,
        manual_drill_ready: bool,
    ) -> None:
        self._require_preflight(preflight)
        if await self._kill_store.is_stopped():
            self._block()
            raise PaperRuntimeError("PAPER runtime is stopped by the local operator")
        if not shadow_evidence_ready or not manual_drill_ready:
            self._block()
            raise PaperRuntimeError("PAPER readiness requires current shadow evidence and manual drill")
        self._last_preflight = preflight
        self._shadow_evidence_ready = True
        self._manual_drill_ready = True
        self._state = PaperRuntimeState.PAPER_READY

    async def activate(
        self, *, receipt: AuthorityReceipt, preflight: PaperPreflight
    ) -> None:
        """Enter PAPER active only after receipt, evidence, drill, and preflight checks."""
        self._require_preflight(preflight)
        if await self._kill_store.is_stopped():
            self._block()
            raise PaperRuntimeError("PAPER runtime is stopped by the local operator")
        if not self._shadow_evidence_ready or not self._manual_drill_ready:
            self._block()
            raise PaperRuntimeError("PAPER activation requires completed shadow and drill gates")
        try:
            self._authority.require_paper_receipt(
                receipt,
                expected_build_sha=self._identity.build_sha,
                expected_evidence_digest=self._identity.evidence_digest,
                expected_strategy_id=self._identity.strategy_id,
                expected_strategy_version=self._identity.strategy_version,
                expected_policy_hashes=self._identity.policy_hashes,
                expected_paper_account=self._identity.paper_account,
                expected_client_id=self._identity.client_id,
                trusted_receipt_verifiers=self._trusted_receipt_verifiers,
                now=datetime.now(timezone.utc),
            )
        except PermissionError as exc:
            self._block()
            raise PaperRuntimeError(str(exc)) from exc
        raise AssertionError("current authority must reject PAPER activation")

    async def require_paper_submission(self, *, intent: TradeIntent, context: object) -> None:
        """Revalidate the active receipt and all monotonic entry fences per order."""
        del context
        if self._state is not PaperRuntimeState.PAPER_ACTIVE:
            raise PaperRuntimeError("PAPER runtime is not active")
        if await self._kill_store.is_stopped():
            self._block()
            raise PaperRuntimeError("PAPER runtime is stopped by the local operator")
        if self._last_preflight is None or not self._last_preflight.is_healthy():
            self._block()
            raise PaperRuntimeError("PAPER preflight is not healthy")
        validation = self._validate_receipt()
        if not validation.valid:
            self._block()
            raise PaperRuntimeError(f"PAPER receipt rejected: {validation.reason}")
        if (
            intent.strategy_id != self._identity.strategy_id
            or intent.strategy_version != self._identity.strategy_version
        ):
            raise PaperRuntimeError("trade intent strategy is not receipt-bound")

    async def stop(self, *, reason: str = "operator emergency stop") -> None:
        await self._kill_store.stop(reason=reason)
        self._block()

    def block(self, *, reason: str) -> None:
        """Fail closed after a disconnect, stale data, or reconciliation uncertainty."""
        if not reason.strip():
            raise ValueError("block reason is required")
        self._block()

    def _validate_receipt(self, receipt: AuthorityReceipt | None = None) -> ReceiptValidation:
        return self._authority.validate_receipt(
            receipt or self._receipt,
            expected_build_sha=self._identity.build_sha,
            expected_evidence_digest=self._identity.evidence_digest,
            expected_strategy_id=self._identity.strategy_id,
            expected_strategy_version=self._identity.strategy_version,
            expected_policy_hashes=self._identity.policy_hashes,
            expected_paper_account=self._identity.paper_account,
            expected_client_id=self._identity.client_id,
            trusted_receipt_verifiers=self._trusted_receipt_verifiers,
            now=datetime.now(timezone.utc),
        )

    @staticmethod
    def _require_preflight(preflight: PaperPreflight) -> None:
        if preflight.verified_at.tzinfo is None or preflight.verified_at.utcoffset() is None:
            raise PaperRuntimeError("PAPER preflight timestamp must be timezone-aware")
        if not preflight.is_healthy():
            raise PaperRuntimeError("PAPER preflight is not healthy")

    def _block(self) -> None:
        self._state = PaperRuntimeState.BLOCKED
        self._receipt = None
