"""Unit tests for the read-only production Assessment·Eligibility Authority Reader adapter (#746).

#712가 저장한 authority를 exact lookup으로 읽고, 저장된 semantic facts로 artifact identity를
재계산해 자기무결성을 확인하는지 검증한다. 실제 PostgreSQL round-trip은
`tests/integration/rag/test_evidence_authority_reader_postgresql.py`가 담당한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_evidence_authority import (
    SqlAlchemyAssessmentEligibilityAuthorityReader,
    _assessment_ref_statement,
    _selection_statement,
)
from ai_worker.tasks.rag.assessment_eligibility_authority import (
    AssessmentEligibilityAuthorityReaderError,
    AssessmentEligibilityAuthorityReaderPort,
)
from rag_runtime.evidence_authority import (
    EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS,
    ImmutableArtifactRef,
    compute_assessment_artifact_ref,
    compute_eligibility_receipt_ref,
    compute_validity_policy_ref,
    compute_verifier_artifact_ref,
)

_AUTHORITY_ID = UUID("74600000-0000-4000-8000-000000000001")
_RUN_ID = UUID("74600000-0000-4000-8000-000000000002")
_CHUNK_ID = UUID("74600000-0000-4000-8000-000000000003")
_SNAPSHOT_ID = UUID("74600000-0000-4000-8000-000000000004")
_MEMBER_ID = UUID("74600000-0000-4000-8000-000000000005")

_SOURCE_CODE = "MFDS"
_SOURCE_VERSION = "2026.09.17"
_CONTENT_SHA256 = "d" * 64

_EVALUATED_AT = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)
_MAX_VALIDITY = timedelta(seconds=EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS)
_VALID_UNTIL = _EVALUATED_AT + _MAX_VALIDITY

_POLICY_REF = compute_validity_policy_ref()
_VERIFIER_REF = compute_verifier_artifact_ref()


def _receipt_ref(
    *,
    source_version: str = _SOURCE_VERSION,
    content_sha256: str = _CONTENT_SHA256,
    verifier_artifact_ref: ImmutableArtifactRef = _VERIFIER_REF,
) -> ImmutableArtifactRef:
    return compute_eligibility_receipt_ref(
        retrieval_run_id=_RUN_ID,
        knowledge_chunk_id=_CHUNK_ID,
        source_snapshot_id=_SNAPSHOT_ID,
        source_snapshot_member_id=_MEMBER_ID,
        source_code=_SOURCE_CODE,
        source_version=source_version,
        content_sha256=content_sha256,
        evaluated_at=_EVALUATED_AT,
        verifier_artifact_ref=verifier_artifact_ref,
    )


def _assessment_ref(
    *,
    eligibility_receipt_ref: ImmutableArtifactRef | None = None,
    validity_policy_ref: ImmutableArtifactRef = _POLICY_REF,
    valid_from: datetime = _EVALUATED_AT,
    valid_until: datetime = _VALID_UNTIL,
) -> ImmutableArtifactRef:
    return compute_assessment_artifact_ref(
        retrieval_run_id=_RUN_ID,
        knowledge_chunk_id=_CHUNK_ID,
        eligibility_receipt_ref=eligibility_receipt_ref or _receipt_ref(),
        validity_policy_ref=validity_policy_ref,
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )


ASSESSMENT_REF = _assessment_ref()


def _row(**overrides: Any) -> dict[str, Any]:
    """#712 issuer가 남겼을 self-consistent authority row."""
    receipt = _receipt_ref()
    row: dict[str, Any] = {
        "id": str(_AUTHORITY_ID),
        "retrieval_run_id": str(_RUN_ID),
        "knowledge_chunk_id": str(_CHUNK_ID),
        "source_snapshot_id": str(_SNAPSHOT_ID),
        "source_snapshot_member_id": str(_MEMBER_ID),
        "source_code": _SOURCE_CODE,
        "source_version": _SOURCE_VERSION,
        "content_sha256": _CONTENT_SHA256,
        "eligibility_receipt_artifact_code": receipt.artifact_code,
        "eligibility_receipt_version": receipt.version,
        "eligibility_receipt_sha256": receipt.content_sha256,
        "assessment_artifact_code": ASSESSMENT_REF.artifact_code,
        "assessment_artifact_version": ASSESSMENT_REF.version,
        "assessment_artifact_sha256": ASSESSMENT_REF.content_sha256,
        "verifier_artifact_code": _VERIFIER_REF.artifact_code,
        "verifier_artifact_version": _VERIFIER_REF.version,
        "verifier_artifact_sha256": _VERIFIER_REF.content_sha256,
        "validity_policy_artifact_code": _POLICY_REF.artifact_code,
        "validity_policy_version": _POLICY_REF.version,
        "validity_policy_sha256": _POLICY_REF.content_sha256,
        "evaluated_at": _EVALUATED_AT,
        "assessment_valid_from": _EVALUATED_AT,
        "assessment_valid_until": _VALID_UNTIL,
        "created_at": _EVALUATED_AT,
    }
    row.update(overrides)
    return row


