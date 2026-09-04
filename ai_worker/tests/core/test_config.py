import zoneinfo
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from ai_worker.core.config import Config
from provider_contracts.observability import DeploymentEnvironment

# ZoneInfo.utcoffset()은 offset 조회 기준 datetime이 필요합니다(None으로는 조회 불가).
# 참조 시각을 고정하지 않으면 tzdata 유무에 따라 fallback timezone(고정 offset, None 허용)과
# 실제 ZoneInfo(datetime 필요)가 다르게 동작해 CI(tzdata 있음)와 로컬(Windows, tzdata 없음)의
# 결과가 갈립니다.
_REFERENCE_INSTANT = datetime(2026, 1, 1)


# Worker runtime은 Job 실행 DB에 접속해야 하므로 DB_* 4개가 필수입니다(#233).
# 기본값을 주면 오설정된 배포가 조용히 localhost로 붙으므로 필수로 두고, 테스트는
# 합성 값을 명시합니다.
_REQUIRED_SETTINGS: dict[str, Any] = {
    "ENV": DeploymentEnvironment.LOCAL,
    "DB_HOST": "127.0.0.1",
    "DB_NAME": "test",
    "DB_USER": "worker",
    "DB_PASSWORD": "worker-password",
    "CLOVA_OCR_INVOKE_URL": "https://clova.test/ocr",
    "CLOVA_OCR_SECRET": "synthetic-clova-secret",
    "STORAGE_DIR": "/tmp/medical-documents",
}


def _config(**overrides: Any) -> Config:
    return Config(  # type: ignore[call-arg]
        _env_file=None,
        **{**_REQUIRED_SETTINGS, **overrides},
    )


def _raise_zoneinfo_not_found(key: str) -> zoneinfo.ZoneInfo:
    raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {key}")


def test_config_loads_with_default_timezone() -> None:
    """환경변수 없이도 tzdata 유무와 무관하게 Config가 생성됩니다(현재 실행 환경의 정상 경로)."""
    config = _config()

    assert config.TIMEZONE.utcoffset(_REFERENCE_INSTANT) == timedelta(hours=9)


def test_config_accepts_timezone_env_var_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """TIMEZONE 환경변수 문자열이 tzinfo로 변환되어 검증을 통과합니다(현재 실행 환경의 정상 경로)."""
    monkeypatch.setenv("TIMEZONE", "Asia/Seoul")

    config = _config()

    assert config.TIMEZONE.utcoffset(_REFERENCE_INSTANT) == timedelta(hours=9)


def test_config_default_factory_falls_back_when_zoneinfo_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """tzdata가 없어 ZoneInfo가 실패하는 환경(Windows 로컬 등)을 직접 재현해 기본값 fallback을 확인합니다."""
    monkeypatch.setattr(zoneinfo, "ZoneInfo", _raise_zoneinfo_not_found)

    config = _config()

    assert config.TIMEZONE == timezone(timedelta(hours=9), name="Asia/Seoul")


def test_config_env_var_falls_back_when_zoneinfo_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """TIMEZONE=Asia/Seoul 환경변수 입력도 ZoneInfo 부재 시 같은 고정 UTC+9로 fallback합니다."""
    monkeypatch.setattr(zoneinfo, "ZoneInfo", _raise_zoneinfo_not_found)
    monkeypatch.setenv("TIMEZONE", "Asia/Seoul")

    config = _config()

    assert config.TIMEZONE == timezone(timedelta(hours=9), name="Asia/Seoul")


def test_config_accepts_utc_timezone_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """UTC 문자열도 대소문자와 무관하게 정상 변환됩니다."""
    monkeypatch.setenv("TIMEZONE", "UTC")

    config = _config()

    assert config.TIMEZONE.utcoffset(None) == timedelta(0)


def test_config_accepts_already_constructed_timezone_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """문자열이 아닌 tzinfo 인스턴스가 들어와도 그대로 통과합니다."""
    fixed_offset = timezone(timedelta(hours=9), name="Asia/Seoul")
    config = _config(TIMEZONE=fixed_offset)

    assert config.TIMEZONE is fixed_offset


def test_config_rejects_unknown_timezone_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """알려지지 않은 시간대 이름은 여전히 에러로 처리됩니다(Asia/Seoul만 예외 fallback)."""
    monkeypatch.setenv("TIMEZONE", "Not/A_Real_Zone")

    with pytest.raises(zoneinfo.ZoneInfoNotFoundError):
        _config()


def test_config_has_approved_redis_defaults() -> None:
    config = _config()

    assert config.REDIS_HOST == "redis"
    assert config.REDIS_PORT == 6379
    assert config.REDIS_STREAM_NAME == "oryak:jobs"
    assert config.REDIS_DLQ_STREAM_NAME == "oryak:jobs:dead-letter"
    assert config.REDIS_CONSUMER_GROUP == "ai-workers"
    assert config.REDIS_CONSUMER_NAME == "ai-worker-local"
    assert config.RECONCILER_CONSUMER_NAME == "ai-worker-reconciler"
    assert config.REDIS_BLOCK_MS == 5000
    assert config.REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS == 5.0
    assert config.REDIS_SOCKET_TIMEOUT_SECONDS == 10.0

    assert config.RECONCILER_MIN_IDLE_MS == 30_000
    assert config.RECONCILER_BATCH_SIZE == 100
    assert config.RECONCILER_INTERVAL_SECONDS == 5.0

    assert config.DLQ_OUTBOX_CLAIM_TTL_SECONDS == 30.0
    assert config.DLQ_PUBLISHER_INTERVAL_SECONDS == 1.0


