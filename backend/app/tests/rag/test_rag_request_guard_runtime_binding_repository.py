from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_request_authority import RagRequestGuardAuthority
from app.models.rag_runtime import (
    RagRuntimeBundleStatus,
    RagRuntimeExecutionManifest,
    RagRuntimeReleaseBundle,
)
from app.models.users import User
from app.repositories.rag_request_guard_runtime_binding_repository import (
    RagRequestGuardRuntimeBindingRepository,
    RequestGuardRuntimeBindingConflictError,
    RequestGuardRuntimeBindingValidationError,
)
from rag_runtime.request_authority import (
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    compute_request_guard_authority_ref,
)
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    canonical_scope_manifest_hash,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode

USER_ID = UUID("80610000-0000-4000-8000-000000000001")
REQUEST_ID = UUID("80610000-0000-4000-8000-000000000002")
BUNDLE_ID = UUID("80610000-0000-4000-8000-000000000003")
MANIFEST_ID = UUID("80610000-0000-4000-8000-000000000004")
OPERATION = "GUIDE_SYNC_ANSWER"
SCOPES = ("GUIDE", "PATIENT_CITATION")


async def _seed_authorities(db_session: AsyncSession) -> tuple[User, RequestAuthorityArtifactRef]:
    user = User(
        id=USER_ID,
        email="request-binding-806@example.com",
        hashed_password="synthetic-hash",
        name="테스트",
    )
    manifest = RagRuntimeExecutionManifest(
        id=MANIFEST_ID,
        manifest_key="synthetic-request-binding",
        manifest_version="1",
        manifest_hash="a" * 64,
        schema_version="1",
        git_commit_sha="8060000",
    )
    bundle = RagRuntimeReleaseBundle(
        id=BUNDLE_ID,
        bundle_key="synthetic-bundle",
        bundle_version="1",
        bundle_status=RagRuntimeBundleStatus.BUILDING,
        execution_manifest_id=MANIFEST_ID,
        bundle_manifest_hash="b" * 64,
        environment_code=RuntimeEnvironmentCode.PRODUCTION.value,
        catalog_version="catalog-1",
        catalog_manifest_hash="c" * 64,
    )
    legacy_ref = compute_request_guard_authority_ref(
        user_id=USER_ID,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
    )
    legacy = RagRequestGuardAuthority(
        id=uuid4(),
        artifact_code=legacy_ref.artifact_code,
        artifact_version=legacy_ref.version,
        artifact_content_sha256=legacy_ref.content_sha256,
        user_id=USER_ID,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST.value,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(manifest)
    await db_session.flush()
    db_session.add(bundle)
    await db_session.flush()
    db_session.add(legacy)
    await db_session.flush()
    return user, legacy_ref


def _observation(legacy_ref: RequestAuthorityArtifactRef, **overrides: object) -> RequestGuardRuntimeBindingObservation:
    values: dict[str, Any] = {
        "request_guard_decision_id": REQUEST_ID,
        "actual_decision_outcome": RequestAuthorityDecisionOutcome.PASS,
        "user_id": USER_ID,
        "request_operation_code": OPERATION,
        "decision_stage": RequestAuthorityDecisionStage.REQUEST,
        "environment": RuntimeEnvironmentCode.PRODUCTION,
        "bundle_id": BUNDLE_ID,
        "bundle_manifest_hash": "b" * 64,
        "request_scope_codes": SCOPES,
        "scope_manifest_hash": canonical_scope_manifest_hash(SCOPES),
        "legacy_request_authority_ref": legacy_ref,
    }
    values.update(overrides)
    return RequestGuardRuntimeBindingObservation(**values)


async def test_write_then_exact_read_preserves_the_per_request_binding(db_session: AsyncSession) -> None:
    _, legacy_ref = await _seed_authorities(db_session)
    repository = RagRequestGuardRuntimeBindingRepository(db_session)
    observation = _observation(legacy_ref)

    reference = await repository.record(observation)

    assert await repository.get_exact(reference) == observation


async def test_same_user_and_operation_with_different_request_ids_are_distinct(
    db_session: AsyncSession,
) -> None:
    _, legacy_ref = await _seed_authorities(db_session)
    repository = RagRequestGuardRuntimeBindingRepository(db_session)

    first = await repository.record(_observation(legacy_ref))
    second = await repository.record(
        _observation(legacy_ref, request_guard_decision_id=UUID("80610000-0000-4000-8000-000000000005"))
    )

    assert first != second


async def test_same_request_id_with_different_semantics_conflicts(db_session: AsyncSession) -> None:
    _, legacy_ref = await _seed_authorities(db_session)
    repository = RagRequestGuardRuntimeBindingRepository(db_session)
    await repository.record(_observation(legacy_ref))

    with pytest.raises(RequestGuardRuntimeBindingConflictError):
        await repository.record(
            replace(
                _observation(legacy_ref),
                actual_decision_outcome=RequestAuthorityDecisionOutcome.FAIL,
            )
        )


async def test_missing_legacy_guard_is_rejected(db_session: AsyncSession) -> None:
    await _seed_authorities(db_session)
    repository = RagRequestGuardRuntimeBindingRepository(db_session)
    missing = RequestAuthorityArtifactRef("request_guard_authority", "1.0", "d" * 64)

    with pytest.raises(RequestGuardRuntimeBindingValidationError):
        await repository.record(_observation(missing))


async def test_bundle_id_and_manifest_hash_must_match_before_insert(db_session: AsyncSession) -> None:
    _, legacy_ref = await _seed_authorities(db_session)
    repository = RagRequestGuardRuntimeBindingRepository(db_session)

    with pytest.raises(RequestGuardRuntimeBindingValidationError, match="Bundle id/hash"):
        await repository.record(_observation(legacy_ref, bundle_manifest_hash="c" * 64))
