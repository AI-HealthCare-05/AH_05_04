"""Pure shared contract and factory for #180 B2 Guide query binding.

Both Backend composition and AI Worker retrieval consume this module.  It owns
no environment/configuration access, persistence, or secret provisioning.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Protocol

from rag_runtime.query_binding import (
    ImmutableArtifactRef,
    QueryBindingFailureReason,
    QueryBindingVerificationFailure,
    QueryBindingVerificationSuccess,
    QueryBindingVerifierPort,
    QueryFingerprint,
    SensitiveText,
)

__all__ = [
    "APPROVED_QUERY_FINGERPRINT_ALGORITHM",
    "ApprovedGuideQueryHmacKey",
    "GuideQueryFingerprintDependencyError",
    "GuideQueryFingerprintProducer",
    "GuideQueryHmacKeyProvider",
    "ImmutableArtifactRef",
    "ProductionQueryBindingVerifier",
    "QUERY_BINDING_VERIFIER_ARTIFACT_CODE",
    "QUERY_BINDING_VERIFIER_ARTIFACT_VERSION",
    "QUERY_HMAC_PREIMAGE_VERSION",
    "QueryBindingFailureReason",
    "QueryBindingVerificationFailure",
    "QueryBindingVerificationSuccess",
    "QueryBindingVerifierPort",
    "QueryFingerprint",
    "SensitiveText",
    "build_guide_query_fingerprint_producer",
    "build_production_query_binding_verifier",
    "compute_query_binding_verifier_artifact_ref",
    "query_binding_verifier_policy_projection",
]

APPROVED_QUERY_FINGERPRINT_ALGORITHM = "HMAC-SHA-256"
QUERY_HMAC_PREIMAGE_VERSION = "query-hmac@1"
QUERY_HMAC_KEY_VERSION_NAMESPACE = "guide-query-hmac-key@<positive-integer>"
QUERY_BINDING_VERIFIER_ARTIFACT_CODE = "guide-query-binding-verifier"
QUERY_BINDING_VERIFIER_ARTIFACT_VERSION = "1.0.0"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_VERSION_RE = re.compile(r"^guide-query-hmac-key@[1-9][0-9]*$")


class GuideQueryFingerprintDependencyError(RuntimeError):
    """The injected approved-key dependency is unavailable or malformed."""

    def __init__(self) -> None:
        super().__init__("approved Guide query HMAC key dependency is unavailable")


class ApprovedGuideQueryHmacKey:
    """Opaque key material supplied only by the Backend/Infrastructure boundary."""

    __slots__ = ("__material",)

    def __init__(self, material: bytes) -> None:
        if type(material) is not bytes or not material:
            raise ValueError("Guide query HMAC key material must be non-empty bytes")
        self.__material = material

    def __repr__(self) -> str:
        return "<ApprovedGuideQueryHmacKey redacted>"

    def _digest(self, preimage: bytes) -> str:
        return hmac.new(self.__material, preimage, hashlib.sha256).hexdigest()


class GuideQueryHmacKeyProvider(Protocol):
    """Injected owner of the active and retained approved Guide HMAC keys."""

    def active_key_version(self) -> str: ...

    def key_for_version(self, key_version: str) -> ApprovedGuideQueryHmacKey | None: ...


class GuideQueryFingerprintProducer:
    """Produces a B2 fingerprint using the provider's active approved key version."""

    __slots__ = ("__keys",)

    def __init__(self, keys: GuideQueryHmacKeyProvider) -> None:
        self.__keys = keys

    def __repr__(self) -> str:
        return "<GuideQueryFingerprintProducer key_dependency=redacted>"

    def produce(self, normalized_query: SensitiveText) -> QueryFingerprint:
        try:
            key_version = self.__keys.active_key_version()
            if not _is_approved_key_version(key_version):
                raise GuideQueryFingerprintDependencyError()
            key = self.__keys.key_for_version(key_version)
            if type(key) is not ApprovedGuideQueryHmacKey:
                raise GuideQueryFingerprintDependencyError()
            digest = _compute_digest(key, normalized_query)
        except GuideQueryFingerprintDependencyError:
            raise
        except Exception as error:
            raise GuideQueryFingerprintDependencyError() from error
        return QueryFingerprint(APPROVED_QUERY_FINGERPRINT_ALGORITHM, key_version, digest)


