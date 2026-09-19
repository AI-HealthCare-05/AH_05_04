from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_worker.adapters.sqlalchemy_request_guard_runtime_binding import (
    RequestGuardRuntimeBindingReadError,
    SqlAlchemyRequestGuardRuntimeBindingReader,
)
from ai_worker.tasks.rag.citation_authorization import GuardDecision, RuntimeEnvironment
from ai_worker.tasks.rag.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingAssemblyError,
    build_origin_request_guard_binding,
    build_runtime_authorization_binding,
)
from rag_runtime.request_authority import (
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
)
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    RequestGuardRuntimeBindingRef,
    canonical_scope_manifest_hash,
    compute_request_guard_runtime_binding_ref,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode


def _observation(
    outcome: RequestAuthorityDecisionOutcome = RequestAuthorityDecisionOutcome.PASS,
) -> RequestGuardRuntimeBindingObservation:
    scopes = ("GUIDE", "PATIENT_CITATION")
    return RequestGuardRuntimeBindingObservation(
        request_guard_decision_id=UUID("80620000-0000-4000-8000-000000000001"),
        actual_decision_outcome=outcome,
        user_id=UUID("80620000-0000-4000-8000-000000000002"),
        request_operation_code="GUIDE_SYNC_ANSWER",
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
        environment=RuntimeEnvironmentCode.PRODUCTION,
        bundle_id=UUID("80620000-0000-4000-8000-000000000003"),
        bundle_manifest_hash="b" * 64,
        request_scope_codes=scopes,
        scope_manifest_hash=canonical_scope_manifest_hash(scopes),
        legacy_request_authority_ref=RequestAuthorityArtifactRef("request_guard_authority", "1.0", "a" * 64),
    )


def test_runtime_binding_is_built_only_from_persisted_observation() -> None:
    binding = build_runtime_authorization_binding(_observation())

    assert binding.environment is RuntimeEnvironment.PRODUCTION
    assert binding.bundle_id == "80620000-0000-4000-8000-000000000003"
    assert binding.request_scope_codes == ("GUIDE", "PATIENT_CITATION")


def test_origin_binding_uses_the_per_request_authority_ref_and_actual_pass() -> None:
    binding = build_origin_request_guard_binding(_observation())

    assert binding.guard_ref.artifact_code == "request_guard_runtime_binding"
    assert binding.guard_ref.content_sha256
    assert binding.decision is GuardDecision.PASS
    assert binding.environment is RuntimeEnvironment.PRODUCTION


def test_persisted_fail_cannot_be_projected_as_origin_pass() -> None:
    with pytest.raises(RequestGuardRuntimeBindingAssemblyError):
        build_origin_request_guard_binding(_observation(RequestAuthorityDecisionOutcome.FAIL))


class _FakeResult:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def mappings(self) -> "_FakeResult":
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self._row


class _FakeSession:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, _statement: object) -> _FakeResult:
        return _FakeResult(self._row)


def _row_from_observation(observation: RequestGuardRuntimeBindingObservation) -> dict[str, object]:
    reference = compute_request_guard_runtime_binding_ref(observation)
    return {
        "request_guard_decision_id": str(observation.request_guard_decision_id),
        "artifact_code": reference.artifact_code,
        "artifact_version": reference.version,
        "artifact_content_sha256": reference.content_sha256,
        "actual_decision_outcome": observation.actual_decision_outcome.value,
        "user_id": str(observation.user_id),
        "request_operation_code": observation.request_operation_code,
        "decision_stage": observation.decision_stage.value,
        "environment_code": observation.environment.value,
        "bundle_id": str(observation.bundle_id),
        "bundle_manifest_hash": observation.bundle_manifest_hash,
        "request_scope_codes": list(observation.request_scope_codes),
        "scope_manifest_hash": observation.scope_manifest_hash,
        "legacy_request_guard_artifact_code": observation.legacy_request_authority_ref.artifact_code,
        "legacy_request_guard_artifact_version": observation.legacy_request_authority_ref.version,
        "legacy_request_guard_content_sha256": observation.legacy_request_authority_ref.content_sha256,
    }


class _FakeSessionFactory:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def __call__(self) -> _FakeSession:
        return _FakeSession(self._row)


async def test_reader_recomputes_semantic_identity_after_exact_lookup() -> None:
    observation = _observation()
    row = _row_from_observation(observation)
    requested = RequestGuardRuntimeBindingRef(
        artifact_code=str(row["artifact_code"]),
        version=str(row["artifact_version"]),
        content_sha256=str(row["artifact_content_sha256"]),
    )
    row["request_operation_code"] = "MUTATED_OPERATION"

    reader = SqlAlchemyRequestGuardRuntimeBindingReader(
        cast(async_sessionmaker[AsyncSession], _FakeSessionFactory(row))
    )
    with pytest.raises(RequestGuardRuntimeBindingReadError, match="artifact identity mismatch"):
        await reader.read_exact(requested)
