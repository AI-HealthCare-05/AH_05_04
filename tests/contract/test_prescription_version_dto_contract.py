from typing import Any

from app.dtos.chat import ChatSessionData
from app.dtos.guides import GuideData
from app.dtos.jobs import JobStatusData
from app.dtos.medication_candidates import (
    ConfirmMedicationCandidateData,
    MedicationCandidateSearchData,
    RejectMedicationCandidateData,
)
from app.dtos.prescriptions import MedicationData, PrescriptionData
from app.main import fastapi_app


def _property_schema(model: type[Any], field: str) -> dict[str, Any]:
    schema = model.model_json_schema()
    assert field in schema["required"]
    return schema["properties"][field]


def test_runtime_prescription_version_ids_are_required_and_non_nullable() -> None:
    for model, field in (
        (PrescriptionData, "prescription_version_id"),
        (MedicationData, "prescription_version_medication_id"),
        (GuideData, "prescription_version_id"),
        (ChatSessionData, "prescription_version_id"),
        (MedicationCandidateSearchData, "prescription_version_medication_id"),
        (ConfirmMedicationCandidateData, "prescription_version_medication_id"),
        (RejectMedicationCandidateData, "prescription_version_medication_id"),
    ):
        field_schema = _property_schema(model, field)
        assert field_schema["type"] == "string"
        assert "anyOf" not in field_schema


def test_job_prescription_version_id_is_required_but_nullable_for_ocr() -> None:
    field_schema = _property_schema(JobStatusData, "prescription_version_id")

    assert {item.get("type") for item in field_schema["anyOf"]} == {"string", "null"}


def test_openapi_prescription_version_ids_match_runtime_dtos() -> None:
    schemas = fastapi_app.openapi()["components"]["schemas"]

    for schema_name, field in (
        ("PrescriptionData", "prescription_version_id"),
        ("MedicationData", "prescription_version_medication_id"),
        ("GuideData", "prescription_version_id"),
        ("ChatSessionData", "prescription_version_id"),
        ("MedicationCandidateSearchData", "prescription_version_medication_id"),
        ("ConfirmMedicationCandidateData", "prescription_version_medication_id"),
        ("RejectMedicationCandidateData", "prescription_version_medication_id"),
    ):
        schema = schemas[schema_name]
        assert field in schema["required"]
        assert schema["properties"][field]["type"] == "string"
        assert "anyOf" not in schema["properties"][field]

    job_schema = schemas["JobStatusData"]
    assert "prescription_version_id" in job_schema["required"]
    assert {item.get("type") for item in job_schema["properties"]["prescription_version_id"]["anyOf"]} == {
        "string",
        "null",
    }
