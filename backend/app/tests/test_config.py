import pytest
from pydantic import ValidationError

from app.core.config import Config, Env

BASE_CONFIG = {
    "DB_HOST": "localhost",
    "DB_PORT": 5432,
    "DB_USER": "test_user",
    "DB_PASSWORD": "test_password",
    "DB_NAME": "test_database",
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
