"""Unit tests for the read-only production GuideEvidenceAuthorityReaderPort adapter (#709)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_guide_evidence_authority import (
    SqlAlchemyGuideEvidenceAuthorityReader,
    _guard_statement,
    _member_decision_statement,
    _source_decision_statement,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guide_evidence_authority import GuideEvidenceAuthorityReaderError
from ai_worker.tasks.rag.guide_evidence_handoff import ObservedDecisionOutcome, RequestDecisionStage
from ai_worker.tasks.rag.request_authority_artifact import (
    compute_request_guard_authority_ref,
    compute_request_member_decision_authority_ref,
    compute_request_source_decision_authority_ref,
    worker_artifact_ref,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberIdentity, SourceMemberKind

_USER_ID = UUID("70900000-0000-4000-8000-000000000001")
_SNAPSHOT_ID = UUID("70900000-0000-4000-8000-000000000002")
_MEMBER_ID = UUID("70900000-0000-4000-8000-000000000003")

_OPERATION = "GUIDE_SYNC_ANSWER"
_SOURCE_CODE = "MFDS"
_SOURCE_VERSION = "2026.09.01"

_ENDPOINT_IDENTITY = SourceMemberIdentity(
    member_kind=SourceMemberKind.ENDPOINT_OPERATION,
    endpoint_code="MFDS_DUR",
)
_ARTIFACT_IDENTITY = SourceMemberIdentity(
    member_kind=SourceMemberKind.ARTIFACT_MEMBER,
    artifact_code="mfds_label_bundle",
    artifact_version="2026.09",
)

GUARD_REF = compute_request_guard_authority_ref(
    user_id=_USER_ID,
    request_operation_code=_OPERATION,
    decision_stage=RequestDecisionStage.REQUEST,
)


def _source_ref(outcome: ObservedDecisionOutcome = ObservedDecisionOutcome.PASS) -> ImmutableArtifactRef:
    return compute_request_source_decision_authority_ref(
        request_guard_ref=GUARD_REF,
        user_id=_USER_ID,
        request_operation_code=_OPERATION,
        decision_stage=RequestDecisionStage.REQUEST,
        source_snapshot_id=_SNAPSHOT_ID,
        source_code=_SOURCE_CODE,
        source_version=_SOURCE_VERSION,
        actual_decision_outcome=outcome,
    )


def _member_ref(
    identity: SourceMemberIdentity = _ENDPOINT_IDENTITY,
    outcome: ObservedDecisionOutcome = ObservedDecisionOutcome.PASS,
) -> ImmutableArtifactRef:
    return compute_request_member_decision_authority_ref(
        request_guard_ref=GUARD_REF,
        user_id=_USER_ID,
        request_operation_code=_OPERATION,
        decision_stage=RequestDecisionStage.REQUEST,
        source_snapshot_id=_SNAPSHOT_ID,
        source_snapshot_member_id=_MEMBER_ID,
        member_identity=identity,
        actual_decision_outcome=outcome,
    )


def _guard_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "artifact_code": GUARD_REF.artifact_code,
        "artifact_version": GUARD_REF.version,
        "artifact_content_sha256": GUARD_REF.content_sha256,
        "user_id": str(_USER_ID),
        "request_operation_code": _OPERATION,
        "decision_stage": "REQUEST",
    }
    row.update(overrides)
    return row


def _guard_ref_columns() -> dict[str, Any]:
    return {
        "request_guard_artifact_code": GUARD_REF.artifact_code,
        "request_guard_artifact_version": GUARD_REF.version,
        "request_guard_content_sha256": GUARD_REF.content_sha256,
    }


def _source_row(**overrides: Any) -> dict[str, Any]:
    ref = _source_ref()
    row = {
        "artifact_code": ref.artifact_code,
        "artifact_version": ref.version,
        "artifact_content_sha256": ref.content_sha256,
        **_guard_ref_columns(),
        "user_id": str(_USER_ID),
        "request_operation_code": _OPERATION,
        "decision_stage": "REQUEST",
        "source_snapshot_id": str(_SNAPSHOT_ID),
        "source_code": _SOURCE_CODE,
        "source_version": _SOURCE_VERSION,
        "actual_decision_outcome": "PASS",
    }
    row.update(overrides)
    return row


def _member_row(**overrides: Any) -> dict[str, Any]:
    ref = _member_ref()
    row = {
        "artifact_code": ref.artifact_code,
        "artifact_version": ref.version,
        "artifact_content_sha256": ref.content_sha256,
        **_guard_ref_columns(),
        "user_id": str(_USER_ID),
        "request_operation_code": _OPERATION,
        "decision_stage": "REQUEST",
        "source_snapshot_id": str(_SNAPSHOT_ID),
        "source_snapshot_member_id": str(_MEMBER_ID),
        "member_kind": "ENDPOINT_OPERATION",
        "endpoint_code": "MFDS_DUR",
        "operation_code": None,
        "member_artifact_code": None,
        "member_artifact_version": None,
        "actual_decision_outcome": "PASS",
    }
    row.update(overrides)
    return row


def _reader(
    rows: list[dict[str, Any]] | None = None,
    *,
    error: BaseException | None = None,
) -> SqlAlchemyGuideEvidenceAuthorityReader:
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.begin.return_value.__aenter__.return_value = None

    executed: list[Any] = []

    async def _execute(statement, *args, **kwargs):
        executed.append(statement)
        # The first statement is the read-only transaction declaration.
        if error is not None and len(executed) > 1:
            raise error
        result = MagicMock()
        result.mappings.return_value.all.return_value = list(rows or [])
        return result

    session.execute.side_effect = _execute
    return SqlAlchemyGuideEvidenceAuthorityReader(lambda: session)


def _dependency_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("synthetic dependency failure"))


# ---------------------------------------------------------------------------
# Exact lookup shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "table"),
    [
        (_guard_statement(GUARD_REF), "rag_request_guard_authority"),
        (_source_decision_statement(_source_ref()), "rag_request_source_decision"),
        (_member_decision_statement(_member_ref()), "rag_request_member_decision"),
    ],
)
def test_statements_are_exact_read_only_lookups_without_fallback(statement: Any, table: str) -> None:
    sql = str(statement)
    upper = sql.upper()

    assert f"FROM {table}" in sql
    assert "artifact_code = :" in sql
    assert "artifact_version = :" in sql
    assert "artifact_content_sha256 = :" in sql
    # latest/CURRENT/newest/PK fallback을 어떤 형태로도 쓰지 않는다.
    assert "LIMIT" not in upper
    assert "ORDER BY" not in upper
    assert "FOR UPDATE" not in upper


# ---------------------------------------------------------------------------
# Successful exact reads
# ---------------------------------------------------------------------------


async def test_exact_request_guard_read_returns_authoritative_observation() -> None:
    observation = await _reader([_guard_row()]).read_request_guard(request_guard_ref=GUARD_REF)

    assert observation is not None
    assert observation.artifact_ref == GUARD_REF
    assert observation.user_id == _USER_ID
    assert observation.request_operation_code == _OPERATION
    assert observation.decision_stage == "REQUEST"


async def test_exact_source_decision_read_returns_authoritative_observation() -> None:
    ref = _source_ref()
    observation = await _reader([_source_row()]).read_source_decision(request_source_decision_ref=ref)

    assert observation is not None
    assert observation.artifact_ref == ref
    assert observation.request_guard_ref == GUARD_REF
    assert observation.user_id == _USER_ID
    assert observation.request_operation_code == _OPERATION
    assert observation.decision_stage == "REQUEST"
    assert observation.source_snapshot_id == _SNAPSHOT_ID
    assert observation.source_code == _SOURCE_CODE
    assert observation.source_version == _SOURCE_VERSION
    assert observation.actual_decision_outcome is ObservedDecisionOutcome.PASS


async def test_exact_member_decision_read_returns_authoritative_observation() -> None:
    ref = _member_ref()
    observation = await _reader([_member_row()]).read_member_decision(request_member_decision_ref=ref)

    assert observation is not None
    assert observation.artifact_ref == ref
    assert observation.request_guard_ref == GUARD_REF
    assert observation.source_snapshot_id == _SNAPSHOT_ID
    assert observation.source_snapshot_member_id == _MEMBER_ID
    assert observation.member_identity == _ENDPOINT_IDENTITY
    assert observation.actual_decision_outcome is ObservedDecisionOutcome.PASS


# ---------------------------------------------------------------------------
# Normal no-row
# ---------------------------------------------------------------------------


async def test_guard_no_row_returns_none() -> None:
    assert await _reader([]).read_request_guard(request_guard_ref=GUARD_REF) is None


async def test_source_decision_no_row_returns_none() -> None:
    assert await _reader([]).read_source_decision(request_source_decision_ref=_source_ref()) is None


async def test_member_decision_no_row_returns_none() -> None:
    assert await _reader([]).read_member_decision(request_member_decision_ref=_member_ref()) is None


# ---------------------------------------------------------------------------
# Dependency / ambiguity failures
# ---------------------------------------------------------------------------


async def test_dependency_failure_becomes_reader_error() -> None:
    reader = _reader([_guard_row()], error=_dependency_error())

    with pytest.raises(GuideEvidenceAuthorityReaderError):
        await reader.read_request_guard(request_guard_ref=GUARD_REF)


async def test_ambiguous_guard_lookup_fails_closed() -> None:
    reader = _reader([_guard_row(), _guard_row()])

    with pytest.raises(GuideEvidenceAuthorityReaderError):
        await reader.read_request_guard(request_guard_ref=GUARD_REF)


async def test_ambiguous_member_lookup_fails_closed() -> None:
    reader = _reader([_member_row(), _member_row()])

    with pytest.raises(GuideEvidenceAuthorityReaderError):
        await reader.read_member_decision(request_member_decision_ref=_member_ref())


# ---------------------------------------------------------------------------
# Corrupt persisted authority
# ---------------------------------------------------------------------------


async def test_corrupt_decision_outcome_fails_closed() -> None:
    reader = _reader([_source_row(actual_decision_outcome="MAYBE")])

    with pytest.raises(GuideEvidenceAuthorityReaderError):
        await reader.read_source_decision(request_source_decision_ref=_source_ref())


async def test_corrupt_decision_stage_fails_closed() -> None:
    reader = _reader([_guard_row(decision_stage="APPROVAL")])

    with pytest.raises(GuideEvidenceAuthorityReaderError):
        await reader.read_request_guard(request_guard_ref=GUARD_REF)


async def test_corrupt_member_kind_fails_closed() -> None:
    reader = _reader([_member_row(member_kind="ENDPOINT")])

    with pytest.raises(GuideEvidenceAuthorityReaderError):
        await reader.read_member_decision(request_member_decision_ref=_member_ref())


async def test_malformed_persisted_uuid_fails_closed() -> None:
    reader = _reader([_guard_row(user_id="not-a-uuid")])

    with pytest.raises(GuideEvidenceAuthorityReaderError):
        await reader.read_request_guard(request_guard_ref=GUARD_REF)


async def test_persisted_identity_mismatch_fails_closed() -> None:
    """persisted 사실이 요청한 artifact identity와 어긋나면 관측치로 반환하지 않는다."""
    reader = _reader([_guard_row(request_operation_code="GUIDE_SYNC_OTHER")])

    with pytest.raises(GuideEvidenceAuthorityReaderError):
        await reader.read_request_guard(request_guard_ref=GUARD_REF)


async def test_source_decision_identity_mismatch_fails_closed() -> None:
    reader = _reader([_source_row(source_version="2026.09.02")])

    with pytest.raises(GuideEvidenceAuthorityReaderError):
        await reader.read_source_decision(request_source_decision_ref=_source_ref())


# ---------------------------------------------------------------------------
# Member identity variants / outcome mapping
# ---------------------------------------------------------------------------


async def test_endpoint_member_preserves_null_operation_code() -> None:
    observation = await _reader([_member_row()]).read_member_decision(request_member_decision_ref=_member_ref())

    assert observation is not None
    assert observation.member_identity.member_kind is SourceMemberKind.ENDPOINT_OPERATION
    assert observation.member_identity.endpoint_code == "MFDS_DUR"
    assert observation.member_identity.operation_code is None


async def test_endpoint_member_preserves_present_operation_code() -> None:
    identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="MFDS_DUR",
        operation_code="LIST",
    )
    ref = _member_ref(identity)
    row = _member_row(
        artifact_content_sha256=ref.content_sha256,
        operation_code="LIST",
    )

    observation = await _reader([row]).read_member_decision(request_member_decision_ref=ref)

    assert observation is not None
    assert observation.member_identity == identity


async def test_artifact_member_projection() -> None:
    ref = _member_ref(_ARTIFACT_IDENTITY)
    row = _member_row(
        artifact_content_sha256=ref.content_sha256,
        member_kind="ARTIFACT",
        endpoint_code=None,
        operation_code=None,
        member_artifact_code="mfds_label_bundle",
        member_artifact_version="2026.09",
    )

    observation = await _reader([row]).read_member_decision(request_member_decision_ref=ref)

    assert observation is not None
    assert observation.member_identity == _ARTIFACT_IDENTITY


async def test_source_decision_fail_outcome_maps_exactly() -> None:
    ref = _source_ref(ObservedDecisionOutcome.FAIL)
    row = _source_row(artifact_content_sha256=ref.content_sha256, actual_decision_outcome="FAIL")

    observation = await _reader([row]).read_source_decision(request_source_decision_ref=ref)

    assert observation is not None
    assert observation.actual_decision_outcome is ObservedDecisionOutcome.FAIL


async def test_member_decision_fail_outcome_maps_exactly() -> None:
    ref = _member_ref(_ENDPOINT_IDENTITY, ObservedDecisionOutcome.FAIL)
    row = _member_row(artifact_content_sha256=ref.content_sha256, actual_decision_outcome="FAIL")

    observation = await _reader([row]).read_member_decision(request_member_decision_ref=ref)

    assert observation is not None
    assert observation.actual_decision_outcome is ObservedDecisionOutcome.FAIL


# ---------------------------------------------------------------------------
# Programming errors are never hidden
# ---------------------------------------------------------------------------


async def test_unexpected_programming_exception_propagates() -> None:
    boom = AttributeError("synthetic programming bug")

    def _factory():
        raise boom

    reader = SqlAlchemyGuideEvidenceAuthorityReader(_factory)

    with pytest.raises(AttributeError, match="synthetic programming bug"):
        await reader.read_request_guard(request_guard_ref=GUARD_REF)


async def test_reader_satisfies_the_existing_port_protocol() -> None:
    from ai_worker.tasks.rag.guide_evidence_authority import GuideEvidenceAuthorityReaderPort

    reader: GuideEvidenceAuthorityReaderPort = _reader([_guard_row()])

    assert worker_artifact_ref is not None
    assert await reader.read_request_guard(request_guard_ref=GUARD_REF) is not None
