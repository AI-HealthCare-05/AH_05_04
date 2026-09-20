"""Worker compatibility export for the shared #180 B2 query-binding contract.

The implementation is intentionally owned by ``rag_runtime.guide_query_binding``
so Backend composition and Worker retrieval depend on the same pure boundary.
"""

from rag_runtime.guide_query_binding import (
    APPROVED_QUERY_FINGERPRINT_ALGORITHM,
    QUERY_BINDING_VERIFIER_ARTIFACT_CODE,
    QUERY_BINDING_VERIFIER_ARTIFACT_VERSION,
    QUERY_HMAC_PREIMAGE_VERSION,
    ApprovedGuideQueryHmacKey,
    GuideQueryFingerprintDependencyError,
    GuideQueryFingerprintProducer,
    GuideQueryHmacKeyProvider,
    ProductionQueryBindingVerifier,
    build_guide_query_fingerprint_producer,
    build_production_query_binding_verifier,
    compute_query_binding_verifier_artifact_ref,
    query_binding_verifier_policy_projection,
)

__all__ = [
    "APPROVED_QUERY_FINGERPRINT_ALGORITHM",
    "ApprovedGuideQueryHmacKey",
    "GuideQueryFingerprintDependencyError",
    "GuideQueryFingerprintProducer",
    "GuideQueryHmacKeyProvider",
    "ProductionQueryBindingVerifier",
    "build_guide_query_fingerprint_producer",
    "build_production_query_binding_verifier",
    "QUERY_BINDING_VERIFIER_ARTIFACT_CODE",
    "QUERY_BINDING_VERIFIER_ARTIFACT_VERSION",
    "QUERY_HMAC_PREIMAGE_VERSION",
    "compute_query_binding_verifier_artifact_ref",
    "query_binding_verifier_policy_projection",
]