def _reader(
    rows: list[dict[str, Any]] | None = None,
    *,
    error: BaseException | None = None,
) -> SqlAlchemyAssessmentEligibilityAuthorityReader:
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
    return SqlAlchemyAssessmentEligibilityAuthorityReader(lambda: session)


def _dependency_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("synthetic dependency failure"))


async def _read_selection(rows: list[dict[str, Any]] | None, **kwargs: Any):
    return await _reader(rows, **kwargs).read_by_selection(
        retrieval_run_id=_RUN_ID,
        knowledge_chunk_id=_CHUNK_ID,
    )


async def _read_assessment(rows: list[dict[str, Any]] | None, **kwargs: Any):
    return await _reader(rows, **kwargs).read_by_assessment_ref(assessment_artifact_ref=ASSESSMENT_REF)


# ---------------------------------------------------------------------------
# Exact lookup shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        _selection_statement(_RUN_ID, _CHUNK_ID),
        _assessment_ref_statement(ASSESSMENT_REF),
    ],
)
def test_statements_are_exact_read_only_lookups_without_fallback(statement: Any) -> None:
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True})).lower()

    assert "from rag_evidence_authority" in compiled
    for forbidden in ("order by", "limit", "for update", "insert", "update ", "delete", "like", "desc"):
        assert forbidden not in compiled


def test_selection_statement_matches_only_the_selection_identity() -> None:
    compiled = str(_selection_statement(_RUN_ID, _CHUNK_ID).compile(compile_kwargs={"literal_binds": True})).lower()

    assert str(_RUN_ID) in compiled
    assert str(_CHUNK_ID) in compiled
    assert "created_at" not in compiled.split("where", 1)[1]


def test_assessment_ref_statement_matches_all_three_identity_columns() -> None:
    compiled = str(_assessment_ref_statement(ASSESSMENT_REF).compile(compile_kwargs={"literal_binds": True})).lower()
    where = compiled.split("where", 1)[1]

    assert "assessment_artifact_code" in where
    assert "assessment_artifact_version" in where
    assert ASSESSMENT_REF.content_sha256 in where


# ---------------------------------------------------------------------------
# Exact reads
# ---------------------------------------------------------------------------


async def test_exact_selection_read_returns_persisted_authority() -> None:
    authority = await _read_selection([_row()])

    assert authority is not None
    assert authority.id == _AUTHORITY_ID
    assert authority.retrieval_run_id == _RUN_ID
    assert authority.knowledge_chunk_id == _CHUNK_ID
    assert authority.source_snapshot_id == _SNAPSHOT_ID
    assert authority.source_snapshot_member_id == _MEMBER_ID
    assert authority.source_code == _SOURCE_CODE
    assert authority.source_version == _SOURCE_VERSION
    assert authority.content_sha256 == _CONTENT_SHA256
    assert authority.eligibility_receipt_ref == _receipt_ref()
    assert authority.assessment_artifact_ref == ASSESSMENT_REF
    assert authority.verifier_artifact_ref == _VERIFIER_REF
    assert authority.validity_policy_ref == _POLICY_REF
    assert authority.evaluated_at == _EVALUATED_AT
    assert authority.assessment_valid_from == _EVALUATED_AT
    assert authority.assessment_valid_until == _VALID_UNTIL


async def test_exact_assessment_ref_read_returns_the_same_authority() -> None:
    by_selection = await _read_selection([_row()])
    by_assessment = await _read_assessment([_row()])

    assert by_assessment == by_selection


async def test_reader_satisfies_the_read_only_port_protocol() -> None:
    reader: AssessmentEligibilityAuthorityReaderPort = _reader([_row()])

    assert await reader.read_by_selection(retrieval_run_id=_RUN_ID, knowledge_chunk_id=_CHUNK_ID) is not None


# ---------------------------------------------------------------------------
# Normal no-row is the only None
# ---------------------------------------------------------------------------


async def test_selection_no_row_returns_none() -> None:
    assert await _read_selection([]) is None


async def test_assessment_ref_no_row_returns_none() -> None:
    assert await _read_assessment([]) is None


# ---------------------------------------------------------------------------
# Dependency / ambiguity / malformed input
# ---------------------------------------------------------------------------


async def test_dependency_failure_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row()], error=_dependency_error())


async def test_ambiguous_selection_result_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(), _row()])


async def test_ambiguous_assessment_ref_result_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_assessment([_row(), _row()])


