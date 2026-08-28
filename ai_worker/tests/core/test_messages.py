"""Worker Stream 메시지 schema 단위 테스트입니다."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_worker.schemas.messages import (
    DomainType,
    JobType,
    WorkerMessage,
)


def valid_message_payload() -> dict[str, object]:
    """실제 의료정보가 없는 합성 Worker envelope를 생성합니다."""

    now = datetime.now(UTC)

    return {
        "schema_version": "1.0",
        "event_id": str(uuid4()),
        "event_kind": "JOB_EXECUTE",
        "job_id": str(uuid4()),
        "job_type": "OCR",
        "domain_type": "OCR_JOB",
        "domain_id": str(uuid4()),
        "attempt": 1,
        "available_at": now.isoformat(),
        "enqueued_at": now.isoformat(),
        "trace_id": "test-trace-id",
    }


def test_valid_worker_message_is_parsed() -> None:
    message = WorkerMessage.model_validate(valid_message_payload())

    assert message.schema_version == "1.0"
    assert message.job_type is JobType.OCR
    assert message.domain_type is DomainType.OCR_JOB
    assert message.attempt == 1


def test_unsupported_schema_version_is_rejected() -> None:
    payload = valid_message_payload()
    payload["schema_version"] = "2.0"

    with pytest.raises(ValidationError):
        WorkerMessage.model_validate(payload)


def test_job_type_and_domain_type_mismatch_is_rejected() -> None:
    payload = valid_message_payload()
    payload["job_type"] = "CHAT"
    payload["domain_type"] = "OCR_JOB"

    with pytest.raises(
        ValidationError,
        match="job_type과 domain_type 조합",
    ):
        WorkerMessage.model_validate(payload)


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "prescription_text",
        "medication_name",
        "question",
        "answer",
        "ocr_text",
        "user_id",
    ],
)
def test_message_rejects_medical_or_user_content(
    forbidden_field: str,
) -> None:
    payload = valid_message_payload()

    # Stream envelope에는 의료 원문, 사용자 질문·답변 또는
    # 사용자 식별정보를 직접 포함하지 않습니다.
    payload[forbidden_field] = "SYNTHETIC_SENSITIVE_VALUE"

    with pytest.raises(ValidationError):
        WorkerMessage.model_validate(payload)


@pytest.mark.parametrize(
    "attempt",
    [0, -1, True, 1.5, "1"],
)
def test_invalid_attempt_is_rejected(attempt: object) -> None:
    payload = valid_message_payload()
    payload["attempt"] = attempt

    with pytest.raises(ValidationError):
        WorkerMessage.model_validate(payload)


def test_timezone_naive_datetime_is_rejected() -> None:
    payload = valid_message_payload()
    payload["available_at"] = "2026-08-25T10:00:00"

    with pytest.raises(ValidationError):
        WorkerMessage.model_validate(payload)


def test_empty_trace_id_is_rejected() -> None:
    payload = valid_message_payload()
    payload["trace_id"] = "   "

    with pytest.raises(ValidationError):
        WorkerMessage.model_validate(payload)
