import hashlib
import hmac

import pytest

from ai_worker.tasks.rag.evidence_retrieval import (
    QueryBindingFailureReason,
    QueryBindingVerificationFailure,
    QueryBindingVerificationSuccess,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.production_query_binding import (
    ApprovedGuideQueryHmacKey,
    GuideQueryFingerprintDependencyError,
    GuideQueryFingerprintProducer,
    ProductionQueryBindingVerifier,
    compute_query_binding_verifier_artifact_ref,
    query_binding_verifier_policy_projection,
)
from rag_runtime.guide_query_binding import (
    GuideQueryFingerprintProducer as SharedGuideQueryFingerprintProducer,
)
from rag_runtime.guide_query_binding import (
    ProductionQueryBindingVerifier as SharedProductionQueryBindingVerifier,
)
from rag_runtime.guide_query_binding import (
    build_guide_query_fingerprint_producer,
    build_production_query_binding_verifier,
)


class RetainedKeys:
    def __init__(self, keys: dict[str, ApprovedGuideQueryHmacKey], active_version: str) -> None:
        self._keys = keys
        self._active_version = active_version

    def active_key_version(self) -> str:
        return self._active_version

    def key_for_version(self, key_version: str) -> ApprovedGuideQueryHmacKey | None:
        return self._keys.get(key_version)


class UnavailableKeys:
    def active_key_version(self) -> str:
        raise RuntimeError("key dependency unavailable")

    def key_for_version(self, key_version: str) -> ApprovedGuideQueryHmacKey | None:
        raise RuntimeError("key dependency unavailable")


def test_worker_module_reexports_the_shared_query_binding_contract() -> None:
    assert GuideQueryFingerprintProducer is SharedGuideQueryFingerprintProducer
    assert ProductionQueryBindingVerifier is SharedProductionQueryBindingVerifier


def test_shared_factories_accept_only_the_typed_key_provider(keys: RetainedKeys) -> None:
    assert isinstance(build_guide_query_fingerprint_producer(keys), GuideQueryFingerprintProducer)
    assert isinstance(build_production_query_binding_verifier(keys), ProductionQueryBindingVerifier)


@pytest.fixture
def keys() -> RetainedKeys:
    return RetainedKeys(
        {"guide-query-hmac-key@1": ApprovedGuideQueryHmacKey(b"synthetic-test-key")},
        "guide-query-hmac-key@1",
    )


def test_producer_uses_the_frozen_hmac_preimage_without_normalizing_query(keys: RetainedKeys) -> None:
    query = SensitiveText("약 이름  10mg")

    fingerprint = GuideQueryFingerprintProducer(keys).produce(query)

    expected = hmac.new(
        b"synthetic-test-key",
        b"query-hmac@1\x00" + "약 이름  10mg".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert fingerprint == QueryFingerprint("HMAC-SHA-256", "guide-query-hmac-key@1", expected)
    assert GuideQueryFingerprintProducer(keys).produce(query) == fingerprint


def test_verifier_accepts_the_producer_fingerprint_and_returns_stable_artifact(keys: RetainedKeys) -> None:
    query = SensitiveText("합성 복약 정보")
    fingerprint = GuideQueryFingerprintProducer(keys).produce(query)

    result = ProductionQueryBindingVerifier(keys).verify(query, fingerprint)

    assert result == QueryBindingVerificationSuccess(fingerprint, compute_query_binding_verifier_artifact_ref())
    assert compute_query_binding_verifier_artifact_ref().artifact_code == "guide-query-binding-verifier"
    assert compute_query_binding_verifier_artifact_ref().version == "1.0.0"


@pytest.mark.parametrize(
    "fingerprint",
    [
        QueryFingerprint("SHA-256", "guide-query-hmac-key@1", "0" * 64),
        QueryFingerprint("HMAC-SHA-256", "guide-query-hmac-key@2", "0" * 64),
        QueryFingerprint("HMAC-SHA-256", "guide-query-hmac-key@1", "A" * 64),
        QueryFingerprint("HMAC-SHA-256", "guide-query-hmac-key@1", "0" * 63),
        QueryFingerprint("HMAC-SHA-256", "guide-query-hmac-key@1", "0" * 65),
    ],
)
def test_verifier_rejects_malformed_fingerprints(keys: RetainedKeys, fingerprint: QueryFingerprint) -> None:
    result = ProductionQueryBindingVerifier(keys).verify(SensitiveText("합성 복약 정보"), fingerprint)

    assert result == QueryBindingVerificationFailure(QueryBindingFailureReason.INVALID_BINDING)


def test_verifier_rejects_one_byte_query_change(keys: RetainedKeys) -> None:
    fingerprint = GuideQueryFingerprintProducer(keys).produce(SensitiveText("합성 복약 정보"))

    result = ProductionQueryBindingVerifier(keys).verify(SensitiveText("합성 복약 정보!"), fingerprint)

    assert result == QueryBindingVerificationFailure(QueryBindingFailureReason.INVALID_BINDING)


def test_verifier_rejects_one_character_digest_tamper(keys: RetainedKeys) -> None:
    query = SensitiveText("합성 복약 정보")
    fingerprint = GuideQueryFingerprintProducer(keys).produce(query)
    replacement = "0" if fingerprint.digest[0] != "0" else "1"
    tampered = QueryFingerprint(fingerprint.algorithm, fingerprint.key_version, replacement + fingerprint.digest[1:])

    result = ProductionQueryBindingVerifier(keys).verify(query, tampered)

    assert result == QueryBindingVerificationFailure(QueryBindingFailureReason.INVALID_BINDING)


def test_verifier_masks_unavailable_key_dependency() -> None:
    result = ProductionQueryBindingVerifier(UnavailableKeys()).verify(
        SensitiveText("합성 복약 정보"),
        QueryFingerprint("HMAC-SHA-256", "guide-query-hmac-key@1", "0" * 64),
    )

    assert result == QueryBindingVerificationFailure(QueryBindingFailureReason.DEPENDENCY_ERROR)


def test_producer_masks_unavailable_key_dependency_without_secret_text() -> None:
    secret = "synthetic-test-key"

    with pytest.raises(GuideQueryFingerprintDependencyError) as raised:
        GuideQueryFingerprintProducer(UnavailableKeys()).produce(SensitiveText("합성 복약 정보"))

    assert secret not in str(raised.value)


def test_secret_free_representations_and_artifact_projection() -> None:
    secret = b"synthetic-test-key"
    key = ApprovedGuideQueryHmacKey(secret)
    projection = query_binding_verifier_policy_projection()

    assert secret.decode() not in repr(key)
    assert secret.decode() not in repr(GuideQueryFingerprintProducer(RetainedKeys({}, "guide-query-hmac-key@1")))
    assert secret.decode() not in repr(ProductionQueryBindingVerifier(RetainedKeys({}, "guide-query-hmac-key@1")))
    assert secret.decode() not in str(projection)
    assert "digest" not in projection
    assert "query" not in projection
