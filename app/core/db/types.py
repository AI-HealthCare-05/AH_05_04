"""UUID를 DB 문자열과 Python UUID 객체 사이에서 변환하는 공통 타입입니다."""

from typing import Any
from uuid import UUID

from sqlalchemy import CHAR
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator


class UUIDChar(TypeDecorator[UUID]):
    """Python UUID를 DB의 CHAR(36) 문자열로 저장합니다.

    PostgreSQL 전환 1단계에서는 기존 MySQL 데이터 및 API 호환성을 위해
    UUID 저장 형식을 CHAR(36)으로 유지합니다.

    PostgreSQL 네이티브 UUID 타입 전환은 별도 migration에서 처리합니다.
    """

    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(
        self,
        value: UUID | str | None,
        dialect: Dialect,
    ) -> str | None:
        """Python UUID 또는 UUID 문자열을 DB 저장 문자열로 변환합니다."""

        if value is None:
            return None

        if isinstance(value, UUID):
            return str(value)

        return str(UUID(value))

    def process_result_value(
        self,
        value: Any,
        dialect: Dialect,
    ) -> UUID | None:
        """DB 값을 Python UUID 객체로 변환합니다."""

        if value is None:
            return None

        if isinstance(value, UUID):
            return value

        return UUID(str(value))
