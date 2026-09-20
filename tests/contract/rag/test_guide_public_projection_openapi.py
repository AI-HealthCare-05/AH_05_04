from typing import Any

from app.main import fastapi_app


def _openapi() -> dict[str, Any]:
    return fastapi_app.openapi()


def _schema(name: str) -> dict[str, Any]:
    return _openapi()["components"]["schemas"][name]


def test_guide_post_and_get_share_frozen_public_response() -> None:
    paths = _openapi()["paths"]

    assert (
        paths["/api/v1/guides"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/GuideResponse"
    )
    assert (
        paths["/api/v1/guides/{guide_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/GuideResponse"
    )


def test_guide_data_requires_nullable_release_fields_and_ordered_citations() -> None:
    schema = _schema("GuideData")
    fields = set(schema["properties"])

    assert set(schema["required"]) == fields
    assert {
        "release_decision",
        "release_is_current",
        "fallback_code",
        "fallback_text",
        "citations",
    } <= fields
    assert schema["properties"]["citations"] == {
        "items": {"$ref": "#/components/schemas/GuideCitationData"},
        "type": "array",
        "title": "Citations",
    }


def test_guide_citation_schema_exposes_only_approved_public_fields() -> None:
    schema = _schema("GuideCitationData")
    expected = {"source_type", "source_code", "source_version", "locator", "display_order"}

    assert set(schema["required"]) == expected
    assert set(schema["properties"]) == expected
    serialized = str(_schema("GuideResponse")) + str(_schema("GuideData")) + str(schema)
    for internal_field in (
        "card_target_ref",
        "claim_key",
        "evidence_key",
        "source_snapshot_id",
        "source_snapshot_member_id",
        "content_sha256",
        "score",
        "rank",
        "confidence",
        "raw_source",
    ):
        assert internal_field not in serialized


def test_guide_release_enum_values_match_frozen_contract() -> None:
    assert _schema("GuideRuntimeReleaseDecision")["enum"] == ["PASS", "LIMITED", "REJECTED", "STALE"]
    assert _schema("GuideRuntimeCitationSourceType")["enum"] == ["LIFESTYLE_GUIDELINE"]
    assert _schema("GuideRuntimeFallbackCode")["enum"] == [
        "NO_APPROVED_EVIDENCE",
        "CONFLICTING_EVIDENCE",
        "PROVIDER_TIMEOUT",
        "DEPENDENCY_UNAVAILABLE",
        "VALIDATION_FAILED",
        "PRESCRIPTION_STALE",
        "EXECUTION_CONTEXT_STALE",
        "UNSUPPORTED_REQUEST",
    ]
