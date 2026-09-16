from __future__ import annotations

from dataclasses import replace

import pytest

from ai_worker.tasks.rag.citation_authorization import (
    AuthorizationBuildDecision,
    AuthorizationReason,
    AuthorizationVerificationDecision,
    CitationAuthorizationReceipt,
    CitationAuthorizationSelectionReceipt,
    GuardDecision,
    GuardOperation,
    OriginRequestGuardBinding,
    RuntimeAuthorizationBinding,
    RuntimeEnvironment,
    UsePurpose,
    build_citation_authorization_request,
    canonical_scope_manifest_hash,
    verify_citation_authorization_receipt,
)
from ai_worker.tasks.rag.claim_citation_validator import CitationSourceType, validate_claim_citations
from ai_worker.tests.rag.test_claim_citation_validator import _artifact, _candidate_set, _support_receipt

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64


def _validated_selection():
    candidate_set = _candidate_set()
    outcome = validate_claim_citations(candidate_set, (_support_receipt(candidate_set),))
    assert outcome.validated_selection is not None
    return outcome.validated_selection


def _runtime_binding() -> RuntimeAuthorizationBinding:
    scopes = ("GUIDE", "PATIENT_CITATION")
    return RuntimeAuthorizationBinding(
        environment=RuntimeEnvironment.TEST,
        bundle_id="bundle-001",
        bundle_manifest_hash=_A,
        request_scope_codes=scopes,
        scope_manifest_hash=canonical_scope_manifest_hash(scopes),
    )


def _origin_guard(runtime: RuntimeAuthorizationBinding | None = None) -> OriginRequestGuardBinding:
    binding = runtime or _runtime_binding()
    return OriginRequestGuardBinding(
        guard_ref=_artifact("request-guard", _B),
        decision=GuardDecision.PASS,
        operation=GuardOperation.REQUEST,
        environment=binding.environment,
        bundle_id=binding.bundle_id,
        bundle_manifest_hash=binding.bundle_manifest_hash,
        request_scope_codes=binding.request_scope_codes,
        scope_manifest_hash=binding.scope_manifest_hash,
    )


def _built_request():
    selection = _validated_selection()
    runtime = _runtime_binding()
    outcome = build_citation_authorization_request(selection, runtime, _origin_guard(runtime))
    assert outcome.request is not None
    return selection, outcome.request


def _passing_receipt(request) -> CitationAuthorizationReceipt:
    return CitationAuthorizationReceipt(
        receipt_ref=_artifact("citation-authorization-receipt", _C),
        request_sha256=request.request_sha256,
        origin_guard_ref=request.origin_request_guard.guard_ref,
        origin_decision=GuardDecision.PASS,
        operation=GuardOperation.CITATION_AUTHORIZATION,
        environment=request.runtime_binding.environment,
        bundle_id=request.runtime_binding.bundle_id,
        bundle_manifest_hash=request.runtime_binding.bundle_manifest_hash,
        request_scope_codes=request.runtime_binding.request_scope_codes,
        scope_manifest_hash=request.runtime_binding.scope_manifest_hash,
        validated_selection_sha256=request.validated_selection_sha256,
        selection_manifest_sha256=request.selection_manifest_sha256,
        selections=tuple(
            CitationAuthorizationSelectionReceipt(
                selection=entry,
                source_decision_ref=_artifact(f"{entry.source_code}-citation-decision", _B),
                member_decision_ref=_artifact(f"{entry.artifact_code}-citation-decision", _C),
                selected_for_operation=True,
                purpose=UsePurpose.PATIENT_CITATION,
                source_decision=GuardDecision.PASS,
                member_decision=GuardDecision.PASS,
            )
            for entry in request.selection_manifest
        ),
    )


