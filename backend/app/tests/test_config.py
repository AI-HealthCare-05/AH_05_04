import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Config, Env
from provider_contracts.observability import DeploymentEnvironment

BASE_CONFIG = {
    "DB_HOST": "localhost",
    "DB_PORT": 5432,
    "DB_USER": "test_user",
    "DB_PASSWORD": "test_password",
    "DB_NAME": "test_database",
    "CHAT_HISTORY_CONTEXT_ENABLED": False,
    "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED": False,
}

# IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY용 placeholder가 아닌 실제 형식의(Fernet 32byte
# urlsafe-base64) 테스트 값입니다. config.py의 placeholder 기본값과만 달라야 하며,
# 프로덕션에서 실제로 쓰이지 않습니다.
REAL_SNAPSHOT_ENCRYPTION_KEY = "mNZgOOlYI_KL5_6HjgyDFGPkMW7xU7CBpPYY5awEaRg="


def test_backend_env_is_shared_deployment_environment() -> None:
    assert Env is DeploymentEnvironment


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
            # production은 IDEMPOTENCY_HMAC_KEY placeholder·길이 검증을 거부하므로, 이 테스트가
            # 검증하는 ENV 파싱과 무관한 실패를 피하려면 32자 이상의 실제 값을 넣어야 합니다.
            "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
            # 같은 이유로 IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY도 placeholder가 아닌 값을 넣습니다.
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
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


def test_guide_query_hmac_config_uses_the_frozen_initial_version_and_secret_carrier() -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "GUIDE_QUERY_HMAC_KEY": "synthetic-guide-query-hmac-key",
        }
    )

    assert config.GUIDE_QUERY_HMAC_KEY_VERSION == "guide-query-hmac-key@1"
    assert config.GUIDE_QUERY_HMAC_KEY is not None
    assert config.GUIDE_QUERY_HMAC_KEY.get_secret_value() == "synthetic-guide-query-hmac-key"
    assert "synthetic-guide-query-hmac-key" not in repr(config.GUIDE_QUERY_HMAC_KEY)


def test_guide_runtime_is_disabled_by_default() -> None:
    config = Config.model_validate(BASE_CONFIG)

    assert config.GUIDE_RUNTIME_ENABLED is False
    assert config.GUIDE_RUNTIME_BOOTSTRAP_FACTORY == ""


@pytest.mark.parametrize("factory_path", ("", "missing-separator", "module:", ":factory"))
def test_enabled_guide_runtime_requires_a_bootstrap_factory(factory_path: str) -> None:
    with pytest.raises(ValidationError, match="GUIDE_RUNTIME_BOOTSTRAP_FACTORY"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "GUIDE_RUNTIME_ENABLED": True,
                "GUIDE_RUNTIME_BOOTSTRAP_FACTORY": factory_path,
            }
        )


def test_closed_demo_chat_query_hmac_config_uses_an_independent_namespace_and_secret_carrier() -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "CHAT_CLOSED_DEMO_QUERY_HMAC_KEY": "synthetic-closed-demo-chat-query-hmac-key",
        }
    )

    assert config.CHAT_CLOSED_DEMO_QUERY_HMAC_KEY_VERSION == "closed-demo-chat-query-hmac-key@1"
    assert config.CHAT_CLOSED_DEMO_QUERY_HMAC_KEY is not None
    assert config.CHAT_CLOSED_DEMO_QUERY_HMAC_KEY.get_secret_value() == "synthetic-closed-demo-chat-query-hmac-key"
    assert "synthetic-closed-demo-chat-query-hmac-key" not in repr(config.CHAT_CLOSED_DEMO_QUERY_HMAC_KEY)


@pytest.mark.parametrize("environment", (Env.STAGING, Env.PRODUCTION))
def test_closed_demo_rag_config_rejects_nonlocal_environment(environment: Env) -> None:
    with pytest.raises(ValidationError, match="CHAT_CLOSED_DEMO_RAG_ENABLED is allowed only in local environment"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "CHAT_CLOSED_DEMO_RAG_ENABLED": True,
                "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
            }
        )


