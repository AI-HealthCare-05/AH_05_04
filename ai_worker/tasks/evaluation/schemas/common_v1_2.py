from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BeforeValidator, model_validator

from ai_worker.tasks.evaluation.schemas.common import (
    ActorNamespace,
    ExternalMedicalReviewStatus,
    ImmutableReference,
    StableId,
    StrictContractModel,
    TeamGoldStatus,
    UtcTimestamp,
    _enum_from_wire,
    _immutable_reference_sort_key,
)


class ActorRoleV12(StrEnum):
    EVALUATION_IMPLEMENTER = "EVALUATION_IMPLEMENTER"
    DATASET_CUSTODIAN = "DATASET_CUSTODIAN"
    PRODUCT_SAFETY_REVIEWER = "PRODUCT_SAFETY_REVIEWER"
    MEDICAL_REVIEWER = "MEDICAL_REVIEWER"
    PRIVACY_REVIEWER = "PRIVACY_REVIEWER"
    SYSTEM_VALIDATOR = "SYSTEM_VALIDATOR"
    EVALUATION_REVIEWER = "EVALUATION_REVIEWER"


class ActorRefV12(StrictContractModel):
    namespace: Annotated[
        ActorNamespace,
        BeforeValidator(lambda value: _enum_from_wire(ActorNamespace, value)),
    ]
    actor_id: StableId
    role: Annotated[
        ActorRoleV12,
        BeforeValidator(lambda value: _enum_from_wire(ActorRoleV12, value)),
    ]

    @property
    def identity(self) -> tuple[str, str]:
        return (self.namespace, self.actor_id)


class ReviewProvenanceV12(StrictContractModel):
    authored_by: ActorRefV12
    reviewed_by: ActorRefV12 | None
    approved_by: ActorRefV12 | None
    authored_at: UtcTimestamp
    reviewed_at: UtcTimestamp | None
    approved_at: UtcTimestamp | None
    team_gold_status: Annotated[
        TeamGoldStatus,
        BeforeValidator(lambda value: _enum_from_wire(TeamGoldStatus, value)),
    ]
    external_medical_review_status: Annotated[
        ExternalMedicalReviewStatus,
        BeforeValidator(lambda value: _enum_from_wire(ExternalMedicalReviewStatus, value)),
    ]
    external_medical_approval_receipt_ref: ImmutableReference | None
    evidence_review_refs: Annotated[
        tuple[ImmutableReference, ...],
        BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value),
    ]

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        self._validate_actor_rules()
        self._validate_team_gold_rules()
        self._validate_external_review_rules()
        self._validate_evidence_refs()
        return self

    def _validate_actor_rules(self) -> None:
        actors = [self.authored_by]
        if self.reviewed_by is not None:
            actors.append(self.reviewed_by)
        if self.approved_by is not None:
            actors.append(self.approved_by)
        identities = [actor.identity for actor in actors]
        if len(identities) != len(set(identities)):
            raise ValueError("author, reviewer, and approver must be different actors")
        if any(
            actor.namespace is ActorNamespace.SYSTEM or actor.role is ActorRoleV12.SYSTEM_VALIDATOR for actor in actors
        ):
            raise ValueError("human review provenance cannot use system actors")
        if self.approved_by is not None and self.approved_by.role is ActorRoleV12.EVALUATION_IMPLEMENTER:
            raise ValueError("an evaluation implementer cannot approve review provenance")

    def _validate_team_gold_rules(self) -> None:
        self._validate_event_pairs()
        if self.team_gold_status is TeamGoldStatus.DRAFT:
            self._validate_draft_state()
            return
        self._validate_completed_review()
        if self.team_gold_status is TeamGoldStatus.REVIEWED:
            self._validate_reviewed_state()
            return
        self._validate_approved_state()

    def _validate_event_pairs(self) -> None:
        if (self.reviewed_by is None) != (self.reviewed_at is None):
            raise ValueError("review provenance requires reviewer and review time together")
        if (self.approved_by is None) != (self.approved_at is None):
            raise ValueError("approval provenance requires approver and approval time together")

    def _validate_draft_state(self) -> None:
        if self.reviewed_by is not None or self.approved_by is not None or self.evidence_review_refs:
            raise ValueError("draft provenance must not carry review or approval evidence")

    def _validate_completed_review(self) -> None:
        if self.reviewed_by is None or self.reviewed_at is None:
            raise ValueError("reviewed provenance requires a reviewer and review time")
        if self.authored_at > self.reviewed_at:
            raise ValueError("authored_at must not be after reviewed_at")
        if not self.evidence_review_refs:
            raise ValueError("reviewed provenance requires immutable review evidence")

    def _validate_reviewed_state(self) -> None:
        if self.approved_by is not None:
            raise ValueError("reviewed provenance must not carry approval fields")

    def _validate_approved_state(self) -> None:
        if self.approved_by is None or self.approved_at is None:
            raise ValueError("approved provenance requires an approver and approval time")
        if self.reviewed_at is None:
            raise ValueError("approved provenance requires a review time")
        if self.reviewed_at > self.approved_at:
            raise ValueError("reviewed_at must not be after approved_at")

    def _validate_external_review_rules(self) -> None:
        if self.external_medical_review_status is ExternalMedicalReviewStatus.APPROVED:
            if self.external_medical_approval_receipt_ref is None:
                raise ValueError("external medical approval requires an immutable receipt")
        elif self.external_medical_approval_receipt_ref is not None:
            raise ValueError("non-approved external review must not carry an approval receipt")

    def _validate_evidence_refs(self) -> None:
        keys = [_immutable_reference_sort_key(reference) for reference in self.evidence_review_refs]
        if len(keys) != len(set(keys)) or keys != sorted(keys):
            raise ValueError("evidence review references must be unique and sorted")
