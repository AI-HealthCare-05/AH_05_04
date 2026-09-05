"""비민감 Dead-letter envelope를 Redis Stream에 발행합니다."""

from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from ai_worker.adapters.errors import StreamOperationError
from ai_worker.core.quarantine import DeadLetterEnvelope

type RedisCommandValue = bytes | bytearray | memoryview[int] | str | int | float


class RedisDeadLetterStreamPublisher:
    """Dead-letter 전용 Stream Publisher입니다."""

    def __init__(
        self,
        client: Redis,
        *,
        stream_name: str = "oryak:jobs:dead-letter",
    ) -> None:
        normalized_stream_name = stream_name.strip()

        if not normalized_stream_name:
            raise ValueError("stream_name은 비어 있을 수 없습니다.")

        self._client = client
        self._stream_name = normalized_stream_name

    async def publish(
        self,
        envelope: DeadLetterEnvelope,
    ) -> str:
        """허용된 비민감 필드만 DLQ Stream에 발행합니다."""

        fields: dict[str, str] = {
            "schema_version": "1.0",
            "event_kind": "QUARANTINE_RECORDED",
            "event_id": str(envelope.event_id),
            "quarantine_id": str(envelope.quarantine_id),
            "stream_entry_id": envelope.stream_entry_id,
            "message_digest": envelope.message_digest,
            "failure_code": envelope.failure_code.value,
        }

        if envelope.original_schema_version is not None:
            fields["original_schema_version"] = envelope.original_schema_version

        if envelope.trace_id is not None:
            fields["trace_id"] = envelope.trace_id

        redis_fields = cast(
            dict[RedisCommandValue, RedisCommandValue],
            fields,
        )

        try:
            stream_message_id = await self._client.xadd(
                self._stream_name,
                redis_fields,
            )
        except RedisError:
            # Redis 주소·응답·인증 세부정보를 외부 오류에 연결하지 않습니다.
            raise StreamOperationError() from None

        return _decode_stream_id(stream_message_id)


def _decode_stream_id(value: object) -> str:
    if isinstance(value, bytes):
        try:
            decoded_value = value.decode("utf-8")
        except UnicodeDecodeError:
            raise StreamOperationError() from None

        if decoded_value.strip():
            return decoded_value

        raise StreamOperationError()

    if isinstance(value, str) and value.strip():
        return value

    raise StreamOperationError()
