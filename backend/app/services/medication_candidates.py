from datetime import datetime
from typing import Any
from uuid import UUID

from app.core import config
from app.core.errors import ApiError, ErrorDetail
from app.dtos.medication_candidates import (
    ConfirmMedicationCandidateData,
    ConfirmMedicationCandidateRequest,
    MedicationCandidateSearchData,
    MedicationCandidateSearchStatus,
    MedicationCandidateSnapshot,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
    RejectMedicationCandidateData,
    RejectMedicationCandidateRequest,
)
from app.models.rag_candidate import MedicationCandidateSearchStatus as ModelCandidateSearchStatus
from app.models.users import User
from app.repositories.medication_candidate_repository import MedicationCandidateRepository
from app.services.idempotency import RUNTIME_RELEASE_BUNDLE_PLACEHOLDER, SyncMutationIdempotencyService
from app.services.medication_identification import MedicationIdentificationService

# idempotency-v1.md "동기 상태 변경 처리 규칙": F Candidate 확인·거절의
# OpenAPI operation_id와 멱등성 operation_id는 같은 값을 사용한다.
MEDICATION_CANDIDATE_CONFIRM_OPERATION_ID = "medication-candidate.confirm"
MEDICATION_CANDIDATE_REJECT_OPERATION_ID = "medication-candidate.reject"


