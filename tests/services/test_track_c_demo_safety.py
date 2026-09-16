"""Synthetic routing and activation boundaries; these are not clinical evaluations."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Config, Env
from app.core.errors import ApiError
from app.services import track_c_demo_safety as demo
from app.services.track_c_flow import ContractFoundationSafetyPolicy

USER_ID = UUID("00000000-0000-4000-8000-000000000193")
NOW = datetime(2026, 9, 16, 3, tzinfo=UTC)
EMERGENCY_CODES = (
    "SUDDEN_AIRWAY_SWELLING",
    "SEVERE_BREATHING_DIFFICULTY",
    "THROAT_TIGHTNESS_OR_SWALLOWING_DIFFICULTY",
    "BLUE_GREY_OR_PALE_SKIN_LIPS_TONGUE",
    "SUDDEN_CONFUSION_DROWSINESS_OR_DIZZINESS",
    "SUDDEN_PERSISTENT_CHEST_PAIN",
    "CHEST_PAIN_SPREADING",
    "CHEST_PAIN_WITH_ASSOCIATED_SYMPTOMS",
    "CURRENT_OR_RECENT_FACE_WEAKNESS",
    "CURRENT_OR_RECENT_ARM_WEAKNESS",
    "CURRENT_OR_RECENT_SPEECH_CHANGE",
)


def settings(**overrides: object) -> Config:
    return Config.model_validate(
        {
            "DB_HOST": "localhost",
            "DB_USER": "synthetic",
            "DB_PASSWORD": "synthetic",
            "DB_NAME": "test",
            "ENV": Env.LOCAL,
            "TRACK_C_SAFETY_DEMO_ENABLED": True,
            "TRACK_C_SAFETY_DEMO_STARTS_AT": NOW,
            "TRACK_C_SAFETY_DEMO_EXPIRES_AT": NOW + timedelta(days=7),
            "TRACK_C_SAFETY_DEMO_USER_IDS": [USER_ID],
            **overrides,
        }
    )


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(demo, "datetime", Clock)


@pytest.mark.parametrize("code", EMERGENCY_CODES)
def test_each_selected_emergency_signal_routes_to_help(code: str) -> None:
    result = demo.InternalDemoSafetyPolicy(settings()).evaluate((code,), user_id=USER_ID)
    assert result.response_level == "EMERGENCY"
    assert result.safety_disposition == "EMERGENCY_ROUTED"
    assert result.message_code == "DEMO_SAFETY_EMERGENCY_HELP"
    assert result.copy_version == "track-c-safety-demo-ko-2026-09-16.1"
    assert result.source_version == "track-c-safety-demo-source-2026-09-16.1"


@pytest.mark.parametrize(
    ("codes", "level"),
    [
        (("SUDDEN_OTHER_BODY_SWELLING",), "URGENT"),
        (("OTHER_SYMPTOM",), "UNKNOWN"),
        (("UNSURE_ABOUT_SYMPTOMS",), "UNKNOWN"),
        (("SYNTHETIC_UNREGISTERED",), "UNKNOWN"),
        (("SUDDEN_OTHER_BODY_SWELLING", "OTHER_SYMPTOM", "SYNTHETIC_UNREGISTERED"), "URGENT"),
        (("SUDDEN_OTHER_BODY_SWELLING", "SUDDEN_AIRWAY_SWELLING", "UNSURE_ABOUT_SYMPTOMS"), "EMERGENCY"),
    ],
)
def test_priority_is_invariant_to_order_and_duplicates(codes: tuple[str, ...], level: str) -> None:
    policy = demo.InternalDemoSafetyPolicy(settings())
    result = policy.evaluate(codes, user_id=USER_ID)
    assert result.response_level == level
    assert policy.evaluate(tuple(reversed(codes)), user_id=USER_ID) == result
    assert policy.evaluate(codes + codes, user_id=USER_ID) == result


def test_empty_input_keeps_the_existing_meaning_and_versions() -> None:
    assert demo.InternalDemoSafetyPolicy(settings()).evaluate(
        (), user_id=USER_ID
    ) == ContractFoundationSafetyPolicy().evaluate(())


@pytest.mark.parametrize("code", EMERGENCY_CODES + ("SUDDEN_OTHER_BODY_SWELLING",))
def test_default_policy_does_not_activate_demo_codes(code: str) -> None:
    assert Config.model_fields["TRACK_C_SAFETY_DEMO_ENABLED"].default is False
    assert ContractFoundationSafetyPolicy().evaluate((code,), user_id=USER_ID).response_level == "UNKNOWN"


@pytest.mark.parametrize(
    "overrides",
    [
        {"ENV": Env.STAGING},
        {"ENV": Env.PRODUCTION},
        {"TRACK_C_SAFETY_DEMO_USER_IDS": []},
        {"TRACK_C_SAFETY_DEMO_STARTS_AT": None},
        {"TRACK_C_SAFETY_DEMO_EXPIRES_AT": None},
        {"TRACK_C_SAFETY_DEMO_STARTS_AT": NOW.replace(tzinfo=None)},
        {"TRACK_C_SAFETY_DEMO_EXPIRES_AT": NOW.replace(tzinfo=None)},
        {"TRACK_C_SAFETY_DEMO_EXPIRES_AT": NOW},
        {"TRACK_C_SAFETY_DEMO_EXPIRES_AT": NOW - timedelta(seconds=1)},
        {"TRACK_C_SAFETY_DEMO_EXPIRES_AT": NOW + timedelta(days=7, microseconds=1)},
    ],
)
def test_invalid_demo_configuration_rejected_at_startup(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        settings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"ENV": Env.STAGING},
        {"ENV": Env.PRODUCTION},
        {"TRACK_C_SAFETY_DEMO_ENABLED": False},
        {"TRACK_C_SAFETY_DEMO_USER_IDS": frozenset({uuid4()})},
        {"TRACK_C_SAFETY_DEMO_STARTS_AT": NOW + timedelta(seconds=1)},
        {"TRACK_C_SAFETY_DEMO_STARTS_AT": NOW - timedelta(days=7), "TRACK_C_SAFETY_DEMO_EXPIRES_AT": NOW},
        {"TRACK_C_SAFETY_DEMO_STARTS_AT": None},
        {"TRACK_C_SAFETY_DEMO_EXPIRES_AT": NOW + timedelta(days=8)},
    ],
)
def test_runtime_rechecks_config_and_time_before_every_new_mutation(overrides: dict) -> None:
    configured = settings().model_copy(update=overrides)
    with pytest.raises(ApiError) as error:
        demo.InternalDemoSafetyPolicy(configured).evaluate((), user_id=USER_ID)
    assert error.value.status_code == 503
    assert error.value.code == "SAFETY_DEMO_UNAVAILABLE"
    assert str(USER_ID) not in str(error.value)


@pytest.mark.parametrize("failure", ["missing", "invalid_json", "copy_changed", "source_removed", "version_changed"])
def test_missing_or_changed_artifact_never_falls_back_to_routine(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = demo.DEMO_ARTIFACT_PATH.read_text()
    path = tmp_path / "artifact.json"
    if failure == "invalid_json":
        path.write_text("{")
    elif failure != "missing":
        payload = json.loads(raw)
        if failure == "copy_changed":
            payload["messages"]["URGENT"]["body"] = "SYNTHETIC_UNAPPROVED"
        elif failure == "source_removed":
            payload.pop("sources")
        else:
            payload["copy_version"] = "future"
        path.write_text(json.dumps(payload))
    monkeypatch.setattr(demo, "DEMO_ARTIFACT_PATH", path)
    with pytest.raises(ApiError) as error:
        demo.InternalDemoSafetyPolicy(settings()).evaluate((), user_id=USER_ID)
    assert error.value.code == "SAFETY_DEMO_UNAVAILABLE"


def test_artifact_keeps_condition_labels_sources_and_demo_notice() -> None:
    artifact = json.loads(demo.DEMO_ARTIFACT_PATH.read_bytes())
    choices = {choice["code"]: choice for choice in artifact["choices"]}
    assert set(choices) == set(EMERGENCY_CODES) | {
        "SUDDEN_OTHER_BODY_SWELLING",
        "OTHER_SYMPTOM",
        "UNSURE_ABOUT_SYMPTOMS",
    }
    assert "의료 검토를 받지 않은" in artifact["notice"]
    assert "갑자기" in choices["SUDDEN_PERSISTENT_CHEST_PAIN"]["label"]
    assert "계속" in choices["SUDDEN_PERSISTENT_CHEST_PAIN"]["label"]
    for code in EMERGENCY_CODES[-3:]:
        assert "지금 증상이 있거나 최근 24시간" in choices[code]["label"]
    assert "판단할 수는 없어요" in artifact["messages"]["URGENT"]["body"]
    sources = {source["id"] for source in artifact["sources"]}
    assert all(set(choice["source_ids"]) <= sources for choice in choices.values())
