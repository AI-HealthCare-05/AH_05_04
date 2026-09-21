"""Frozen HMAC authority for direct Sync Chat CLOSED_DEMO questions only."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Protocol

from rag_runtime.query_binding import ImmutableArtifactRef, QueryFingerprint, SensitiveText

__all__ = [
    "ApprovedClosedDemoChatQueryHmacKey",
    "ClosedDemoChatQueryBindingDependencyError",
    "ClosedDemoChatQueryFingerprintProducer",
    "ClosedDemoChatQueryHmacKeyProvider",
    "ClosedDemoChatQueryVerificationFailure",
    "ClosedDemoChatQueryVerificationSuccess",
    "ClosedDemoChatQueryVerifier",
    "build_closed_demo_chat_query_fingerprint_producer",
    "build_closed_demo_chat_query_verifier",
    "compute_closed_demo_chat_query_verifier_artifact_ref",
]

_ALGORITHM = "HMAC-SHA-256"
_PREIMAGE_VERSION = "closed-demo-chat-query-hmac@1"
_KEY_VERSION_NAMESPACE = "closed-demo-chat-query-hmac-key@<positive-integer>"
_VERIFIER_ARTIFACT_CODE = "closed-demo-chat-query-binding-verifier"
_VERIFIER_ARTIFACT_VERSION = "1.0.0"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_VERSION_RE = re.compile(r"^closed-demo-chat-query-hmac-key@[1-9][0-9]*$")


class ClosedDemoChatQueryBindingDependencyError(RuntimeError):
    """The approved CLOSED_DEMO Chat key dependency is unavailable or invalid."""

    def __init__(self) -> None:
        super().__init__("approved CLOSED_DEMO Chat query HMAC key dependency is unavailable")


class ApprovedClosedDemoChatQueryHmacKey:
    """Opaque HMAC material owned by the Backend configuration boundary."""

    __slots__ = ("__material",)

    def __init__(self, material: bytes) -> None:
        if type(material) is not bytes or not material:
            raise ValueError("CLOSED_DEMO Chat query HMAC key material must be non-empty bytes")
        self.__material = material

    def __repr__(self) -> str:
        return "<ApprovedClosedDemoChatQueryHmacKey redacted>"

    def _digest(self, preimage: bytes) -> str:
        return hmac.new(self.__material, preimage, hashlib.sha256).hexdigest()


class ClosedDemoChatQueryHmacKeyProvider(Protocol):
    """Owner of active and retained CLOSED_DEMO Chat HMAC keys."""

    def active_key_version(self) -> str: ...

    def key_for_version(self, key_version: str) -> ApprovedClosedDemoChatQueryHmacKey | None: ...


@dataclass(frozen=True, slots=True)
class ClosedDemoChatQueryVerificationSuccess:
    query_fingerprint: QueryFingerprint
    verifier_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class ClosedDemoChatQueryVerificationFailure:
    reason: str


class ClosedDemoChatQueryFingerprintProducer:
    """Binds the exact validated Chat question under the dedicated authority."""

    __slots__ = ("__keys",)

    def __init__(self, keys: ClosedDemoChatQueryHmacKeyProvider) -> None:
        self.__keys = keys

    def __repr__(self) -> str:
        return "<ClosedDemoChatQueryFingerprintProducer key_dependency=redacted>"

    def produce(self, question: SensitiveText) -> QueryFingerprint:
        try:
            key_version = self.__keys.active_key_version()
            if not _is_approved_key_version(key_version):
                raise ClosedDemoChatQueryBindingDependencyError()
            key = self.__keys.key_for_version(key_version)
            if type(key) is not ApprovedClosedDemoChatQueryHmacKey:
                raise ClosedDemoChatQueryBindingDependencyError()
            return QueryFingerprint(_ALGORITHM, key_version, _compute_digest(key, question))
        except ClosedDemoChatQueryBindingDependencyError:
            raise
        except Exception as error:
            raise ClosedDemoChatQueryBindingDependencyError() from error


class ClosedDemoChatQueryVerifier:
    """Fail-closed verifier for the direct Chat-question binding only."""

    __slots__ = ("__keys",)

    def __init__(self, keys: ClosedDemoChatQueryHmacKeyProvider) -> None:
        self.__keys = keys

    def __repr__(self) -> str:
        return "<ClosedDemoChatQueryVerifier key_dependency=redacted>"

    def verify(
        self,
        question: SensitiveText,
        fingerprint: QueryFingerprint,
    ) -> ClosedDemoChatQueryVerificationSuccess | ClosedDemoChatQueryVerificationFailure:
        if not _is_valid_claim(question, fingerprint):
            return ClosedDemoChatQueryVerificationFailure("INVALID_BINDING")
        try:
            key = self.__keys.key_for_version(fingerprint.key_version)
            if key is None:
                return ClosedDemoChatQueryVerificationFailure("INVALID_BINDING")
            if type(key) is not ApprovedClosedDemoChatQueryHmacKey:
                raise ClosedDemoChatQueryBindingDependencyError()
            expected_digest = _compute_digest(key, question)
        except Exception:
            return ClosedDemoChatQueryVerificationFailure("DEPENDENCY_ERROR")
        if not hmac.compare_digest(expected_digest, fingerprint.digest):
            return ClosedDemoChatQueryVerificationFailure("INVALID_BINDING")
        return ClosedDemoChatQueryVerificationSuccess(
            fingerprint,
            compute_closed_demo_chat_query_verifier_artifact_ref(),
        )


def build_closed_demo_chat_query_fingerprint_producer(
    keys: ClosedDemoChatQueryHmacKeyProvider,
) -> ClosedDemoChatQueryFingerprintProducer:
    return ClosedDemoChatQueryFingerprintProducer(keys)


def build_closed_demo_chat_query_verifier(keys: ClosedDemoChatQueryHmacKeyProvider) -> ClosedDemoChatQueryVerifier:
    return ClosedDemoChatQueryVerifier(keys)


def compute_closed_demo_chat_query_verifier_artifact_ref() -> ImmutableArtifactRef:
    projection = {
        "algorithm": _ALGORITHM,
        "artifact_code": _VERIFIER_ARTIFACT_CODE,
        "artifact_version": _VERIFIER_ARTIFACT_VERSION,
        "digest_format": "64-character lowercase hex",
        "failure_semantics": {
            "approved_key_dependency_unavailable": "DEPENDENCY_ERROR",
            "invalid_or_mismatch": "INVALID_BINDING",
        },
        "key_version_namespace": _KEY_VERSION_NAMESPACE,
        "preimage_version": _PREIMAGE_VERSION,
        "query_scope": "direct-validated-sync-chat-question",
    }
    canonical_projection = json.dumps(
        projection,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return ImmutableArtifactRef(
        artifact_code=_VERIFIER_ARTIFACT_CODE,
        version=_VERIFIER_ARTIFACT_VERSION,
        content_sha256=hashlib.sha256(canonical_projection).hexdigest(),
    )


def _compute_digest(key: ApprovedClosedDemoChatQueryHmacKey, question: SensitiveText) -> str:
    if type(question) is not SensitiveText:
        raise ValueError("question must be SensitiveText")
    value = question.reveal()
    if type(value) is not str:
        raise ValueError("question must contain text")
    return key._digest(_PREIMAGE_VERSION.encode("ascii") + b"\x00" + value.encode("utf-8"))


def _is_valid_claim(question: object, fingerprint: object) -> bool:
    if type(question) is not SensitiveText or type(fingerprint) is not QueryFingerprint:
        return False
    return (
        fingerprint.algorithm == _ALGORITHM
        and _is_approved_key_version(fingerprint.key_version)
        and type(fingerprint.digest) is str
        and _DIGEST_RE.fullmatch(fingerprint.digest) is not None
    )


def _is_approved_key_version(value: object) -> bool:
    return type(value) is str and _KEY_VERSION_RE.fullmatch(value) is not None
