import hashlib
import hmac
import json
import re
from typing import Any

# $는 마지막 \n 앞에서도 매치하므로(예: "...key\n"), 끝을 \Z로 고정해 계약이 요구하는
# 순수 16~255자 ASCII 영숫자와 -._: 만 통과하도록 합니다.
_KEY_PATTERN = re.compile(r"\A[A-Za-z0-9\-._:]{16,255}\Z")


class IdempotencyKeyFormatError(ValueError):
    """`Idempotency-Key` 헤더 값이 idempotency-v1.md의 형식 요구사항을 위반할 때 발생합니다."""


def validate_idempotency_key_format(raw_key: str) -> None:
    """idempotency-v1.md "적용 요청": 16~255자의 ASCII 영숫자와 -._: 만 허용합니다."""
    if not raw_key:
        raise IdempotencyKeyFormatError("IDEMPOTENCY_KEY_REQUIRED")
    if not _KEY_PATTERN.match(raw_key):
        raise IdempotencyKeyFormatError("IDEMPOTENCY_KEY_INVALID")


def compute_key_hmac(raw_key: str, *, hmac_key: str) -> str:
    """원문 key는 저장하지 않고, 서버 secret으로 versioned HMAC-SHA-256 처리한 값만 저장합니다."""
    return hmac.new(
        hmac_key.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def compute_request_hash(fingerprint: dict[str, Any]) -> str:
    """idempotency-v1.md "식별 범위와 요청 해시": canonical JSON 직렬화 후 SHA-256.

    호출자는 인증 토큰, trace ID, 전송 시각을 fingerprint에 포함하지 않아야 합니다.
    직렬화 불가능한 값이 섞여 들어오면 조용히 문자열로 바꾸지 않고 `TypeError`로
    바로 드러나야 호출자의 fingerprint 조립 실수를 fail-closed로 잡을 수 있습니다.
    """
    canonical_json = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