def test_closed_demo_rag_config_rejects_a_public_track_instance() -> None:
    with pytest.raises(ValidationError, match="CHAT_CLOSED_DEMO_RAG_ENABLED requires PUBLIC_TRACK_F_ENABLED=false"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "CHAT_CLOSED_DEMO_RAG_ENABLED": True,
                "PUBLIC_TRACK_F_ENABLED": True,
            }
        )


@pytest.mark.parametrize("version", ("", "guide-query-hmac-key@0", "guide-query-hmac-key@01", "query-hmac@1"))
def test_guide_query_hmac_config_rejects_noncanonical_key_versions(version: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "GUIDE_QUERY_HMAC_KEY": "synthetic-guide-query-hmac-key",
                "GUIDE_QUERY_HMAC_KEY_VERSION": version,
            }
        )


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


def test_account_withdrawal_enabled_requires_cleanup_credentials_outside_local() -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": "production",
            "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
            "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED": True,
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE": "account_cleanup",
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD": "cleanup-password",
        }
    )

    assert config.ACCOUNT_WITHDRAWAL_REQUEST_ENABLED is True
    assert config.ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE == "account_cleanup"


@pytest.mark.parametrize(
    "cleanup_credentials",
    [
        {
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE": "account_cleanup",
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD": "",
        },
        {
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE": "",
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD": "",
        },
    ],
)
def test_account_withdrawal_enabled_rejects_missing_cleanup_credentials_outside_local(
    cleanup_credentials: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": "production",
                "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
                "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED": True,
                **cleanup_credentials,
            }
        )


def test_chat_history_context_is_disabled_by_default() -> None:
    assert Config.model_fields["CHAT_HISTORY_CONTEXT_ENABLED"].default is False


def test_guide_and_chat_use_gpt_4o_by_default() -> None:
    assert Config.model_fields["OPENAI_MODEL"].default == "gpt-4o"


def test_guide_and_chat_accept_only_gpt_4o() -> None:
    config = Config.model_validate({**BASE_CONFIG, "OPENAI_MODEL": " gpt-4o "})

    assert config.OPENAI_MODEL == "gpt-4o"


@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-4", "configured-model", ""])
def test_guide_and_chat_reject_other_openai_models(model: str) -> None:
    with pytest.raises(ValidationError, match="OPENAI_MODEL must be gpt-4o"):
        Config.model_validate({**BASE_CONFIG, "OPENAI_MODEL": model})


def test_ocr_structure_model_remains_gpt_4o_mini_by_default() -> None:
    assert Config.model_fields["OCR_STRUCTURE_MODEL"].default == "gpt-4o-mini"


@pytest.mark.parametrize(
    ("env", "enabled", "should_succeed"),
    [
        ("local", False, True),
        ("local", True, True),
        ("staging", False, True),
        ("staging", True, False),
        ("production", False, True),
        ("production", True, True),
    ],
)
def test_chat_history_context_environment_matrix(env: str, enabled: bool, should_succeed: bool) -> None:
    config_dict = {
        **BASE_CONFIG,
        "ENV": env,
        "CHAT_HISTORY_CONTEXT_ENABLED": enabled,
    }
    if env != "local":
        config_dict["IDEMPOTENCY_HMAC_KEY"] = "a-real-idempotency-hmac-secret-value"
        config_dict["IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY"] = REAL_SNAPSHOT_ENCRYPTION_KEY

    if should_succeed:
        config = Config.model_validate(config_dict)
        assert config.CHAT_HISTORY_CONTEXT_ENABLED is enabled
    else:
        with pytest.raises(ValidationError):
            Config.model_validate(config_dict)


def test_chat_history_context_rejects_unsupported_environment() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": "unsupported",
                "CHAT_HISTORY_CONTEXT_ENABLED": True,
            }
        )