@pytest.mark.parametrize(
    "ref",
    [
        ImmutableArtifactRef(artifact_code="", version="v1", content_sha256="a" * 64),
        ImmutableArtifactRef(artifact_code="production_evidence_assessment", version="", content_sha256="a" * 64),
        ImmutableArtifactRef(artifact_code="production_evidence_assessment", version="v1", content_sha256="not-a-hash"),
    ],
)
async def test_malformed_assessment_ref_becomes_typed_error(ref: ImmutableArtifactRef) -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _reader([_row()]).read_by_assessment_ref(assessment_artifact_ref=ref)


async def test_malformed_selection_identity_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _reader([_row()]).read_by_selection(
            retrieval_run_id="74600000-0000-4000-8000-000000000002",  # type: ignore[arg-type]
            knowledge_chunk_id=_CHUNK_ID,
        )


async def test_persisted_malformed_uuid_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(source_snapshot_id="not-a-uuid")])


async def test_row_pointing_at_a_different_selection_becomes_typed_error() -> None:
    other = str(uuid4())
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(retrieval_run_id=other)])


# ---------------------------------------------------------------------------
# Persisted self-integrity: artifact identity mutations
# ---------------------------------------------------------------------------


async def test_eligibility_receipt_hash_mutation_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(eligibility_receipt_sha256="f" * 64)])


async def test_assessment_artifact_hash_mutation_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _reader([_row(assessment_artifact_sha256="f" * 64)]).read_by_selection(
            retrieval_run_id=_RUN_ID,
            knowledge_chunk_id=_CHUNK_ID,
        )


async def test_verifier_ref_mutation_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(verifier_artifact_sha256="f" * 64)])


async def test_verifier_artifact_code_mutation_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(verifier_artifact_code="rogue_verifier")])


async def test_validity_policy_ref_mutation_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(validity_policy_sha256="f" * 64)])


async def test_malformed_persisted_artifact_ref_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(eligibility_receipt_sha256="NOT-A-SHA256")])


# ---------------------------------------------------------------------------
# Persisted self-integrity: semantic fact mutations
# ---------------------------------------------------------------------------


async def test_source_version_mutation_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(source_version="2026.09.18")])


async def test_content_sha256_mutation_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(content_sha256="e" * 64)])


async def test_source_code_mutation_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(source_code="EMA")])


async def test_source_snapshot_member_mutation_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(source_snapshot_member_id=str(uuid4()))])


async def test_blank_source_version_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(source_version="   ")])


# ---------------------------------------------------------------------------
# Datetime / validity interval
# ---------------------------------------------------------------------------


async def test_naive_datetime_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(evaluated_at=_EVALUATED_AT.replace(tzinfo=None))])


async def test_non_utc_datetime_becomes_typed_error() -> None:
    seoul = timezone(timedelta(hours=9))
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(assessment_valid_until=_VALID_UNTIL.astimezone(seoul))])


async def test_valid_from_not_equal_to_evaluated_at_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(assessment_valid_from=_EVALUATED_AT - timedelta(seconds=1))])


async def test_valid_until_not_after_valid_from_becomes_typed_error() -> None:
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([_row(assessment_valid_until=_EVALUATED_AT)])


async def test_valid_until_beyond_pd722_ceiling_becomes_typed_error() -> None:
    beyond = _EVALUATED_AT + _MAX_VALIDITY + timedelta(seconds=1)
    row = _row(
        assessment_valid_until=beyond,
        assessment_artifact_sha256=_assessment_ref(valid_until=beyond).content_sha256,
    )

    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _read_selection([row])


async def test_earlier_authoritative_upper_bound_is_accepted_without_reconstruction() -> None:
    """더 이른 upper bound가 왜 존재했는지는 Reader가 현재 상태에서 재구성하지 않는다."""
    earlier = _EVALUATED_AT + timedelta(hours=1)
    ref = _assessment_ref(valid_until=earlier)
    row = _row(
        assessment_valid_until=earlier,
        assessment_artifact_code=ref.artifact_code,
        assessment_artifact_version=ref.version,
        assessment_artifact_sha256=ref.content_sha256,
    )

    authority = await _read_selection([row])

    assert authority is not None
    assert authority.assessment_valid_until == earlier


# ---------------------------------------------------------------------------
# Read-only transaction declaration
# ---------------------------------------------------------------------------


async def test_read_declares_repeatable_read_and_read_only_transaction() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.begin.return_value.__aenter__.return_value = None

    executed: list[str] = []

    async def _execute(statement, *args, **kwargs):
        executed.append(str(statement))
        result = MagicMock()
        result.mappings.return_value.all.return_value = [_row()]
        return result

    session.execute.side_effect = _execute
    reader = SqlAlchemyAssessmentEligibilityAuthorityReader(lambda: session)

    await reader.read_by_selection(retrieval_run_id=_RUN_ID, knowledge_chunk_id=_CHUNK_ID)

    assert executed[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