def test_builds_a_deterministic_request_bound_to_origin_runtime_and_validated_selection() -> None:
    selection = _validated_selection()
    runtime = _runtime_binding()
    origin = _origin_guard(runtime)

    first = build_citation_authorization_request(selection, runtime, origin)
    second = build_citation_authorization_request(selection, runtime, origin)

    assert first.decision is AuthorizationBuildDecision.BUILT
    assert first.reasons == ()
    assert first.request == second.request
    assert first.request is not None
    assert first.request.validated_selection_sha256 == selection.selection_sha256
    assert len(first.request.selection_manifest) == 1
    assert first.request.selection_manifest[0].source_code == "knowledge-source"
    assert not hasattr(first.request.selection_manifest[0], "source_decision_ref")
    assert first.request.selection_manifest_sha256 == (
        "a1c277c295e59b6375452dd7a829d13e57a030b89e3b9bc84dd99439c70ae396"
    )
    assert first.request.request_sha256 == "74105ba87a0c4e326b803e7ba7f559d71c9b3dfff1a43c6c60ff84f6096317a3"


@pytest.mark.parametrize(
    "origin_mutation",
    (
        lambda value: replace(value, decision=GuardDecision.FAIL),
        lambda value: replace(value, operation=GuardOperation.CITATION_AUTHORIZATION),
        lambda value: replace(value, bundle_id="bundle-other"),
        lambda value: replace(value, request_scope_codes=tuple(reversed(value.request_scope_codes))),
    ),
)
def test_rejects_origin_guard_that_is_not_an_exact_request_pass(origin_mutation) -> None:
    selection = _validated_selection()
    runtime = _runtime_binding()

    outcome = build_citation_authorization_request(selection, runtime, origin_mutation(_origin_guard(runtime)))

    assert outcome.decision is AuthorizationBuildDecision.REJECTED
    assert AuthorizationReason.ORIGIN_REQUEST_MISMATCH in outcome.reasons
    assert outcome.request is None


def test_accepts_only_an_exact_bound_complete_patient_citation_receipt() -> None:
    _, request = _built_request()

    outcome = verify_citation_authorization_receipt(request, _passing_receipt(request))

    assert outcome.decision is AuthorizationVerificationDecision.AUTHORIZED
    assert outcome.reasons == ()


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    (
        (
            lambda receipt: replace(receipt, request_sha256=_A),
            AuthorizationReason.RECEIPT_BINDING_MISMATCH,
        ),
        (
            lambda receipt: replace(receipt, operation=GuardOperation.REQUEST),
            AuthorizationReason.RECEIPT_BINDING_MISMATCH,
        ),
        (
            lambda receipt: replace(receipt, bundle_id="bundle-other"),
            AuthorizationReason.RECEIPT_BINDING_MISMATCH,
        ),
        (
            lambda receipt: replace(
                receipt,
                selections=(replace(receipt.selections[0], purpose=UsePurpose.RETRIEVAL),),
            ),
            AuthorizationReason.SELECTION_NOT_AUTHORIZED,
        ),
        (
            lambda receipt: replace(
                receipt,
                selections=(replace(receipt.selections[0], selected_for_operation=False),),
            ),
            AuthorizationReason.SELECTION_NOT_AUTHORIZED,
        ),
        (
            lambda receipt: replace(
                receipt,
                selections=(replace(receipt.selections[0], member_decision=GuardDecision.FAIL),),
            ),
            AuthorizationReason.SELECTION_NOT_AUTHORIZED,
        ),
        (
            lambda receipt: replace(receipt, selections=()),
            AuthorizationReason.SELECTION_RECEIPT_MISMATCH,
        ),
    ),
)
def test_rejects_partial_or_mutated_authorization_receipts(mutate, expected_reason: AuthorizationReason) -> None:
    _, request = _built_request()

    outcome = verify_citation_authorization_receipt(request, mutate(_passing_receipt(request)))

    assert outcome.decision is AuthorizationVerificationDecision.REJECTED
    assert expected_reason in outcome.reasons