def test_config_accepts_redis_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_CONSUMER_NAME", "worker-test-1")

    config = _config()

    assert config.REDIS_HOST == "localhost"
    assert config.REDIS_PORT == 6380
    assert config.REDIS_CONSUMER_NAME == "worker-test-1"


def test_config_rejects_blank_redis_group() -> None:
    with pytest.raises(ValidationError):
        _config(REDIS_CONSUMER_GROUP="   ")


@pytest.mark.parametrize("port", [0, 65536])
def test_config_rejects_invalid_redis_port(port: int) -> None:
    with pytest.raises(ValidationError):
        _config(REDIS_PORT=port)


@pytest.mark.parametrize(
    "socket_timeout_seconds",
    [5.0, 4.9],
)
def test_config_rejects_socket_timeout_not_longer_than_blocking_read(
    socket_timeout_seconds: float,
) -> None:
    with pytest.raises(
        ValidationError,
        match="REDIS_SOCKET_TIMEOUT_SECONDS",
    ):
        _config(
            REDIS_BLOCK_MS=5000,
            REDIS_SOCKET_TIMEOUT_SECONDS=socket_timeout_seconds,
        )


def test_config_rejects_blank_dlq_stream_name() -> None:
    with pytest.raises(ValidationError):
        _config(REDIS_DLQ_STREAM_NAME="   ")


def test_config_rejects_blank_reconciler_consumer_name() -> None:
    with pytest.raises(ValidationError):
        _config(RECONCILER_CONSUMER_NAME="   ")


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("RECONCILER_MIN_IDLE_MS", -1),
        ("RECONCILER_BATCH_SIZE", 0),
        ("RECONCILER_INTERVAL_SECONDS", 0),
        ("DLQ_OUTBOX_CLAIM_TTL_SECONDS", 0),
        ("DLQ_PUBLISHER_INTERVAL_SECONDS", 0),
    ],
)
def test_config_rejects_invalid_recovery_setting(
    field_name: str,
    invalid_value: int,
) -> None:
    with pytest.raises(ValidationError):
        _config(**{field_name: invalid_value})


@pytest.mark.parametrize(
    ("configured_value", "expected"),
    [
        ("local", DeploymentEnvironment.LOCAL),
        ("staging", DeploymentEnvironment.STAGING),
        ("production", DeploymentEnvironment.PRODUCTION),
    ],
)
def test_config_parses_required_environment(
    configured_value: str,
    expected: DeploymentEnvironment,
) -> None:
    config = Config.model_validate(
        {
            **_REQUIRED_SETTINGS,
            "ENV": configured_value,
        }
    )

    assert config.ENV is expected


def test_config_rejects_missing_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENV", raising=False)

    with pytest.raises(ValidationError):
        Config(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("configured_value", ["test", "dev", "prod"])
def test_config_rejects_unknown_environment(
    configured_value: str,
) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                **_REQUIRED_SETTINGS,
                "ENV": configured_value,
            }
        )


def test_config_has_approved_ocr_budget_defaults() -> None:
    config = _config()

    assert config.OCR_REQUEST_DEADLINE_SECONDS == 60.0
    assert config.OCR_RESPONSE_MARGIN_SECONDS == 5.0
    assert config.OCR_PROVIDER_BUDGET_SECONDS == 55.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"OCR_REQUEST_DEADLINE_SECONDS": 0.0},
        {"OCR_RESPONSE_MARGIN_SECONDS": -1.0},
        {
            "OCR_REQUEST_DEADLINE_SECONDS": 60.0,
            "OCR_RESPONSE_MARGIN_SECONDS": 60.0,
        },
        {
            "OCR_REQUEST_DEADLINE_SECONDS": 60.0,
            "OCR_RESPONSE_MARGIN_SECONDS": 61.0,
        },
    ],
)
def test_config_rejects_invalid_ocr_budget(
    overrides: dict[str, float],
) -> None:
    with pytest.raises(ValidationError):
        _config(**overrides)


def test_config_exposes_clova_secret_only_explicitly() -> None:
    config = _config()

    assert config.CLOVA_OCR_SECRET.get_secret_value() == "synthetic-clova-secret"
    assert "synthetic-clova-secret" not in repr(config)
    assert "synthetic-clova-secret" not in str(config)


@pytest.mark.parametrize(
    "invoke_url",
    [
        "",
        "http://clova.test/ocr",
        "clova.test/ocr",
    ],
)
def test_config_rejects_non_https_clova_url(invoke_url: str) -> None:
    with pytest.raises(ValidationError):
        _config(CLOVA_OCR_INVOKE_URL=invoke_url)


def test_config_rejects_blank_clova_secret() -> None:
    with pytest.raises(ValidationError):
        _config(CLOVA_OCR_SECRET="   ")


def test_config_rejects_blank_storage_dir() -> None:
    with pytest.raises(ValidationError):
        _config(STORAGE_DIR="   ")
