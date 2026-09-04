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

# IDEMPOTENCY_HMAC_KEY 필드 기본값과 envs/example.prod.env·example.local.env에 공개된 예시
# 값입니다. 전부 저장소에 노출돼 있어 실제 비밀값이 아니므로, non-local 환경 기동 시 그대로
# 쓰이면 거부해야 합니다(예: local 템플릿 값을 실수로 staging 설정에 복사하는 경로 차단).
_IDEMPOTENCY_HMAC_KEY_PLACEHOLDERS = frozenset(
    {
        "not-configured-idempotency-hmac-key",
        "replace-with-random-production-idempotency-hmac-key-at-least-32-characters",
        "replace-with-random-local-idempotency-hmac-key-at-least-32-characters",
    }
)

# example 파일들의 placeholder 명명 규칙("-at-least-32-characters")과 맞춘 최소 길이입니다.
_IDEMPOTENCY_HMAC_KEY_MIN_LENGTH = 32


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
    # 평소엔 둘 다 5432로 같습니다. 호스트에 5432를 쓰는 다른 프로세스가 있어 충돌할 때만
    # DB_EXPOSE_PORT(호스트 매핑 포트)를 바꾸면 되고, DB_PORT(컨테이너 내부 포트)는 그대로 둡니다.
    DB_PORT: int = 5432
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

    # idempotency-v1.md: 원문 Idempotency-Key는 저장하지 않고 versioned HMAC만 저장합니다.
    # 실제 key rotation 절차·물리 secret 관리는 Privacy·보안 승인 후 별도로 확정합니다(문서 "단일 테이블과
    # 저장 필드" 참고) — 지금은 단일 active version만 지원합니다.
    # 기본값은 프로세스마다 값이 달라지면 안 됩니다 — 서버 재시작이나 여러 Backend 인스턴스가 같은
    # Idempotency-Key를 서로 다른 HMAC으로 계산하면 기존 레코드를 찾지 못해 중복 Job·Outbox가 생깁니다.
    # 그래서 uuid4() 같은 프로세스별 난수 대신 안정적인 placeholder 문자열을 쓰고, production 기동은
    # 아래 validator가 이 placeholder·빈 값으로 시작하지 못하게 막습니다.
    #
    # 운영 주의: 이 키를 교체하면 같은 원문 Idempotency-Key라도 새 digest가 계산되어, 교체 이전
    # 레코드에 대한 재시도가 중복으로 인식되지 못하고 새 Job·Outbox가 생길 수 있습니다(현재는
    # active key 하나로만 조회하며 key_hmac_version별 조회는 지원하지 않음). "rotation 주기를
    # 보존기간보다 길게 제한"하는 것만으로는 안전하지 않습니다 — 교체 직전에 생성된 레코드는
    # 교체 이후에도 최대 IDEMPOTENCY_RECORD_TTL_DAYS만큼 남아 있어, 그 기간 안에 같은 요청이
    # 새 키로 재시도되면 기존 레코드를 찾지 못합니다. 그래서 #235(retained key 전체 조회 구현)
    # 전까지는 이 키를 절대 교체하지 않습니다.
    IDEMPOTENCY_HMAC_KEY: str = "not-configured-idempotency-hmac-key"
    IDEMPOTENCY_HMAC_KEY_VERSION: str = "v1"
    IDEMPOTENCY_RECORD_TTL_DAYS: int = 7

    @field_validator("IDEMPOTENCY_HMAC_KEY", mode="after")
    @classmethod
    def _strip_idempotency_hmac_key(cls, value: str) -> str:
        # 검증(validate_idempotency_hmac_key_configured)과 실제 HMAC 계산
        # (job_intake.py의 compute_key_hmac 호출)이 항상 같은 값을 보도록, 필드 자체를
        # 정규화합니다. 앞뒤 공백만 다른 값이 인스턴스마다 주입되면(K8s secret, YAML
        # quoting 차이 등) 검증은 통과해도 실제 digest가 달라져 기존 레코드를 못 찾습니다.
        return value.strip()

    # app/services/guide_ai 및 chat_ai 연동용 OpenAI 설정.
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

    @model_validator(mode="after")
    def validate_idempotency_hmac_key_configured(self) -> "Config":
        if self.ENV is not Env.LOCAL:
            key = self.IDEMPOTENCY_HMAC_KEY
            if not key:
                raise ValueError("IDEMPOTENCY_HMAC_KEY must not be empty outside local environment")
            if key in _IDEMPOTENCY_HMAC_KEY_PLACEHOLDERS:
                raise ValueError("IDEMPOTENCY_HMAC_KEY must be set to a real secret outside local environment")
            if len(key) < _IDEMPOTENCY_HMAC_KEY_MIN_LENGTH:
                raise ValueError(
                    f"IDEMPOTENCY_HMAC_KEY must be at least {_IDEMPOTENCY_HMAC_KEY_MIN_LENGTH} "
                    "characters outside local environment"
                )
        return self

    @model_validator(mode="after")
    def validate_idempotency_record_ttl_days(self) -> "Config":
        # 0 이하 값이 들어오면 레코드가 저장 즉시(또는 그 전에) 만료돼 재조회에서 항상 걸러지므로,
        # 멱등성 자체가 조용히 무력화됩니다. 환경 구분 없이 항상 막습니다.
        if self.IDEMPOTENCY_RECORD_TTL_DAYS <= 0:
            raise ValueError("IDEMPOTENCY_RECORD_TTL_DAYS must be a positive number of days")
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
