from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_source import RagSourceSnapshot
from app.models.users import User
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
from app.repositories.rag_source_use_approval_repository import (
    RagSourceUseApprovalRepository,
    SourceUseApprovalConflictError,
    SourceUseApprovalCreate,
    SourceUseApprovalRevoke,
    SourceUseApprovalValidationError,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import SourceUsePurpose

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)


def _hash(char: str) -> str:
    return char * 64


async def _seed_snapshot(session: AsyncSession) -> tuple[RagSourceSnapshot, str, User, User]:
    suffix = uuid4().hex[:12]
    approver = User(email=f"807-ap-{suffix}@example.com", hashed_password="x" * 60, name="Approver")
    revoker = User(email=f"807-rv-{suffix}@example.com", hashed_password="x" * 60, name="Revoker")
    session.add_all([approver, revoker])
    await session.flush()

    catalog = RagSourceCatalogRepository(session)
    source = await catalog.create_source(
        RagSourceCreate(source_code=f"SOURCE_{suffix}", display_name="Synthetic Source")
    )
    endpoint = await catalog.create_endpoint(
        RagSourceEndpointCreate(source_id=source.id, endpoint_code="ENDPOINT", display_name="Endpoint")
    )
    operation = await catalog.create_operation(
        RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="OPERATION", display_name="Operation")
    )
    snapshot = await catalog.create_snapshot(
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version=f"version-{suffix}",
            raw_manifest_checksum=_hash("a"),
            canonical_checksum=_hash("b"),
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            collected_at=_NOW,
        )
    )
    return snapshot, source.source_code, approver, revoker


def _request(
    snapshot: RagSourceSnapshot,
    source_code: str,
    approver: User,
    *,
    source_snapshot_id: UUID | None = None,
    source_version: str | None = None,
    purpose: SourceUsePurpose = SourceUsePurpose.PATIENT_CITATION,
    environment: RuntimeEnvironmentCode = RuntimeEnvironmentCode.PRODUCTION,
) -> SourceUseApprovalCreate:
    return SourceUseApprovalCreate(
        source_snapshot_id=snapshot.id if source_snapshot_id is None else source_snapshot_id,
        source_code=source_code,
        source_version=snapshot.source_version if source_version is None else source_version,
        environment=environment,
        purpose=purpose,
        approval_version="approval-1",
        valid_from=_NOW,
        expires_at=datetime(2026, 9, 20, tzinfo=UTC),
        actor_id=approver.id,
        evidence_ref="evidence://patient-citation/approval-1",
    )


async def test_repository_persists_and_reads_exact_patient_citation_approval(db_session: AsyncSession) -> None:
    snapshot, source_code, approver, _ = await _seed_snapshot(db_session)
    repository = RagSourceUseApprovalRepository(db_session)
    request = _request(snapshot, source_code, approver)

    created = await repository.create_approval(request)
    observed = await repository.get_exact(request.identity())

    assert observed == created
    assert observed is not None
    assert observed.identity.source_snapshot_id == snapshot.id
    assert observed.identity.source_code == source_code
    assert observed.identity.source_version == snapshot.source_version
    assert observed.identity.environment is RuntimeEnvironmentCode.PRODUCTION
    assert observed.identity.purpose is SourceUsePurpose.PATIENT_CITATION
    assert observed.identity.approval_version == "approval-1"
    assert observed.is_usable_at(datetime(2026, 9, 19, 12, tzinfo=UTC))


async def test_same_immutable_approval_retry_is_idempotent_and_changed_payload_conflicts(
    db_session: AsyncSession,
) -> None:
    snapshot, source_code, approver, _ = await _seed_snapshot(db_session)
    repository = RagSourceUseApprovalRepository(db_session)
    request = _request(snapshot, source_code, approver)

    first = await repository.create_approval(request)
    retry = await repository.create_approval(request)

    assert retry.id == first.id

    with pytest.raises(SourceUseApprovalConflictError):
        await repository.create_approval(replace(request, evidence_ref="evidence://different"))


async def test_writer_rejects_source_snapshot_code_or_version_mismatch(db_session: AsyncSession) -> None:
    snapshot, source_code, approver, _ = await _seed_snapshot(db_session)
    repository = RagSourceUseApprovalRepository(db_session)

    with pytest.raises(SourceUseApprovalValidationError):
        await repository.create_approval(_request(snapshot, source_code, approver, source_snapshot_id=uuid4()))

    with pytest.raises(SourceUseApprovalValidationError):
        await repository.create_approval(_request(snapshot, "WRONG_SOURCE", approver))

    with pytest.raises(SourceUseApprovalValidationError):
        await repository.create_approval(_request(snapshot, source_code, approver, source_version="wrong-version"))


async def test_retrieval_approval_does_not_satisfy_patient_citation_exact_read(
    db_session: AsyncSession,
) -> None:
    snapshot, source_code, approver, _ = await _seed_snapshot(db_session)
    repository = RagSourceUseApprovalRepository(db_session)
    retrieval = _request(snapshot, source_code, approver, purpose=SourceUsePurpose.RETRIEVAL)

    await repository.create_approval(retrieval)

    patient_citation_identity = _request(snapshot, source_code, approver).identity()
    assert await repository.get_exact(patient_citation_identity) is None


async def test_environment_scopes_approval_exactly(db_session: AsyncSession) -> None:
    snapshot, source_code, approver, _ = await _seed_snapshot(db_session)
    repository = RagSourceUseApprovalRepository(db_session)
    local_request = _request(
        snapshot,
        source_code,
        approver,
        environment=RuntimeEnvironmentCode.LOCAL,
    )

    await repository.create_approval(local_request)

    production_identity = _request(
        snapshot,
        source_code,
        approver,
        environment=RuntimeEnvironmentCode.PRODUCTION,
    ).identity()
    assert await repository.get_exact(production_identity) is None


async def test_revoke_is_one_way_and_historical_read_remains_available(db_session: AsyncSession) -> None:
    snapshot, source_code, approver, revoker = await _seed_snapshot(db_session)
    repository = RagSourceUseApprovalRepository(db_session)
    request = _request(snapshot, source_code, approver)
    created = await repository.create_approval(request)
    revoke = SourceUseApprovalRevoke(
        approval_id=created.id,
        revoked_at=datetime(2026, 9, 19, 12, tzinfo=UTC),
        revoked_by=revoker.id,
        revoked_reason="policy change",
    )

    revoked = await repository.revoke(revoke)
    retry = await repository.revoke(revoke)
    observed = await repository.get_exact(request.identity())

    assert revoked == retry
    assert observed is not None
    assert observed.revoked_at == revoke.revoked_at
    assert observed.is_usable_at(datetime(2026, 9, 19, 13, tzinfo=UTC)) is False

    with pytest.raises(SourceUseApprovalConflictError):
        await repository.revoke(
            SourceUseApprovalRevoke(
                approval_id=created.id,
                revoked_at=revoke.revoked_at,
                revoked_by=revoker.id,
                revoked_reason="different reason",
            )
        )
