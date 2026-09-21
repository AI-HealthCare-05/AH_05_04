"""Persistence-free Guideline Card finalization kernel.

The kernel consumes only medication identities pinned by its caller and the
production evidence projected from the #760 authoritative Guide evidence handoff
(`guideline_production_evidence.ProductionGuidelineEvidenceSet`). It owns neither
source approval nor public release. Self-hashes provide integrity only; every
approved policy, fallback, and evidence binding must also pass a caller-supplied
authoritative approval verifier. Production approval adapters, persistence,
Citation Authorization, and the final Release Gate remain downstream
responsibilities.

Evidence authority boundary (#774):
- The legacy RAG-14 `evidence_gate` domain (`EvidenceGateOutcome`,
  `GatePassedKnowledgeEvidenceSelection`, `canonical_gate_selection_hash()`) is not a
  production input and is not reconstructed here. There is no
  handoff-to-EvidenceGateOutcome converter.
- The kernel does not re-judge the Source, member, assessment, content or freshness
  authority that #760 already verified. It validates only the structural shape of the
  production evidence it was handed, and binds drafts and approved bindings to it.
- `ApprovedGuidelineEvidenceBinding.selection_projection_sha256` means the production
  canonical projection SHA-256 from
  `compute_production_guideline_evidence_selection_hash()`. A legacy
  `canonical_gate_selection_hash()` value is not accepted.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    SensitiveText,
)
from ai_worker.tasks.rag.guideline_production_evidence import (
    ProductionGuidelineEvidence,
    ProductionGuidelineEvidenceSet,
    compute_production_guideline_evidence_selection_hash,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_HANGUL_RE = re.compile(r"[가-힣]")
_ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
_FOREIGN_SYSTEM_RE = re.compile(
    r"(?:https?://|www\.|\b(?:FDA|NHS|CDC|911|111)\b|"
    r"(?:미국|영국|일본|중국|캐나다|호주|프랑스|독일|해외)\s*(?:의료|병원|보건|기관))",
    re.IGNORECASE,
)
_SAFE_NEGATIVE_ACTION_RE = re.compile(
    r"(?:(?:복용|투여)(?:을|를)?|(?:복용\s*시간|복용량|투여량|용량)(?:을|를)?)"
    r"[^.!?\n]{0,16}(?:중단|중지|증량|감량|변경|조절|늘리|줄이|바꾸)하지\s*마(?:세요|십시오)"
)
_FORBIDDEN_ACTION_RES = (
    re.compile(r"(?:복용|투여)(?:을|를)?[^.!?\n]{0,16}(?:중단|중지|증량|감량|변경|조절)"),
    re.compile(r"(?:복용\s*시간|복용량|투여량|용량)(?:을|를)?[^.!?\n]{0,16}(?:늘리|줄이|바꾸|증량|감량|변경|조절)"),
    re.compile(r"(?:진단|확진)(?:됩니다|입니다|하세요|하십시오|할\s*수\s*있습니다)"),
    re.compile(r"(?:이|해당)\s*증상(?:은|이)[^.!?\n]{0,30}(?:입니다|이에요|예요|일\s*수\s*있습니다)"),
    re.compile(
        r"(?:하루|매일|매주)?[^.!?\n]{0,12}(?:반|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|\d+)\s*"
        r"(?:알|정|캡슐|회|시간|mg|ml|mL)"
    ),
    re.compile(r"(?:약|복용|투여)[^.!?\n]{0,20}(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|\d+)\s*배"),
    re.compile(r"(?:증상|질환|고혈압|당뇨|암)(?:은|는|이|가)?[^.!?\n]{0,16}(?:확실|분명)(?:합니다|해요|하다)"),
    re.compile(r"(?:증상|질환|고혈압|당뇨|암)(?:은|는|이|가|으로)?[^.!?\n]{0,16}(?:보입니다|보여요|의심됩니다)"),
    re.compile(
        r"(?:식사\s*(?:전|후)|공복(?:에)?|취침\s*전|아침|점심|저녁)[^.!?\n]{0,24}"
        r"(?:약|복용|투여)[^.!?\n]{0,12}(?:드세요|복용하세요|투여하세요)"
    ),
    re.compile(r"(?:다른|새)\s*약(?:으로|을|를)?[^.!?\n]{0,12}(?:교체|변경|바꾸)"),
    re.compile(r"(?:독립\s*)?(?:식단|운동)\s*(?:처방|계획을\s*따르|프로그램을\s*따르)"),
)


class GuidelineScope(StrEnum):
    FOOD_CAUTION = "FOOD_CAUTION"
    DAILY_ACTIVITY = "DAILY_ACTIVITY"


_SCOPE_PATTERNS = {
    GuidelineScope.FOOD_CAUTION: re.compile(
        r"(?:음식|식사|음주|주류|(?<![가-힣])술(?:을|은|이|과|이나|\s*관련)|음료|섭취|공복|식후)"
    ),
    GuidelineScope.DAILY_ACTIVITY: re.compile(r"(?:활동|걷기|산책|운동|운전|휴식|수면|일상|햇빛)"),
}


class GuidelineActionClass(StrEnum):
    FOOD_AVOIDANCE = "FOOD_AVOIDANCE"
    DAILY_ACTIVITY_PRECAUTION = "DAILY_ACTIVITY_PRECAUTION"


_ACTION_CLASS_BY_SCOPE = {
    GuidelineScope.FOOD_CAUTION: GuidelineActionClass.FOOD_AVOIDANCE,
    GuidelineScope.DAILY_ACTIVITY: GuidelineActionClass.DAILY_ACTIVITY_PRECAUTION,
}

_ACTION_TEXT_BY_CLASS = {
    GuidelineActionClass.FOOD_AVOIDANCE: (
        "이 약을 복용하는 동안 과도한 음주는 피하고 임의로 복용을 중단하지 마세요. 궁금한 점은 약사와 상담하세요."
    ),
    GuidelineActionClass.DAILY_ACTIVITY_PRECAUTION: ("이 약을 복용하는 동안 무리한 운동은 피하고 충분히 휴식하세요."),
}


class GuidelineCitationSourceType(StrEnum):
    LIFESTYLE_GUIDELINE = "LIFESTYLE_GUIDELINE"


class GuidelineCardStatus(StrEnum):
    GENERATED = "GENERATED"
    LIMITED = "LIMITED"
    NO_RESULT = "NO_RESULT"
    STALE = "STALE"
    VALIDATION_REJECTED = "VALIDATION_REJECTED"


class GuidelineFallbackCode(StrEnum):
    NO_APPROVED_EVIDENCE = "NO_APPROVED_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PRESCRIPTION_STALE = "PRESCRIPTION_STALE"
    EXECUTION_CONTEXT_STALE = "EXECUTION_CONTEXT_STALE"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"


_APPROVED_UNCERTAINTY_TEXT = "승인된 근거 범위 밖의 내용은 확인할 수 없습니다."
_APPROVED_CONSULTATION_TEXT = "불편하거나 궁금한 점은 의사 또는 약사와 상담하세요."
_APPROVED_FALLBACK_TEXT_BY_CODE = {
    code: "현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요." for code in GuidelineFallbackCode
}


class GuidelineCardReason(StrEnum):
    CARD_GENERATED = "CARD_GENERATED"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    EVIDENCE_CONFLICTED = "EVIDENCE_CONFLICTED"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PRESCRIPTION_STALE = "PRESCRIPTION_STALE"
    EXECUTION_CONTEXT_STALE = "EXECUTION_CONTEXT_STALE"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"


class GuidelineGenerationFailure(StrEnum):
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PRESCRIPTION_STALE = "PRESCRIPTION_STALE"
    EXECUTION_CONTEXT_STALE = "EXECUTION_CONTEXT_STALE"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"


@dataclass(frozen=True, slots=True)
class GuidelineApprovalVerificationSuccess:
    artifact_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class GuidelineApprovalVerificationFailure:
    pass


class GuidelineApprovalVerifierPort(Protocol):
    def verify(
        self,
        artifact_ref: ImmutableArtifactRef,
    ) -> GuidelineApprovalVerificationSuccess | GuidelineApprovalVerificationFailure: ...


@dataclass(frozen=True, slots=True)
class MedicationIdentityRef:
    prescription_version_medication_id: str
    code_system: str
    canonical_code: str


@dataclass(frozen=True, slots=True)
class GuidelineCitationDraft:
    """Draft citation restored from production evidence only.

    Source identity is the production Source coordinate verified by #760
    (`source_snapshot_id`, `source_snapshot_member_id`, `source_code`) rather than a
    legacy `source_snapshot_ref` artifact reference, which production evidence does
    not carry and which this kernel must not forge.
    """

    evidence_key: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    locator: str
    content_sha256: str
    retrieval_receipt_ref: ImmutableArtifactRef | None = None


@dataclass(frozen=True, slots=True)
class GuidelineClaimDraft:
    claim_key: str
    medication_identity: MedicationIdentityRef
    scope: GuidelineScope
    action_class: GuidelineActionClass
    action_text: SensitiveText
    citations: tuple[GuidelineCitationDraft, ...]


@dataclass(frozen=True, slots=True)
class GuidelineCardDraft:
    claims: tuple[GuidelineClaimDraft, ...]
    uncertainty_text: SensitiveText
    consultation_text: SensitiveText


def create_canonical_claim_draft(
    *,
    claim_key: str,
    medication_identity: MedicationIdentityRef,
    scope: GuidelineScope,
    citations: tuple[GuidelineCitationDraft, ...],
) -> GuidelineClaimDraft:
    action_class = _ACTION_CLASS_BY_SCOPE[scope]
    action_text = SensitiveText(_ACTION_TEXT_BY_CLASS[action_class])
    return GuidelineClaimDraft(
        claim_key=claim_key,
        medication_identity=medication_identity,
        scope=scope,
        action_class=action_class,
        action_text=action_text,
        citations=citations,
    )


def create_canonical_card_draft(
    claims: tuple[GuidelineClaimDraft, ...],
) -> GuidelineCardDraft:
    return GuidelineCardDraft(
        claims=claims,
        uncertainty_text=SensitiveText(_APPROVED_UNCERTAINTY_TEXT),
        consultation_text=SensitiveText(_APPROVED_CONSULTATION_TEXT),
    )


@dataclass(frozen=True, slots=True)
class GuidelineGenerationProvenance:
    prompt_ref: ImmutableArtifactRef
    model_ref: ImmutableArtifactRef
    parser_ref: ImmutableArtifactRef
    validator_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class VersionedGuidelinePolicy:
    artifact_ref: ImmutableArtifactRef
    maximum_claims: int
    uncertainty_text_sha256: str
    consultation_text_sha256: str

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        *,
        maximum_claims: int,
        uncertainty_text_sha256: str,
        consultation_text_sha256: str,
    ) -> Self:
        payload = {
            "action_classes_by_scope": {scope.value: _ACTION_CLASS_BY_SCOPE[scope].value for scope in GuidelineScope},
            "action_template_hashes": {
                action_class.value: hashlib.sha256(text.encode()).hexdigest()
                for action_class, text in _ACTION_TEXT_BY_CLASS.items()
            },
            "allowed_scopes": [scope.value for scope in GuidelineScope],
            "consultation_text_sha256": consultation_text_sha256,
            "foreign_system_filter": "guideline-foreign-system-filter-v1",
            "forbidden_action_filter": "guideline-forbidden-action-filter-v1",
            "maximum_claims": maximum_claims,
            "uncertainty_text_sha256": uncertainty_text_sha256,
        }
        return cls(
            ImmutableArtifactRef(artifact_code, version, _canonical_sha256(payload)),
            maximum_claims,
            uncertainty_text_sha256,
            consultation_text_sha256,
        )


@dataclass(frozen=True, slots=True)
class ApprovedGuidelineEvidenceBinding:
    artifact_ref: ImmutableArtifactRef
    medication_identity: MedicationIdentityRef
    scope: GuidelineScope
    action_class: GuidelineActionClass
    evidence_key: str
    assessment_artifact_ref: ImmutableArtifactRef
    selection_projection_sha256: str
    action_text_sha256: str

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        *,
        medication_identity: MedicationIdentityRef,
        scope: GuidelineScope,
        action_class: GuidelineActionClass,
        evidence_key: str,
        assessment_artifact_ref: ImmutableArtifactRef,
        selection_projection_sha256: str,
        action_text_sha256: str,
    ) -> Self:
        payload = {
            "assessment_artifact_ref": _artifact_payload(assessment_artifact_ref),
            "action_text_sha256": action_text_sha256,
            "action_class": action_class.value,
            "evidence_key": evidence_key,
            "medication_identity": _medication_payload(medication_identity),
            "selection_projection_sha256": selection_projection_sha256,
            "scope": scope.value,
        }
        return cls(
            ImmutableArtifactRef(artifact_code, version, _canonical_sha256(payload)),
            medication_identity,
            scope,
            action_class,
            evidence_key,
            assessment_artifact_ref,
            selection_projection_sha256,
            action_text_sha256,
        )


@dataclass(frozen=True, slots=True)
class ApprovedGuidelineFallback:
    artifact_ref: ImmutableArtifactRef
    code: GuidelineFallbackCode
    text: SensitiveText

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        *,
        code: GuidelineFallbackCode,
        text: SensitiveText,
    ) -> Self:
        payload = {"code": code.value, "text": text.reveal()}
        return cls(ImmutableArtifactRef(artifact_code, version, _canonical_sha256(payload)), code, text)


def create_canonical_guideline_policy(
    *,
    artifact_code: str,
    version: str,
    maximum_claims: int,
) -> VersionedGuidelinePolicy:
    return VersionedGuidelinePolicy.create(
        artifact_code,
        version,
        maximum_claims=maximum_claims,
        uncertainty_text_sha256=hashlib.sha256(_APPROVED_UNCERTAINTY_TEXT.encode("utf-8")).hexdigest(),
        consultation_text_sha256=hashlib.sha256(_APPROVED_CONSULTATION_TEXT.encode("utf-8")).hexdigest(),
    )


def create_canonical_guideline_fallback(
    *,
    artifact_code: str,
    version: str,
    code: GuidelineFallbackCode,
) -> ApprovedGuidelineFallback:
    return ApprovedGuidelineFallback.create(
        artifact_code,
        version,
        code=code,
        text=SensitiveText(_APPROVED_FALLBACK_TEXT_BY_CODE[code]),
    )


@dataclass(frozen=True, slots=True)
class GuidelineCitation:
    source_type: GuidelineCitationSourceType
    evidence_key: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    locator: str
    content_sha256: str
    assessment_artifact_ref: ImmutableArtifactRef
    eligibility_receipt_ref: ImmutableArtifactRef
    retrieval_receipt_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef
    guideline_evidence_binding_ref: ImmutableArtifactRef
    guideline_evidence_binding_verifier_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class GuidelineClaim:
    claim_key: str
    medication_identity: MedicationIdentityRef
    scope: GuidelineScope
    action_class: GuidelineActionClass
    action_text: SensitiveText
    citations: tuple[GuidelineCitation, ...]


@dataclass(frozen=True, slots=True)
class GuidelineCardProvenance:
    prompt_ref: ImmutableArtifactRef
    model_ref: ImmutableArtifactRef
    parser_ref: ImmutableArtifactRef
    validator_ref: ImmutableArtifactRef
    guideline_policy_ref: ImmutableArtifactRef
    guideline_policy_verifier_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class GuidelineCard:
    artifact_ref: ImmutableArtifactRef
    claims: tuple[GuidelineClaim, ...]
    uncertainty_text: SensitiveText
    consultation_text: SensitiveText
    provenance: GuidelineCardProvenance
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class GuidelineCardRequest:
    medication_identities: tuple[MedicationIdentityRef, ...]
    evidence: ProductionGuidelineEvidenceSet
    draft: GuidelineCardDraft | None
    generation_failure: GuidelineGenerationFailure | None
    policy: VersionedGuidelinePolicy
    provenance: GuidelineGenerationProvenance
    approved_fallbacks: tuple[ApprovedGuidelineFallback, ...]
    evaluated_at: datetime
    approved_evidence_bindings: tuple[ApprovedGuidelineEvidenceBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class VerifiedGuidelineFallback:
    artifact_ref: ImmutableArtifactRef
    code: GuidelineFallbackCode
    text: SensitiveText
    approval_verifier_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class GuidelineCardOutcome:
    status: GuidelineCardStatus
    reason: GuidelineCardReason
    fallback_code: GuidelineFallbackCode | None = None
    card: GuidelineCard | None = None
    fallback: VerifiedGuidelineFallback | None = None


@dataclass(frozen=True, slots=True)
class _VerifiedApprovalContext:
    fallbacks: dict[GuidelineFallbackCode, ApprovedGuidelineFallback]
    verifier_refs: dict[ImmutableArtifactRef, ImmutableArtifactRef]


def finalize_guideline_card(
    request: GuidelineCardRequest,
    *,
    approval_verifier: GuidelineApprovalVerifierPort,
) -> GuidelineCardOutcome:
    """Validate and bind a draft to pinned medication and production evidence."""
    request_snapshot = _detached_request_snapshot(request)
    if request_snapshot is None:
        return _unverified_approval_outcome(dependency_error=False)
    return _finalize_guideline_card_snapshot(request_snapshot, approval_verifier)


def _finalize_guideline_card_snapshot(
    request: GuidelineCardRequest,
    approval_verifier: GuidelineApprovalVerifierPort,
) -> GuidelineCardOutcome:
    context, dependency_error = _verified_fallback_context(request, approval_verifier)
    if context is None:
        return _unverified_approval_outcome(dependency_error=dependency_error)
    if not _is_valid_request_shell(request):
        return _validation_fallback(context)

    execution_verification, dependency_error = _verify_approval_refs(
        (
            request.policy.artifact_ref,
            *(item.artifact_ref for item in request.approved_evidence_bindings),
        ),
        approval_verifier,
    )
    if execution_verification is None:
        if dependency_error:
            return _fallback_outcome(
                context,
                GuidelineCardStatus.NO_RESULT,
                GuidelineCardReason.DEPENDENCY_UNAVAILABLE,
                GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE,
            )
        return _validation_fallback(context)
    context = _VerifiedApprovalContext(
        context.fallbacks,
        {**context.verifier_refs, **execution_verification},
    )

    if request.generation_failure is not None:
        return _generation_failure_outcome(request.generation_failure, context)

    if not _is_bindable_production_evidence(request.evidence, request.evaluated_at):
        return _validation_fallback(context)
    if request.draft is None or not _is_valid_draft_shape(request.draft, request.policy):
        return _validation_fallback(context)

    evidence_by_key = {item.evidence_key: item for item in request.evidence.selections}
    bindings = _validated_evidence_bindings(request.approved_evidence_bindings, evidence_by_key)
    if bindings is None:
        return _validation_fallback(context)
    claims = _bind_claims(request, evidence_by_key, bindings, context.verifier_refs)
    if claims is None:
        return _validation_fallback(context)
    return GuidelineCardOutcome(
        GuidelineCardStatus.GENERATED,
        GuidelineCardReason.CARD_GENERATED,
        card=_create_card(request, claims, context.verifier_refs),
    )


def _detached_request_snapshot(value: object) -> GuidelineCardRequest | None:
    if type(value) is not GuidelineCardRequest:
        return None
    try:
        snapshot = deepcopy(value)
    except Exception:
        return None
    return snapshot if type(snapshot) is GuidelineCardRequest else None


def _verified_fallback_context(
    request: object,
    verifier: GuidelineApprovalVerifierPort,
) -> tuple[_VerifiedApprovalContext | None, bool]:
    if type(request) is not GuidelineCardRequest:
        return None, False
    fallbacks = _validated_fallbacks(request.approved_fallbacks)
    if fallbacks is None:
        return None, False
    verified, dependency_error = _verify_approval_refs(
        tuple(item.artifact_ref for item in fallbacks.values()),
        verifier,
    )
    if verified is None:
        return None, dependency_error
    return _VerifiedApprovalContext(fallbacks, verified), False


def _bind_claims(
    request: GuidelineCardRequest,
    evidence_by_key: dict[str, ProductionGuidelineEvidence],
    bindings: dict[tuple[str, MedicationIdentityRef, GuidelineScope], ApprovedGuidelineEvidenceBinding],
    verifier_refs: dict[ImmutableArtifactRef, ImmutableArtifactRef],
) -> tuple[GuidelineClaim, ...] | None:
    assert request.draft is not None
    medication_identities = set(request.medication_identities)
    claims: list[GuidelineClaim] = []
    for draft_claim in request.draft.claims:
        if draft_claim.medication_identity not in medication_identities:
            return None
        citations = tuple(
            _bind_citation(item, draft_claim, evidence_by_key, bindings, verifier_refs)
            for item in draft_claim.citations
        )
        if any(item is None for item in citations):
            return None
        claims.append(
            GuidelineClaim(
                draft_claim.claim_key,
                _copy_medication_identity(draft_claim.medication_identity),
                draft_claim.scope,
                draft_claim.action_class,
                SensitiveText(draft_claim.action_text.reveal()),
                tuple(item for item in citations if item is not None),
            )
        )
    return tuple(claims)


def _create_card(
    request: GuidelineCardRequest,
    claims: tuple[GuidelineClaim, ...],
    verifier_refs: dict[ImmutableArtifactRef, ImmutableArtifactRef],
) -> GuidelineCard:
    assert request.draft is not None
    provenance = GuidelineCardProvenance(
        _copy_artifact_ref(request.provenance.prompt_ref),
        _copy_artifact_ref(request.provenance.model_ref),
        _copy_artifact_ref(request.provenance.parser_ref),
        _copy_artifact_ref(request.provenance.validator_ref),
        _copy_artifact_ref(request.policy.artifact_ref),
        _copy_artifact_ref(verifier_refs[request.policy.artifact_ref]),
    )
    card_payload = {
        "claims": [_claim_payload(item) for item in claims],
        "consultation_text": request.draft.consultation_text.reveal(),
        "evaluated_at": request.evaluated_at.isoformat(),
        "provenance": _provenance_payload(provenance),
        "uncertainty_text": request.draft.uncertainty_text.reveal(),
    }
    card = GuidelineCard(
        artifact_ref=ImmutableArtifactRef(
            "guideline-card",
            "guideline-card@1",
            _canonical_sha256(card_payload),
        ),
        claims=claims,
        uncertainty_text=SensitiveText(request.draft.uncertainty_text.reveal()),
        consultation_text=SensitiveText(request.draft.consultation_text.reveal()),
        provenance=provenance,
        evaluated_at=request.evaluated_at,
    )
    return card


def _bind_citation(
    draft: GuidelineCitationDraft,
    claim: GuidelineClaimDraft,
    evidence_by_key: dict[str, ProductionGuidelineEvidence],
    bindings: dict[tuple[str, MedicationIdentityRef, GuidelineScope], ApprovedGuidelineEvidenceBinding],
    verifier_refs: dict[ImmutableArtifactRef, ImmutableArtifactRef],
) -> GuidelineCitation | None:
    evidence = evidence_by_key.get(draft.evidence_key)
    binding = bindings.get((draft.evidence_key, claim.medication_identity, claim.scope))
    if evidence is None or binding is None:
        return None
    if (
        hashlib.sha256(claim.action_text.reveal().encode()).hexdigest() != binding.action_text_sha256
        or claim.action_class is not binding.action_class
        or compute_production_guideline_evidence_selection_hash(evidence) != binding.selection_projection_sha256
        or draft.source_snapshot_id != evidence.source_snapshot_id
        or draft.source_snapshot_member_id != evidence.source_snapshot_member_id
        or draft.source_code != evidence.source_code
        or draft.source_version != evidence.source_version
        or draft.locator != evidence.locator
        or draft.content_sha256 != evidence.content_sha256
    ):
        return None
    return GuidelineCitation(
        GuidelineCitationSourceType.LIFESTYLE_GUIDELINE,
        evidence.evidence_key,
        evidence.source_snapshot_id,
        evidence.source_snapshot_member_id,
        evidence.source_code,
        evidence.source_version,
        evidence.locator,
        evidence.content_sha256,
        _copy_artifact_ref(evidence.assessment_artifact_ref),
        _copy_artifact_ref(evidence.eligibility_receipt_ref),
        _copy_artifact_ref(evidence.retrieval_receipt_ref),
        _copy_artifact_ref(evidence.verifier_artifact_ref),
        _copy_artifact_ref(binding.artifact_ref),
        _copy_artifact_ref(verifier_refs[binding.artifact_ref]),
    )


def _is_bindable_production_evidence(
    evidence: ProductionGuidelineEvidenceSet,
    evaluated_at: datetime,
) -> bool:
    """Validate the structural shape of the production evidence handed to this kernel.

    This is a shape check, not an authority re-judgment. #760 already verified the
    Source, member, assessment, content and freshness authority of every selection and
    fails closed before RAG-15 runs, so the kernel must not re-derive
    `assessment_valid_from`/`assessment_valid_until` freshness or re-verify
    `handoff_sha256`. What it does enforce is that the evidence is a non-empty set of
    well-formed, non-duplicated selections whose content matches its own
    `content_sha256`, and that this Card evaluation is the same request evaluation the
    evidence was projected from (`evaluated_at` exact match).

    Legacy `EvidenceGateOutcome` status mapping is deliberately absent: the
    insufficient, conflicted and stale evidence outcomes were RAG-14 Gate states, and
    in the production path an unusable handoff never reaches this kernel at all.
    """
    if (
        type(evidence) is not ProductionGuidelineEvidenceSet
        or not _is_utc_datetime(evidence.evaluated_at)
        or evidence.evaluated_at != evaluated_at
        or not _is_sha256(evidence.handoff_sha256)
        or type(evidence.selections) is not tuple
        or not evidence.selections
    ):
        return False

    evidence_keys: set[str] = set()
    source_coordinates: set[tuple[str, str, str]] = set()
    for item in evidence.selections:
        if not _is_valid_production_evidence_selection(item):
            return False
        coordinate = (str(item.source_snapshot_member_id), item.source_version, item.locator)
        if item.evidence_key in evidence_keys or coordinate in source_coordinates:
            return False
        evidence_keys.add(item.evidence_key)
        source_coordinates.add(coordinate)
    return True


def _is_valid_production_evidence_selection(value: object) -> bool:
    if (
        type(value) is not ProductionGuidelineEvidence
        or not _bounded_nfc(value.evidence_key, 300)
        or type(value.source_snapshot_id) is not UUID
        or type(value.source_snapshot_member_id) is not UUID
        or not _bounded_nfc(value.source_code, 100)
        or not _bounded_nfc(value.source_version, 200)
        or not _bounded_nfc(value.locator, 500)
        or not _is_sha256(value.content_sha256)
        or type(value.content_text) is not SensitiveText
        or not all(
            _is_valid_artifact_ref(artifact_ref)
            for artifact_ref in (
                value.retrieval_receipt_ref,
                value.eligibility_receipt_ref,
                value.assessment_artifact_ref,
                value.verifier_artifact_ref,
            )
        )
    ):
        return False
    content = value.content_text.reveal()
    return _bounded_nfc(content, 10_000) and hashlib.sha256(content.encode()).hexdigest() == value.content_sha256


def _generation_failure_outcome(
    failure: GuidelineGenerationFailure,
    context: _VerifiedApprovalContext,
) -> GuidelineCardOutcome:
    mapping = {
        GuidelineGenerationFailure.PROVIDER_TIMEOUT: (
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.PROVIDER_TIMEOUT,
            GuidelineFallbackCode.PROVIDER_TIMEOUT,
        ),
        GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE: (
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.DEPENDENCY_UNAVAILABLE,
            GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE,
        ),
        GuidelineGenerationFailure.VALIDATION_FAILED: (
            GuidelineCardStatus.VALIDATION_REJECTED,
            GuidelineCardReason.VALIDATION_FAILED,
            GuidelineFallbackCode.VALIDATION_FAILED,
        ),
        GuidelineGenerationFailure.PRESCRIPTION_STALE: (
            GuidelineCardStatus.STALE,
            GuidelineCardReason.PRESCRIPTION_STALE,
            GuidelineFallbackCode.PRESCRIPTION_STALE,
        ),
        GuidelineGenerationFailure.EXECUTION_CONTEXT_STALE: (
            GuidelineCardStatus.STALE,
            GuidelineCardReason.EXECUTION_CONTEXT_STALE,
            GuidelineFallbackCode.EXECUTION_CONTEXT_STALE,
        ),
        GuidelineGenerationFailure.UNSUPPORTED_REQUEST: (
            GuidelineCardStatus.LIMITED,
            GuidelineCardReason.UNSUPPORTED_REQUEST,
            GuidelineFallbackCode.UNSUPPORTED_REQUEST,
        ),
    }
    status, reason, code = mapping[failure]
    return _fallback_outcome(context, status, reason, code)


def _fallback_outcome(
    context: _VerifiedApprovalContext,
    status: GuidelineCardStatus,
    reason: GuidelineCardReason,
    code: GuidelineFallbackCode,
) -> GuidelineCardOutcome:
    approved = context.fallbacks[code]
    return GuidelineCardOutcome(
        status,
        reason,
        code,
        fallback=VerifiedGuidelineFallback(
            _copy_artifact_ref(approved.artifact_ref),
            approved.code,
            SensitiveText(approved.text.reveal()),
            _copy_artifact_ref(context.verifier_refs[approved.artifact_ref]),
        ),
    )


def _validation_fallback(
    context: _VerifiedApprovalContext,
) -> GuidelineCardOutcome:
    return _fallback_outcome(
        context,
        GuidelineCardStatus.VALIDATION_REJECTED,
        GuidelineCardReason.VALIDATION_FAILED,
        GuidelineFallbackCode.VALIDATION_FAILED,
    )


def _is_valid_request_shell(request: object) -> bool:
    return (
        type(request) is GuidelineCardRequest
        and _is_valid_medications(request.medication_identities)
        and type(request.evidence) is ProductionGuidelineEvidenceSet
        and (request.draft is None or type(request.draft) is GuidelineCardDraft)
        and (request.generation_failure is None or type(request.generation_failure) is GuidelineGenerationFailure)
        and _is_valid_policy(request.policy)
        and _is_valid_provenance(request.provenance)
        and _is_utc_datetime(request.evaluated_at)
        and type(request.approved_evidence_bindings) is tuple
        and all(_is_valid_evidence_binding(item) for item in request.approved_evidence_bindings)
    )


def _is_valid_medications(value: object) -> bool:
    if type(value) is not tuple or not value:
        return False
    medication_ids: set[str] = set()
    product_identities: set[tuple[str, str]] = set()
    for item in value:
        if (
            type(item) is not MedicationIdentityRef
            or not _bounded_nfc(item.prescription_version_medication_id, 36)
            or _CANONICAL_UUID_RE.fullmatch(item.prescription_version_medication_id) is None
            or not _bounded_nfc(item.code_system, 100)
            or not _bounded_nfc(item.canonical_code, 200)
            or item.prescription_version_medication_id in medication_ids
            or (item.code_system, item.canonical_code) in product_identities
        ):
            return False
        medication_ids.add(item.prescription_version_medication_id)
        product_identities.add((item.code_system, item.canonical_code))
    return True


def _is_valid_policy(value: object) -> bool:
    if (
        type(value) is not VersionedGuidelinePolicy
        or type(value.maximum_claims) is not int
        or not 1 <= value.maximum_claims <= 100
        or not _is_sha256(value.uncertainty_text_sha256)
        or not _is_sha256(value.consultation_text_sha256)
        or not _is_valid_artifact_ref(value.artifact_ref)
        or value.uncertainty_text_sha256 != hashlib.sha256(_APPROVED_UNCERTAINTY_TEXT.encode()).hexdigest()
        or value.consultation_text_sha256 != hashlib.sha256(_APPROVED_CONSULTATION_TEXT.encode()).hexdigest()
    ):
        return False
    return (
        VersionedGuidelinePolicy.create(
            value.artifact_ref.artifact_code,
            value.artifact_ref.version,
            maximum_claims=value.maximum_claims,
            uncertainty_text_sha256=value.uncertainty_text_sha256,
            consultation_text_sha256=value.consultation_text_sha256,
        ).artifact_ref
        == value.artifact_ref
    )


def _is_valid_provenance(value: object) -> bool:
    return type(value) is GuidelineGenerationProvenance and all(
        _is_valid_artifact_ref(item)
        for item in (value.prompt_ref, value.model_ref, value.parser_ref, value.validator_ref)
    )


def _validated_fallbacks(
    value: object,
) -> dict[GuidelineFallbackCode, ApprovedGuidelineFallback] | None:
    if type(value) is not tuple or len(value) != len(GuidelineFallbackCode):
        return None
    by_code: dict[GuidelineFallbackCode, ApprovedGuidelineFallback] = {}
    for item in value:
        detached = _detached_fallback_snapshot(item)
        if detached is None or not _is_valid_fallback(detached) or detached.code in by_code:
            return None
        by_code[detached.code] = detached
    return by_code if set(by_code) == set(GuidelineFallbackCode) else None


def _detached_fallback_snapshot(value: object) -> ApprovedGuidelineFallback | None:
    if type(value) is not ApprovedGuidelineFallback:
        return None
    try:
        return ApprovedGuidelineFallback(
            _copy_artifact_ref(value.artifact_ref),
            value.code,
            SensitiveText(value.text.reveal()),
        )
    except Exception:
        return None


def _is_valid_fallback(value: object) -> bool:
    if (
        type(value) is not ApprovedGuidelineFallback
        or type(value.code) is not GuidelineFallbackCode
        or type(value.text) is not SensitiveText
        or not _is_valid_artifact_ref(value.artifact_ref)
    ):
        return False
    text = value.text.reveal()
    if (
        not _is_korean_safe_text(text, maximum_length=500)
        or _contains_forbidden_action(text)
        or text != _APPROVED_FALLBACK_TEXT_BY_CODE[value.code]
    ):
        return False
    expected = ApprovedGuidelineFallback.create(
        value.artifact_ref.artifact_code,
        value.artifact_ref.version,
        code=value.code,
        text=SensitiveText(text),
    )
    return expected.artifact_ref == value.artifact_ref


def _validated_evidence_bindings(
    value: object,
    evidence_by_key: dict[str, ProductionGuidelineEvidence],
) -> dict[tuple[str, MedicationIdentityRef, GuidelineScope], ApprovedGuidelineEvidenceBinding] | None:
    if type(value) is not tuple or not value:
        return None
    validated: dict[tuple[str, MedicationIdentityRef, GuidelineScope], ApprovedGuidelineEvidenceBinding] = {}
    for item in value:
        if not _is_valid_evidence_binding(item):
            return None
        evidence = evidence_by_key.get(item.evidence_key)
        if evidence is None or evidence.assessment_artifact_ref != item.assessment_artifact_ref:
            return None
        key = (item.evidence_key, item.medication_identity, item.scope)
        if key in validated:
            return None
        validated[key] = item
    return validated


def _is_valid_evidence_binding(value: object) -> bool:
    if (
        type(value) is not ApprovedGuidelineEvidenceBinding
        or not _is_valid_artifact_ref(value.artifact_ref)
        or not _is_valid_medications((value.medication_identity,))
        or type(value.scope) is not GuidelineScope
        or not _bounded_nfc(value.evidence_key, 300)
        or not _is_valid_artifact_ref(value.assessment_artifact_ref)
        or type(value.action_class) is not GuidelineActionClass
        or value.action_class is not _ACTION_CLASS_BY_SCOPE[value.scope]
        or not _is_sha256(value.selection_projection_sha256)
        or not _is_sha256(value.action_text_sha256)
    ):
        return False
    expected = ApprovedGuidelineEvidenceBinding.create(
        value.artifact_ref.artifact_code,
        value.artifact_ref.version,
        medication_identity=value.medication_identity,
        scope=value.scope,
        action_class=value.action_class,
        evidence_key=value.evidence_key,
        assessment_artifact_ref=value.assessment_artifact_ref,
        selection_projection_sha256=value.selection_projection_sha256,
        action_text_sha256=value.action_text_sha256,
    )
    return expected.artifact_ref == value.artifact_ref


def _is_valid_draft_shape(draft: GuidelineCardDraft, policy: VersionedGuidelinePolicy) -> bool:
    if (
        type(draft.claims) is not tuple
        or not 1 <= len(draft.claims) <= policy.maximum_claims
        or type(draft.uncertainty_text) is not SensitiveText
        or type(draft.consultation_text) is not SensitiveText
        or not _is_korean_safe_text(draft.uncertainty_text.reveal(), maximum_length=500)
        or not _is_korean_safe_text(draft.consultation_text.reveal(), maximum_length=500)
        or _contains_forbidden_action(draft.uncertainty_text.reveal())
        or _contains_forbidden_action(draft.consultation_text.reveal())
        or draft.uncertainty_text.reveal() != _APPROVED_UNCERTAINTY_TEXT
        or draft.consultation_text.reveal() != _APPROVED_CONSULTATION_TEXT
        or hashlib.sha256(draft.uncertainty_text.reveal().encode()).hexdigest() != policy.uncertainty_text_sha256
        or hashlib.sha256(draft.consultation_text.reveal().encode()).hexdigest() != policy.consultation_text_sha256
        or not any(term in draft.consultation_text.reveal() for term in ("의사", "약사", "의료진", "전문가"))
    ):
        return False
    claim_keys: set[str] = set()
    for claim in draft.claims:
        if (
            type(claim) is not GuidelineClaimDraft
            or not _bounded_nfc(claim.claim_key, 100)
            or claim.claim_key in claim_keys
            or type(claim.medication_identity) is not MedicationIdentityRef
            or not _is_valid_medications((claim.medication_identity,))
            or type(claim.scope) is not GuidelineScope
            or type(claim.action_class) is not GuidelineActionClass
            or claim.action_class is not _ACTION_CLASS_BY_SCOPE[claim.scope]
            or type(claim.action_text) is not SensitiveText
            or not _is_korean_safe_text(claim.action_text.reveal(), maximum_length=500)
            or claim.action_text.reveal() != _ACTION_TEXT_BY_CLASS[claim.action_class]
            or _contains_forbidden_action(claim.action_text.reveal())
            or _SCOPE_PATTERNS[claim.scope].search(claim.action_text.reveal()) is None
            or type(claim.citations) is not tuple
            or not claim.citations
        ):
            return False
        evidence_keys: set[str] = set()
        for citation in claim.citations:
            if (
                type(citation) is not GuidelineCitationDraft
                or not _bounded_nfc(citation.evidence_key, 300)
                or citation.evidence_key in evidence_keys
                or type(citation.source_snapshot_id) is not UUID
                or type(citation.source_snapshot_member_id) is not UUID
                or not _bounded_nfc(citation.source_code, 100)
                or not _bounded_nfc(citation.source_version, 200)
                or not _bounded_nfc(citation.locator, 500)
                or not _is_sha256(citation.content_sha256)
            ):
                return False
            evidence_keys.add(citation.evidence_key)
        claim_keys.add(claim.claim_key)
    return True


def _contains_forbidden_action(text: str) -> bool:
    scrubbed = _SAFE_NEGATIVE_ACTION_RE.sub("", text)
    return any(pattern.search(scrubbed) is not None for pattern in _FORBIDDEN_ACTION_RES)


def _is_korean_safe_text(value: str, *, maximum_length: int) -> bool:
    return (
        _bounded_nfc(value, maximum_length)
        and _HANGUL_RE.search(value) is not None
        and _ASCII_ALPHA_RE.search(value) is None
        and _FOREIGN_SYSTEM_RE.search(value) is None
    )


def _is_valid_artifact_ref(value: object) -> bool:
    return (
        type(value) is ImmutableArtifactRef
        and _bounded_nfc(value.artifact_code, 100)
        and _bounded_nfc(value.version, 200)
        and _is_sha256(value.content_sha256)
    )


def _bounded_nfc(value: object, maximum_length: int) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum_length
        and unicodedata.normalize("NFC", value) == value
        and all(ord(character) >= 0x20 and character not in "\u007f\u2028\u2029" for character in value)
    )


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _is_utc_datetime(value: object) -> bool:
    return type(value) is datetime and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _verify_approval_refs(
    refs: tuple[ImmutableArtifactRef, ...],
    verifier: GuidelineApprovalVerifierPort,
) -> tuple[dict[ImmutableArtifactRef, ImmutableArtifactRef] | None, bool]:
    """Return verifier evidence for every unique ref, failing closed on adapter faults."""
    try:
        if not refs or not hasattr(verifier, "verify"):
            return None, False
        verified: dict[ImmutableArtifactRef, ImmutableArtifactRef] = {}
        for artifact_ref in dict.fromkeys(refs):
            if not _is_valid_artifact_ref(artifact_ref):
                return None, False
            verifier_input = deepcopy(artifact_ref)
            expected_input = deepcopy(verifier_input)
            response = deepcopy(verifier.verify(verifier_input))
            if verifier_input != expected_input:
                return None, True
            if type(response) is GuidelineApprovalVerificationFailure:
                return None, False
            if (
                type(response) is not GuidelineApprovalVerificationSuccess
                or response.artifact_ref != artifact_ref
                or not _is_valid_artifact_ref(response.verifier_artifact_ref)
            ):
                return None, True
            verified[artifact_ref] = response.verifier_artifact_ref
        return verified, False
    except Exception:
        return None, True


def _unverified_approval_outcome(*, dependency_error: bool) -> GuidelineCardOutcome:
    if dependency_error:
        return GuidelineCardOutcome(
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.DEPENDENCY_UNAVAILABLE,
            GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE,
        )
    return GuidelineCardOutcome(
        GuidelineCardStatus.VALIDATION_REJECTED,
        GuidelineCardReason.VALIDATION_FAILED,
        GuidelineFallbackCode.VALIDATION_FAILED,
    )


def _claim_payload(claim: GuidelineClaim) -> dict[str, object]:
    return {
        "action_class": claim.action_class.value,
        "action_text": claim.action_text.reveal(),
        "citations": [
            {
                "assessment_artifact_ref": _artifact_payload(item.assessment_artifact_ref),
                "content_sha256": item.content_sha256,
                "eligibility_receipt_ref": _artifact_payload(item.eligibility_receipt_ref),
                "guideline_evidence_binding_ref": _artifact_payload(item.guideline_evidence_binding_ref),
                "guideline_evidence_binding_verifier_ref": _artifact_payload(
                    item.guideline_evidence_binding_verifier_ref
                ),
                "source_type": item.source_type.value,
                "evidence_key": item.evidence_key,
                "locator": item.locator,
                "retrieval_receipt_ref": _artifact_payload(item.retrieval_receipt_ref),
                "source_code": item.source_code,
                "source_snapshot_id": str(item.source_snapshot_id),
                "source_snapshot_member_id": str(item.source_snapshot_member_id),
                "source_version": item.source_version,
                "verifier_artifact_ref": _artifact_payload(item.verifier_artifact_ref),
            }
            for item in claim.citations
        ],
        "claim_key": claim.claim_key,
        "medication_identity": {
            "canonical_code": claim.medication_identity.canonical_code,
            "code_system": claim.medication_identity.code_system,
            "prescription_version_medication_id": claim.medication_identity.prescription_version_medication_id,
        },
        "scope": claim.scope.value,
    }


def _provenance_payload(value: GuidelineCardProvenance) -> dict[str, object]:
    return {
        "guideline_policy_ref": _artifact_payload(value.guideline_policy_ref),
        "guideline_policy_verifier_ref": _artifact_payload(value.guideline_policy_verifier_ref),
        "model_ref": _artifact_payload(value.model_ref),
        "parser_ref": _artifact_payload(value.parser_ref),
        "prompt_ref": _artifact_payload(value.prompt_ref),
        "validator_ref": _artifact_payload(value.validator_ref),
    }


def _artifact_payload(value: ImmutableArtifactRef) -> dict[str, str]:
    return {
        "artifact_code": value.artifact_code,
        "content_sha256": value.content_sha256,
        "version": value.version,
    }


def _medication_payload(value: MedicationIdentityRef) -> dict[str, str]:
    return {
        "canonical_code": value.canonical_code,
        "code_system": value.code_system,
        "prescription_version_medication_id": value.prescription_version_medication_id,
    }


def _copy_artifact_ref(value: ImmutableArtifactRef) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(value.artifact_code, value.version, value.content_sha256)


def _copy_medication_identity(value: MedicationIdentityRef) -> MedicationIdentityRef:
    return MedicationIdentityRef(
        value.prescription_version_medication_id,
        value.code_system,
        value.canonical_code,
    )


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
