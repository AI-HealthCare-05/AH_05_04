import pytest

from ai_worker.tasks.rag.source_client.endpoints import (
    MFDS_ENDPOINT_CANDIDATES,
)


@pytest.mark.parametrize(
    ("operation_code", "expected_path", "expected_secret_name"),
    (
        (
            "LIST_APPROVED_PRODUCTS",
            "/1471000/DrugPrdtPrmsnInfoService07/getDrugPrdtPrmsnInq07",
            "serviceKey",
        ),
        (
            "LIST_INGREDIENT_CONTRAINDICATIONS",
            "/1471000/DURIrdntInfoService03/getUsjntTabooInfoList02",
            "serviceKey",
        ),
        (
            "LIST_PATIENT_MEDICATION_GUIDES",
            "/1471000/DrbEasyDrugInfoService/getDrbEasyDrugList",
            "ServiceKey",
        ),
    ),
)
def test_official_endpoint_candidate(
    operation_code: str,
    expected_path: str,
    expected_secret_name: str,
) -> None:
    candidate = MFDS_ENDPOINT_CANDIDATES[operation_code]
    contract = candidate.contract

    assert contract.identity.operation_code == operation_code
    assert contract.method == "GET"
    assert contract.scheme == "https"
    assert contract.host == "apis.data.go.kr"
    assert contract.path_template == expected_path
    assert candidate.secret_parameter_name == expected_secret_name
    assert candidate.request_parameters == (("type", "json"),)

    sensitive_parameters = {parameter.name for parameter in contract.required_parameters if parameter.sensitive}
    assert sensitive_parameters == {expected_secret_name}


def test_all_p0_endpoint_candidates_are_registered() -> None:
    assert set(MFDS_ENDPOINT_CANDIDATES) == {
        "LIST_APPROVED_PRODUCTS",
        "LIST_INGREDIENT_CONTRAINDICATIONS",
        "LIST_PATIENT_MEDICATION_GUIDES",
    }
