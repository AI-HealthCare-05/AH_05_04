import zoneinfo
from dataclasses import field
from datetime import UTC, timedelta, timezone, tzinfo
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

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

    # Worker runtime은 Job 실행 DB에만 접근합니다. Admin·Migration 계정과 Provider secret은
    # 주입하지 않습니다(infra/docker/docker-compose.prod.yml의 ai-worker environment 참고).
    DB_HOST: str
    DB_PORT: int = Field(default=5432, ge=1, le=65535)
    DB_NAME: str
    DB_USER: str
    DB_PASSWORD: str
    DB_CONNECT_TIMEOUT: int = Field(default=5, gt=0)
    DB_CONNECTION_POOL_MAXSIZE: int = Field(default=10, gt=0)
    SQLALCHEMY_ECHO: bool = False

    # async-job-v1.md "시도와 재시도": lease가 만료되기 전에 heartbeat가 반드시 한 번 이상
    # 갱신되어야 하므로 두 값의 관계를 기동 시 검증합니다.
    WORKER_LEASE_DURATION_SECONDS: float = Field(default=75.0, gt=0)
    WORKER_HEARTBEAT_INTERVAL_SECONDS: float = Field(default=10.0, gt=0)
    WORKER_HARD_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)
    # 한 프로세스가 동시에 처리하는 delivery 수입니다.
    WORKER_CONCURRENCY: int = Field(default=1, ge=1)
    # 종료 신호 후 진행 중 실행을 기다리는 상한입니다.
    WORKER_SHUTDOWN_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)

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

    @model_validator(mode="after")
    def _validate_lease_relationship(self) -> Self:
        """heartbeat가 lease 만료 전에 갱신되고, 실행이 lease 안에서 끝나게 합니다.

        `SqlAlchemyLeaseHeartbeat`도 같은 조건을 생성자에서 검사하지만, 잘못된 설정을
        실행 중이 아니라 기동 시점에 드러내기 위해 여기서 먼저 막습니다.
        """
        if self.OCR_REQUEST_DEADLINE_SECONDS > self.WORKER_HARD_TIMEOUT_SECONDS:
            raise ValueError(
                "OCR_REQUEST_DEADLINE_SECONDS는 "
                "WORKER_HARD_TIMEOUT_SECONDS를 초과할 수 없습니다."
            )
        if self.WORKER_HEARTBEAT_INTERVAL_SECONDS >= self.WORKER_LEASE_DURATION_SECONDS:
            raise ValueError("WORKER_HEARTBEAT_INTERVAL_SECONDS는 WORKER_LEASE_DURATION_SECONDS보다 짧아야 합니다.")

        if self.WORKER_HARD_TIMEOUT_SECONDS >= self.WORKER_LEASE_DURATION_SECONDS:
            raise ValueError("WORKER_HARD_TIMEOUT_SECONDS는 WORKER_LEASE_DURATION_SECONDS보다 짧아야 합니다.")
        if self.WORKER_HARD_TIMEOUT_SECONDS + self.OCR_RESPONSE_MARGIN_SECONDS > self.WORKER_LEASE_DURATION_SECONDS:
            raise ValueError(
                "WORKER_HARD_TIMEOUT_SECONDS와 OCR_RESPONSE_MARGIN_SECONDS의 합은 "
                "WORKER_LEASE_DURATION_SECONDS를 초과할 수 없습니다."
            )
        return self

    @property
    def database_url(self) -> URL:
        """URL.create를 사용해 비밀번호의 @·/·% 문자도 안전하게 처리합니다."""

        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        )

    @property
    def lease_duration(self) -> timedelta:
        return timedelta(seconds=self.WORKER_LEASE_DURATION_SECONDS)

    @property
    def heartbeat_interval(self) -> timedelta:
        return timedelta(seconds=self.WORKER_HEARTBEAT_INTERVAL_SECONDS)
