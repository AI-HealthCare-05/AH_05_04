from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.config import Config, is_guide_closed_demo_active
from app.tests.test_config import BASE_CONFIG


def test_guide_closed_demo_config_valid_window_and_allowlist() -> None:
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
            "GUIDE_CLOSED_DEMO_RAG_USER_IDS": str(user_id),
            "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
            "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
        }
    )
    assert config.GUIDE_CLOSED_DEMO_RAG_ENABLED is True
    assert user_id in config.GUIDE_CLOSED_DEMO_RAG_USER_IDS
    assert is_guide_closed_demo_active(config, user_id) is True
    assert is_guide_closed_demo_active(config, uuid.uuid4()) is False


def test_guide_closed_demo_config_rejects_empty_allowlist() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="Guide closed demo requires an explicit allowlist"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
                "GUIDE_CLOSED_DEMO_RAG_USER_IDS": "",
                "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
                "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
            }
        )


def test_guide_closed_demo_config_rejects_window_over_7_days() -> None:
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="Guide closed demo window must be positive and at most seven days"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
                "GUIDE_CLOSED_DEMO_RAG_USER_IDS": str(user_id),
                "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
                "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=8)).isoformat(),
            }
        )


def test_guide_closed_demo_config_rejects_public_track_f() -> None:
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="GUIDE_CLOSED_DEMO_RAG_ENABLED requires PUBLIC_TRACK_F_ENABLED=false"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
                "PUBLIC_TRACK_F_ENABLED": True,
                "GUIDE_CLOSED_DEMO_RAG_USER_IDS": str(user_id),
                "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
                "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
            }
        )


def test_guide_closed_demo_config_rejects_guide_runtime_enabled() -> None:
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="GUIDE_CLOSED_DEMO_RAG_ENABLED requires GUIDE_RUNTIME_ENABLED=false"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
                "GUIDE_RUNTIME_ENABLED": True,
                "GUIDE_RUNTIME_BOOTSTRAP_FACTORY": "some.module:factory",
                "GUIDE_CLOSED_DEMO_RAG_USER_IDS": str(user_id),
                "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
                "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
            }
        )


def test_is_guide_closed_demo_active_checks_timing_window() -> None:
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
            "GUIDE_CLOSED_DEMO_RAG_USER_IDS": str(user_id),
            "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": (now + timedelta(hours=1)).isoformat(),
            "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(hours=5)).isoformat(),
        }
    )
    # Before window starts
    assert is_guide_closed_demo_active(config, user_id, now=now) is False
    # During window
    assert is_guide_closed_demo_active(config, user_id, now=now + timedelta(hours=2)) is True
    # After window expires
    assert is_guide_closed_demo_active(config, user_id, now=now + timedelta(hours=6)) is False
