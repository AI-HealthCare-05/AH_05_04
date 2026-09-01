import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.dtos.prescriptions import MedicationData
from app.models.ocr import FieldType

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CONTRACT_PATH = PROJECT_ROOT / "docs" / "contracts" / "current" / "ocr-provider-field-aliases.md"
CONTRACT_INDEX_PATH = PROJECT_ROOT / "docs" / "contracts" / "README.md"
FRONTEND_API_PATH = PROJECT_ROOT / "frontend" / "src" / "api" / "prescriptions.ts"

CANONICAL_BACKEND_FIELD = "medication_name"
CANONICAL_OCR_FIELD_TYPE = "MEDICATION_NAME"
UNREGISTERED_ALIASES = {"drugName", "medicine_name"}


def _read(path: Path) -> str:
    assert path.is_file(), f"계약 검증 대상 파일이 없습니다: {path}"
    return path.read_text(encoding="utf-8")


def _frontend_medication_fields() -> set[str]:
    source = _read(FRONTEND_API_PATH)
    match = re.search(
        r"export type Medication = \{(?P<body>.*?)^\}",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert match is not None, "frontend/src/api/prescriptions.ts에서 Medication type을 찾지 못했습니다."

    return set(
        re.findall(
            r"^\s{2}([A-Za-z_][A-Za-z0-9_]*)(?:\?)?:",
            match.group("body"),
            flags=re.MULTILINE,
        )
    )


def test_backend_and_ocr_use_canonical_medication_name() -> None:
    assert CANONICAL_BACKEND_FIELD in MedicationData.model_fields
    assert FieldType.MEDICATION_NAME.value == CANONICAL_OCR_FIELD_TYPE


@pytest.mark.parametrize("alias", sorted(UNREGISTERED_ALIASES))
def test_unregistered_alias_cannot_replace_backend_canonical_field(
    alias: str,
) -> None:
    with pytest.raises(ValidationError):
        MedicationData.model_validate(
            {
                alias: "합성약정",
                "display_order": 1,
            }
        )


def test_backend_response_contains_only_canonical_medication_name() -> None:
    medication = MedicationData(
        medication_name="합성약정",
        display_order=1,
    )

    payload = medication.model_dump()

    assert payload[CANONICAL_BACKEND_FIELD] == "합성약정"
    assert UNREGISTERED_ALIASES.isdisjoint(payload)


def test_frontend_medication_type_uses_only_canonical_name() -> None:
    fields = _frontend_medication_fields()

    assert CANONICAL_BACKEND_FIELD in fields
    assert UNREGISTERED_ALIASES.isdisjoint(fields)


def test_contract_declares_no_active_external_alias() -> None:
    contract = _read(CONTRACT_PATH)

    assert "> Status: Current Preventive Contract · No active external alias" in contract

    assert "| `drugName` | 확인된 Source 없음 | 허용하지 않음 | 없음 |" in contract
    assert "| `medicine_name` | 확인된 Source 없음 | 허용하지 않음 | 없음 |" in contract


def test_contract_is_registered_in_contract_index() -> None:
    index = _read(CONTRACT_INDEX_PATH)

    assert "./current/ocr-provider-field-aliases.md" in index