class MedicationCandidateService:
    """RAG-09(MedicationIdentificationService)의 persistence를 RAG-10 공개 계약
    (medication-identification-v1.md)의 DTO로 변환하는 service adapter입니다. 이 계층은
    자체 domain mutation을 수행하지 않고 조회·확인·거절을 RAG-09 repository/service에 위임합니다.

    확인·거절은 idempotency-v1.md "동기 상태 변경 처리 규칙"(SYNC_MUTATION)이 적용되는
    Track F 요청이라, 실제 mutation을 `SyncMutationIdempotencyService`가 감싼다."""

    def __init__(
        self,
        repository: MedicationCandidateRepository,
        identification_service: MedicationIdentificationService,
        idempotency_service: SyncMutationIdempotencyService,
    ) -> None:
        self._repository = repository
        self._identification_service = identification_service
        self._idempotency_service = idempotency_service

    async def get_candidate_search(
        self,
        *,
        user: User,
        prescription_version_medication_id: UUID,
    ) -> MedicationCandidateSearchData:
        medication = await self._repository.get_medication_owned(
            prescription_version_medication_id=prescription_version_medication_id,
            user_id=user.id,
        )
        if medication is None:
            raise ApiError(
                status_code=404,
                code="PRESCRIPTION_MEDICATION_NOT_FOUND",
                message="처방 약제를 찾을 수 없습니다.",
                details=[ErrorDetail(field="prescription_version_medication_id", reason="NOT_FOUND")],
            )

        search = await self._repository.get_latest_search_for_medication(
            prescription_version_medication_id=prescription_version_medication_id,
        )
        if search is None:
            raise ApiError(
                status_code=404,
                code="CANDIDATE_SEARCH_NOT_FOUND",
                message="약품 후보 검색 결과를 찾을 수 없습니다.",
                details=[ErrorDetail(field="prescription_version_medication_id", reason="NOT_FOUND")],
            )

        # READY지만 만료 시각이 지난 Search는, 다른 lifecycle 요청(확인·거절)이 아직 DB 상태를
        # EXPIRED로 전환하기 전이라도 조회에서는 이미 만료된 것으로 투영한다. 계약상 EXPIRED는
        # candidate_search_result_id/candidate가 모두 null이어야 한다(#312 리뷰 지적).
        now = datetime.now(config.TIMEZONE)
        is_expired = (
            search.status == ModelCandidateSearchStatus.READY
            and search.expires_at is not None
            and search.expires_at <= now
        )
        public_status = ModelCandidateSearchStatus.EXPIRED if is_expired else search.status

        candidate_search_result_id: UUID | None = None
        candidate: MedicationCandidateSnapshot | None = None
        # READY가 아니면 과거 표시 이력이 남아 있어도(#260/계약 111행) 공개 후보를 반환하지 않는다.
        if public_status == ModelCandidateSearchStatus.READY:
            displayed = await self._repository.get_displayed_result_for_search(search_id=search.id)
            if displayed is not None:
                # chk_medication_candidate_result_display_snapshot가 is_displayed=true일 때
                # product_name/product_status non-null을 보장한다.
                assert displayed.product_name is not None
                assert displayed.product_status is not None
                candidate_search_result_id = displayed.id
                candidate = MedicationCandidateSnapshot(
                    product_name=displayed.product_name,
                    strength_text=displayed.strength_text,
                    dosage_form=displayed.dosage_form,
                    manufacturer_name=displayed.manufacturer_name,
                    product_status=displayed.product_status,
                )

        return MedicationCandidateSearchData(
            search_id=search.id,
            prescription_version_medication_id=search.prescription_version_medication_id,
            medication_index=medication.display_order,
            status=MedicationCandidateSearchStatus(public_status),
            candidate_search_result_id=candidate_search_result_id,
            candidate=candidate,
            expires_at=search.expires_at,
        )

    async def confirm_candidate(
        self,
        *,
        user: User,
        request: ConfirmMedicationCandidateRequest,
        idempotency_key: str,
    ) -> ConfirmMedicationCandidateData:
        # 계약: F Candidate 확인·거절의 parent_resource_id는 prescription_version_medication_id로
        # 고정된다. confirm은 request body에 이미 그 값이 있어 별도 조회가 필요 없다.
        async def mutate() -> dict[str, Any]:
            identification = await self._identification_service.confirm_identification(
                prescription_version_medication_id=request.prescription_version_medication_id,
                candidate_search_result_id=request.candidate_search_result_id,
                user_id=user.id,
            )
            # chk_medication_identification_matched_payload가 MATCHED일 때
            # product_id/confirmed_at non-null을 보장한다.
            assert identification.product_id is not None
            assert identification.confirmed_at is not None
            data = ConfirmMedicationCandidateData(
                identification_id=identification.id,
                prescription_version_medication_id=identification.prescription_version_medication_id,
                status=MedicationIdentificationStatus(identification.status),
                source=MedicationIdentificationSource(identification.source),
                product_id=identification.product_id,
                confirmed_at=identification.confirmed_at,
            )
            return data.model_dump(mode="json")

        result = await self._idempotency_service.execute(
            user_id=user.id,
            operation_id=MEDICATION_CANDIDATE_CONFIRM_OPERATION_ID,
            parent_resource_id=request.prescription_version_medication_id,
            idempotency_key=idempotency_key,
            fingerprint={
                "action": "confirm",
                "candidate_search_result_id": str(request.candidate_search_result_id),
                "runtime_release_bundle": RUNTIME_RELEASE_BUNDLE_PLACEHOLDER,
            },
            success_status=200,
            mutate=mutate,
        )
        return ConfirmMedicationCandidateData.model_validate(result.response_body)

    async def reject_candidate(
        self,
        *,
        user: User,
        request: RejectMedicationCandidateRequest,
        idempotency_key: str,
    ) -> RejectMedicationCandidateData:
        # 거절 요청 body에는 prescription_version_medication_id가 직접 없어, 멱등성 scope를
        # 계산하기 전에 미리 조회해서 도출해야 한다(놓치기 쉬운 지점, #311).
        parent_resource_id = await self._resolve_reject_parent_resource_id(user=user, request=request)

        async def mutate() -> dict[str, Any]:
            identification = await self._identification_service.reject_identification(
                search_id=request.search_id,
                candidate_search_result_id=request.candidate_search_result_id,
                user_id=user.id,
            )
            assert identification.rejected_at is not None
            data = RejectMedicationCandidateData(
                identification_event_id=identification.id,
                prescription_version_medication_id=identification.prescription_version_medication_id,
                status=MedicationIdentificationStatus(identification.status),
                # reject_identification 성공은 항상 Search를 INVALIDATED_USER_REJECTED로 전환한다
                # (계약 124행). 별도 조회 없이 이 불변식으로 채운다.
                search_status=MedicationCandidateSearchStatus.INVALIDATED_USER_REJECTED,
                rejected_at=identification.rejected_at,
            )
            return data.model_dump(mode="json")

        result = await self._idempotency_service.execute(
            user_id=user.id,
            operation_id=MEDICATION_CANDIDATE_REJECT_OPERATION_ID,
            parent_resource_id=parent_resource_id,
            idempotency_key=idempotency_key,
            fingerprint={
                "action": "reject",
                "search_id": str(request.search_id),
                "candidate_search_result_id": str(request.candidate_search_result_id),
                "runtime_release_bundle": RUNTIME_RELEASE_BUNDLE_PLACEHOLDER,
            },
            success_status=200,
            mutate=mutate,
        )
        return RejectMedicationCandidateData.model_validate(result.response_body)

    async def _resolve_reject_parent_resource_id(
        self,
        *,
        user: User,
        request: RejectMedicationCandidateRequest,
    ) -> UUID:
        """계약: F Candidate 확인·거절 둘 다 parent_resource_id는
        prescription_version_medication_id로 고정된다. `reject_identification`이 내부적으로
        쓰는 것과 같은 조회(`get_result_selection_for_update_owned`)를 재사용해, 같은 검증
        (선택한 결과가 요청한 search_id에 속하는지)을 먼저 적용한다 — 같은 트랜잭션이라 같은
        행에 대한 재조회·재잠금은 안전하다."""
        selection = await self._repository.get_result_selection_for_update_owned(
            candidate_search_result_id=request.candidate_search_result_id,
            user_id=user.id,
        )
        if selection is None or selection.search.id != request.search_id:
            raise ApiError(
                status_code=404,
                code="CANDIDATE_SEARCH_NOT_FOUND",
                message="약품 후보 검색 결과를 찾을 수 없습니다.",
                details=[ErrorDetail(field="candidate_search_result_id", reason="NOT_FOUND")],
            )
        return selection.search.prescription_version_medication_id
