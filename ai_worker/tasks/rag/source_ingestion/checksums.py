"""Source 원본 바이트의 무결성 checksum을 계산합니다."""

import hashlib
from collections.abc import Iterable


def raw_checksum(chunks: Iterable[bytes]) -> str:
    """원본 청크를 순서대로 연결한 바이트의 SHA-256을 반환합니다."""
    digest = hashlib.sha256()

    for chunk in chunks:
        digest.update(chunk)

    return digest.hexdigest()
