import pytest
from pydantic import ValidationError

from app.core.config import Config, Env

BASE_CONFIG = {
    "DB_HOST": "localhost",
    "DB_PORT": 5432,
    "DB_USER": "test_user",
    "DB_PASSWORD": "test_password",
    "DB_NAME": "test_database",
    "CHAT_HISTORY_CONTEXT_ENABLED": False,
}


def test_config_builds_postgresql_async_url() -> None:
    config = Config.model_validate(BASE_CONFIG)

    # MySQL 드라이버가 다시 들어오는 회귀를 방지합니다.
    assert config.database_url.startswith("postgresql+asyncpg://")
    assert "mysql+asyncmy" not in config.database_url
    assert "charset=utf8mb4" not in config.database_url


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("local", Env.LOCAL),
        ("staging", Env.STAGING),
        ("production", Env.PRODUCTION),
    ],
)
def test_config_parses_environment(
    env_value: str,
    expected: Env,
) -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": env_value,
            # production은 IDEMPOTENCY_HMAC_KEY placeholder 기동을 거부하므로, 이 테스트가 검증하는
            # ENV 파싱과 무관한 실패를 피하려면 실제 값을 넣어야 합니다.
            "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret",
        }
    )

    assert config.ENV is expected


@pytest.mark.parametrize("env_value", ["dev", "prod"])
def test_config_rejects_legacy_environment(
    env_value: str,
) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": env_value,
            }
        )


def test_ocr_structure_llm_is_disabled_by_default() -> None:
    # 실제 .env나 환경변수의 영향을 받지 않고
    # Config에 선언된 Production 안전 기본값을 직접 검증합니다.
    field_info = Config.model_fields["OCR_STRUCTURE_LLM_ENABLED"]

    assert field_info.default is False


@pytest.mark.parametrize(
    ("configured_value", "expected"),
    [
        ("true", True),
        ("false", False),
    ],
)
def test_config_parses_ocr_structure_llm_enabled(
    configured_value: str,
    expected: bool,
) -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "OCR_STRUCTURE_LLM_ENABLED": configured_value,
        }
    )

    assert config.OCR_STRUCTURE_LLM_ENABLED is expected


def test_chat_history_context_is_disabled_by_default() -> None:
    assert Config.model_fields["CHAT_HISTORY_CONTEXT_ENABLED"].default is False


def test_chat_history_context_can_be_enabled_in_local_environment() -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": "local",
            "CHAT_HISTORY_CONTEXT_ENABLED": True,
        }
    )

    assert config.CHAT_HISTORY_CONTEXT_ENABLED is True


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_chat_history_context_cannot_be_enabled_outside_local(environment: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "CHAT_HISTORY_CONTEXT_ENABLED": True,
            }
        )


def test_release_validation_is_disabled_by_default() -> None:
    assert Config.model_fields["RELEASE_VALIDATION_ALLOWED"].default is False


def test_release_validation_can_be_enabled_in_local_environment() -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": "local",
            "RELEASE_VALIDATION_ALLOWED": True,
        }
    )

    assert config.RELEASE_VALIDATION_ALLOWED is True


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_release_validation_cannot_be_enabled_outside_local(environment: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "RELEASE_VALIDATION_ALLOWED": True,
            }
        )


def test_config_rejects_ocr_timeout_budget_exceeding_deadline() -> None:
    """개별 Provider 상한과 로컬 예약의 합이 전체 deadline을 넘으면 기동을 거부합니다."""
    with pytest.raises(ValidationError) as error:
        Config.model_validate(
            {
                **BASE_CONFIG,
                # 20 + 30 + 5 + 3 = 58 > 30
                "OCR_STRUCTURE_LLM_ENABLED": True,
                "OCR_REQUEST_DEADLINE_SECONDS": 30.0,
            }
        )

    assert "OCR_REQUEST_DEADLINE_SECONDS" in str(error.value)


def test_config_allows_llm_structuring_within_default_deadline() -> None:
    """기본값에서 LLM 구조화를 켜도 기동합니다.

    D=55였다면 20 + 30 + 5 + 3 = 58 > 55로 기존 배포가 기동 거부됩니다.
    D 기본값을 60으로 정한 이유가 이 조합입니다.
    """
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "OCR_STRUCTURE_LLM_ENABLED": True,
        }
    )

    assert config.OCR_REQUEST_DEADLINE_SECONDS == 60.0


def test_config_rejects_non_positive_ocr_deadline() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "OCR_REQUEST_DEADLINE_SECONDS": 0.0,
            }
        )


def test_idempotency_hmac_key_default_is_a_fixed_placeholder() -> None:
    """서버 재시작·여러 인스턴스에서 기본값이 매번 달라지면 같은 Idempotency-Key가 서로 다른
    key_hmac으로 계산되어 기존 레코드를 찾지 못하고 중복 Job·Outbox가 생깁니다. `uuid.uuid4()`
    같은 프로세스별 난수 기본값은 같은 프로세스 안에서 두 인스턴스를 비교해서는 잡히지 않으므로
    (클래스 정의 시점에 한 번만 평가되어 프로세스 내에서는 항상 동일), 필드 기본값 자체가 고정
    literal인지 `model_fields`로 직접 확인합니다(실제 환경변수 값에 영향받지 않는 유일한 방법)."""
    assert Config.model_fields["IDEMPOTENCY_HMAC_KEY"].default == "not-configured-idempotency-hmac-key"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_key_rejects_placeholder_outside_local(environment: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                # 실행 환경의 실제 IDEMPOTENCY_HMAC_KEY 환경변수가 이 값을 덮어쓰지 않도록 명시적으로
                # placeholder를 지정합니다 — pydantic-settings는 dict에 없는 키만 env var로 채우므로,
                # 키를 생략하면 로컬 .env에 실제 값이 설정된 환경에서 이 테스트가 거짓으로 통과합니다.
                "IDEMPOTENCY_HMAC_KEY": "not-configured-idempotency-hmac-key",
            }
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_key_rejects_blank_outside_local(environment: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "   ",
            }
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_key_rejects_whitespace_padded_placeholder_outside_local(environment: str) -> None:
    """앞뒤 공백으로 감싼 placeholder가 문자열 완전 일치 검사를 우회하지 못하는지 확인합니다."""
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "  not-configured-idempotency-hmac-key  ",
            }
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_key_rejects_example_prod_env_placeholder_outside_local(environment: str) -> None:
    """envs/example.prod.env에 저장소 공개로 노출된 예시 값도 실제 비밀값이 아니므로 거부합니다."""
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "replace-with-random-production-idempotency-hmac-key-at-least-32-characters",
            }
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_key_accepts_configured_value_outside_local(environment: str) -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": environment,
            "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret",
        }
    )

    assert config.IDEMPOTENCY_HMAC_KEY == "a-real-idempotency-hmac-secret"
