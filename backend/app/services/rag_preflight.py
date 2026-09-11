from dataclasses import dataclass
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.models.rag_candidate import MedicationIdentification
from app.repositories.rag_preflight_repository import RagPreflightRepository


@dataclass(frozen=True)
class RagPreflightMatchedMedication:
    prescription_version_medication_id: UUID
    medication_identification_id: UUID


@dataclass(frozen=True)
class RagPreflightResult:
    prescription_id: UUID
    prescription_version_id: UUID
    matched_medications: tuple[RagPreflightMatchedMedication, ...]


class RagPreflightService:
    def __init__(self, repository: RagPreflightRepository) -> None:
        self._repository = repository

    async def ensure_all_active_medications_matched(
        self,
        *,
        prescription_id: UUID,
        user_id: UUID,
        expected_prescription_version_id: UUID | None = None,
    ) -> RagPreflightResult:
        prescription = await self._repository.lock_active_prescription_owned(
            prescription_id=prescription_id,
            user_id=user_id,
        )
        if prescription is None:
            raise self._not_found_error(field="prescription_id")

        if (
            expected_prescription_version_id is not None
            and prescription.active_version_id != expected_prescription_version_id
        ):
            raise self._version_conflict_error()

        version = await self._repository.lock_prescription_version_for_update(
            prescription_version_id=prescription.active_version_id,
        )
        if version is None:
            raise self._version_conflict_error()

        medications = await self._repository.list_active_version_medications_for_update(
            prescription_version_id=prescription.active_version_id,
        )
        if not medications:
            raise self._identification_incomplete_error(reason="ACTIVE_MEDICATION_REQUIRED")

        matched = await self._repository.list_matched_identifications_for_update(
            prescription_version_medication_ids=[medication.id for medication in medications],
        )
        matched_by_medication = self._matched_by_medication(matched)
        missing_medication_ids = [
            medication.id for medication in medications if medication.id not in matched_by_medication
        ]
        if missing_medication_ids:
            raise self._identification_incomplete_error(reason="MATCHED_IDENTIFICATION_REQUIRED")

        return RagPreflightResult(
            prescription_id=prescription.id,
            prescription_version_id=prescription.active_version_id,
            matched_medications=tuple(
                RagPreflightMatchedMedication(
                    prescription_version_medication_id=medication.id,
                    medication_identification_id=matched_by_medication[medication.id].id,
                )
                for medication in medications
            ),
        )

    @staticmethod
    def _matched_by_medication(
        identifications: list[MedicationIdentification],
    ) -> dict[UUID, MedicationIdentification]:
        return {identification.prescription_version_medication_id: identification for identification in identifications}

    @staticmethod
    def _not_found_error(*, field: str) -> ApiError:
        return ApiError(
            status_code=404,
            code="PRESCRIPTION_NOT_FOUND",
            message="처방 정보를 찾을 수 없습니다.",
            details=[ErrorDetail(field=field, reason="NOT_FOUND")],
        )

    @staticmethod
    def _version_conflict_error() -> ApiError:
        return ApiError(
            status_code=409,
            code="PRESCRIPTION_VERSION_CONFLICT",
            message="처방 버전이 최신 상태가 아닙니다. 최신 처방 상태를 다시 확인해 주세요.",
            details=[ErrorDetail(field="prescription_version_id", reason="STALE")],
        )

    @staticmethod
    def _identification_incomplete_error(*, reason: str) -> ApiError:
        return ApiError(
            status_code=409,
            code="PRESCRIPTION_MEDICATION_IDENTIFICATION_INCOMPLETE",
            message="약품 확인이 완료되지 않아 다음 단계를 진행할 수 없습니다.",
            details=[ErrorDetail(field="prescription_id", reason=reason)],
        )
