"""Tests for knowledge materialization admin runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from ai_worker.admin.knowledge_materialization import (
    MaterializationRunnerConfig,
    build_sanitized_summary,
    discover_authoritative_members,
    execute_materialization,
    main,
    parse_args,
)
from ai_worker.tasks.rag.knowledge_materialization import (
    CHUNK_POLICY_VERSION,
    KnowledgeDocumentDraft,
    KnowledgeMaterializationError,
    KnowledgeMaterializationFailureReason,
    KnowledgeMaterializationReceipt,
    KnowledgeMaterializationResult,
    MaterializationOutcome,
    MaterializationSourceDocument,
    MaterializedChunkReceipt,
    MaterializedDocumentReceipt,
)
from ai_worker.tasks.rag.mfds_label_chunk_policy import KnowledgeChunkDraft
from ai_worker.tasks.rag.source_ingestion.mfds_label import SnapshotMemberBinding
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SourceSnapshotMemberKind

_VALID_SNAPSHOT_ID = UUID("073ee706-d039-49b5-8ca5-e92cd021f087")
_VALID_ITEM_SEQ = "200610660"
_VALID_CHECKSUM = "a5df76494560f07db5f919b5c37dce4c681cbc214b27450466348a1849039cd3"


def _make_valid_env(tmp_path: Path) -> dict[str, str]:
    return {
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_NAME": "healthcare_test",
        "KNOWLEDGE_INDEX_BUILDER_USER": "builder_usr",
        "KNOWLEDGE_INDEX_BUILDER_PASSWORD": "builder_secret_pass",
        "SOURCE_ARTIFACT_STORAGE_BACKEND": "LOCAL_PRIVATE",
        "SOURCE_ARTIFACT_LOCAL_ROOT": str(tmp_path.resolve()),
    }


def _make_synthetic_source_doc(
    *,
    snapshot_id: UUID,
    member_id: UUID,
    section: str,
    item_seq: str = _VALID_ITEM_SEQ,
    snapshot_status: str = "CURRENT",
    canonical_checksum: str = _VALID_CHECKSUM,
    source_code: str = "MFDS",
) -> MaterializationSourceDocument:
    return MaterializationSourceDocument(
        source_id=uuid4(),
        source_code=source_code,
        source_lifecycle_status="ACTIVE",
        endpoint_id=uuid4(),
        endpoint_code="PRODUCTS",
        endpoint_lifecycle_status="VERIFIED",
        endpoint_runtime_status="ENABLED",
        endpoint_acquisition_status="APPROVED",
        operation_id=uuid4(),
        operation_code="DEFAULT",
        operation_runtime_status="ENABLED",
        operation_acquisition_status="APPROVED",
        snapshot_id=snapshot_id,
        source_version="external:v1",
        canonical_checksum=canonical_checksum,
        raw_manifest_checksum="a" * 64,
        schema_version="mfds-label-selected-product@1",
        parser_version="mfds-label-xml@1",
        normalization_version="mfds-label-xml-structure@1",
        canonicalization_spec_version="mfds-label-selected-product@1",
        snapshot_verification_status=snapshot_status,
        ingestion_run_id=uuid4(),
        ingestion_run_status="SUCCEEDED",
        member_id=member_id,
        member_kind="ARTIFACT",
        locator=f"mfds-label/{item_seq}/{section}",
        content_sha256="b" * 64,
        ingestion_artifact_id=uuid4(),
        artifact_key=f"mfds-label/{item_seq}/{section}.xml",
        section=section,
        artifact_kind="RAW_RESPONSE",
        page_number=1 if section == "EE" else (2 if section == "UD" else 3),
        storage_backend="LOCAL_PRIVATE",
        reject_code=None,
        parser_location=None,
        raw_checksum="b" * 64,
        byte_size=1000,
        content_type="application/download; UTF-8; charset=UTF-8",
        object_key=f"sha256/{'b' * 2}/{'b' * 64}.artifact",
    )


def _make_synthetic_receipt(
    snapshot_id: UUID,
    item_seq: str,
    member_ids: tuple[UUID, ...],
) -> KnowledgeMaterializationReceipt:
    doc_receipts = []
    for idx, mid in enumerate(member_ids):
        doc_id = uuid4()
        chunk_id = uuid4()
        chunk_receipt = MaterializedChunkReceipt(
            knowledge_chunk_id=chunk_id,
            chunk_index=0,
            content_hash="c" * 64,
        )
        doc_receipts.append(
            MaterializedDocumentReceipt(
                knowledge_document_id=doc_id,
                source_snapshot_member_id=mid,
                external_document_id=f"mfds-label:{item_seq}:{('EE', 'UD', 'NB')[idx]}",
                document_content_hash="b" * 64,
                locator=f"mfds-label/{item_seq}/{('EE', 'UD', 'NB')[idx]}",
                chunks=(chunk_receipt,),
            )
        )
    return KnowledgeMaterializationReceipt(
        snapshot_id=snapshot_id,
        source_code="MFDS",
        source_version="external:v1",
        snapshot_canonical_checksum=_VALID_CHECKSUM,
        canonicalization_spec_version="mfds-label-selected-product@1",
        item_seq=item_seq,
        chunk_policy_version=CHUNK_POLICY_VERSION,
        documents=tuple(doc_receipts),
    )


# =============================================================================
# 1. Config Validation
# =============================================================================


def test_config_from_valid_environment(tmp_path: Path) -> None:
    env = _make_valid_env(tmp_path)
    config = MaterializationRunnerConfig.from_environment(env)
    assert config.builder_user == "builder_usr"
    assert config.artifact_root == tmp_path.resolve()
    assert config.url.host == "localhost"
    assert config.url.port == 5432
    assert config.url.database == "healthcare_test"
    assert config.url.username == "builder_usr"
    # Secret must not leak in repr
    assert "builder_secret_pass" not in repr(config)
    assert "builder_secret_pass" not in repr(config.url)


@pytest.mark.parametrize(
    "leaked_key",
    [
        "DB_PASSWORD",
        "DB_APP_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "DB_ADMIN_PASSWORD",
        "SOURCE_WRITER_PASSWORD",
        "SOURCE_CLEANUP_EXECUTOR_PASSWORD",
        "CATALOG_WRITER_PASSWORD",
    ],
)
def test_config_rejects_credential_leakage(tmp_path: Path, leaked_key: str) -> None:
    env = _make_valid_env(tmp_path)
    env[leaked_key] = "foreign_password"
    with pytest.raises(ValueError, match="isolated credential environment"):
        MaterializationRunnerConfig.from_environment(env)


@pytest.mark.parametrize(
    ("missing_key", "error_match"),
    [
        ("DB_HOST", "Database endpoint configuration"),
        ("DB_PORT", "Database endpoint configuration"),
        ("DB_NAME", "Database endpoint configuration"),
        ("KNOWLEDGE_INDEX_BUILDER_USER", "Builder credentials"),
        ("KNOWLEDGE_INDEX_BUILDER_PASSWORD", "Builder credentials"),
        ("SOURCE_ARTIFACT_STORAGE_BACKEND", "SOURCE_ARTIFACT_STORAGE_BACKEND"),
        ("SOURCE_ARTIFACT_LOCAL_ROOT", "SOURCE_ARTIFACT_LOCAL_ROOT"),
    ],
)
def test_config_rejects_missing_fields(tmp_path: Path, missing_key: str, error_match: str) -> None:
    env = _make_valid_env(tmp_path)
    del env[missing_key]
    with pytest.raises(ValueError, match=error_match):
        MaterializationRunnerConfig.from_environment(env)


def test_config_rejects_non_local_private_backend(tmp_path: Path) -> None:
    env = _make_valid_env(tmp_path)
    env["SOURCE_ARTIFACT_STORAGE_BACKEND"] = "S3_PRIVATE"
    with pytest.raises(ValueError, match="SOURCE_ARTIFACT_STORAGE_BACKEND must be 'LOCAL_PRIVATE'"):
        MaterializationRunnerConfig.from_environment(env)


def test_config_rejects_relative_artifact_root(tmp_path: Path) -> None:
    env = _make_valid_env(tmp_path)
    env["SOURCE_ARTIFACT_LOCAL_ROOT"] = "relative/path"
    with pytest.raises(ValueError, match="must be an absolute path"):
        MaterializationRunnerConfig.from_environment(env)


def test_config_rejects_invalid_port(tmp_path: Path) -> None:
    env = _make_valid_env(tmp_path)
    env["DB_PORT"] = "99999"
    with pytest.raises(ValueError, match="DB_PORT must be between 1 and 65535"):
        MaterializationRunnerConfig.from_environment(env)


# =============================================================================
# 2. Member Discovery & Ordering
# =============================================================================


@pytest.mark.asyncio
async def test_discover_authoritative_members_success() -> None:
    snap_id = _VALID_SNAPSHOT_ID
    ee_id = uuid4()
    ud_id = uuid4()
    nb_id = uuid4()

    # Pass in shuffled order with valid 64-char hex
    mock_bindings = (
        SnapshotMemberBinding(
            source_snapshot_member_id=nb_id,
            source_snapshot_id=snap_id,
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/NB",
            content_sha256="1b" * 32,
        ),
        SnapshotMemberBinding(
            source_snapshot_member_id=ee_id,
            source_snapshot_id=snap_id,
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/EE",
            content_sha256="2e" * 32,
        ),
        SnapshotMemberBinding(
            source_snapshot_member_id=ud_id,
            source_snapshot_id=snap_id,
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/UD",
            content_sha256="3d" * 32,
        ),
    )

    session = MagicMock()
    with patch(
        "ai_worker.adapters.sqlalchemy_source_snapshot_repository.SqlAlchemySourceSnapshotRepository.get_snapshot_member_bindings",
        new=AsyncMock(return_value=mock_bindings),
    ):
        result = await discover_authoritative_members(session, snapshot_id=snap_id, expected_item_seq=_VALID_ITEM_SEQ)

    # Order must strictly follow SECTION_ORDER: EE, UD, NB
    assert result == (ee_id, ud_id, nb_id)


@pytest.mark.asyncio
async def test_discover_authoritative_members_rejects_empty() -> None:
    session = MagicMock()
    with patch(
        "ai_worker.adapters.sqlalchemy_source_snapshot_repository.SqlAlchemySourceSnapshotRepository.get_snapshot_member_bindings",
        new=AsyncMock(return_value=()),
    ):
        with pytest.raises(KnowledgeMaterializationError) as exc_info:
            await discover_authoritative_members(
                session, snapshot_id=_VALID_SNAPSHOT_ID, expected_item_seq=_VALID_ITEM_SEQ
            )
        assert exc_info.value.reason == KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID


@pytest.mark.asyncio
async def test_discover_authoritative_members_rejects_foreign_snapshot_id() -> None:
    mock_bindings = (
        SnapshotMemberBinding(
            source_snapshot_member_id=uuid4(),
            source_snapshot_id=uuid4(),  # Different snapshot ID
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/EE",
            content_sha256="2e" * 32,
        ),
    )
    session = MagicMock()
    with patch(
        "ai_worker.adapters.sqlalchemy_source_snapshot_repository.SqlAlchemySourceSnapshotRepository.get_snapshot_member_bindings",
        new=AsyncMock(return_value=mock_bindings),
    ):
        with pytest.raises(KnowledgeMaterializationError) as exc_info:
            await discover_authoritative_members(
                session, snapshot_id=_VALID_SNAPSHOT_ID, expected_item_seq=_VALID_ITEM_SEQ
            )
        assert exc_info.value.reason == KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID


@pytest.mark.asyncio
async def test_discover_authoritative_members_rejects_nn_or_foreign_section() -> None:
    snap_id = _VALID_SNAPSHOT_ID
    mock_bindings = (
        SnapshotMemberBinding(
            source_snapshot_member_id=uuid4(),
            source_snapshot_id=snap_id,
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/EE",
            content_sha256="2e" * 32,
        ),
        SnapshotMemberBinding(
            source_snapshot_member_id=uuid4(),
            source_snapshot_id=snap_id,
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/NN",  # NN section not allowed in standard EE/UD/NB flow
            content_sha256="4a" * 32,
        ),
    )
    session = MagicMock()
    with patch(
        "ai_worker.adapters.sqlalchemy_source_snapshot_repository.SqlAlchemySourceSnapshotRepository.get_snapshot_member_bindings",
        new=AsyncMock(return_value=mock_bindings),
    ):
        with pytest.raises(KnowledgeMaterializationError) as exc_info:
            await discover_authoritative_members(session, snapshot_id=snap_id, expected_item_seq=_VALID_ITEM_SEQ)
        assert exc_info.value.reason == KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID


@pytest.mark.asyncio
async def test_discover_authoritative_members_rejects_duplicates() -> None:
    snap_id = _VALID_SNAPSHOT_ID
    mock_bindings = (
        SnapshotMemberBinding(
            source_snapshot_member_id=uuid4(),
            source_snapshot_id=snap_id,
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/EE",
            content_sha256="1e" * 32,
        ),
        SnapshotMemberBinding(
            source_snapshot_member_id=uuid4(),
            source_snapshot_id=snap_id,
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/EE",  # Duplicate EE
            content_sha256="2e" * 32,
        ),
    )
    session = MagicMock()
    with patch(
        "ai_worker.adapters.sqlalchemy_source_snapshot_repository.SqlAlchemySourceSnapshotRepository.get_snapshot_member_bindings",
        new=AsyncMock(return_value=mock_bindings),
    ):
        with pytest.raises(KnowledgeMaterializationError) as exc_info:
            await discover_authoritative_members(session, snapshot_id=snap_id, expected_item_seq=_VALID_ITEM_SEQ)
        assert exc_info.value.reason == KnowledgeMaterializationFailureReason.REQUEST_INVALID


@pytest.mark.asyncio
async def test_discover_authoritative_members_rejects_missing_sections() -> None:
    snap_id = _VALID_SNAPSHOT_ID
    mock_bindings = (
        SnapshotMemberBinding(
            source_snapshot_member_id=uuid4(),
            source_snapshot_id=snap_id,
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/EE",
            content_sha256="1e" * 32,
        ),
        SnapshotMemberBinding(
            source_snapshot_member_id=uuid4(),
            source_snapshot_id=snap_id,
            member_kind=SourceSnapshotMemberKind.ARTIFACT,
            ingestion_artifact_id=uuid4(),
            locator=f"mfds-label/{_VALID_ITEM_SEQ}/UD",  # Missing NB
            content_sha256="2d" * 32,
        ),
    )
    session = MagicMock()
    with patch(
        "ai_worker.adapters.sqlalchemy_source_snapshot_repository.SqlAlchemySourceSnapshotRepository.get_snapshot_member_bindings",
        new=AsyncMock(return_value=mock_bindings),
    ):
        with pytest.raises(KnowledgeMaterializationError) as exc_info:
            await discover_authoritative_members(session, snapshot_id=snap_id, expected_item_seq=_VALID_ITEM_SEQ)
        assert exc_info.value.reason == KnowledgeMaterializationFailureReason.REQUEST_INVALID


# =============================================================================
# 3. Preflight Fail-Closed Behavior
# =============================================================================


@pytest.mark.asyncio
async def test_execute_materialization_rejects_pending_snapshot(tmp_path: Path) -> None:
    env = _make_valid_env(tmp_path)
    config = MaterializationRunnerConfig.from_environment(env)
    snap_id = _VALID_SNAPSHOT_ID
    mid = uuid4()

    pending_doc = _make_synthetic_source_doc(
        snapshot_id=snap_id,
        member_id=mid,
        section="EE",
        snapshot_status="PENDING",
    )

    with (
        patch("ai_worker.admin.knowledge_materialization.create_async_engine") as mock_engine,
        patch(
            "ai_worker.admin.knowledge_materialization.discover_authoritative_members",
            new=AsyncMock(return_value=(mid,)),
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.fetch_source_documents",
            new=AsyncMock(return_value=(pending_doc,)),
        ),
    ):
        mock_engine.return_value.dispose = AsyncMock()

        with pytest.raises(KnowledgeMaterializationError) as exc_info:
            await execute_materialization(
                config=config,
                snapshot_id=snap_id,
                expected_item_seq=_VALID_ITEM_SEQ,
                expected_canonical_checksum=_VALID_CHECKSUM,
            )
        assert exc_info.value.reason == KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE


@pytest.mark.asyncio
async def test_execute_materialization_rejects_checksum_mismatch(tmp_path: Path) -> None:
    env = _make_valid_env(tmp_path)
    config = MaterializationRunnerConfig.from_environment(env)
    snap_id = _VALID_SNAPSHOT_ID
    mid = uuid4()

    mismatched_doc = _make_synthetic_source_doc(
        snapshot_id=snap_id,
        member_id=mid,
        section="EE",
        canonical_checksum="f" * 64,
    )

    with (
        patch("ai_worker.admin.knowledge_materialization.create_async_engine") as mock_engine,
        patch(
            "ai_worker.admin.knowledge_materialization.discover_authoritative_members",
            new=AsyncMock(return_value=(mid,)),
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.fetch_source_documents",
            new=AsyncMock(return_value=(mismatched_doc,)),
        ),
    ):
        mock_engine.return_value.dispose = AsyncMock()

        with pytest.raises(KnowledgeMaterializationError) as exc_info:
            await execute_materialization(
                config=config,
                snapshot_id=snap_id,
                expected_item_seq=_VALID_ITEM_SEQ,
                expected_canonical_checksum=_VALID_CHECKSUM,
            )
        assert exc_info.value.reason == KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH


# =============================================================================
# 4. Safe Sanitized Summary Projection & Redaction
# =============================================================================


def test_build_sanitized_summary_allowlist() -> None:
    snap_id = _VALID_SNAPSHOT_ID
    mids = (uuid4(), uuid4(), uuid4())
    receipt = _make_synthetic_receipt(snap_id, _VALID_ITEM_SEQ, mids)
    result = KnowledgeMaterializationResult(
        receipt=receipt,
        outcome=MaterializationOutcome.CREATED,
    )

    summary = build_sanitized_summary(
        result,
        post_commit_audit_passed=True,
        exact_replay_verified=True,
    )

    allowed_keys = {
        "execution_status",
        "outcome",
        "snapshot_id",
        "item_seq",
        "source_code",
        "source_version",
        "snapshot_canonical_checksum",
        "canonicalization_spec_version",
        "chunk_policy_version",
        "document_count",
        "chunk_count",
        "knowledge_document_ids",
        "knowledge_chunk_ids",
        "document_content_hashes",
        "chunk_content_hashes",
        "post_commit_audit_passed",
        "exact_replay_verified",
    }
    assert set(summary.keys()) == allowed_keys
    assert summary["execution_status"] == "SUCCESS"
    assert summary["outcome"] == "CREATED"
    assert summary["document_count"] == 3
    assert summary["chunk_count"] == 3
    assert summary["post_commit_audit_passed"] is True
    assert summary["exact_replay_verified"] is True

    summary_str = json.dumps(summary)
    for forbidden in ("password", "postgresql", "file://", "xml", "<ITEM>", "chunk_text", "artifact_root"):
        assert forbidden not in summary_str.lower()


# =============================================================================
# 5. Full Flow Execution: CREATED, Audit, EXACT_REPLAY
# =============================================================================


@pytest.mark.asyncio
async def test_execute_materialization_created_and_replay_verified(tmp_path: Path) -> None:
    env = _make_valid_env(tmp_path)
    config = MaterializationRunnerConfig.from_environment(env)
    snap_id = _VALID_SNAPSHOT_ID
    mids = (uuid4(), uuid4(), uuid4())

    source_docs = tuple(
        _make_synthetic_source_doc(snapshot_id=snap_id, member_id=mids[i], section=sec)
        for i, sec in enumerate(("EE", "UD", "NB"))
    )
    receipt = _make_synthetic_receipt(snap_id, _VALID_ITEM_SEQ, mids)
    first_result = KnowledgeMaterializationResult(
        receipt=receipt,
        outcome=MaterializationOutcome.CREATED,
    )
    replay_result = KnowledgeMaterializationResult(
        receipt=receipt,
        outcome=MaterializationOutcome.EXACT_REPLAY,
    )

    drafts = (
        KnowledgeDocumentDraft(
            source_snapshot_member_id=mids[0],
            external_document_id=f"mfds-label:{_VALID_ITEM_SEQ}:EE",
            document_content_hash="b" * 64,
            canonicalization_spec_version="mfds-label-selected-product@1",
            title="효능효과",
            chunks=(
                KnowledgeChunkDraft(
                    chunk_index=0,
                    content_hash="c" * 64,
                    normalization_version=CHUNK_POLICY_VERSION,
                    chunk_text="synthetic text",
                ),
            ),
        ),
    )

    with (
        patch("ai_worker.admin.knowledge_materialization.create_async_engine") as mock_engine,
        patch(
            "ai_worker.admin.knowledge_materialization.discover_authoritative_members",
            new=AsyncMock(return_value=mids),
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.fetch_source_documents",
            new=AsyncMock(return_value=source_docs),
        ),
        patch("ai_worker.admin.knowledge_materialization.LocalPrivateSourceArtifactReader"),
        patch(
            "ai_worker.admin.knowledge_materialization.materialize_documents",
            return_value=drafts,
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.persist_materialization",
            new=AsyncMock(side_effect=[first_result, replay_result]),
        ) as mock_persist,
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.audit_post_commit",
            new=AsyncMock(return_value=True),
        ) as mock_audit,
    ):
        mock_engine.return_value.dispose = AsyncMock()

        summary = await execute_materialization(
            config=config,
            snapshot_id=snap_id,
            expected_item_seq=_VALID_ITEM_SEQ,
            expected_canonical_checksum=_VALID_CHECKSUM,
            verify_replay=True,
        )

        assert summary["execution_status"] == "SUCCESS"
        assert summary["outcome"] == "CREATED"
        assert summary["post_commit_audit_passed"] is True
        assert summary["exact_replay_verified"] is True
        assert mock_persist.call_count == 2
        assert mock_audit.call_count == 1


@pytest.mark.asyncio
async def test_execute_materialization_post_commit_audit_failure(tmp_path: Path) -> None:
    env = _make_valid_env(tmp_path)
    config = MaterializationRunnerConfig.from_environment(env)
    snap_id = _VALID_SNAPSHOT_ID
    mids = (uuid4(), uuid4(), uuid4())
    source_docs = tuple(
        _make_synthetic_source_doc(snapshot_id=snap_id, member_id=mids[i], section=sec)
        for i, sec in enumerate(("EE", "UD", "NB"))
    )
    receipt = _make_synthetic_receipt(snap_id, _VALID_ITEM_SEQ, mids)
    res = KnowledgeMaterializationResult(receipt=receipt, outcome=MaterializationOutcome.CREATED)

    with (
        patch("ai_worker.admin.knowledge_materialization.create_async_engine") as mock_engine,
        patch(
            "ai_worker.admin.knowledge_materialization.discover_authoritative_members",
            new=AsyncMock(return_value=mids),
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.fetch_source_documents",
            new=AsyncMock(return_value=source_docs),
        ),
        patch("ai_worker.admin.knowledge_materialization.LocalPrivateSourceArtifactReader"),
        patch(
            "ai_worker.admin.knowledge_materialization.materialize_documents",
            return_value=(),
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.persist_materialization",
            new=AsyncMock(return_value=res),
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.audit_post_commit",
            new=AsyncMock(return_value=False),  # Audit failed!
        ),
    ):
        mock_engine.return_value.dispose = AsyncMock()

        summary = await execute_materialization(
            config=config,
            snapshot_id=snap_id,
            expected_item_seq=_VALID_ITEM_SEQ,
            expected_canonical_checksum=_VALID_CHECKSUM,
            verify_replay=False,
        )

        assert summary["execution_status"] == "FAILED"
        assert summary["failure_reason"] == "POST_COMMIT_AUDIT_FAILED"


# =============================================================================
# 6. Boundary Protection: No source_writer or embedding/index calls
# =============================================================================


@pytest.mark.asyncio
async def test_runner_does_not_call_source_writer_or_index_build(tmp_path: Path) -> None:
    env = _make_valid_env(tmp_path)
    config = MaterializationRunnerConfig.from_environment(env)
    snap_id = _VALID_SNAPSHOT_ID
    mids = (uuid4(), uuid4(), uuid4())
    source_docs = tuple(
        _make_synthetic_source_doc(snapshot_id=snap_id, member_id=mids[i], section=sec)
        for i, sec in enumerate(("EE", "UD", "NB"))
    )
    receipt = _make_synthetic_receipt(snap_id, _VALID_ITEM_SEQ, mids)
    res = KnowledgeMaterializationResult(receipt=receipt, outcome=MaterializationOutcome.CREATED)

    with (
        patch("ai_worker.admin.knowledge_materialization.create_async_engine") as mock_engine,
        patch(
            "ai_worker.admin.knowledge_materialization.discover_authoritative_members",
            new=AsyncMock(return_value=mids),
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.fetch_source_documents",
            new=AsyncMock(return_value=source_docs),
        ),
        patch("ai_worker.admin.knowledge_materialization.LocalPrivateSourceArtifactReader"),
        patch(
            "ai_worker.admin.knowledge_materialization.materialize_documents",
            return_value=(),
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.persist_materialization",
            new=AsyncMock(return_value=res),
        ),
        patch(
            "ai_worker.adapters.sqlalchemy_knowledge_materialization.SqlAlchemyKnowledgeMaterializationRepository.audit_post_commit",
            new=AsyncMock(return_value=True),
        ),
        patch("ai_worker.admin.source_writer.select_snapshot") as mock_source_writer,
        patch("ai_worker.tasks.rag.knowledge_evidence_index.build_knowledge_evidence_index") as mock_index_build,
    ):
        mock_engine.return_value.dispose = AsyncMock()

        summary = await execute_materialization(
            config=config,
            snapshot_id=snap_id,
            expected_item_seq=_VALID_ITEM_SEQ,
            expected_canonical_checksum=_VALID_CHECKSUM,
        )

        assert summary["execution_status"] == "SUCCESS"
        mock_source_writer.assert_not_called()
        mock_index_build.assert_not_called()


# =============================================================================
# 7. CLI main() parsing and error codes
# =============================================================================


def test_parse_args() -> None:
    args = parse_args(
        [
            str(_VALID_SNAPSHOT_ID),
            "--expected-item-seq",
            _VALID_ITEM_SEQ,
            "--expected-canonical-checksum",
            _VALID_CHECKSUM,
            "--verify-replay",
        ]
    )
    assert args.snapshot_id == _VALID_SNAPSHOT_ID
    assert args.expected_item_seq == _VALID_ITEM_SEQ
    assert args.expected_canonical_checksum == _VALID_CHECKSUM
    assert args.verify_replay is True


def test_main_missing_args(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


def test_main_config_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("DB_HOST", "")  # Incomplete
    code = main(
        [
            str(_VALID_SNAPSHOT_ID),
            "--expected-item-seq",
            _VALID_ITEM_SEQ,
            "--expected-canonical-checksum",
            _VALID_CHECKSUM,
        ]
    )
    assert code == 1
    captured = capsys.readouterr()
    err_json = json.loads(captured.err)
    assert err_json["execution_status"] == "FAILED"
    assert err_json["failure_reason"] == "CONFIG_INVALID"