class ProductionQueryBindingVerifier:
    """Recomputes and verifies B2 fingerprints without exposing key-dependency errors."""

    __slots__ = ("__keys",)

    def __init__(self, keys: GuideQueryHmacKeyProvider) -> None:
        self.__keys = keys

    def __repr__(self) -> str:
        return "<ProductionQueryBindingVerifier key_dependency=redacted>"

    def verify(
        self,
        query: SensitiveText,
        query_fingerprint: QueryFingerprint,
    ) -> QueryBindingVerificationSuccess | QueryBindingVerificationFailure:
        if not _is_valid_claim(query, query_fingerprint):
            return QueryBindingVerificationFailure(QueryBindingFailureReason.INVALID_BINDING)
        try:
            key = self.__keys.key_for_version(query_fingerprint.key_version)
            if key is None:
                return QueryBindingVerificationFailure(QueryBindingFailureReason.INVALID_BINDING)
            if type(key) is not ApprovedGuideQueryHmacKey:
                raise GuideQueryFingerprintDependencyError()
            expected_digest = _compute_digest(key, query)
        except Exception:
            return QueryBindingVerificationFailure(QueryBindingFailureReason.DEPENDENCY_ERROR)
        if not hmac.compare_digest(expected_digest, query_fingerprint.digest):
            return QueryBindingVerificationFailure(QueryBindingFailureReason.INVALID_BINDING)
        return QueryBindingVerificationSuccess(query_fingerprint, compute_query_binding_verifier_artifact_ref())


def build_guide_query_fingerprint_producer(keys: GuideQueryHmacKeyProvider) -> GuideQueryFingerprintProducer:
    """Create the producer from the only cross-boundary input: typed key authority."""

    return GuideQueryFingerprintProducer(keys)


def build_production_query_binding_verifier(keys: GuideQueryHmacKeyProvider) -> ProductionQueryBindingVerifier:
    """Create the verifier from the only cross-boundary input: typed key authority."""

    return ProductionQueryBindingVerifier(keys)


def query_binding_verifier_policy_projection() -> dict[str, object]:
    """Return the canonical, secret-free policy projection for the verifier artifact."""

    return {
        "algorithm": APPROVED_QUERY_FINGERPRINT_ALGORITHM,
        "artifact_code": QUERY_BINDING_VERIFIER_ARTIFACT_CODE,
        "artifact_version": QUERY_BINDING_VERIFIER_ARTIFACT_VERSION,
        "digest_format": "64-character lowercase hex",
        "failure_semantics": {
            "approved_key_dependency_unavailable": QueryBindingFailureReason.DEPENDENCY_ERROR.value,
            "invalid_or_mismatch": QueryBindingFailureReason.INVALID_BINDING.value,
        },
        "key_version_namespace": QUERY_HMAC_KEY_VERSION_NAMESPACE,
        "preimage_version": QUERY_HMAC_PREIMAGE_VERSION,
    }


def compute_query_binding_verifier_artifact_ref() -> ImmutableArtifactRef:
    """Return a deterministic identity independent of any injected key or key rotation."""

    canonical_projection = json.dumps(
        query_binding_verifier_policy_projection(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return ImmutableArtifactRef(
        artifact_code=QUERY_BINDING_VERIFIER_ARTIFACT_CODE,
        version=QUERY_BINDING_VERIFIER_ARTIFACT_VERSION,
        content_sha256=hashlib.sha256(canonical_projection).hexdigest(),
    )


def _compute_digest(key: ApprovedGuideQueryHmacKey, normalized_query: SensitiveText) -> str:
    if type(normalized_query) is not SensitiveText:
        raise ValueError("normalized query must be SensitiveText")
    query_value = normalized_query.reveal()
    if type(query_value) is not str:
        raise ValueError("normalized query must contain text")
    preimage = QUERY_HMAC_PREIMAGE_VERSION.encode("ascii") + b"\x00" + query_value.encode("utf-8")
    return key._digest(preimage)


def _is_valid_claim(query: object, fingerprint: object) -> bool:
    if type(query) is not SensitiveText or type(fingerprint) is not QueryFingerprint:
        return False
    try:
        return (
            type(fingerprint.algorithm) is str
            and fingerprint.algorithm == APPROVED_QUERY_FINGERPRINT_ALGORITHM
            and _is_approved_key_version(fingerprint.key_version)
            and type(fingerprint.digest) is str
            and _DIGEST_RE.fullmatch(fingerprint.digest) is not None
        )
    except Exception:
        return False


def _is_approved_key_version(value: object) -> bool:
    return type(value) is str and _KEY_VERSION_RE.fullmatch(value) is not None
