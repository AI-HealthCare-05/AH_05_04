from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from uuid import uuid4

import pytest

from app.services.guide_runtime_projection import (
    GUIDE_RUNTIME_PROJECTION_CARRIER_VERSION,
    GuideRuntimeCanonicalCitationIdentity,
    GuideRuntimeCitationProjectionCarrier,
    GuideRuntimePersistenceGap,
    GuideRuntimeProjectionKind,
    GuideRuntimeReleaseProjectionCarrier,
    project_guide_runtime_release,
)


def test_pass_result_projects_authorized_citation_candidates_without_public_dto() -> None:
    projection = project_guide_runtime_release(_pass_carrier())

    assert projection.kind is GuideRuntimeProjectionKind.ANSWER
    assert projection.is_current is True
    assert projection.answer_text == "승인된 안내입니다."
    assert projection.fallback_text is None
    assert len(projection.citations) == 1
    citation = projection.citations[0]
    assert citation.card_target_ref == "card-sha256"
    assert citation.claim_key == "claim-food"
    assert citation.evidence_key == "evidence-food"
    assert citation.source_type == "LIFESTYLE_GUIDELINE"
    assert citation.display_order == 1
    assert citation.legacy_guide_citation_supported is False
    assert citation.legacy_guide_citation_blocker is not None
    assert GuideRuntimePersistenceGap.RELEASE_STATUS_NOT_PERSISTED in projection.persistence_gaps
    assert GuideRuntimePersistenceGap.LEGACY_CITATION_TABLE_INCOMPATIBLE in projection.persistence_gaps


@pytest.mark.parametrize(
    "mutate_identity",
    (
        lambda identity: replace(identity, card_target_ref="other-card-sha256"),
        lambda identity: replace(identity, source_snapshot_id=uuid4()),
        lambda identity: replace(identity, source_snapshot_member_id=uuid4()),
        lambda identity: replace(identity, evidence_key="other-evidence"),
    ),
)
def test_pass_result_fails_closed_when_canonical_citation_identity_is_not_exact(
    mutate_identity: Callable[[GuideRuntimeCanonicalCitationIdentity], GuideRuntimeCanonicalCitationIdentity],
) -> None:
    identity = _identity()
    carrier = _pass_carrier(
        authorized_identity=mutate_identity(identity),
    )

    projection = project_guide_runtime_release(carrier)

    assert projection.kind is GuideRuntimeProjectionKind.FAIL_CLOSED_REJECTION
    assert projection.answer_text is None
    assert projection.fallback_text is None
    assert projection.citations == ()
    assert projection.persistence_gaps == (GuideRuntimePersistenceGap.NO_PUBLIC_CONTENT,)


def test_pass_result_fails_closed_when_contract_version_is_unknown() -> None:
    projection = project_guide_runtime_release(
        replace(_pass_carrier(), contract_version="guide-runtime-public-projection-carrier-v0")
    )

    assert projection.kind is GuideRuntimeProjectionKind.FAIL_CLOSED_REJECTION
    assert projection.answer_text is None
    assert projection.citations == ()


def test_limited_result_projects_only_verified_fallback_text() -> None:
    projection = project_guide_runtime_release(_fallback_carrier("LIMITED", is_current=True))

    assert projection.kind is GuideRuntimeProjectionKind.LIMITED_FALLBACK
    assert projection.is_current is True
    assert projection.answer_text is None
    assert projection.fallback_text == "현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."
    assert projection.citations == ()
    assert projection.persistence_gaps == (
        GuideRuntimePersistenceGap.RELEASE_STATUS_NOT_PERSISTED,
        GuideRuntimePersistenceGap.FALLBACK_NOT_PERSISTED,
    )


def test_rejected_result_projects_approved_fallback_without_citations() -> None:
    projection = project_guide_runtime_release(_fallback_carrier("REJECTED", is_current=True))

    assert projection.kind is GuideRuntimeProjectionKind.REJECTED_FALLBACK
    assert projection.is_current is True
    assert projection.answer_text is None
    assert projection.fallback_text == "현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."
    assert projection.citations == ()


def test_stale_result_keeps_stale_state_separate_from_normal_answer() -> None:
    projection = project_guide_runtime_release(_fallback_carrier("STALE", is_current=False))

    assert projection.kind is GuideRuntimeProjectionKind.STALE_FALLBACK
    assert projection.is_current is False
    assert projection.answer_text is None
    assert projection.fallback_text is not None
    assert projection.citations == ()


def test_malformed_runtime_release_fails_closed_without_public_content() -> None:
    projection = project_guide_runtime_release(
        GuideRuntimeReleaseProjectionCarrier(
            contract_version=GUIDE_RUNTIME_PROJECTION_CARRIER_VERSION,
            release_decision="REJECTED",
            is_current=True,
            answer_text=None,
            fallback_text=None,
        )
    )

    assert projection.kind is GuideRuntimeProjectionKind.FAIL_CLOSED_REJECTION
    assert projection.is_current is True
    assert projection.answer_text is None
    assert projection.fallback_text is None
    assert projection.citations == ()
    assert projection.persistence_gaps == (GuideRuntimePersistenceGap.NO_PUBLIC_CONTENT,)


def test_projection_shape_does_not_leak_internal_runtime_authority_fields() -> None:
    projection_dump = asdict(project_guide_runtime_release(_pass_carrier()))
    projected_keys = set(projection_dump)

    assert "execution_status" not in projected_keys
    assert "evidence_status" not in projected_keys
    assert "release_decision" not in projected_keys
    assert "fallback_code" not in projected_keys
    assert "authorized_selection" not in projected_keys
    assert "card_outcome" not in projected_keys
    assert "reason" not in str(projection_dump).lower()
    assert "score" not in str(projection_dump).lower()
    assert "rank" not in str(projection_dump).lower()
    assert "confidence" not in str(projection_dump).lower()
    assert "raw_source" not in str(projection_dump).lower()
    assert "artifact_ref" not in str(projection_dump).lower()


def _pass_carrier(
    *,
    authorized_identity: GuideRuntimeCanonicalCitationIdentity | None = None,
) -> GuideRuntimeReleaseProjectionCarrier:
    card_identity = _identity()
    return GuideRuntimeReleaseProjectionCarrier(
        contract_version=GUIDE_RUNTIME_PROJECTION_CARRIER_VERSION,
        release_decision="PASS",
        is_current=True,
        answer_text="승인된 안내입니다.",
        fallback_text=None,
        citations=(
            GuideRuntimeCitationProjectionCarrier(
                card_identity=card_identity,
                authorized_identity=authorized_identity or card_identity,
            ),
        ),
    )


def _fallback_carrier(release_decision: str, *, is_current: bool) -> GuideRuntimeReleaseProjectionCarrier:
    return GuideRuntimeReleaseProjectionCarrier(
        contract_version=GUIDE_RUNTIME_PROJECTION_CARRIER_VERSION,
        release_decision=release_decision,
        is_current=is_current,
        answer_text=None,
        fallback_text="현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요.",
    )


def _identity() -> GuideRuntimeCanonicalCitationIdentity:
    return GuideRuntimeCanonicalCitationIdentity(
        card_target_ref="card-sha256",
        claim_key="claim-food",
        evidence_key="evidence-food",
        source_type="LIFESTYLE_GUIDELINE",
        source_snapshot_id=uuid4(),
        source_snapshot_member_id=uuid4(),
        source_code="MFDS",
        source_version="2026-09-20",
        locator="section-1",
        content_sha256="a" * 64,
    )
