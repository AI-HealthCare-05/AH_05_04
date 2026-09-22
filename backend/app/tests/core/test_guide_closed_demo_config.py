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


def test_guide_closed_demo_config_allow_authenticated_users_valid() -> None:
    now = datetime.now(UTC)
    config = Config.model_validate(
        {
            **BASE_CONFIG,
            "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
            "GUIDE_CLOSED_DEMO_RAG_ALLOW_AUTHENTICATED_USERS": True,
            "GUIDE_CLOSED_DEMO_RAG_USER_IDS": "",
            "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
            "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
        }
    )
    assert config.GUIDE_CLOSED_DEMO_RAG_ENABLED is True
    assert config.GUIDE_CLOSED_DEMO_RAG_ALLOW_AUTHENTICATED_USERS is True
    assert len(config.GUIDE_CLOSED_DEMO_RAG_USER_IDS) == 0

    # Any authenticated user UUID is active during valid window
    arbitrary_user_id = uuid.uuid4()
    assert is_guide_closed_demo_active(config, arbitrary_user_id) is True
    another_user_id = uuid.uuid4()
    assert is_guide_closed_demo_active(config, another_user_id) is True


def test_is_guide_closed_demo_active_behavior_matrix() -> None:
    now = datetime.now(UTC)
    allowlisted_user = uuid.uuid4()
    non_allowlisted_user = uuid.uuid4()

    # Matrix Case 1: allow_authenticated=False + allowlisted UUID -> True, non-allowlisted -> False
    config_allowlist_only = Config.model_validate(
        {
            **BASE_CONFIG,
            "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
            "GUIDE_CLOSED_DEMO_RAG_ALLOW_AUTHENTICATED_USERS": False,
            "GUIDE_CLOSED_DEMO_RAG_USER_IDS": str(allowlisted_user),
            "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
            "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
        }
    )
    assert is_guide_closed_demo_active(config_allowlist_only, allowlisted_user) is True
    assert is_guide_closed_demo_active(config_allowlist_only, non_allowlisted_user) is False

    # Matrix Case 2: allow_authenticated=True + arbitrary authenticated user -> True
    config_all_authenticated = Config.model_validate(
        {
            **BASE_CONFIG,
            "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
            "GUIDE_CLOSED_DEMO_RAG_ALLOW_AUTHENTICATED_USERS": True,
            "GUIDE_CLOSED_DEMO_RAG_USER_IDS": str(allowlisted_user),
            "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
            "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
        }
    )
    assert is_guide_closed_demo_active(config_all_authenticated, allowlisted_user) is True
    assert is_guide_closed_demo_active(config_all_authenticated, non_allowlisted_user) is True

    # Matrix Case 3: expired window -> False even when allow_authenticated=True
    expired_time = now + timedelta(days=5)
    assert is_guide_closed_demo_active(config_all_authenticated, non_allowlisted_user, now=expired_time) is False

    # Matrix Case 4: feature disabled -> False even when allow_authenticated=True
    config_disabled = Config.model_validate(
        {
            **BASE_CONFIG,
            "GUIDE_CLOSED_DEMO_RAG_ENABLED": False,
            "GUIDE_CLOSED_DEMO_RAG_ALLOW_AUTHENTICATED_USERS": True,
            "GUIDE_CLOSED_DEMO_RAG_USER_IDS": str(allowlisted_user),
            "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
            "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
        }
    )
    assert is_guide_closed_demo_active(config_disabled, allowlisted_user) is False
    assert is_guide_closed_demo_active(config_disabled, non_allowlisted_user) is False


def test_guide_closed_demo_config_allow_authenticated_rejects_safety_violations() -> None:
    now = datetime.now(UTC)

    # 1. PUBLIC_TRACK_F_ENABLED=True -> invalid
    with pytest.raises(ValidationError, match="GUIDE_CLOSED_DEMO_RAG_ENABLED requires PUBLIC_TRACK_F_ENABLED=false"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
                "GUIDE_CLOSED_DEMO_RAG_ALLOW_AUTHENTICATED_USERS": True,
                "PUBLIC_TRACK_F_ENABLED": True,
                "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
                "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
            }
        )

    # 2. GUIDE_RUNTIME_ENABLED=True -> invalid
    with pytest.raises(ValidationError, match="GUIDE_CLOSED_DEMO_RAG_ENABLED requires GUIDE_RUNTIME_ENABLED=false"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
                "GUIDE_CLOSED_DEMO_RAG_ALLOW_AUTHENTICATED_USERS": True,
                "GUIDE_RUNTIME_ENABLED": True,
                "GUIDE_RUNTIME_BOOTSTRAP_FACTORY": "some.module:factory",
                "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
                "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=3)).isoformat(),
            }
        )

    # 3. Window > 7 days -> invalid
    with pytest.raises(ValidationError, match="Guide closed demo window must be positive and at most seven days"):
        Config.model_validate(
            {
                **BASE_CONFIG,
                "GUIDE_CLOSED_DEMO_RAG_ENABLED": True,
                "GUIDE_CLOSED_DEMO_RAG_ALLOW_AUTHENTICATED_USERS": True,
                "GUIDE_CLOSED_DEMO_RAG_STARTS_AT": now.isoformat(),
                "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT": (now + timedelta(days=8)).isoformat(),
            }
        )
