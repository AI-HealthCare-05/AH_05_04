from dataclasses import fields
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_worker.core.quarantine import (
    DeadLetterEnvelope,
    QuarantineFailureCode,
    QuarantineRequest,
)


def build_request() -> QuarantineRequest:
    return QuarantineRequest(
        stream_name="oryak:jobs",
        stream_entry_id="1000-0",
        message_digest="a" * 64,
        failure_code=QuarantineFailureCode.INVALID_MESSAGE_SCHEMA,
        job_id=uuid4(),
        original_event_id=uuid4(),
        original_schema_version="1.0",
        trace_id=uuid4().hex,
        received_at=datetime.now(UTC),
    )


def test_quarantine_request_contains_only_safe_metadata() -> None:
    request = build_request()

    assert request.stream_name == "oryak:jobs"
    assert request.message_digest == "a" * 64

    field_names = {field.name for field in fields(QuarantineRequest)}

    assert "raw_message" not in field_names
    assert "payload" not in field_names
    assert "user_id" not in field_names
    assert "failure_detail" not in field_names


def test_quarantine_request_rejects_invalid_digest() -> None:
    with pytest.raises(ValueError, match="message_digest"):
        QuarantineRequest(
            stream_name="oryak:jobs",
            stream_entry_id="1000-0",
            message_digest="not-a-sha256",
            failure_code=QuarantineFailureCode.INVALID_MESSAGE_SCHEMA,
            job_id=None,
            original_event_id=None,
            original_schema_version=None,
            trace_id=None,
            received_at=datetime.now(UTC),
        )


def test_quarantine_request_rejects_naive_received_at() -> None:
    with pytest.raises(ValueError, match="received_at"):
        QuarantineRequest(
            stream_name="oryak:jobs",
            stream_entry_id="1000-0",
            message_digest="b" * 64,
            failure_code=QuarantineFailureCode.INVALID_MESSAGE_SCHEMA,
            job_id=None,
            original_event_id=None,
            original_schema_version=None,
            trace_id=None,
            received_at=datetime.now(),
        )


def test_dead_letter_envelope_excludes_job_and_message_content() -> None:
    envelope = DeadLetterEnvelope(
        event_id=uuid4(),
        quarantine_id=uuid4(),
        stream_entry_id="1000-0",
        message_digest="c" * 64,
        failure_code=QuarantineFailureCode.UNSUPPORTED_SCHEMA_VERSION,
        original_schema_version="2.0",
        trace_id=uuid4().hex,
    )

    field_names = {field.name for field in fields(DeadLetterEnvelope)}

    assert envelope.message_digest == "c" * 64
    assert field_names == {
        "event_id",
        "quarantine_id",
        "stream_entry_id",
        "message_digest",
        "failure_code",
        "original_schema_version",
        "trace_id",
    }
    assert "job_id" not in field_names
    assert "raw_message" not in field_names
