from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MedicationCandidateSearchStatus(StrEnum):
    RUNNING = "RUNNING"
    READY = "READY"
    AMBIGUOUS = "AMBIGUOUS"
    NO_CANDIDATE = "NO_CANDIDATE"
    INGREDIENT_ONLY = "INGREDIENT_ONLY"
    INVALID_INPUT = "INVALID_INPUT"
    INVALIDATED_INPUT_CHANGED = "INVALIDATED_INPUT_CHANGED"
    INVALIDATED_USER_REJECTED = "INVALIDATED_USER_REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    CONSUMED = "CONSUMED"


class MedicationIdentificationStatus(StrEnum):
    MATCHED = "MATCHED"
    UNRESOLVED = "UNRESOLVED"


class MedicationIdentificationSource(StrEnum):
    USER_SELECTED = "USER_SELECTED"
    USER_REJECTED = "USER_REJECTED"


class CreateMedicationCandidateSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prescription_version_medication_id: UUID


class MedicationCandidateSnapshot(BaseModel):
    product_name: str
    strength_text: str | None = None
    dosage_form: str | None = None
    manufacturer_name: str | None = None
    product_status: str


class MedicationCandidateSearchData(BaseModel):
    search_id: UUID
    prescription_version_medication_id: UUID
    medication_index: int
    status: MedicationCandidateSearchStatus
    candidate_search_result_id: UUID | None
    candidate: MedicationCandidateSnapshot | None
    expires_at: datetime | None


class MedicationCandidateSearchResponse(BaseModel):
    data: MedicationCandidateSearchData


class ConfirmMedicationCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prescription_version_medication_id: UUID
    candidate_search_result_id: UUID


class RejectMedicationCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: UUID
    candidate_search_result_id: UUID


class ConfirmMedicationCandidateData(BaseModel):
    identification_id: UUID
    prescription_version_medication_id: UUID
    status: MedicationIdentificationStatus
    source: MedicationIdentificationSource
    product_id: UUID
    confirmed_at: datetime


class ConfirmMedicationCandidateResponse(BaseModel):
    data: ConfirmMedicationCandidateData


class RejectMedicationCandidateData(BaseModel):
    identification_event_id: UUID
    prescription_version_medication_id: UUID
    status: MedicationIdentificationStatus
    search_status: MedicationCandidateSearchStatus
    rejected_at: datetime


class RejectMedicationCandidateResponse(BaseModel):
    data: RejectMedicationCandidateData
