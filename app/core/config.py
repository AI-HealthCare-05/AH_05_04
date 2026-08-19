import os
import uuid
import zoneinfo
from dataclasses import field
from datetime import UTC, timedelta, timezone, tzinfo
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


def get_default_timezone() -> tzinfo:
    # 로컬 Windows 개발 환경에 tzdata가 없어도 Asia/Seoul 기준 시간을 쓸 수 있게 하는 fallback입니다
    # (CI는 ubuntu-latest, macOS 개발 환경은 tzdata가 이미 있어서 영향 없음).
    try:
        return zoneinfo.ZoneInfo("Asia/Seoul")
    except zoneinfo.ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9), name="Asia/Seoul")


def parse_timezone(value: str) -> tzinfo:
    # 배포 환경변수로 TIMEZONE="UTC" 등 문자열이 들어와도 tzinfo로 안전하게 변환합니다.
    if value.upper() == "UTC":
        return UTC
    try:
        return zoneinfo.ZoneInfo(value)
    except zoneinfo.ZoneInfoNotFoundError:
        if value == "Asia/Seoul":
            return timezone(timedelta(hours=9), name="Asia/Seoul")
        raise


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    ENV: Env = Env.LOCAL
    SECRET_KEY: str = f"default-secret-key{uuid.uuid4().hex}"
    TIMEZONE: tzinfo = field(default_factory=get_default_timezone)

    @field_validator("TIMEZONE", mode="before")
    @classmethod
    def _validate_timezone(cls, value: object) -> object:
        if isinstance(value, str):
            return parse_timezone(value)
        return value

    TEMPLATE_DIR: str = os.path.join(
        Path(__file__).resolve().parent.parent,
        "templates",
    )
    # 실제 의료문서 업로드 파일 저장 경로. data/는 비식별 샘플 전용이라 gitignore된 uploads/를 사용합니다.
    STORAGE_DIR: str = os.path.join(
        Path(__file__).resolve().parent.parent.parent,
        "uploads",
        "medical_documents",
    )

    DB_HOST: str
    DB_PORT: int
    DB_EXPOSE_PORT: int = 3306
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    DB_CONNECT_TIMEOUT: int = 5
    DB_CONNECTION_POOL_MAXSIZE: int = 10
    SQLALCHEMY_ECHO: bool = False

    COOKIE_DOMAIN: str = "localhost"

    # 임시값입니다. Frontend 개발 서버 주소는 아직 확정 전이라, 팀 회의에서 정해지는 대로
    # 이 기본값을 실제 주소로 교체합니다(여러 개면 콤마로 구분).
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173"

    @property
    def cors_allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 14 * 24 * 60
    JWT_LEEWAY: int = 5

    # 정현우님(app/services/guide_ai) 및 chat_ai 연동용 OpenAI 설정.
    # CI의 test 잡 env에는 OPENAI_API_KEY가 없어서 필수값(DB_*처럼)으로 두면 전체 테스트가 깨집니다.
    # 실제 키가 없으면 OpenAI 호출 시점에만 401 -> 500으로 실패하도록 placeholder 기본값을 둡니다.
    OPENAI_API_KEY: str = "sk-not-configured"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT_SECONDS: float = 20.0

    @property
    def database_url(self) -> str:
        user = quote_plus(self.DB_USER)
        password = quote_plus(self.DB_PASSWORD)

        return f"mysql+asyncmy://{user}:{password}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
