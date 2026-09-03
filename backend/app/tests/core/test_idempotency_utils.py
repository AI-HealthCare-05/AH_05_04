import pytest

from app.core.utils.idempotency import (
    IdempotencyKeyFormatError,
    compute_request_hash,
    validate_idempotency_key_format,
)


def test_validate_idempotency_key_format_rejects_trailing_newline() -> None:
    """Python 정규식의 `$`는 마지막 `\\n` 앞에서도 매치하므로, 패턴이 `\\Z`로 끝을 고정하지
    않으면 "...key\\n" 같은 값이 16~255자 ASCII 영숫자/`-._:` 계약을 우회해 통과할 수 있습니다.
    """
    with pytest.raises(IdempotencyKeyFormatError):
        validate_idempotency_key_format("a" * 16 + "\n")


def test_compute_request_hash_raises_for_non_serializable_value() -> None:
    """fingerprint는 호출자가 조립하므로, 직렬화 불가능한 값이 섞이면 조용히 문자열로
    바뀌지 않고 바로 실패해야 호출자의 조립 실수를 fail-closed로 잡을 수 있습니다.
    """
    with pytest.raises(TypeError):
        compute_request_hash({"document_id": object()})
