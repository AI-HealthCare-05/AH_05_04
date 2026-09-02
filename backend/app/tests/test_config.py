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
