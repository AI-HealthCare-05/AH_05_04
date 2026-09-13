from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.dtos.prescriptions import CorrectPrescriptionRequest
from provider_contracts.prescription_integrity import prescription_fingerprint
from provider_contracts.prescription_integrity_v1 import prescription_fingerprint as migration_fingerprint

_DATE = date(2026, 9, 10)
_ROW = {"medication_name": "합성검증정", "display_order": 1, "dose_value": Decimal("1")}


def test_round_trip_decimal_and_missing_null_fields_have_same_hash():
    before = prescription_fingerprint(_DATE, [_ROW])
    after = prescription_fingerprint(_DATE, [{**_ROW, "dose_value": Decimal("1.000"), "strength_text": None}])
    assert before == after
    assert before.medication_count == 1


def test_public_contract_matches_frozen_migration_v1() -> None:
    assert prescription_fingerprint(_DATE, [_ROW]) == migration_fingerprint(_DATE, [_ROW])


def test_issue_398_migrations_import_frozen_v1() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in (
        "398a1b2c3d4e_expand_prescription_fingerprint.py",
        "398b2c3d4e5f_require_prescription_fingerprint.py",
        "398e5f607182_remove_prescription_candidate_database_logic.py",
    ):
        source = (root / "backend/alembic/versions" / name).read_text()
        assert "provider_contracts.prescription_integrity_v1" in source
        assert "from provider_contracts.prescription_integrity import" not in source


@pytest.mark.parametrize(
    "field,value",
    [
        ("medication_name", "다른합성정"),
        ("strength_text", "10mg"),
        ("dose_value", Decimal("2")),
        ("dose_unit", "정"),
        ("frequency_per_day", 2),
        ("timing_text", "식후"),
        ("duration_days", 3),
    ],
)
def test_content_change_changes_hash(field, value):
    assert prescription_fingerprint(_DATE, [_ROW]) != prescription_fingerprint(_DATE, [{**_ROW, field: value}])


def test_date_order_and_unicode_are_explicit():
    second = {**_ROW, "display_order": 2, "medication_name": "Synthetic B"}
    assert prescription_fingerprint(_DATE, [_ROW, second]) == prescription_fingerprint(_DATE, [second, _ROW])
    assert prescription_fingerprint(_DATE, [_ROW]) != prescription_fingerprint(date(2026, 9, 11), [_ROW])
    assert prescription_fingerprint(_DATE, [{**_ROW, "medication_name": "é"}]) != prescription_fingerprint(
        _DATE, [{**_ROW, "medication_name": "e\u0301"}]
    )
    assert prescription_fingerprint(_DATE, [_ROW]) != prescription_fingerprint(_DATE, [{**_ROW, "strength_text": ""}])


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1", "0.0001", "10000000"])
def test_invalid_dose_cannot_be_silently_rounded(value):
    with pytest.raises(ValueError):
        prescription_fingerprint(_DATE, [{**_ROW, "dose_value": Decimal(value)}])


def test_api_rejects_slot_gap_before_repository():
    with pytest.raises(ValidationError):
        CorrectPrescriptionRequest(
            base_version_id=uuid4(),
            expected_revision=1,
            prescribed_date=_DATE,
            medications=[{**_ROW, "display_order": 2}],
        )
