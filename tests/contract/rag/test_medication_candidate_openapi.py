from typing import Any

from app.main import fastapi_app


def _openapi() -> dict[str, Any]:
    return fastapi_app.openapi()


def _schema(name: str) -> dict[str, Any]:
    return _openapi()["components"]["schemas"][name]


def _operation(method: str, path: str) -> dict[str, Any]:
    return _openapi()["paths"][path][method]


def _json_schema_ref(operation: dict[str, Any], status_code: str) -> str:
    return operation["responses"][status_code]["content"]["application/json"]["schema"]["$ref"]


def _request_schema_ref(operation: dict[str, Any]) -> str:
    return operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]


def _assert_required_fields(schema_name: str, expected: set[str]) -> None:
    schema = _schema(schema_name)
    assert set(schema["required"]) == expected
    assert set(schema["properties"]) == expected


def test_candidate_routes_match_approved_paths_and_methods() -> None:
    paths = _openapi()["paths"]

    assert "/api/v1/medication-candidate-searches/{prescription_version_medication_id}" in paths
    assert "/api/v1/medication-candidates/confirm" in paths
    assert "/api/v1/medication-candidates/reject" in paths

    assert set(paths["/api/v1/medication-candidate-searches/{prescription_version_medication_id}"]) == {"get"}
    assert set(paths["/api/v1/medication-candidates/confirm"]) == {"post"}
    assert set(paths["/api/v1/medication-candidates/reject"]) == {"post"}


def test_candidate_routes_keep_current_operation_ids_and_response_refs() -> None:
    get_operation = _operation("get", "/api/v1/medication-candidate-searches/{prescription_version_medication_id}")
    confirm_operation = _operation("post", "/api/v1/medication-candidates/confirm")
    reject_operation = _operation("post", "/api/v1/medication-candidates/reject")

    assert (
        get_operation["operationId"]
        == "get_medication_candidate_search_api_v1_medication_candidate_searches__prescription_version_medication_id__get"
    )
    assert confirm_operation["operationId"] == "confirm_medication_candidate_api_v1_medication_candidates_confirm_post"
    assert reject_operation["operationId"] == "reject_medication_candidate_api_v1_medication_candidates_reject_post"

    assert _json_schema_ref(get_operation, "200") == "#/components/schemas/MedicationCandidateSearchResponse"
    assert _json_schema_ref(confirm_operation, "200") == "#/components/schemas/ConfirmMedicationCandidateResponse"
    assert _json_schema_ref(reject_operation, "200") == "#/components/schemas/RejectMedicationCandidateResponse"


def test_candidate_error_responses_use_shared_error_envelope() -> None:
    create_operation = _operation("post", "/api/v1/medication-candidate-searches")
    get_operation = _operation("get", "/api/v1/medication-candidate-searches/{prescription_version_medication_id}")
    confirm_operation = _operation("post", "/api/v1/medication-candidates/confirm")
    reject_operation = _operation("post", "/api/v1/medication-candidates/reject")

    assert set(create_operation["responses"]) == {"202", "422", "503"}
    assert set(get_operation["responses"]) == {"200", "404", "422", "503"}
    assert set(confirm_operation["responses"]) == {"200", "400", "404", "409", "422", "503"}
    assert set(reject_operation["responses"]) == {"200", "400", "404", "409", "422", "503"}

    for operation, status_code in (
        (create_operation, "422"),
        (create_operation, "503"),
        (get_operation, "404"),
        (get_operation, "422"),
        (get_operation, "503"),
        (confirm_operation, "400"),
        (confirm_operation, "404"),
        (confirm_operation, "409"),
        (confirm_operation, "422"),
        (confirm_operation, "503"),
        (reject_operation, "400"),
        (reject_operation, "404"),
        (reject_operation, "409"),
        (reject_operation, "422"),
        (reject_operation, "503"),
    ):
        assert _json_schema_ref(operation, status_code) == "#/components/schemas/ErrorResponse"


def test_candidate_confirm_and_reject_require_idempotency_key_header() -> None:
    for operation in (
        _operation("post", "/api/v1/medication-candidates/confirm"),
        _operation("post", "/api/v1/medication-candidates/reject"),
    ):
        header = next(parameter for parameter in operation["parameters"] if parameter["name"] == "Idempotency-Key")
        assert header["in"] == "header"
        assert header["required"] is True
        assert header["schema"] == {
            "type": "string",
            "minLength": 16,
            "maxLength": 255,
            "pattern": "^[A-Za-z0-9._:-]+$",
        }


def test_candidate_request_schemas_match_confirm_and_reject_contract() -> None:
    confirm_operation = _operation("post", "/api/v1/medication-candidates/confirm")
    reject_operation = _operation("post", "/api/v1/medication-candidates/reject")

    assert _request_schema_ref(confirm_operation) == "#/components/schemas/ConfirmMedicationCandidateRequest"
    assert _request_schema_ref(reject_operation) == "#/components/schemas/RejectMedicationCandidateRequest"

    _assert_required_fields(
        "ConfirmMedicationCandidateRequest",
        {"prescription_version_medication_id", "candidate_search_result_id"},
    )
    _assert_required_fields("RejectMedicationCandidateRequest", {"search_id", "candidate_search_result_id"})


def test_candidate_public_search_dto_exposes_only_allowed_fields() -> None:
    _assert_required_fields(
        "MedicationCandidateSearchData",
        {
            "search_id",
            "prescription_version_medication_id",
            "medication_index",
            "status",
            "candidate_search_result_id",
            "candidate",
            "expires_at",
        },
    )
    _assert_required_fields(
        "MedicationCandidateSnapshot",
        {"product_name", "strength_text", "dosage_form", "manufacturer_name", "product_status"},
    )

    search_fields = set(_schema("MedicationCandidateSearchData")["properties"])
    snapshot_fields = set(_schema("MedicationCandidateSnapshot")["properties"])
    forbidden_fields = {
        "candidate_count",
        "displayed_candidate_count",
        "display_limit",
        "status_reason",
        "query_digest",
        "result_score",
        "result_rank",
        "top_k",
        "source_row_id",
        "candidate_index_version_id",
        "runtime_release_bundle_id",
    }
    assert search_fields.isdisjoint(forbidden_fields)
    assert snapshot_fields.isdisjoint(forbidden_fields)


def test_candidate_status_enums_match_public_contract() -> None:
    assert _schema("MedicationCandidateSearchStatus")["enum"] == [
        "RUNNING",
        "READY",
        "AMBIGUOUS",
        "NO_CANDIDATE",
        "INGREDIENT_ONLY",
        "INVALID_INPUT",
        "INVALIDATED_INPUT_CHANGED",
        "INVALIDATED_USER_REJECTED",
        "EXPIRED",
        "FAILED",
        "CONSUMED",
    ]
    assert _schema("MedicationIdentificationStatus")["enum"] == ["MATCHED", "UNRESOLVED"]
    assert _schema("MedicationIdentificationSource")["enum"] == ["USER_SELECTED", "USER_REJECTED"]


def test_candidate_success_response_schemas_match_contract() -> None:
    _assert_required_fields(
        "ConfirmMedicationCandidateData",
        {
            "identification_id",
            "prescription_version_medication_id",
            "status",
            "source",
            "product_id",
            "confirmed_at",
        },
    )
    _assert_required_fields(
        "RejectMedicationCandidateData",
        {
            "identification_event_id",
            "prescription_version_medication_id",
            "status",
            "search_status",
            "rejected_at",
        },
    )
