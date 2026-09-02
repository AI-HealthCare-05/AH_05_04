"""Redis Stream 메시지 Codec 테스트입니다."""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest

from ai_worker.adapters.errors import (
    StreamMessageDecodingError,
    StreamMessageEncodingError,
)
from ai_worker.adapters.redis_message_codec import (
    decode_stream_message,
    encode_stream_message,
)
from ai_worker.schemas.messages import WorkerMessage


def build_message() -> WorkerMessage:
    now = datetime.now(UTC)

    return WorkerMessage.model_validate(
        {
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
            "trace_id": uuid4().hex,
        }
    )


def test_worker_message_round_trip() -> None:
    message = build_message()

    encoded = encode_stream_message(message)
    decoded = decode_stream_message(encoded)

    assert decoded == message


def test_redis_bytes_are_decoded() -> None:
    message = build_message()
    encoded = encode_stream_message(message)
    redis_fields = {key.encode(): value.encode() for key, value in encoded.items()}

    assert decode_stream_message(redis_fields) == message


def test_unknown_field_is_rejected_without_exposure() -> None:
    encoded = encode_stream_message(build_message())
    encoded["provider_secret"] = "SYNTHETIC_SECRET"

    with pytest.raises(StreamMessageDecodingError) as exc_info:
        decode_stream_message(encoded)

    assert "SYNTHETIC_SECRET" not in str(exc_info.value)
    assert exc_info.value.__context__ is None


def test_message_larger_than_8kib_is_rejected() -> None:
    message = build_message()

    # 향후 envelope 필드가 추가되더라도 직렬화 결과의
    # 8KiB 상한이 유지되는지 검증합니다.
    with patch.object(
        WorkerMessage,
        "model_dump",
        return_value={"synthetic_field": "x" * 9000},
    ):
        with pytest.raises(StreamMessageEncodingError):
            encode_stream_message(message)
