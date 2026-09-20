"""Unit tests for SqlAlchemyEvaluationGuardAuthorityReader (#162 Phase A2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_evaluation_guard_authority import (
    EvaluationGuardAuthorityReadError,
    SqlAlchemyEvaluationGuardAuthorityReader,
)
from ai_worker.tasks.rag.runtime_bundle_builder import (
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberIdentity,
    RuntimeBundleCanonicalConfiguration,
    RuntimeBundleCitationApprovalPinIdentity,
    RuntimeBundleMemberPurpose,
    RuntimeBundleSourceMemberIdentity,
    RuntimeExecutionManifestInput,
    canonical_execution_manifest_hash,
    canonical_runtime_bundle_manifest_hash,
)
from rag_runtime.source_use_approval import SourceUsePurpose

BUNDLE_ID = UUID("55555555-5555-4555-8555-555555555555")
MANIFEST_ID = UUID("66666666-6666-4666-8666-666666666666")
SNAPSHOT_ID = UUID("77777777-7777-4777-8777-777777777777")
APPROVAL_ID = UUID("88888888-8888-4888-8888-888888888888")

MANIFEST_INPUT = RuntimeExecutionManifestInput(
    manifest_key="test-manifest",
    manifest_version="1.0.0",
    schema_version="runtime-manifest-v1",
    git_commit_sha="abcdef1234567",
    worker_artifact_ref="worker-v1",
    model_ref="gpt-4o",
    prompt_ref="prompt-v1",
    parser_ref="parser-v1",
    resolver_ref="resolver-v1",
    guard_policy_ref="policy-v1",
)
MANIFEST_HASH = canonical_execution_manifest_hash(MANIFEST_INPUT)

SOURCE_MEMBER = RuntimeBundleSourceMemberIdentity(
    source_snapshot_id=str(SNAPSHOT_ID),
    source_purpose=RuntimeBundleMemberPurpose.CATALOG,
    source_version="2026.09.20",
    canonical_checksum="1" * 64,
    approval_version="1.0.0",
    scope_policy_hash="2" * 64,
    freshness_policy_hash="3" * 64,
    required=True,
    selected_for_operation=True,
)

CITATION_PIN = RuntimeBundleCitationApprovalPinIdentity(
    source_snapshot_id=str(SNAPSHOT_ID),
    source_use_approval_id=str(APPROVAL_ID),
    source_code="MFDS",
    source_version="2026.09.20",
    approval_version="1.0.0",
    environment="LOCAL",
    purpose=SourceUsePurpose.PATIENT_CITATION,
)

ARTIFACT_MEMBERS = (
    RuntimeBundleArtifactMemberIdentity(
        artifact_kind=RuntimeBundleArtifactKind.CANDIDATE_INDEX,
        artifact_ref="idx-ref-1",
        artifact_version="1.0.0",
        manifest_hash="4" * 64,
    ),
    RuntimeBundleArtifactMemberIdentity(
        artifact_kind=RuntimeBundleArtifactKind.RULE_SET,
        artifact_ref="rule-ref-1",
        artifact_version="1.0.0",
        manifest_hash=None,
    ),
)

BUNDLE_CONFIG = RuntimeBundleCanonicalConfiguration(
    environment_code="LOCAL",
    execution_manifest_hash=MANIFEST_HASH,
    catalog_version="1.0.0",
    catalog_manifest_hash="5" * 64,
    source_members=(SOURCE_MEMBER,),
    artifact_members=ARTIFACT_MEMBERS,
    citation_approval_pins=(CITATION_PIN,),
)
BUNDLE_HASH = canonical_runtime_bundle_manifest_hash(BUNDLE_CONFIG)


def _base_bundle_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "bundle_id": str(BUNDLE_ID),
        "bundle_status": "BUILDING",
        "execution_manifest_id": str(MANIFEST_ID),
        "bundle_manifest_hash": BUNDLE_HASH,
        "environment_code": "LOCAL",
        "catalog_version": "1.0.0",
        "catalog_manifest_hash": "5" * 64,
        "candidate_index_ref": "idx-ref-1",
        "candidate_index_version": "1.0.0",
        "candidate_index_manifest_hash": "4" * 64,
        "knowledge_index_ref": None,
        "knowledge_index_version": None,
        "knowledge_index_manifest_hash": None,
        "rule_set_ref": "rule-ref-1",
        "rule_set_version": "1.0.0",
        "guideline_set_ref": None,
        "guideline_set_version": None,
        "safety_policy_ref": None,
        "safety_policy_version": None,
        "bundle_governance_revision_ref": "GOV-REV-1",
        "manifest_id": str(MANIFEST_ID),
        "manifest_key": "test-manifest",
        "manifest_version": "1.0.0",
        "manifest_hash": MANIFEST_HASH,
        "manifest_schema_version": "runtime-manifest-v1",
        "git_commit_sha": "abcdef1234567",
        "worker_artifact_ref": "worker-v1",
        "model_ref": "gpt-4o",
        "prompt_ref": "prompt-v1",
        "parser_ref": "parser-v1",
        "resolver_ref": "resolver-v1",
        "guard_policy_ref": "policy-v1",
        "env_environment_code": "LOCAL",
        "environment_revision": 10,
        "env_governance_revision_ref": "GOV-REV-1",
        "safety_epoch": 4,
    }
    row.update(overrides)
    return row


def _base_source_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": str(uuid4()),
        "bundle_id": str(BUNDLE_ID),
        "source_snapshot_id": str(SNAPSHOT_ID),
        "source_version": "2026.09.20",
        "canonical_checksum": "1" * 64,
        "approval_version": "1.0.0",
        "scope_policy_hash": "2" * 64,
        "freshness_policy_hash": "3" * 64,
        "source_purpose": "CATALOG",
        "required": True,
        "selected_for_operation": True,
    }
    row.update(overrides)
    return row


def _base_pin_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": str(uuid4()),
        "bundle_id": str(BUNDLE_ID),
        "bundle_manifest_hash": BUNDLE_HASH,
        "source_snapshot_id": str(SNAPSHOT_ID),
        "source_use_approval_id": str(APPROVAL_ID),
        "source_code": "MFDS",
        "source_version": "2026.09.20",
        "approval_version": "1.0.0",
        "environment": "LOCAL",
        "purpose": "PATIENT_CITATION",
    }
    row.update(overrides)
    return row


def _create_mock_reader(
    main_row: dict[str, Any] | None,
    source_rows: list[dict[str, Any]] | None = None,
    pin_rows: list[dict[str, Any]] | None = None,
    *,
    error: Exception | None = None,
) -> SqlAlchemyEvaluationGuardAuthorityReader:
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session

    query_count = 0

    async def _execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal query_count
        query_count += 1
        if error is not None:
            raise error

        result = MagicMock()
        statement_str = str(statement)
        if "rag_runtime_bundle_source" in statement_str:
            result.mappings.return_value.all.return_value = list(source_rows or [])
        elif "rag_runtime_bundle_citation_approval" in statement_str:
            result.mappings.return_value.all.return_value = list(pin_rows or [])
        else:
            result.mappings.return_value.one_or_none.return_value = main_row
        return result

    session.execute.side_effect = _execute
    return SqlAlchemyEvaluationGuardAuthorityReader(lambda: session)


# ---------------------------------------------------------------------------
# Candidate Start Authority Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_candidate_start_success() -> None:
    reader = _create_mock_reader(
        main_row=_base_bundle_row(),
        source_rows=[_base_source_row()],
        pin_rows=[_base_pin_row()],
    )

    obs = await reader.read_candidate_start(
        bundle_id=BUNDLE_ID,
        bundle_manifest_hash=BUNDLE_HASH,
    )

    assert obs is not None
    assert obs.bundle_id == BUNDLE_ID
    assert obs.bundle_manifest_hash == BUNDLE_HASH
    assert obs.bundle_status == "BUILDING"
    assert obs.environment_code == "LOCAL"
    assert obs.runtime_execution_manifest_id == MANIFEST_ID
    assert obs.runtime_execution_manifest_hash == MANIFEST_HASH
    assert obs.governance_revision_ref == "GOV-REV-1"
    assert obs.environment_revision == 10
    assert obs.safety_epoch == 4


@pytest.mark.asyncio
async def test_read_candidate_start_persisted_manifest_corruption() -> None:
    # Corrupt model_ref in manifest while stored manifest_hash is unchanged
    corrupted_row = _base_bundle_row(model_ref="corrupted-model")
    reader = _create_mock_reader(
        main_row=corrupted_row,
        source_rows=[_base_source_row()],
        pin_rows=[_base_pin_row()],
    )

    with pytest.raises(
        EvaluationGuardAuthorityReadError, match="Execution Manifest fields do not match stored manifest_hash"
    ):
        await reader.read_candidate_start(
            bundle_id=BUNDLE_ID,
            bundle_manifest_hash=BUNDLE_HASH,
        )


@pytest.mark.asyncio
async def test_read_candidate_start_persisted_bundle_source_corruption() -> None:
    # Corrupt source checksum while stored bundle_manifest_hash is unchanged
    corrupted_source = _base_source_row(canonical_checksum="9" * 64)
    reader = _create_mock_reader(
        main_row=_base_bundle_row(),
        source_rows=[corrupted_source],
        pin_rows=[_base_pin_row()],
    )

    with pytest.raises(
        EvaluationGuardAuthorityReadError, match="Runtime Bundle rows do not match stored bundle_manifest_hash"
    ):
        await reader.read_candidate_start(
            bundle_id=BUNDLE_ID,
            bundle_manifest_hash=BUNDLE_HASH,
        )


@pytest.mark.asyncio
async def test_read_candidate_start_persisted_bundle_artifact_corruption() -> None:
    # Corrupt rule_set_version while stored bundle_manifest_hash is unchanged
    corrupted_row = _base_bundle_row(rule_set_version="2.0.0")
    reader = _create_mock_reader(
        main_row=corrupted_row,
        source_rows=[_base_source_row()],
        pin_rows=[_base_pin_row()],
    )

    with pytest.raises(
        EvaluationGuardAuthorityReadError, match="Runtime Bundle rows do not match stored bundle_manifest_hash"
    ):
        await reader.read_candidate_start(
            bundle_id=BUNDLE_ID,
            bundle_manifest_hash=BUNDLE_HASH,
        )


@pytest.mark.asyncio
async def test_read_candidate_start_minimal_building_bundle() -> None:
    # Case A: Minimal BUILDING Bundle — only CANDIDATE_INDEX is present, remaining 4 kinds absent
    minimal_artifact_members = (
        RuntimeBundleArtifactMemberIdentity(
            artifact_kind=RuntimeBundleArtifactKind.CANDIDATE_INDEX,
            artifact_ref="idx-ref-1",
            artifact_version="1.0.0",
            manifest_hash="4" * 64,
        ),
    )
    config = RuntimeBundleCanonicalConfiguration(
        environment_code="LOCAL",
        execution_manifest_hash=MANIFEST_HASH,
        catalog_version="1.0.0",
        catalog_manifest_hash="5" * 64,
        source_members=(SOURCE_MEMBER,),
        artifact_members=minimal_artifact_members,
        citation_approval_pins=(CITATION_PIN,),
    )
    minimal_bundle_hash = canonical_runtime_bundle_manifest_hash(config)
    row = _base_bundle_row(
        bundle_manifest_hash=minimal_bundle_hash,
        rule_set_ref=None,
        rule_set_version=None,
        knowledge_index_ref=None,
        knowledge_index_version=None,
        knowledge_index_manifest_hash=None,
        guideline_set_ref=None,
        guideline_set_version=None,
        safety_policy_ref=None,
        safety_policy_version=None,
    )
    reader = _create_mock_reader(
        main_row=row,
        source_rows=[_base_source_row()],
        pin_rows=[_base_pin_row()],
    )
    obs = await reader.read_candidate_start(
        bundle_id=BUNDLE_ID,
        bundle_manifest_hash=minimal_bundle_hash,
    )
    assert obs is not None
    assert obs.bundle_manifest_hash == minimal_bundle_hash


@pytest.mark.asyncio
async def test_read_candidate_start_optional_artifacts_three_kinds() -> None:
    # Case B: CANDIDATE_INDEX, KNOWLEDGE_INDEX, and RULE_SET present
    three_artifact_members = (
        RuntimeBundleArtifactMemberIdentity(
            artifact_kind=RuntimeBundleArtifactKind.CANDIDATE_INDEX,
            artifact_ref="idx-ref-1",
            artifact_version="1.0.0",
            manifest_hash="4" * 64,
        ),
        RuntimeBundleArtifactMemberIdentity(
            artifact_kind=RuntimeBundleArtifactKind.KNOWLEDGE_INDEX,
            artifact_ref="k-ref-1",
            artifact_version="1.0.0",
            manifest_hash="6" * 64,
        ),
        RuntimeBundleArtifactMemberIdentity(
            artifact_kind=RuntimeBundleArtifactKind.RULE_SET,
            artifact_ref="rule-ref-1",
            artifact_version="1.0.0",
            manifest_hash=None,
        ),
    )
    config = RuntimeBundleCanonicalConfiguration(
        environment_code="LOCAL",
        execution_manifest_hash=MANIFEST_HASH,
        catalog_version="1.0.0",
        catalog_manifest_hash="5" * 64,
        source_members=(SOURCE_MEMBER,),
        artifact_members=three_artifact_members,
        citation_approval_pins=(CITATION_PIN,),
    )
    three_bundle_hash = canonical_runtime_bundle_manifest_hash(config)
    row = _base_bundle_row(
        bundle_manifest_hash=three_bundle_hash,
        knowledge_index_ref="k-ref-1",
        knowledge_index_version="1.0.0",
        knowledge_index_manifest_hash="6" * 64,
        rule_set_ref="rule-ref-1",
        rule_set_version="1.0.0",
    )
    reader = _create_mock_reader(
        main_row=row,
        source_rows=[_base_source_row()],
        pin_rows=[_base_pin_row()],
    )
    obs = await reader.read_candidate_start(
        bundle_id=BUNDLE_ID,
        bundle_manifest_hash=three_bundle_hash,
    )
    assert obs is not None
    assert obs.bundle_manifest_hash == three_bundle_hash


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper_field, tamper_value",
    [
        ("candidate_index_ref", "tampered-idx-ref"),
        ("candidate_index_version", "9.9.9"),
        ("candidate_index_manifest_hash", "e" * 64),
        ("rule_set_ref", "tampered-rule-ref"),
        ("rule_set_version", "2.0.0"),
    ],
)
async def test_read_candidate_start_artifact_tamper_matrix(tamper_field: str, tamper_value: str) -> None:
    # Case C: Existing artifact tamper — corrupting artifact_ref, artifact_version, or manifest_hash rejects
    corrupted_row = _base_bundle_row(**{tamper_field: tamper_value})
    reader = _create_mock_reader(
        main_row=corrupted_row,
        source_rows=[_base_source_row()],
        pin_rows=[_base_pin_row()],
    )
    with pytest.raises(
        EvaluationGuardAuthorityReadError, match="Runtime Bundle rows do not match stored bundle_manifest_hash"
    ):
        await reader.read_candidate_start(
            bundle_id=BUNDLE_ID,
            bundle_manifest_hash=BUNDLE_HASH,
        )


@pytest.mark.asyncio
async def test_read_candidate_start_persisted_bundle_pin_corruption() -> None:
    # Corrupt citation approval pin source_version while stored bundle_manifest_hash is unchanged
    corrupted_pin = _base_pin_row(source_version="9999.99.99")
    reader = _create_mock_reader(
        main_row=_base_bundle_row(),
        source_rows=[_base_source_row()],
        pin_rows=[corrupted_pin],
    )

    with pytest.raises(
        EvaluationGuardAuthorityReadError, match="Runtime Bundle rows do not match stored bundle_manifest_hash"
    ):
        await reader.read_candidate_start(
            bundle_id=BUNDLE_ID,
            bundle_manifest_hash=BUNDLE_HASH,
        )


@pytest.mark.asyncio
async def test_read_candidate_start_missing_returns_none() -> None:
    reader = _create_mock_reader(main_row=None)
    obs = await reader.read_candidate_start(
        bundle_id=BUNDLE_ID,
        bundle_manifest_hash=BUNDLE_HASH,
    )
    assert obs is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides, expected_err",
    [
        ({"bundle_status": "READY"}, "bundle status is READY, must be BUILDING"),
        ({"bundle_status": "FAILED"}, "bundle status is FAILED, must be BUILDING"),
        ({"environment_code": "CLOSED_DEMO"}, "environment is CLOSED_DEMO, must be LOCAL"),
        ({"bundle_governance_revision_ref": ""}, "bundle governance_revision_ref is empty"),
        ({"env_governance_revision_ref": "DIFFERENT_GOV"}, "bundle governance_revision_ref does not match environment"),
        ({"environment_revision": 0}, "environment_revision must be >= 1"),
        ({"safety_epoch": 0}, "safety_epoch must be >= 1"),
    ],
)
async def test_read_candidate_start_structural_negative_matrix(
    overrides: dict[str, Any],
    expected_err: str,
) -> None:
    reader = _create_mock_reader(
        main_row=_base_bundle_row(**overrides),
        source_rows=[_base_source_row()],
        pin_rows=[_base_pin_row()],
    )

    with pytest.raises(EvaluationGuardAuthorityReadError, match=expected_err):
        await reader.read_candidate_start(
            bundle_id=BUNDLE_ID,
            bundle_manifest_hash=BUNDLE_HASH,
        )


@pytest.mark.asyncio
async def test_read_candidate_start_active_pointer_non_use() -> None:
    """Ensure active_bundle_id / active_bundle_manifest_hash are not used as authority selectors."""
    captured_statements: list[str] = []
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session

    async def _capture(statement: Any, *args: Any, **kwargs: Any) -> Any:
        statement_str = str(statement)
        captured_statements.append(statement_str)
        result = MagicMock()
        if "rag_runtime_bundle_source" in statement_str:
            result.mappings.return_value.all.return_value = [_base_source_row()]
        elif "rag_runtime_bundle_citation_approval" in statement_str:
            result.mappings.return_value.all.return_value = [_base_pin_row()]
        else:
            result.mappings.return_value.one_or_none.return_value = _base_bundle_row()
        return result

    session.execute.side_effect = _capture
    reader = SqlAlchemyEvaluationGuardAuthorityReader(lambda: session)

    obs = await reader.read_candidate_start(
        bundle_id=BUNDLE_ID,
        bundle_manifest_hash=BUNDLE_HASH,
    )
    assert obs is not None

    main_query = captured_statements[0]
    assert "active_bundle_id" not in main_query
    assert "active_bundle_manifest_hash" not in main_query


# ---------------------------------------------------------------------------
# Environment Fence Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_environment_fence_success() -> None:
    fence_row = {
        "environment_code": "LOCAL",
        "environment_revision": 10,
        "governance_revision_ref": "GOV-REV-1",
        "safety_epoch": 4,
    }
    reader = _create_mock_reader(main_row=fence_row)

    fence = await reader.read_environment_fence(environment_code="LOCAL")
    assert fence is not None
    assert fence.environment_code == "LOCAL"
    assert fence.environment_revision == 10
    assert fence.governance_revision_ref == "GOV-REV-1"
    assert fence.safety_epoch == 4


@pytest.mark.asyncio
async def test_read_environment_fence_missing_returns_none() -> None:
    reader = _create_mock_reader(main_row=None)
    fence = await reader.read_environment_fence(environment_code="LOCAL")
    assert fence is None


@pytest.mark.asyncio
async def test_read_environment_fence_corrupt_row() -> None:
    fence_row = {
        "environment_code": "LOCAL",
        "environment_revision": 0,
        "governance_revision_ref": "GOV-REV-1",
        "safety_epoch": 4,
    }
    reader = _create_mock_reader(main_row=fence_row)

    with pytest.raises(EvaluationGuardAuthorityReadError, match="corrupt environment row"):
        await reader.read_environment_fence(environment_code="LOCAL")
