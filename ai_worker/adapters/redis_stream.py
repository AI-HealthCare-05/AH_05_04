"""redis-py를 사용하는 Redis Streams Adapter입니다."""

import hashlib
import re
from collections.abc import Awaitable, Sequence
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from ai_worker.adapters.errors import (
    StreamMessageDecodingError,
    StreamOperationError,
)
from ai_worker.adapters.redis_message_codec import (
    RedisKey,
    RedisValue,
    decode_stream_message,
    encode_stream_message,
)
from ai_worker.core.quarantine import (
    QuarantineFailureCode,
    RejectedWorkerDelivery,
)
from ai_worker.core.stream import (
    AutoClaimResult,
    PendingMessage,
    WorkerDelivery,
)
from ai_worker.schemas.messages import WorkerMessage

type RedisStreamId = str | bytes
type RedisFields = dict[RedisKey, RedisValue]
type RedisReadResult = list[
    tuple[
        RedisStreamId,
        list[tuple[RedisStreamId, RedisFields]],
    ]
]
type RedisCommandValue = bytes | bytearray | memoryview[int] | str | int | float

_SAFE_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class RedisStreamAdapter:
    """Redis Streams 명령을 Worker 계약 뒤에 감춥니다."""

    def __init__(
        self,
        client: Redis,
        *,
        stream_name: str = "oryak:jobs",
        group_name: str = "ai-workers",
    ) -> None:
        self._client = client
        self._stream_name = stream_name
        self._group_name = group_name

    async def ensure_consumer_group(self) -> None:
        group_exists = False
        operation_failed = False

        try:
            await self._client.xgroup_create(
                name=self._stream_name,
                groupname=self._group_name,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            group_exists = "BUSYGROUP" in str(exc)
            operation_failed = not group_exists
        except RedisError:
            operation_failed = True

        if group_exists:
            return

        if operation_failed:
            raise StreamOperationError()

    async def publish(self, message: WorkerMessage) -> str:
        fields = cast(
            dict[RedisCommandValue, RedisCommandValue],
            encode_stream_message(message),
        )

        try:
            stream_message_id = await _run_redis(
                self._client.xadd(
                    self._stream_name,
                    fields,
                )
            )
        except RedisError:
            raise StreamOperationError() from None

        return _decode_stream_id(stream_message_id)

    async def read(
        self,
        *,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 5000,
    ) -> tuple[WorkerDelivery | RejectedWorkerDelivery, ...]:
        if not consumer_name.strip():
            raise ValueError("consumer_name은 비어 있을 수 없습니다.")
        if count < 1:
            raise ValueError("count는 1 이상이어야 합니다.")
        if block_ms < 0:
            raise ValueError("block_ms는 0 이상이어야 합니다.")

        try:
            result = await _run_redis(
                self._client.xreadgroup(
                    groupname=self._group_name,
                    consumername=consumer_name,
                    streams={self._stream_name: ">"},
                    count=count,
                    block=block_ms,
                    noack=False,
                )
            )
        except RedisError:
            raise StreamOperationError() from None

        return _decode_read_result(
            result,
            stream_name=self._stream_name,
        )

    async def acknowledge(self, stream_message_id: str) -> None:
        try:
            acknowledged_count = await _run_redis(
                self._client.xack(
                    self._stream_name,
                    self._group_name,
                    stream_message_id,
                )
            )
        except RedisError:
            raise StreamOperationError() from None

        # 존재하지 않거나 이미 ACK된 메시지를 성공으로 처리하지 않습니다.
        if acknowledged_count != 1:
            raise StreamOperationError()

    async def list_pending(
        self,
        *,
        count: int = 100,
    ) -> tuple[PendingMessage, ...]:
        if count < 1:
            raise ValueError("count는 1 이상이어야 합니다.")

        try:
            result = await _run_redis(
                self._client.xpending_range(
                    name=self._stream_name,
                    groupname=self._group_name,
                    min="-",
                    max="+",
                    count=count,
                )
            )
        except RedisError:
            raise StreamOperationError() from None

        return _decode_pending_result(result)

    async def claim(
        self,
        *,
        consumer_name: str,
        stream_message_ids: Sequence[str],
        min_idle_ms: int,
    ) -> tuple[WorkerDelivery | RejectedWorkerDelivery, ...]:
        if not consumer_name.strip():
            raise ValueError("consumer_name은 비어 있을 수 없습니다.")
        if min_idle_ms < 0:
            raise ValueError("min_idle_ms는 0 이상이어야 합니다.")
        if not stream_message_ids:
            return ()

        try:
            result = await _run_redis(
                self._client.xclaim(
                    self._stream_name,
                    self._group_name,
                    consumer_name,
                    min_idle_ms,
                    list(stream_message_ids),
                )
            )
        except RedisError:
            raise StreamOperationError() from None

        return _decode_claim_result(
            result,
            stream_name=self._stream_name,
        )

    async def auto_claim(
        self,
        *,
        consumer_name: str,
        min_idle_ms: int,
        start_id: str = "0-0",
        count: int = 100,
    ) -> AutoClaimResult:
        if not consumer_name.strip():
            raise ValueError("consumer_name은 비어 있을 수 없습니다.")
        if min_idle_ms < 0:
            raise ValueError("min_idle_ms는 0 이상이어야 합니다.")
        if not start_id.strip():
            raise ValueError("start_id는 비어 있을 수 없습니다.")
        if count < 1:
            raise ValueError("count는 1 이상이어야 합니다.")

        try:
            result = await _run_redis(
                self._client.xautoclaim(
                    self._stream_name,
                    self._group_name,
                    consumer_name,
                    min_idle_ms,
                    start_id,
                    count=count,
                )
            )
        except RedisError:
            raise StreamOperationError() from None

        return _decode_auto_claim_result(
            result,
            stream_name=self._stream_name,
        )


def _decode_read_result(
    result: object,
    *,
    stream_name: str,
) -> tuple[WorkerDelivery | RejectedWorkerDelivery, ...]:
    redis_result = cast(RedisReadResult, result)
    deliveries: list[WorkerDelivery | RejectedWorkerDelivery] = []

    for _, entries in redis_result:
        for stream_message_id, fields in entries:
            try:
                message = decode_stream_message(fields)
            except StreamMessageDecodingError:
                deliveries.append(
                    _build_rejected_delivery(
                        stream_name=stream_name,
                        stream_message_id=stream_message_id,
                        fields=fields,
                    )
                )
                continue

            deliveries.append(
                WorkerDelivery(
                    stream_message_id=_decode_stream_id(stream_message_id),
                    message=message,
                    stream_name=stream_name,
                    message_digest=_digest_stream_fields(fields),
                )
            )

    return tuple(deliveries)


def _decode_stream_id(value: object) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            raise StreamOperationError() from None

    if isinstance(value, str) and value.strip():
        return value

    raise StreamOperationError()


def _build_rejected_delivery(
    *,
    stream_name: str,
    stream_message_id: RedisStreamId,
    fields: RedisFields,
) -> RejectedWorkerDelivery:
    raw_schema_version = _safe_text_field(fields, "schema_version")

    failure_code = (
        QuarantineFailureCode.UNSUPPORTED_SCHEMA_VERSION
        if raw_schema_version is not None and raw_schema_version != "1.0"
        else QuarantineFailureCode.INVALID_MESSAGE_SCHEMA
    )

    original_schema_version = raw_schema_version
    if original_schema_version is not None and (
        not original_schema_version.strip() or len(original_schema_version) > 20
    ):
        original_schema_version = None

    trace_id = _safe_text_field(fields, "trace_id")
    if trace_id is not None and _SAFE_TRACE_ID_PATTERN.fullmatch(trace_id) is None:
        trace_id = None

    return RejectedWorkerDelivery(
        stream_name=stream_name,
        stream_entry_id=_decode_stream_id(stream_message_id),
        message_digest=_digest_stream_fields(fields),
        failure_code=failure_code,
        job_id=_safe_uuid_field(fields, "job_id"),
        original_event_id=_safe_uuid_field(fields, "event_id"),
        original_schema_version=original_schema_version,
        trace_id=trace_id,
    )


def _safe_uuid_field(
    fields: RedisFields,
    field_name: str,
) -> UUID | None:
    value = _safe_text_field(fields, field_name)

    if value is None:
        return None

    try:
        return UUID(value)
    except ValueError:
        return None


def _safe_text_field(
    fields: RedisFields,
    field_name: str,
) -> str | None:
    value = fields.get(field_name)

    if value is None:
        value = fields.get(field_name.encode())

    if value is None:
        return None

    try:
        if isinstance(value, bytes):
            return value.decode("utf-8")

        return str(value)
    except UnicodeDecodeError:
        return None


def _digest_stream_fields(fields: RedisFields) -> str:
    """순서와 무관한 Redis field SHA-256을 만들고 원문은 보존하지 않습니다."""

    encoded_items = sorted(
        (
            _digest_component(key),
            _digest_component(value),
        )
        for key, value in fields.items()
    )
    digest = hashlib.sha256()

    for key, value in encoded_items:
        for component in (key, value):
            digest.update(len(component).to_bytes(8, byteorder="big"))
            digest.update(component)

    return digest.hexdigest()


def _digest_component(value: RedisKey | RedisValue) -> bytes:
    if isinstance(value, bytes):
        return b"bytes:" + value

    return f"{type(value).__name__}:{value}".encode()


def _decode_claim_result(
    result: object,
    *,
    stream_name: str,
) -> tuple[WorkerDelivery | RejectedWorkerDelivery, ...]:
    entries = cast(
        list[tuple[RedisStreamId, RedisFields]],
        result,
    )
    deliveries: list[WorkerDelivery | RejectedWorkerDelivery] = []

    for stream_message_id, fields in entries:
        try:
            message = decode_stream_message(fields)
        except StreamMessageDecodingError:
            deliveries.append(
                _build_rejected_delivery(
                    stream_name=stream_name,
                    stream_message_id=stream_message_id,
                    fields=fields,
                )
            )
            continue

        deliveries.append(
            WorkerDelivery(
                stream_message_id=_decode_stream_id(stream_message_id),
                message=message,
                stream_name=stream_name,
                message_digest=_digest_stream_fields(fields),
            )
        )

    return tuple(deliveries)


def _decode_auto_claim_result(
    result: object,
    *,
    stream_name: str,
) -> AutoClaimResult:
    if not isinstance(result, (list, tuple)):
        raise StreamOperationError()

    if len(result) not in (2, 3):
        raise StreamOperationError()

    next_start_id = _decode_stream_id(result[0])

    raw_entries = result[1]

    if not isinstance(raw_entries, list):
        raise StreamOperationError()

    deliveries = _decode_claim_result(
        raw_entries,
        stream_name=stream_name,
    )

    deleted_message_ids: tuple[str, ...] = ()

    if len(result) == 3:
        raw_deleted_ids = result[2]

        if not isinstance(raw_deleted_ids, list):
            raise StreamOperationError()

        deleted_message_ids = tuple(_decode_stream_id(stream_message_id) for stream_message_id in raw_deleted_ids)

    return AutoClaimResult(
        next_start_id=next_start_id,
        deliveries=deliveries,
        deleted_message_ids=deleted_message_ids,
    )


def _decode_pending_result(
    result: object,
) -> tuple[PendingMessage, ...]:
    rows = cast(
        list[dict[str | bytes, object]],
        result,
    )
    pending_messages: list[PendingMessage] = []

    try:
        for row in rows:
            pending_messages.append(
                PendingMessage(
                    stream_message_id=_decode_stream_id(_get_pending_value(row, "message_id")),
                    consumer_name=_decode_stream_id(_get_pending_value(row, "consumer")),
                    idle_ms=_decode_integer(
                        _get_pending_value(
                            row,
                            "time_since_delivered",
                        )
                    ),
                    delivery_count=_decode_integer(
                        _get_pending_value(
                            row,
                            "times_delivered",
                        )
                    ),
                )
            )
    except (TypeError, ValueError):
        raise StreamOperationError() from None

    return tuple(pending_messages)


def _get_pending_value(
    row: dict[str | bytes, object],
    name: str,
) -> object:
    if name in row:
        return row[name]

    encoded_name = name.encode("utf-8")

    if encoded_name in row:
        return row[encoded_name]

    raise StreamOperationError()


def _decode_integer(value: object) -> int:
    if isinstance(value, bool):
        raise StreamOperationError()

    if isinstance(value, int):
        return value

    if isinstance(value, bytes):
        try:
            return int(value.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise StreamOperationError() from None

    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            raise StreamOperationError() from None

    raise StreamOperationError()


async def _run_redis[T](operation: Awaitable[T]) -> T:
    """Redis 원본 예외를 외부 예외 chain에 남기지 않습니다."""

    try:
        result = await operation
    except RedisError:
        pass
    else:
        return result

    # 활성 예외 처리 구간 밖에서 안전한 오류를 생성합니다.
    raise StreamOperationError()
