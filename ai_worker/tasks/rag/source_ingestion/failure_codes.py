"""Shared failure vocabulary for recording and reading Source attempts."""

from enum import StrEnum


class IngestionProcessingFailureCode(StrEnum):
    """Parser 이후 단계에서 기록할 수 있는 안전한 고정 실패 코드입니다."""

    PARSER_VALIDATION_FAILED = "PARSER_VALIDATION_FAILED"
    REJECTION_LIMIT_EXCEEDED = "REJECTION_LIMIT_EXCEEDED"


COLLECTION_EMPTY_RESULT = "COLLECTION_EMPTY_RESULT"
SNAPSHOT_POLICY_EMPTY_RESULT = "SNAPSHOT_POLICY_EMPTY_RESULT"