def test_rejects_noncanonical_scope_instead_of_silently_sorting_it() -> None:
    selection = _validated_selection()
    runtime = replace(_runtime_binding(), request_scope_codes=("PATIENT_CITATION", "GUIDE"))

    outcome = build_citation_authorization_request(selection, runtime, _origin_guard(runtime))

    assert outcome.decision is AuthorizationBuildDecision.REJECTED
    assert AuthorizationReason.RUNTIME_BINDING_INVALID in outcome.reasons


def test_rejects_empty_authorization_selection_until_persistence_contract_models_it() -> None:
    candidate_set = _candidate_set(source_type=CitationSourceType.PRESCRIPTION)
    validation = validate_claim_citations(candidate_set, (_support_receipt(candidate_set),))
    assert validation.validated_selection is not None
    runtime = _runtime_binding()

    outcome = build_citation_authorization_request(validation.validated_selection, runtime, _origin_guard(runtime))

    assert outcome.decision is AuthorizationBuildDecision.REJECTED
    assert outcome.reasons == (AuthorizationReason.AUTHORIZATION_SELECTION_REQUIRED,)


def test_rejects_forged_validated_selection_without_raising() -> None:
    selection = replace(_validated_selection(), candidate_set=object())
    runtime = _runtime_binding()

    outcome = build_citation_authorization_request(selection, runtime, _origin_guard(runtime))

    assert outcome.decision is AuthorizationBuildDecision.REJECTED
    assert outcome.reasons == (AuthorizationReason.VALIDATED_SELECTION_INVALID,)


def test_rejects_validated_selection_that_drops_its_support_receipt_trace() -> None:
    selection = replace(_validated_selection(), support_receipts=())
    runtime = _runtime_binding()

    outcome = build_citation_authorization_request(selection, runtime, _origin_guard(runtime))

    assert outcome.decision is AuthorizationBuildDecision.REJECTED
    assert outcome.reasons == (AuthorizationReason.VALIDATED_SELECTION_INVALID,)


def test_receipt_verifier_rejects_a_request_with_a_mutated_self_hash() -> None:
    _, request = _built_request()
    altered_request = replace(request, request_sha256=_A)

    outcome = verify_citation_authorization_receipt(altered_request, _passing_receipt(altered_request))

    assert outcome.decision is AuthorizationVerificationDecision.REJECTED
    assert outcome.reasons == (AuthorizationReason.AUTHORIZATION_REQUEST_INVALID,)


def test_accepts_endpoint_operation_with_nullable_operation_code() -> None:
    from ai_worker.tasks.rag.claim_citation_validator import (
        KnowledgeChunkEvidenceRef,
        SourceExecutionProvenance,
        SourceMemberKind,
    )

    binding = SourceExecutionProvenance(
        source_code="knowledge-source",
        source_version="2026-09-01",
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="endpoint-001",
        operation_code=None,
        artifact_code=None,
        artifact_version=None,
        request_source_decision_ref=_artifact("knowledge-source-decision", _B),
        request_member_decision_ref=_artifact("knowledge-source-member-decision", _C),
    )
    evidence = KnowledgeChunkEvidenceRef(
        knowledge_chunk_ref="knowledge-chunk-001",
        source_snapshot_ref=_artifact("knowledge-snapshot"),
        source_version="2026-09-01",
        locator="section-1",
        content_sha256=_A,
        execution_provenance=binding,
    )
    candidate_set = _candidate_set(evidence=evidence)
    receipt = _support_receipt(candidate_set)
    val_outcome = validate_claim_citations(candidate_set, (receipt,))
    assert val_outcome.validated_selection is not None

    runtime = _runtime_binding()
    origin = _origin_guard(runtime)
    outcome = build_citation_authorization_request(val_outcome.validated_selection, runtime, origin)

    assert outcome.decision is AuthorizationBuildDecision.BUILT
    assert outcome.reasons == ()
    assert outcome.request is not None
    assert outcome.request.selection_manifest[0].operation_code is None
