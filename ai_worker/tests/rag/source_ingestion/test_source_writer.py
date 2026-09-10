"""Writer의 전용 credential 및 입력 경계를 검증합니다."""

from pathlib import Path

import pytest
import yaml

from ai_worker.admin.source_writer import WriterConfig


def _environment():
    return {
        f"SOURCE_WRITER_{key}": value
        for key, value in {
            "HOST": "localhost",
            "PORT": "5432",
            "NAME": "synthetic",
            "USER": "synthetic_writer",
            "PASSWORD": "synthetic-test-only",
            "ACTOR": "synthetic-operator",
        }.items()
    }


@pytest.mark.parametrize("key", ["HOST", "PORT", "NAME", "USER", "PASSWORD", "ACTOR"])
def test_requires_each_dedicated_setting(key):
    env = _environment()
    del env[f"SOURCE_WRITER_{key}"]
    with pytest.raises(ValueError):
        WriterConfig.from_environment(env)


@pytest.mark.parametrize("key", ["DB_PASSWORD", "DB_APP_PASSWORD", "DB_MIGRATION_PASSWORD", "DB_ADMIN_PASSWORD"])
def test_rejects_mixed_credentials(key):
    with pytest.raises(ValueError):
        WriterConfig.from_environment({**_environment(), key: "synthetic-other-secret"})


def test_password_not_in_configuration_repr():
    config = WriterConfig.from_environment(_environment())
    assert "synthetic-test-only" not in repr(config)
    assert config.url.username == "synthetic_writer"


def test_compose_writer_is_opt_in_and_has_only_dedicated_environment():
    root = Path(__file__).resolve().parents[4]
    service = yaml.safe_load((root / "infra/docker/docker-compose.prod.yml").read_text())["services"]["source-writer"]
    assert service["profiles"] == ["source-admin"]
    assert all(key.startswith("SOURCE_WRITER_") for key in service["environment"])
    assert "env_file" not in service
    assert "ai_worker.admin.source_writer" in service["entrypoint"]
