import zoneinfo
from dataclasses import field
from datetime import UTC, timedelta, timezone, tzinfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    TIMEZONE: tzinfo = field(default_factory=_get_default_timezone)
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = Field(default=6379, ge=1, le=65535)
    REDIS_PASSWORD: str | None = None

    REDIS_STREAM_NAME: str = "oryak:jobs"
    REDIS_CONSUMER_GROUP: str = "ai-workers"
    REDIS_CONSUMER_NAME: str = "ai-worker-local"
    REDIS_BLOCK_MS: int = Field(default=5000, ge=0)

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
