"""Ed25519 signing for authority receipts without widening runtime authority."""
from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import re
import subprocess
from typing import Protocol, Self

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel

from trading.contracts import AuthorityReceipt, require_aware


PRIVATE_KEY_BYTES = 32
PUBLIC_KEY_BYTES = 32
SIGNATURE_BYTES = 64
_ENCODED_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

ReceiptPayload = BaseModel | Mapping[str, object]


class ReceiptSigningError(ValueError):
    """Raised when receipt signing material or an encoded signature is invalid."""


class KeychainError(ReceiptSigningError):
    """Raised when the macOS Keychain cannot provide valid signing material."""


class ReceiptSigner(Protocol):
    def sign(self, payload: ReceiptPayload) -> str:
        """Return a URL-safe, unpadded Ed25519 signature."""

    def public_key(self) -> str:
        """Return the URL-safe, unpadded public key."""


class ReceiptVerifier(Protocol):
    def verify(self, payload: ReceiptPayload, signature: str) -> bool:
        """Return false for any malformed or invalid signature."""


def canonical_payload(payload: ReceiptPayload) -> bytes:
    """Serialize a receipt payload deterministically before signing."""
    if isinstance(payload, BaseModel):
        document = payload.model_dump(mode="json")
    elif isinstance(payload, Mapping):
        document = dict(payload)
    else:
        raise TypeError("receipt payload must be a Pydantic model or mapping")
    document.pop("signature", None)
    try:
        encoded = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ReceiptSigningError("receipt payload is not canonicalizable") from exc
    return encoded.encode("utf-8")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str, *, expected_size: int, field: str) -> bytes:
    if not isinstance(value, str) or not value or not _ENCODED_PATTERN.fullmatch(value):
        raise ReceiptSigningError(f"{field} encoding is invalid")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise ReceiptSigningError(f"{field} encoding is invalid") from exc
    if len(decoded) != expected_size:
        raise ReceiptSigningError(f"{field} length is invalid")
    return decoded


def _private_key(value: Ed25519PrivateKey | bytes) -> Ed25519PrivateKey:
    if isinstance(value, Ed25519PrivateKey):
        return value
    if not isinstance(value, bytes) or len(value) != PRIVATE_KEY_BYTES:
        raise ReceiptSigningError("Ed25519 private key must be 32 raw bytes")
    try:
        return Ed25519PrivateKey.from_private_bytes(value)
    except (TypeError, ValueError) as exc:
        raise ReceiptSigningError("Ed25519 private key is invalid") from exc


def _public_key(value: Ed25519PublicKey | bytes | str) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if isinstance(value, str):
        value = _decode(value, expected_size=PUBLIC_KEY_BYTES, field="public key")
    if not isinstance(value, bytes) or len(value) != PUBLIC_KEY_BYTES:
        raise ReceiptSigningError("Ed25519 public key must be 32 raw bytes")
    try:
        return Ed25519PublicKey.from_public_bytes(value)
    except (TypeError, ValueError) as exc:
        raise ReceiptSigningError("Ed25519 public key is invalid") from exc


def verify_signature(
    payload: ReceiptPayload,
    signature: str,
    public_key: Ed25519PublicKey | bytes | str,
) -> bool:
    """Verify a canonical payload; malformed inputs fail closed as false."""
    try:
        key = _public_key(public_key)
        raw_signature = _decode(
            signature,
            expected_size=SIGNATURE_BYTES,
            field="signature",
        )
        key.verify(raw_signature, canonical_payload(payload))
    except (InvalidSignature, ReceiptSigningError, TypeError, ValueError):
        return False
    return True


def _timestamp(value: datetime) -> str:
    return require_aware(value).isoformat().replace("+00:00", "Z")


class Ed25519ReceiptSigner:
    """In-memory Ed25519 signer for canonical authority receipt payloads."""

    def __init__(self, private_key: Ed25519PrivateKey | bytes) -> None:
        self._private_key = _private_key(private_key)

    @classmethod
    def generate(cls) -> Self:
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_encoded_private_key(cls, value: str) -> Self:
        return cls(_decode(value, expected_size=PRIVATE_KEY_BYTES, field="private key"))

    def sign(self, payload: ReceiptPayload) -> str:
        return _encode(self._private_key.sign(canonical_payload(payload)))

    def public_key(self) -> str:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return _encode(raw)

    def verify(self, payload: ReceiptPayload, signature: str) -> bool:
        return verify_signature(payload, signature, self.public_key())


