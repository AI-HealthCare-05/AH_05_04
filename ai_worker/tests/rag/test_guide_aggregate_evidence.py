"""P0-B regressions for the multi-run Guide evidence boundary."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from ai_worker.tasks.rag.guide_aggregate_evidence import (
    GuideAggregateEvidenceAssemblyDecision,
    GuideAggregateEvidenceEntry,
    assemble_guide_aggregate_evidence,
    project_guideline_evidence_from_aggregate,
)
from ai_worker.tasks.rag.guide_personalized_composition import compose_personalized_guide
from ai_worker.tasks.rag.guideline_card import GuidelineGenerationFailure, MedicationIdentityRef
from ai_worker.tasks.rag.guideline_generator import GuidelineGenerationRequest, GuidelineGenerationResult
from ai_worker.tests.rag.test_guideline_generator import make_synthetic_request
from ai_worker.tests.rag.test_guideline_production_evidence import artifact, verified_handoff, verified_selection

MEDICATION_A = MedicationIdentityRef(
    prescription_version_medication_id="11111111-1111-4111-8111-111111111111",
    code_system="MFDS_ITEM_SEQ",
    canonical_code="SYNTHETIC-A",
)
MEDICATION_B = MedicationIdentityRef(
    prescription_version_medication_id="22222222-2222-4222-8222-222222222222",
    code_system="MFDS_ITEM_SEQ",
    canonical_code="SYNTHETIC-B",
)


class _RecordingGenerator:
    def __init__(self) -> None:
        self.calls = 0
        self.last_request: GuidelineGenerationRequest | None = None

    async def generate(self, request: GuidelineGenerationRequest) -> GuidelineGenerationResult:
        self.calls += 1
        self.last_request = request
        return GuidelineGenerationFailure.PROVIDER_TIMEOUT


def _handoff(*, receipt_digest: str, handoff_digest: str, evidence_key: str = "knowledge:common"):
    receipt = artifact("retrieval-receipt", receipt_digest)
    selection = replace(verified_selection(evidence_key=evidence_key), retrieval_receipt_ref=receipt)
    return replace(
        verified_handoff(selection),
        retrieval_receipt_ref=receipt,
        handoff_sha256=handoff_digest,
    )


async def test_two_handoffs_preserve_child_boundaries_and_call_existing_generator_once() -> None:
    handoff_a = _handoff(receipt_digest="a" * 64, handoff_digest="b" * 64)
    handoff_b = _handoff(receipt_digest="c" * 64, handoff_digest="d" * 64)
    outcome = assemble_guide_aggregate_evidence(
        (
            GuideAggregateEvidenceEntry(MEDICATION_A, UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), handoff_a),
            GuideAggregateEvidenceEntry(MEDICATION_B, UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), handoff_b),
        )
    )

    assert outcome.decision is GuideAggregateEvidenceAssemblyDecision.ASSEMBLED
    assert outcome.aggregate is not None
    aggregate = outcome.aggregate
    assert aggregate.entries[0].handoff is handoff_a
    assert aggregate.entries[1].handoff is handoff_b
    assert tuple(entry.evidence.handoff_sha256 for entry in aggregate.entries) == ("b" * 64, "d" * 64)
    assert tuple(entry.evidence.selections[0].retrieval_receipt_ref.content_sha256 for entry in aggregate.entries) == (
        "a" * 64,
        "c" * 64,
    )

    generator = _RecordingGenerator()
    request = GuidelineGenerationRequest(
        medication_identities=(MEDICATION_A, MEDICATION_B),
        evidence=project_guideline_evidence_from_aggregate(aggregate),
        policy=make_synthetic_request().policy,
    )
    await compose_personalized_guide(request, generator=generator)

    assert generator.calls == 1
    assert generator.last_request is request


async def test_conflicting_snapshot_scoped_evidence_rejects_before_generator_can_be_called() -> None:
    handoff_a = _handoff(receipt_digest="a" * 64, handoff_digest="b" * 64)
    conflicting = replace(
        _handoff(receipt_digest="c" * 64, handoff_digest="d" * 64),
        selections=(
            replace(_handoff(receipt_digest="c" * 64, handoff_digest="d" * 64).selections[0], locator="$.other"),
        ),
    )

    outcome = assemble_guide_aggregate_evidence(
        (
            GuideAggregateEvidenceEntry(MEDICATION_A, UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), handoff_a),
            GuideAggregateEvidenceEntry(MEDICATION_B, UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), conflicting),
        )
    )

    assert outcome.aggregate is None
    assert outcome.decision is GuideAggregateEvidenceAssemblyDecision.REJECTED
    generator = _RecordingGenerator()
    if outcome.aggregate is not None:
        await compose_personalized_guide(
            GuidelineGenerationRequest(
                medication_identities=(MEDICATION_A, MEDICATION_B),
                evidence=project_guideline_evidence_from_aggregate(outcome.aggregate),
                policy=make_synthetic_request().policy,
            ),
            generator=generator,
        )
    assert generator.calls == 0


def test_single_handoff_keeps_its_existing_production_evidence_shape() -> None:
    handoff = _handoff(receipt_digest="a" * 64, handoff_digest="b" * 64)

    outcome = assemble_guide_aggregate_evidence(
        (GuideAggregateEvidenceEntry(MEDICATION_A, UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), handoff),)
    )

    assert outcome.aggregate is not None
    projected = project_guideline_evidence_from_aggregate(outcome.aggregate)
    assert len(projected.entries) == 1
    assert projected.entries[0].evidence.handoff_sha256 == handoff.handoff_sha256
