import zoneinfo
from dataclasses import field
from datetime import UTC, timedelta, timezone, tzinfo
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from provider_contracts.observability import DeploymentEnvironment


def _get_default_timezone() -> tzinfo:
    # 로컬 Windows 개발 환경에 tzdata가 없어도 Asia/Seoul 기준 시간을 쓸 수 있게 하는 fallback입니다
    # (CI는 ubuntu-latest, macOS 개발 환경은 tzdata가 이미 있어서 영향 없음).
    try:
        return zoneinfo.ZoneInfo("Asia/Seoul")
    except zoneinfo.ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9), name="Asia/Seoul")


def _parse_timezone(value: str) -> tzinfo:
    # 배포 환경변수로 들어오는 "Asia/Seoul" 같은 문자열을 tzinfo로 변환합니다.
    if value.upper() == "UTC":
        return UTC
    try:
        return zoneinfo.ZoneInfo(value)
    except zoneinfo.ZoneInfoNotFoundError:
        if value == "Asia/Seoul":
            return timezone(timedelta(hours=9), name="Asia/Seoul")
        raise


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="allow")

    ENV: DeploymentEnvironment
    TIMEZONE: tzinfo = field(default_factory=_get_default_timezone)
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = Field(default=6379, ge=1, le=65535)
    REDIS_PASSWORD: str | None = None

    REDIS_STREAM_NAME: str = "oryak:jobs"
    REDIS_CONSUMER_GROUP: str = "ai-workers"
    REDIS_CONSUMER_NAME: str = "ai-worker-local"
    REDIS_BLOCK_MS: int = Field(default=5000, ge=0)
    REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0)
    REDIS_SOCKET_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)
    OCR_REQUEST_DEADLINE_SECONDS: float = Field(
        default=60.0,
        gt=0,
    )
    OCR_PROVIDER_BUDGET_SECONDS: float = Field(
        default=55.0,
        gt=0,
    )
    OCR_RESPONSE_MARGIN_SECONDS: float = Field(
        default=5.0,
        ge=0,
    )

    @field_validator("TIMEZONE", mode="before")
    @classmethod
    def _validate_timezone(cls, value: object) -> object:
        if isinstance(value, str):
            return _parse_timezone(value)
        return value

    @field_validator(
        "REDIS_HOST",
        "REDIS_STREAM_NAME",
        "REDIS_CONSUMER_GROUP",
        "REDIS_CONSUMER_NAME",
    )
    @classmethod
    def _validate_non_empty_redis_setting(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("Redis 설정값은 비어 있을 수 없습니다.")

        return normalized

    @field_validator("REDIS_PASSWORD", mode="before")
    @classmethod
    def _normalize_redis_password(
        cls,
        value: object,
    ) -> object:
        if value == "":
            return None

        return value

    @model_validator(mode="after")
    def _validate_redis_timeout_relationship(self) -> Self:
        """Blocking read보다 socket timeout을 길게 유지합니다."""

        block_seconds = self.REDIS_BLOCK_MS / 1000

        if self.REDIS_SOCKET_TIMEOUT_SECONDS <= block_seconds:
            raise ValueError("REDIS_SOCKET_TIMEOUT_SECONDS는 REDIS_BLOCK_MS보다 길어야 합니다.")

        return self

    @model_validator(mode="after")
    def _validate_ocr_budget_relationship(self) -> Self:
        """Provider 실행과 완료 여유가 OCR 전체 deadline을 넘지 않게 합니다."""

        required_seconds = self.OCR_PROVIDER_BUDGET_SECONDS + self.OCR_RESPONSE_MARGIN_SECONDS

        if required_seconds > self.OCR_REQUEST_DEADLINE_SECONDS:
            raise ValueError("OCR Provider 예산과 완료 여유의 합은 OCR_REQUEST_DEADLINE_SECONDS를 초과할 수 없습니다.")

        return self
