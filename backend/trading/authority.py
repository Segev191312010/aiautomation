"""Current-release authority for Autotrader V1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from collections.abc import Mapping
from typing import ClassVar, Literal

from trading.contracts import AuthorityReceipt, EngineMode, require_aware
from trading.receipt_signing import ReceiptVerifier, verify_authority_receipt


class EngineAuthorityError(PermissionError):
    """Raised when a requested engine mode lacks evidence-backed authority."""


class AuthorityReceiptError(EngineAuthorityError):
    """Raised when a future execution receipt is invalid or unavailable."""


ReceiptStatus = Literal[
    "valid",
    "missing",
    "invalid",
    "build_mismatch",
    "evidence_mismatch",
    "strategy_mismatch",
    "policy_mismatch",
    "account_mismatch",
    "client_mismatch",
    "expired",
    "signature_missing",
    "signature_untrusted",
    "signature_invalid",
]


@dataclass(frozen=True, slots=True)
class ReceiptValidation:
    """Non-sensitive result of validating a future PAPER authority receipt."""

    status: ReceiptStatus
    reason: str

    @property
    def valid(self) -> bool:
        return self.status == "valid"


class EngineAuthority:
    """Fixed authority boundary; PAPER requires a later receipt-backed service."""

    _ENABLED_MODES: ClassVar[frozenset[EngineMode]] = frozenset(
        {EngineMode.BACKTEST, EngineMode.SHADOW}
    )

    @property
    def enabled_modes(self) -> frozenset[EngineMode]:
        return self._ENABLED_MODES

    def require_mode(self, mode: EngineMode) -> None:
        if not isinstance(mode, EngineMode) or mode not in self.enabled_modes:
            mode_name = mode.value if isinstance(mode, EngineMode) else repr(mode)
            raise EngineAuthorityError(
                f"Autotrader {mode_name} mode is unavailable until its evidence gate is approved"
            )

    def validate_receipt(
        self,
        receipt: AuthorityReceipt | None,
        *,
        expected_build_sha: str,
        now: datetime | None = None,
        expected_evidence_digest: str | None = None,
        expected_strategy_id: str | None = None,
        expected_strategy_version: int | None = None,
        expected_policy_hashes: tuple[str, ...] | None = None,
        expected_paper_account: str | None = None,
        expected_client_id: int | None = None,
        trusted_receipt_verifiers: Mapping[str, ReceiptVerifier] | None = None,
    ) -> ReceiptValidation:
        """Validate receipt provenance without granting PAPER authority.

        This is deliberately separate from :meth:`require_mode`: a future PAPER
        gate can consume this result, while the current release continues to
        reject PAPER even when every receipt field is valid.  Reasons are fixed
        non-sensitive codes and never include receipt values or approval text.
        """

        if receipt is None:
            return ReceiptValidation("missing", "authority receipt is required")
        if not isinstance(receipt, AuthorityReceipt):
            return ReceiptValidation("invalid", "authority receipt has an invalid type")
        if trusted_receipt_verifiers is not None:
            if receipt.signature is None or receipt.public_key_id is None or receipt.issued_at is None:
                return ReceiptValidation(
                    "signature_missing", "authority receipt must carry a detached signature"
                )
            verifier = trusted_receipt_verifiers.get(receipt.public_key_id)
            if verifier is None:
                return ReceiptValidation(
                    "signature_untrusted", "authority receipt signing key is not trusted"
                )
            if not verify_authority_receipt(receipt, verifier):
                return ReceiptValidation(
                    "signature_invalid", "authority receipt signature is invalid"
                )

        current_time = require_aware(now) if now is not None else datetime.now(timezone.utc)
        if not _is_build_sha(expected_build_sha):
            return ReceiptValidation("invalid", "expected build SHA is invalid")
        if not _is_build_sha(receipt.build_sha):
            return ReceiptValidation("invalid", "receipt build SHA is invalid")
        if receipt.build_sha != expected_build_sha:
            return ReceiptValidation("build_mismatch", "receipt build SHA does not match runtime")
        if not _is_digest(receipt.evidence_digest):
            return ReceiptValidation("invalid", "receipt evidence digest is invalid")
        if expected_evidence_digest is not None and not _is_digest(expected_evidence_digest):
            return ReceiptValidation("invalid", "expected evidence digest is invalid")
        if (
            expected_evidence_digest is not None
            and receipt.evidence_digest != expected_evidence_digest
        ):
            return ReceiptValidation("evidence_mismatch", "receipt evidence does not match runtime")
        if not receipt.strategy_id.strip() or receipt.strategy_version < 1:
            return ReceiptValidation("invalid", "receipt strategy identity is invalid")
        if not receipt.policy_hashes or any(
            not _is_digest(policy_hash) for policy_hash in receipt.policy_hashes
        ):
            return ReceiptValidation("invalid", "receipt policy hashes are invalid")
        if len(set(receipt.policy_hashes)) != len(receipt.policy_hashes):
            return ReceiptValidation("invalid", "receipt policy hashes are not unique")
        if not receipt.paper_account.strip() or receipt.client_id < 0:
            return ReceiptValidation("invalid", "receipt PAPER identity is invalid")
        if not receipt.owner_approval.strip():
            return ReceiptValidation("invalid", "owner approval is required")
        if receipt.expires_at <= current_time:
            return ReceiptValidation("expired", "authority receipt has expired")

        if expected_strategy_id is not None and receipt.strategy_id != expected_strategy_id:
            return ReceiptValidation("strategy_mismatch", "receipt strategy does not match runtime")
        if (
            expected_strategy_version is not None
            and receipt.strategy_version != expected_strategy_version
        ):
            return ReceiptValidation(
                "strategy_mismatch", "receipt strategy version does not match runtime"
            )
        if expected_policy_hashes is not None and set(receipt.policy_hashes) != set(
            expected_policy_hashes
        ):
            return ReceiptValidation("policy_mismatch", "receipt policies do not match runtime")
        if expected_paper_account is not None and receipt.paper_account != expected_paper_account:
            return ReceiptValidation("account_mismatch", "receipt PAPER account does not match runtime")
        if expected_client_id is not None and receipt.client_id != expected_client_id:
            return ReceiptValidation("client_mismatch", "receipt client ID does not match runtime")

        return ReceiptValidation("valid", "authority receipt is valid for the runtime")

    def validate_paper_receipt(
        self,
        receipt: AuthorityReceipt | None,
        *,
        expected_build_sha: str,
        now: datetime | None = None,
        expected_evidence_digest: str | None = None,
        expected_strategy_id: str | None = None,
        expected_strategy_version: int | None = None,
        expected_policy_hashes: tuple[str, ...] | None = None,
        expected_paper_account: str | None = None,
        expected_client_id: int | None = None,
        trusted_receipt_verifiers: Mapping[str, ReceiptVerifier] | None = None,
    ) -> ReceiptValidation:
        """Compatibility-named wrapper for the future PAPER receipt gate."""

        return self.validate_receipt(
            receipt,
            expected_build_sha=expected_build_sha,
            now=now,
            expected_evidence_digest=expected_evidence_digest,
            expected_strategy_id=expected_strategy_id,
            expected_strategy_version=expected_strategy_version,
            expected_policy_hashes=expected_policy_hashes,
            expected_paper_account=expected_paper_account,
            expected_client_id=expected_client_id,
            trusted_receipt_verifiers=trusted_receipt_verifiers,
        )

    def require_paper_receipt(
        self,
        receipt: AuthorityReceipt | None,
        *,
        expected_build_sha: str,
        now: datetime | None = None,
        expected_evidence_digest: str | None = None,
        expected_strategy_id: str | None = None,
        expected_strategy_version: int | None = None,
        expected_policy_hashes: tuple[str, ...] | None = None,
        expected_paper_account: str | None = None,
        expected_client_id: int | None = None,
        trusted_receipt_verifiers: Mapping[str, ReceiptVerifier] | None = None,
    ) -> None:
        """Validate a receipt, then retain the current hard PAPER deny.

        A valid receipt is provenance only in V1.  This method intentionally
        cannot turn that provenance into execution authority; Phase 5 must add
        an independently reviewed authority gate before PAPER is considered.
        """

        validation = self.validate_receipt(
            receipt,
            expected_build_sha=expected_build_sha,
            now=now,
            expected_evidence_digest=expected_evidence_digest,
            expected_strategy_id=expected_strategy_id,
            expected_strategy_version=expected_strategy_version,
            expected_policy_hashes=expected_policy_hashes,
            expected_paper_account=expected_paper_account,
            expected_client_id=expected_client_id,
            trusted_receipt_verifiers=trusted_receipt_verifiers,
        )
        if not validation.valid:
            raise AuthorityReceiptError(f"Authority receipt rejected: {validation.reason}")
        raise EngineAuthorityError(
            "Autotrader paper mode is unavailable until its evidence gate is approved"
        )


_BUILD_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _is_build_sha(value: str) -> bool:
    return isinstance(value, str) and bool(_BUILD_SHA_PATTERN.fullmatch(value))


def _is_digest(value: str) -> bool:
    return isinstance(value, str) and bool(_DIGEST_PATTERN.fullmatch(value))