class Ed25519ReceiptVerifier:
    """Verifier that exposes no private-key operation."""

    def __init__(self, public_key: Ed25519PublicKey | bytes | str) -> None:
        self._public_key = _public_key(public_key)

    @classmethod
    def from_encoded_public_key(cls, value: str) -> Self:
        return cls(value)

    def verify(self, payload: ReceiptPayload, signature: str) -> bool:
        return verify_signature(payload, signature, self._public_key)


def sign_authority_receipt(
    receipt: AuthorityReceipt,
    signer: ReceiptSigner,
    *,
    public_key_id: str | None = None,
    issued_at: datetime | None = None,
) -> AuthorityReceipt:
    """Return a signed immutable receipt using a detached signature envelope."""
    if (
        receipt.signature is not None
        or receipt.public_key_id is not None
        or receipt.issued_at is not None
    ):
        raise ReceiptSigningError("receipt is already signed")
    issued = require_aware(issued_at or datetime.now(timezone.utc))
    if issued > receipt.expires_at:
        raise ReceiptSigningError("receipt issued time must precede expiry")
    key_id = public_key_id or signer.public_key()
    if not key_id.strip():
        raise ReceiptSigningError("public key identifier is required")
    payload = receipt.model_dump(mode="json")
    payload.update({"public_key_id": key_id, "issued_at": _timestamp(issued)})
    signature = signer.sign(payload)
    try:
        return receipt.model_copy(
            update={
                "signature": signature,
                "public_key_id": key_id,
                "issued_at": issued,
            }
        )
    except (TypeError, ValueError) as exc:
        raise ReceiptSigningError("signed authority receipt is invalid") from exc


def verify_authority_receipt(
    receipt: AuthorityReceipt,
    verifier: ReceiptVerifier,
) -> bool:
    """Verify a signed receipt envelope without making an authority decision."""
    if receipt.signature is None or receipt.public_key_id is None or receipt.issued_at is None:
        return False
    return verifier.verify(receipt, receipt.signature)


class KeychainCommandRunner(Protocol):
    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Run a command without shell interpolation."""


def _run_keychain_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


class MacOSKeychainReceiptSigner:
    """Lazily load an Ed25519 seed from macOS Keychain generic-password data.

    The keychain item must contain either an unpadded URL-safe base64 seed or a
    64-character hexadecimal seed.  The command is executed only on first use;
    stdout and stderr are never included in exceptions or logs.
    """

    def __init__(
        self,
        *,
        service: str,
        account: str,
        command_runner: KeychainCommandRunner = _run_keychain_command,
    ) -> None:
        if not service.strip() or not account.strip():
            raise ValueError("Keychain service and account are required")
        self._service = service
        self._account = account
        self._command_runner = command_runner
        self._delegate: Ed25519ReceiptSigner | None = None

    def _load(self) -> Ed25519ReceiptSigner:
        if self._delegate is not None:
            return self._delegate
        command = (
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            self._account,
            "-s",
            self._service,
            "-w",
        )
        try:
            result = self._command_runner(command)
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeychainError("unable to read receipt signing key from Keychain") from exc
        if result.returncode != 0 or not isinstance(result.stdout, str):
            raise KeychainError("unable to read receipt signing key from Keychain")
        encoded = result.stdout.strip()
        try:
            seed = self._decode_seed(encoded)
            delegate = Ed25519ReceiptSigner(seed)
        except ReceiptSigningError as exc:
            raise KeychainError("Keychain receipt signing key is invalid") from exc
        self._delegate = delegate
        return delegate

    @staticmethod
    def _decode_seed(value: str) -> bytes:
        if len(value) == PRIVATE_KEY_BYTES * 2 and all(
            character in "0123456789abcdefABCDEF" for character in value
        ):
            return bytes.fromhex(value)
        return _decode(value, expected_size=PRIVATE_KEY_BYTES, field="private key")

    def sign(self, payload: ReceiptPayload) -> str:
        return self._load().sign(payload)

    def public_key(self) -> str:
        return self._load().public_key()

    def verify(self, payload: ReceiptPayload, signature: str) -> bool:
        return self._load().verify(payload, signature)


__all__ = [
    "Ed25519ReceiptSigner",
    "Ed25519ReceiptVerifier",
    "KeychainCommandRunner",
    "KeychainError",
    "MacOSKeychainReceiptSigner",
    "ReceiptPayload",
    "ReceiptSigner",
    "ReceiptSigningError",
    "ReceiptVerifier",
    "canonical_payload",
    "sign_authority_receipt",
    "verify_authority_receipt",
    "verify_signature",
]
