"""WorkerMessage와 Redis Stream field 사이의 변환 경계입니다."""

from collections.abc import Mapping

from pydantic import ValidationError

from ai_worker.adapters.errors import (
    StreamMessageDecodingError,
    StreamMessageEncodingError,
)
from ai_worker.schemas.messages import WorkerMessage

MAX_STREAM_MESSAGE_BYTES = 8 * 1024

type RedisKey = str | bytes
type RedisValue = str | bytes | int | float


def encode_stream_message(
    message: WorkerMessage,
) -> dict[str, str]:
    """검증된 WorkerMessage를 Redis XADD field로 변환합니다."""

    payload = message.model_dump(mode="json")
    encoded = {key: str(value) for key, value in payload.items()}

    message_size = sum(len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in encoded.items())

    if message_size > MAX_STREAM_MESSAGE_BYTES:
        raise StreamMessageEncodingError()

    return encoded


def decode_stream_message[
    K: RedisKey,
    V: RedisValue,
](
    fields: Mapping[K, V],
) -> WorkerMessage:
    """Redis field를 검증된 WorkerMessage로 변환합니다."""

    try:
        normalized: dict[str, str | int] = {_decode_text(key): _decode_text(value) for key, value in fields.items()}

        normalized["attempt"] = int(normalized["attempt"])
        message = WorkerMessage.model_validate(normalized)
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        ValidationError,
    ):
        pass
    else:
        return message

    # 원본 Redis field나 검증 오류가 예외 chain에 남지 않습니다.
    raise StreamMessageDecodingError()


def _decode_text(value: RedisKey | RedisValue) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")

    return str(value)
