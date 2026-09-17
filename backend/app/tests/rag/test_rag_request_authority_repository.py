"""#713 REQUEST authority persistence: writer, exact read, immutability, conflict."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_request_authority import (
    RagRequestGuardAuthority,
    RagRequestMemberDecision,
    RagRequestSourceDecision,
)
from app.models.users import User
from app.repositories.rag_request_authority_repository import (
    RagRequestAuthorityRepository,
    RequestAuthorityConflictError,
    RequestAuthorityCorruptError,
    RequestAuthorityValidationError,
    RequestGuardAuthorityRecord,
    RequestMemberDecisionRecord,
    RequestSourceDecisionRecord,
)
from rag_runtime.request_authority import (
    REQUEST_AUTHORITY_ARTIFACT_VERSION,
    REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE,
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    RequestAuthorityMemberIdentity,
    RequestAuthorityMemberKind,
    compute_request_guard_authority_ref,
    compute_request_source_decision_authority_ref,
)

OPERATION = "GUIDE_SYNC_ANSWER"
ENDPOINT_IDENTITY = RequestAuthorityMemberIdentity(
    member_kind=RequestAuthorityMemberKind.ENDPOINT_OPERATION,
    endpoint_code="MFDS_DUR",
)
ARTIFACT_IDENTITY = RequestAuthorityMemberIdentity(
    member_kind=RequestAuthorityMemberKind.ARTIFACT_MEMBER,
    artifact_code="mfds_label_bundle",
    artifact_version="2026.09",
)


async def _seed_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid4(),
        email=f"authority-{uuid4().hex[:8]}@example.com",
        hashed_password="hash",
        name="테스트",
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _guard_record(user_id: UUID) -> RequestGuardAuthorityRecord:
    return RequestGuardAuthorityRecord(
        user_id=user_id,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
    )


def _source_record(user_id: UUID, guard_ref: RequestAuthorityArtifactRef, **overrides) -> RequestSourceDecisionRecord:
    base = RequestSourceDecisionRecord(
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
        source_snapshot_id=uuid4(),
        source_code="MFDS",
        source_version="2026.09.01",
        actual_decision_outcome=RequestAuthorityDecisionOutcome.PASS,
    )
    return replace(base, **overrides) if overrides else base


def _member_record(user_id: UUID, guard_ref: RequestAuthorityArtifactRef, **overrides) -> RequestMemberDecisionRecord:
    base = RequestMemberDecisionRecord(
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
        source_snapshot_id=uuid4(),
        source_snapshot_member_id=uuid4(),
        member_identity=ENDPOINT_IDENTITY,
        actual_decision_outcome=RequestAuthorityDecisionOutcome.PASS,
    )
    return replace(base, **overrides) if overrides else base


async def _persisted_guard(
    repository: RagRequestAuthorityRepository,
    user_id: UUID,
) -> tuple[RequestGuardAuthorityRecord, RequestAuthorityArtifactRef]:
    record = _guard_record(user_id)
    ref = await repository.record_request_guard_authority(record)
    return record, ref


# ---------------------------------------------------------------------------
# Guard write / exact read
# ---------------------------------------------------------------------------


async def test_guard_write_then_exact_read(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)

    record, ref = await _persisted_guard(repository, user.id)

    assert ref == compute_request_guard_authority_ref(
        user_id=user.id,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
    )
    assert await repository.get_request_guard_authority_by_artifact_ref(ref) == record


async def test_guard_read_returns_none_for_wrong_artifact_code(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, ref = await _persisted_guard(repository, user.id)

    wrong = RequestAuthorityArtifactRef(
        artifact_code="request_source_decision_authority",
        version=ref.version,
        content_sha256=ref.content_sha256,
    )
    assert await repository.get_request_guard_authority_by_artifact_ref(wrong) is None


async def test_guard_read_returns_none_for_wrong_version(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, ref = await _persisted_guard(repository, user.id)

    wrong = RequestAuthorityArtifactRef(
        artifact_code=ref.artifact_code,
        version="9.9",
        content_sha256=ref.content_sha256,
    )
    assert await repository.get_request_guard_authority_by_artifact_ref(wrong) is None


async def test_guard_read_returns_none_for_wrong_content_sha256(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, ref = await _persisted_guard(repository, user.id)

    wrong = RequestAuthorityArtifactRef(
        artifact_code=ref.artifact_code,
        version=ref.version,
        content_sha256="f" * 64,
    )
    assert await repository.get_request_guard_authority_by_artifact_ref(wrong) is None


async def test_guard_read_has_no_latest_fallback(db_session: AsyncSession) -> None:
    """다른 user의 Guard가 뒤에 저장돼도 잘못된 ref 조회가 그 행으로 넘어가지 않는다."""
    repository = RagRequestAuthorityRepository(db_session)
    first = await _seed_user(db_session)
    second = await _seed_user(db_session)
    _, first_ref = await _persisted_guard(repository, first.id)
    await _persisted_guard(repository, second.id)
    await db_session.flush()

    unknown = RequestAuthorityArtifactRef(
        artifact_code=first_ref.artifact_code,
        version=first_ref.version,
        content_sha256="a" * 64,
    )
    assert await repository.get_request_guard_authority_by_artifact_ref(unknown) is None


# ---------------------------------------------------------------------------
# Guard validation
# ---------------------------------------------------------------------------


async def test_guard_write_rejects_non_request_stage(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_guard_authority(
            RequestGuardAuthorityRecord(
                user_id=user.id,
                request_operation_code=OPERATION,
                decision_stage="APPROVAL",  # type: ignore[arg-type]
            )
        )


async def test_guard_write_rejects_blank_operation_code(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_guard_authority(
            RequestGuardAuthorityRecord(
                user_id=user.id,
                request_operation_code="  ",
                decision_stage=RequestAuthorityDecisionStage.REQUEST,
            )
        )


# ---------------------------------------------------------------------------
# Source Decision
# ---------------------------------------------------------------------------


async def test_source_decision_write_then_exact_read(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    record = _source_record(user.id, guard_ref)
    ref = await repository.record_request_source_decision(record)

    assert ref == compute_request_source_decision_authority_ref(
        request_guard_ref=guard_ref,
        user_id=user.id,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
        source_snapshot_id=record.source_snapshot_id,
        source_code=record.source_code,
        source_version=record.source_version,
        actual_decision_outcome=RequestAuthorityDecisionOutcome.PASS,
    )
    assert await repository.get_request_source_decision_by_artifact_ref(ref) == record


async def test_source_decision_persists_fail_outcome_exactly(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    record = _source_record(user.id, guard_ref, actual_decision_outcome=RequestAuthorityDecisionOutcome.FAIL)
    ref = await repository.record_request_source_decision(record)

    read_back = await repository.get_request_source_decision_by_artifact_ref(ref)
    assert read_back is not None
    assert read_back.actual_decision_outcome is RequestAuthorityDecisionOutcome.FAIL


async def test_source_decision_rejects_unknown_outcome(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_source_decision(
            _source_record(user.id, guard_ref, actual_decision_outcome="UNKNOWN")
        )


async def test_source_decision_rejects_null_outcome(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_source_decision(
            _source_record(user.id, guard_ref, actual_decision_outcome=None)
        )


async def test_source_decision_rejects_missing_guard(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)

    unknown_guard = RequestAuthorityArtifactRef(
        artifact_code=REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE,
        version=REQUEST_AUTHORITY_ARTIFACT_VERSION,
        content_sha256="b" * 64,
    )
    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_source_decision(_source_record(user.id, unknown_guard))


async def test_source_decision_rejects_guard_user_mismatch(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    owner = await _seed_user(db_session)
    other = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, owner.id)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_source_decision(_source_record(other.id, guard_ref))


async def test_source_decision_rejects_guard_operation_mismatch(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_source_decision(
            _source_record(user.id, guard_ref, request_operation_code="GUIDE_SYNC_OTHER")
        )


async def test_source_decision_rejects_non_request_stage(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_source_decision(_source_record(user.id, guard_ref, decision_stage="APPROVAL"))


async def test_source_decision_rejects_blank_source_binding(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_source_decision(_source_record(user.id, guard_ref, source_version=" "))


async def test_source_decision_read_returns_none_for_wrong_hash(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)
    ref = await repository.record_request_source_decision(_source_record(user.id, guard_ref))

    wrong = RequestAuthorityArtifactRef(
        artifact_code=ref.artifact_code,
        version=ref.version,
        content_sha256="c" * 64,
    )
    assert await repository.get_request_source_decision_by_artifact_ref(wrong) is None


# ---------------------------------------------------------------------------
# Member Decision
# ---------------------------------------------------------------------------


async def test_member_decision_endpoint_projection_round_trip(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    record = _member_record(user.id, guard_ref)
    ref = await repository.record_request_member_decision(record)

    read_back = await repository.get_request_member_decision_by_artifact_ref(ref)
    assert read_back == record
    assert read_back is not None
    assert read_back.member_identity.member_kind is RequestAuthorityMemberKind.ENDPOINT_OPERATION


async def test_member_decision_preserves_null_operation_code(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    record = _member_record(user.id, guard_ref)
    assert record.member_identity.operation_code is None
    ref = await repository.record_request_member_decision(record)

    read_back = await repository.get_request_member_decision_by_artifact_ref(ref)
    assert read_back is not None
    assert read_back.member_identity.operation_code is None


async def test_member_decision_preserves_present_operation_code(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    identity = RequestAuthorityMemberIdentity(
        member_kind=RequestAuthorityMemberKind.ENDPOINT_OPERATION,
        endpoint_code="MFDS_DUR",
        operation_code="LIST",
    )
    ref = await repository.record_request_member_decision(_member_record(user.id, guard_ref, member_identity=identity))

    read_back = await repository.get_request_member_decision_by_artifact_ref(ref)
    assert read_back is not None
    assert read_back.member_identity == identity


async def test_member_decision_artifact_member_projection_round_trip(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    ref = await repository.record_request_member_decision(
        _member_record(user.id, guard_ref, member_identity=ARTIFACT_IDENTITY)
    )

    read_back = await repository.get_request_member_decision_by_artifact_ref(ref)
    assert read_back is not None
    assert read_back.member_identity == ARTIFACT_IDENTITY


async def test_member_decision_persists_fail_outcome_exactly(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    ref = await repository.record_request_member_decision(
        _member_record(user.id, guard_ref, actual_decision_outcome=RequestAuthorityDecisionOutcome.FAIL)
    )

    read_back = await repository.get_request_member_decision_by_artifact_ref(ref)
    assert read_back is not None
    assert read_back.actual_decision_outcome is RequestAuthorityDecisionOutcome.FAIL


async def test_member_decision_rejects_invalid_member_identity(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    invalid = RequestAuthorityMemberIdentity(member_kind=RequestAuthorityMemberKind.ENDPOINT_OPERATION)
    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_member_decision(_member_record(user.id, guard_ref, member_identity=invalid))


async def test_member_decision_rejects_guard_user_mismatch(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    owner = await _seed_user(db_session)
    other = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, owner.id)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_member_decision(_member_record(other.id, guard_ref))


async def test_member_decision_rejects_guard_operation_mismatch(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_member_decision(
            _member_record(user.id, guard_ref, request_operation_code="GUIDE_SYNC_OTHER")
        )


async def test_member_decision_rejects_non_request_stage(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    with pytest.raises(RequestAuthorityValidationError):
        await repository.record_request_member_decision(_member_record(user.id, guard_ref, decision_stage="APPROVAL"))


# ---------------------------------------------------------------------------
# Idempotency / conflict / immutability
# ---------------------------------------------------------------------------


async def test_identical_guard_retry_does_not_duplicate(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)

    first = await repository.record_request_guard_authority(_guard_record(user.id))
    second = await repository.record_request_guard_authority(_guard_record(user.id))
    await db_session.flush()

    assert first == second
    rows = (
        (
            await db_session.execute(
                select(RagRequestGuardAuthority).where(
                    RagRequestGuardAuthority.artifact_content_sha256 == first.content_sha256
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_identical_source_decision_retry_does_not_duplicate(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)
    record = _source_record(user.id, guard_ref)

    first = await repository.record_request_source_decision(record)
    second = await repository.record_request_source_decision(record)
    await db_session.flush()

    assert first == second
    rows = (
        (
            await db_session.execute(
                select(RagRequestSourceDecision).where(
                    RagRequestSourceDecision.artifact_content_sha256 == first.content_sha256
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_identical_member_decision_retry_does_not_duplicate(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)
    record = _member_record(user.id, guard_ref)

    first = await repository.record_request_member_decision(record)
    second = await repository.record_request_member_decision(record)
    await db_session.flush()

    assert first == second
    rows = (
        (
            await db_session.execute(
                select(RagRequestMemberDecision).where(
                    RagRequestMemberDecision.artifact_content_sha256 == first.content_sha256
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_conflicting_content_under_same_identity_fails_closed(db_session: AsyncSession) -> None:
    """같은 immutable identity에 다른 semantic content가 이미 있으면 덮어쓰지 않고 실패한다."""
    repository = RagRequestAuthorityRepository(db_session)
    owner = await _seed_user(db_session)
    other = await _seed_user(db_session)
    record = _guard_record(owner.id)
    ref = compute_request_guard_authority_ref(
        user_id=owner.id,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
    )

    # writer를 우회해 동일 identity·다른 소유자로 위조된 행을 심는다.
    db_session.add(
        RagRequestGuardAuthority(
            id=uuid4(),
            artifact_code=ref.artifact_code,
            artifact_version=ref.version,
            artifact_content_sha256=ref.content_sha256,
            user_id=other.id,
            request_operation_code=OPERATION,
            decision_stage="REQUEST",
        )
    )
    await db_session.flush()

    with pytest.raises(RequestAuthorityConflictError):
        await repository.record_request_guard_authority(record)

    rows = (
        (
            await db_session.execute(
                select(RagRequestGuardAuthority).where(
                    RagRequestGuardAuthority.artifact_content_sha256 == ref.content_sha256
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].user_id == other.id


async def test_corrupt_persisted_authority_is_not_hidden_as_not_found(db_session: AsyncSession) -> None:
    """persisted 사실이 artifact identity와 어긋나면 None이 아니라 명시적 오류다."""
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    forged_ref = RequestAuthorityArtifactRef(
        artifact_code=REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE,
        version=REQUEST_AUTHORITY_ARTIFACT_VERSION,
        content_sha256="d" * 64,
    )
    db_session.add(
        RagRequestGuardAuthority(
            id=uuid4(),
            artifact_code=forged_ref.artifact_code,
            artifact_version=forged_ref.version,
            artifact_content_sha256=forged_ref.content_sha256,
            user_id=user.id,
            request_operation_code=OPERATION,
            decision_stage="REQUEST",
        )
    )
    await db_session.flush()

    with pytest.raises(RequestAuthorityCorruptError):
        await repository.get_request_guard_authority_by_artifact_ref(forged_ref)


async def test_repository_exposes_no_update_or_delete_api() -> None:
    forbidden = {"update", "delete", "remove", "set_outcome", "revoke"}
    exposed = {name for name in dir(RagRequestAuthorityRepository) if not name.startswith("_")}
    assert not {name for name in exposed if any(token in name for token in forbidden)}


# ---------------------------------------------------------------------------
# DB-backed integration: one REQUEST, one transaction, read-back after commit
# ---------------------------------------------------------------------------


async def test_single_request_authority_chain_round_trip(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    snapshot_id = uuid4()
    member_id = uuid4()

    guard_record = _guard_record(user.id)
    guard_ref = await repository.record_request_guard_authority(guard_record)

    source_record = _source_record(user.id, guard_ref, source_snapshot_id=snapshot_id)
    source_ref = await repository.record_request_source_decision(source_record)

    member_record = _member_record(
        user.id,
        guard_ref,
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
    )
    member_ref = await repository.record_request_member_decision(member_record)

    # Repository는 스스로 commit하지 않는다. transaction 경계는 caller가 소유한다.
    await db_session.commit()

    fresh = RagRequestAuthorityRepository(db_session)
    assert await fresh.get_request_guard_authority_by_artifact_ref(guard_ref) == guard_record
    assert await fresh.get_request_source_decision_by_artifact_ref(source_ref) == source_record
    assert await fresh.get_request_member_decision_by_artifact_ref(member_ref) == member_record


async def test_member_decision_fail_outcome_survives_commit(db_session: AsyncSession) -> None:
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    _, guard_ref = await _persisted_guard(repository, user.id)

    record = _member_record(user.id, guard_ref, actual_decision_outcome=RequestAuthorityDecisionOutcome.FAIL)
    ref = await repository.record_request_member_decision(record)
    await db_session.commit()

    read_back = await RagRequestAuthorityRepository(db_session).get_request_member_decision_by_artifact_ref(ref)
    assert read_back is not None
    assert read_back.actual_decision_outcome is RequestAuthorityDecisionOutcome.FAIL


async def test_noncanonical_persisted_operation_code_is_corrupt_not_none(db_session: AsyncSession) -> None:
    """DB CHECK를 통과하는 값이라도 계약 identity를 계산할 수 없으면 손상으로 드러낸다."""
    repository = RagRequestAuthorityRepository(db_session)
    user = await _seed_user(db_session)
    forged_ref = RequestAuthorityArtifactRef(
        artifact_code=REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE,
        version=REQUEST_AUTHORITY_ARTIFACT_VERSION,
        content_sha256="e" * 64,
    )
    db_session.add(
        RagRequestGuardAuthority(
            id=uuid4(),
            artifact_code=forged_ref.artifact_code,
            artifact_version=forged_ref.version,
            artifact_content_sha256=forged_ref.content_sha256,
            user_id=user.id,
            # 공백이 섞여 있어 CHECK는 통과하지만 canonical operation code가 아니다.
            request_operation_code=f" {OPERATION} ",
            decision_stage="REQUEST",
        )
    )
    await db_session.flush()

    with pytest.raises(RequestAuthorityCorruptError):
        await repository.get_request_guard_authority_by_artifact_ref(forged_ref)
