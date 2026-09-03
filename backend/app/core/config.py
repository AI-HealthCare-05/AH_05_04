import math
import os
import uuid
import zoneinfo
from dataclasses import field
from datetime import UTC, timedelta, timezone, tzinfo
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

from provider_contracts.observability import DeploymentEnvironment as Env


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
    # backend/ 폴더 분리 이후에도 저장소 루트의 uploads/를 가리키도록 parent를 한 단계 더 올라갑니다.
    STORAGE_DIR: str = os.path.join(
        Path(__file__).resolve().parent.parent.parent.parent,
        "uploads",
        "medical_documents",
    )

    DB_HOST: str
    # PostgreSQL 컨테이너 내부 기본 포트입니다.
    DB_PORT: int = 5432
    # 로컬 PC에서 컨테이너에 접근하는 포트입니다.
    DB_EXPOSE_PORT: int = 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    DB_CONNECT_TIMEOUT: int = 5
    DB_CONNECTION_POOL_MAXSIZE: int = 10
    SQLALCHEMY_ECHO: bool = False

    COOKIE_DOMAIN: str = "localhost"

    # 팀 로컬 개발 기준 Frontend origin입니다.
    # 여러 origin을 허용해야 하면 콤마로 구분합니다.
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
    CHAT_HISTORY_CONTEXT_ENABLED: bool = False
    RELEASE_VALIDATION_ALLOWED: bool = False

    CLOVA_OCR_INVOKE_URL: str = ""
    CLOVA_OCR_SECRET: str = ""
    CLOVA_OCR_TIMEOUT_SECONDS: float = 20.0

    # OCR 결과를 OpenAI Structured Outputs로 구조화할지 결정합니다.
    # 명시적으로 활성화하지 않은 환경에서는 외부 LLM에 OCR 원문을 전달하지 않고
    # 기존 규칙 기반 구조화기를 사용합니다.
    OCR_STRUCTURE_LLM_ENABLED: bool = False

    # Guide·Chat과 독립적으로 OCR 구조화 모델을 변경할 수 있게 분리합니다.
    # 이 값들은 OCR_STRUCTURE_LLM_ENABLED=true일 때만 사용됩니다.
    OCR_STRUCTURE_MODEL: str = "gpt-4o-mini"
    OCR_STRUCTURE_TIMEOUT_SECONDS: float = 30.0
    # OCR 동기 요청 전체 deadline입니다. 개별 Provider timeout의 상한이 아니라
    # 요청 시작부터 응답 생성까지의 monotonic 예산입니다.
    OCR_REQUEST_DEADLINE_SECONDS: float = 60.0

    # 파일 읽기·구조화·필드 저장 등 Provider 호출 밖 로컬 처리 예약입니다.
    # 기동 검증에만 사용하고 런타임 timeout으로는 쓰지 않습니다.
    OCR_LOCAL_PROCESSING_RESERVE_SECONDS: float = 3.0

    # 실패 상태 저장과 응답 생성에 남겨두는 여유입니다.
    OCR_RESPONSE_MARGIN_SECONDS: float = 5.0

    @model_validator(mode="after")
    def validate_chat_history_environment(self) -> "Config":
        if self.CHAT_HISTORY_CONTEXT_ENABLED and self.ENV is not Env.LOCAL:
            raise ValueError("CHAT_HISTORY_CONTEXT_ENABLED is allowed only in local environment")
        if self.RELEASE_VALIDATION_ALLOWED and self.ENV is not Env.LOCAL:
            raise ValueError("RELEASE_VALIDATION_ALLOWED is allowed only in local environment")
        return self

    @property
    def database_url(self) -> str:
        # URL.create()를 사용하면 비밀번호에 @, /, % 같은 문자가 있어도
        # 연결 문자열이 깨지지 않습니다.
        url = URL.create(
            drivername="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        )

        # Alembic은 문자열 URL을 사용하므로 실제 연결 문자열로 렌더링합니다.
        return url.render_as_string(hide_password=False)

    @model_validator(mode="after")
    def validate_ocr_timeout_budget(self) -> "Config":
        for name, value in (
            ("OCR_REQUEST_DEADLINE_SECONDS", self.OCR_REQUEST_DEADLINE_SECONDS),
            ("OCR_LOCAL_PROCESSING_RESERVE_SECONDS", self.OCR_LOCAL_PROCESSING_RESERVE_SECONDS),
            ("OCR_RESPONSE_MARGIN_SECONDS", self.OCR_RESPONSE_MARGIN_SECONDS),
            ("CLOVA_OCR_TIMEOUT_SECONDS", self.CLOVA_OCR_TIMEOUT_SECONDS),
            ("OCR_STRUCTURE_TIMEOUT_SECONDS", self.OCR_STRUCTURE_TIMEOUT_SECONDS),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")

        # Provider 개별 상한의 합이 전체 deadline을 채우면 로컬 처리 예산이 0이 되므로,
        # 로컬 예약과 응답 여유를 포함해 비교합니다.
        structure_timeout = self.OCR_STRUCTURE_TIMEOUT_SECONDS if self.OCR_STRUCTURE_LLM_ENABLED else 0.0
        required = (
            self.CLOVA_OCR_TIMEOUT_SECONDS
            + structure_timeout
            + self.OCR_RESPONSE_MARGIN_SECONDS
            + self.OCR_LOCAL_PROCESSING_RESERVE_SECONDS
        )

        if required > self.OCR_REQUEST_DEADLINE_SECONDS:
            raise ValueError(
                "OCR timeout budget exceeds OCR_REQUEST_DEADLINE_SECONDS: "
                f"required={required}, deadline={self.OCR_REQUEST_DEADLINE_SECONDS}"
            )

        return self
