"""Meaning-neutral query-binding value types shared by retrieval authorities.

This module deliberately owns no key namespace, HMAC preimage, or verifier
artifact identity.  Those are authority-specific policy in the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = [
    "ImmutableArtifactRef",
    "QueryBindingFailureReason",
    "QueryBindingVerificationFailure",
    "QueryBindingVerificationSuccess",
    "QueryBindingVerifierPort",
    "QueryFingerprint",
    "SensitiveText",
]


class SensitiveText:
    """Immutable text whose ordinary representations are redacted."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_SensitiveText__value" and not hasattr(self, name):
            object.__setattr__(self, name, value)
            return
        raise AttributeError("SensitiveText is immutable")

    def __deepcopy__(self, memo: dict[int, object]) -> SensitiveText:
        return SensitiveText(self.__value)

    def reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
    algorithm: str
    key_version: str
    digest: str


@dataclass(frozen=True, slots=True)
class ImmutableArtifactRef:
    artifact_code: str
    version: str
    content_sha256: str


class QueryBindingFailureReason(StrEnum):
    INVALID_BINDING = "INVALID_BINDING"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


@dataclass(frozen=True, slots=True)
class QueryBindingVerificationSuccess:
    query_fingerprint: QueryFingerprint
    verifier_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class QueryBindingVerificationFailure:
    reason: QueryBindingFailureReason


class QueryBindingVerifierPort(Protocol):
    def verify(
        self,
        query: SensitiveText,
        query_fingerprint: QueryFingerprint,
    ) -> QueryBindingVerificationSuccess | QueryBindingVerificationFailure: ...
