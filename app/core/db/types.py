"""
DB에는 하이픈이 포함된 36자리 문자열로 저장되고,
Python에서는 계속 UUID 객체로 사용하기 위해 types.py 생성

"""

from typing import Any
from uuid import UUID

from sqlalchemy import CHAR
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator


class UUIDChar(TypeDecorator[UUID]):
    """Python UUID를 MySQL CHAR(36)으로 저장한다."""

    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(
        self,
        value: UUID | str | None,
        dialect: Dialect,
    ) -> str | None:
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
        if value is None:
            return None

        if isinstance(value, UUID):
            return value

        return UUID(str(value))