def test_chat_history_context_production_regression_with_required_config() -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": "production",
            "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
            "CHAT_HISTORY_CONTEXT_ENABLED": True,
            "OPENAI_MODEL": "gpt-4o",
        }
    )
    assert config.CHAT_HISTORY_CONTEXT_ENABLED is True
    assert config.OPENAI_MODEL == "gpt-4o"


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
def test_idempotency_hmac_key_rejects_example_local_env_placeholder_outside_local(environment: str) -> None:
    """envs/example.local.env에 공개된 예시 값도 실수로 non-local 환경에 복사될 수 있으니 거부합니다."""
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "replace-with-random-local-idempotency-hmac-key-at-least-32-characters",
            }
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_key_rejects_too_short_value_outside_local(environment: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "a-real-but-short-secret",
            }
        )


@pytest.mark.parametrize("ttl_days", [0, -1])
def test_idempotency_record_ttl_days_rejects_non_positive_value(ttl_days: int) -> None:
    """0 이하 값은 레코드를 저장 즉시(또는 그 전에) 만료시켜 멱등성을 조용히 무력화하므로,
    환경 구분 없이 항상 거부합니다."""
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "IDEMPOTENCY_RECORD_TTL_DAYS": ttl_days,
            }
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_key_field_is_normalized_to_stripped_value(environment: str) -> None:
    """검증(validator)과 실제 HMAC 계산이 항상 같은 값을 보도록, 필드 자체가 앞뒤 공백을
    제거한 값으로 정규화되는지 확인합니다 — 공백만 다른 값이 인스턴스마다 주입되면
    검증은 통과해도 계산된 digest가 달라질 수 있습니다."""
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": environment,
            "IDEMPOTENCY_HMAC_KEY": "  a-real-idempotency-hmac-secret-value  ",
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
        }
    )

    assert config.IDEMPOTENCY_HMAC_KEY == "a-real-idempotency-hmac-secret-value"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_key_accepts_configured_value_outside_local(environment: str) -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": environment,
            "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
        }
    )

    assert config.IDEMPOTENCY_HMAC_KEY == "a-real-idempotency-hmac-secret-value"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_retired_keys_accept_configured_previous_key(environment: str) -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": environment,
            "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
            "IDEMPOTENCY_HMAC_KEY_VERSION": "v2",
            "IDEMPOTENCY_HMAC_RETIRED_KEYS": {" v1 ": "  a-previous-idempotency-hmac-secret-value  "},
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
        }
    )

    assert config.IDEMPOTENCY_HMAC_RETIRED_KEYS == {"v1": "a-previous-idempotency-hmac-secret-value"}


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_retired_keys_reject_trimmed_version_collision(environment: str) -> None:
    with pytest.raises(ValidationError) as error:
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
                "IDEMPOTENCY_HMAC_KEY_VERSION": "v3",
                "IDEMPOTENCY_HMAC_RETIRED_KEYS": {
                    "v1": "a-previous-idempotency-hmac-secret-value-a",
                    " v1 ": "a-previous-idempotency-hmac-secret-value-b",
                },
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
            }
        )

    message = str(error.value)
    assert "duplicate versions after trimming" in message
    assert "a-previous-idempotency-hmac-secret-value" not in message


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_retired_keys_reject_active_version_collision(environment: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
                "IDEMPOTENCY_HMAC_KEY_VERSION": "v2",
                "IDEMPOTENCY_HMAC_RETIRED_KEYS": {"v2": "a-previous-idempotency-hmac-secret-value"},
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
            }
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_retired_keys_reject_active_key_collision(environment: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
                "IDEMPOTENCY_HMAC_KEY_VERSION": "v2",
                "IDEMPOTENCY_HMAC_RETIRED_KEYS": {"v1": "a-real-idempotency-hmac-secret-value"},
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
            }
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_hmac_retired_keys_reject_placeholder_outside_local(environment: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
                "IDEMPOTENCY_HMAC_KEY_VERSION": "v2",
                "IDEMPOTENCY_HMAC_RETIRED_KEYS": {"v1": "not-configured-idempotency-hmac-key"},
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
            }
        )


