import zoneinfo
from datetime import datetime, timedelta, timezone

import pytest

from ai_worker.core.config import Config

# ZoneInfo.utcoffset()은 offset 조회 기준 datetime이 필요합니다(None으로는 조회 불가).
# 참조 시각을 고정하지 않으면 tzdata 유무에 따라 fallback timezone(고정 offset, None 허용)과
# 실제 ZoneInfo(datetime 필요)가 다르게 동작해 CI(tzdata 있음)와 로컬(Windows, tzdata 없음)의
# 결과가 갈립니다.
_REFERENCE_INSTANT = datetime(2026, 1, 1)


def test_config_loads_with_default_timezone_fallback() -> None:
    """환경변수 없이도 tzdata 유무와 무관하게 Config가 생성됩니다."""
    config = Config()

    assert config.TIMEZONE.utcoffset(_REFERENCE_INSTANT) == timedelta(hours=9)


def test_config_accepts_timezone_env_var_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """TIMEZONE 환경변수 문자열이 tzinfo로 변환되어 검증을 통과합니다."""
    monkeypatch.setenv("TIMEZONE", "Asia/Seoul")

    config = Config()

    assert config.TIMEZONE.utcoffset(_REFERENCE_INSTANT) == timedelta(hours=9)


def test_config_accepts_utc_timezone_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """UTC 문자열도 대소문자와 무관하게 정상 변환됩니다."""
    monkeypatch.setenv("TIMEZONE", "UTC")

    config = Config()

    assert config.TIMEZONE.utcoffset(None) == timedelta(0)


def test_config_accepts_already_constructed_timezone_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """문자열이 아닌 tzinfo 인스턴스가 들어와도 그대로 통과합니다."""
    fixed_offset = timezone(timedelta(hours=9), name="Asia/Seoul")
    config = Config(TIMEZONE=fixed_offset)

    assert config.TIMEZONE is fixed_offset


def test_config_rejects_unknown_timezone_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """알려지지 않은 시간대 이름은 여전히 에러로 처리됩니다(Asia/Seoul만 예외 fallback)."""
    monkeypatch.setenv("TIMEZONE", "Not/A_Real_Zone")

    with pytest.raises(zoneinfo.ZoneInfoNotFoundError):
        Config()
