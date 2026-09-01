from __future__ import annotations

import pytest

from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "ocr_raw",
        "OCR-Raw",
        "normalized_value",
        "ocr_draft",
        "draft_value",
        "insurance_code",
        "insurance_code_digest",
        "internal-identifier-digest",
        "provider_payload",
        "provider_response_payload",
        "api_key",
        "client_secret",
        "secret_key",
        "access_token",
        "refresh_token",
        "password",
    ],
)
def test_privacy_boundary_rejects_complete_normalized_deny_key_set(forbidden_key: str) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        validate_privacy_boundary({"context": {"nested": {forbidden_key: "SECRET_SENTINEL"}}})

    assert caught.value.code is EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN
    assert caught.value.safe_path is not None
    assert caught.value.safe_path.startswith("/")
    assert "SECRET_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize(
    "sentinel",
    [
        "patient@example.com",
        "010-1234-5678",
        "900101-1234567",
        "Bearer abc.def.ghi",
        "sk-proj-abcdefghijklmnopqrstuvwxyz",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "xoxb-1234567890-secret",
    ],
)
def test_privacy_boundary_rejects_value_sentinels_in_every_string_leaf(sentinel: str) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        validate_privacy_boundary({"items": [{"safe": "SYNTHETIC"}, [sentinel]]})

    assert caught.value.code is EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN
    assert sentinel not in str(caught.value)


def test_privacy_boundary_rejects_nested_ocr_raw_without_echoing_value() -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        validate_privacy_boundary({"context": {"nested": {"ocr_raw": "SECRET_SENTINEL"}}})

    assert caught.value.code is EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN
    assert "SECRET_SENTINEL" not in str(caught.value)


def test_privacy_boundary_accepts_synthetic_fixture_references() -> None:
    validate_privacy_boundary(
        {
            "context": {
                "prescription_fixture": "fixtures/synthetic-prescription.json",
                "medication_fixtures": ["fixtures/synthetic-medication.json"],
                "patient_context_fixture": None,
                "runtime_fixture": "fixtures/synthetic-runtime.json",
            }
        }
    )