def test_idempotency_snapshot_encryption_key_default_is_a_fixed_placeholder() -> None:
    """IDEMPOTENCY_HMAC_KEY와 같은 이유로, 기본값이 프로세스마다 달라지면 서버 재시작
    사이에 저장된 snapshot을 복호화하지 못합니다. 고정 literal인지 model_fields로 확인합니다."""
    assert (
        Config.model_fields["IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY"].default
        == "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
    )


def test_idempotency_snapshot_encryption_key_default_is_a_valid_fernet_key() -> None:
    """local 환경은 이 값을 그대로 쓰므로, placeholder라도 Fernet이 실제로 로드할 수 있는
    형식이어야 기동이 됩니다."""
    config = Config.model_validate(BASE_CONFIG)

    Fernet(config.IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY.encode("utf-8"))


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_snapshot_encryption_key_rejects_placeholder_outside_local(environment: str) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "ENV": environment,
                "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
                # 실행 환경의 실제 값이 이 테스트를 거짓으로 통과시키지 않도록 명시적으로
                # placeholder를 지정합니다(IDEMPOTENCY_HMAC_KEY 테스트와 동일한 이유).
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            }
        )


def test_idempotency_snapshot_encryption_key_rejects_invalid_fernet_format() -> None:
    """환경과 무관하게, Fernet이 로드할 수 없는 값은 첫 암호화 호출까지 미루지 않고
    기동 시점에 바로 드러나야 합니다."""
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": "not-a-valid-fernet-key",
            }
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idempotency_snapshot_encryption_key_accepts_configured_value_outside_local(environment: str) -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "ENV": environment,
            "IDEMPOTENCY_HMAC_KEY": "a-real-idempotency-hmac-secret-value",
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY": REAL_SNAPSHOT_ENCRYPTION_KEY,
        }
    )

    assert config.IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY == REAL_SNAPSHOT_ENCRYPTION_KEY


# PR #346 리뷰: key/version 교체 뒤에도 TTL이 남은 snapshot을 복호화하기 위한 retired key ring.
RETIRED_SNAPSHOT_ENCRYPTION_KEY = "xarzX9iazRmjjafi3G4NAVluYNu0n0YYgT3INatokNU="


def test_idempotency_snapshot_encryption_retired_keys_default_is_empty() -> None:
    config = Config.model_validate(BASE_CONFIG)

    assert config.IDEMPOTENCY_SNAPSHOT_ENCRYPTION_RETIRED_KEYS == {}


def test_idempotency_snapshot_encryption_retired_keys_accepts_valid_fernet_keys() -> None:
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY_VERSION": "v2",
            "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_RETIRED_KEYS": {"v1": RETIRED_SNAPSHOT_ENCRYPTION_KEY},
        }
    )

    assert config.IDEMPOTENCY_SNAPSHOT_ENCRYPTION_RETIRED_KEYS == {"v1": RETIRED_SNAPSHOT_ENCRYPTION_KEY}


def test_idempotency_snapshot_encryption_retired_keys_rejects_invalid_fernet_format() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY_VERSION": "v2",
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_RETIRED_KEYS": {"v1": "not-a-valid-fernet-key"},
            }
        )


def test_idempotency_snapshot_encryption_retired_keys_rejects_active_version_reused() -> None:
    """같은 version 문자열이 active key와 retired key 양쪽에 배포되는 모호한 구성은
    운영 절차가 아니라 기동 시점에 바로 막습니다."""
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY_VERSION": "v1",
                "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_RETIRED_KEYS": {"v1": RETIRED_SNAPSHOT_ENCRYPTION_KEY},
            }
        )
