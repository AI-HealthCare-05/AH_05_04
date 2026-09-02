from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ApiError
from app.dtos.jobs import JobErrorData, JobStatusData
from app.models.async_jobs import AiJob, AiJobStatus, DomainType
from app.models.users import User
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.guide_repository import GuideRepository
from app.repositories.ocr_repository import OcrRepository

# async-job-v1.md "시도와 재시도"의 7개 failure_code 각각에 대한 안전한 사용자 노출 메시지입니다.
# attempt_count, progress, failure_detail, Provider 원문 오류는 외부 응답에 포함하지 않습니다.
# ai_worker.core.errors.SAFE_MESSAGE_BY_FAILURE_CODE와 대상 독자가 달라(Worker 내부 대 API
# 응답) 문구는 공유하지 않지만, key 집합은 test_failure_messages_cover_every_allowed_failure_code로
# _FAILURE_CODE_VALUES와 동기화된 상태를 고정합니다.
_FAILURE_MESSAGES: dict[str, str] = {
    "TIMEOUT": "처리 시간이 초과되어 작업이 실패했습니다.",
    "DEPENDENCY_UNAVAILABLE": "일시적으로 서비스를 이용할 수 없어 작업이 실패했습니다.",
    "INVALID_INPUT": "요청 내용이 올바르지 않아 작업이 실패했습니다.",
    "UNSUPPORTED_SCHEMA": "지원하지 않는 형식이라 작업이 실패했습니다.",
    "SAFETY_VALIDATION_FAILED": "안전성 검증을 통과하지 못해 작업이 실패했습니다.",
    "RETRY_EXHAUSTED": "재시도 횟수를 모두 사용해 작업이 실패했습니다.",
    "INTERNAL_ERROR": "내부 오류로 작업이 실패했습니다.",
}


def _job_not_found_error() -> ApiError:
    return ApiError(
        status_code=404,
        code="AI_JOB_NOT_FOUND",
        message="작업 정보를 찾을 수 없습니다.",
    )


@dataclass(frozen=True)
class JobStatusResult:
    data: JobStatusData
    # RETRY_WAIT일 때만 값이 있고, 라우트가 이 값으로 Retry-After 헤더를 설정합니다.
    retry_after_seconds: int | None


class JobStatusService:
    """`GET /jobs/{job_id}` 공통 조회(async-job-v1.md)를 구현합니다.

    `domain_type`/`domain_id`는 임시로 `AsyncJobRepository.get_interim_domain_reference()`를
    통해 얻습니다 — 그 메서드 docstring에 이유가 있습니다(정본은 도메인 row의 `ai_job_id`
    역참조지만 아직 없음).

    소유권은 §6(track-a-migration-rollback-v1.md)에 따라 두 단계로 확인합니다:
    (1) `ai_job.user_id`로 1차 필터링, (2) 도메인 row의 `profile_id` chain(SELF profile
    기준)으로 재확인. 두 기준이 어긋나거나 도메인 row 자체가 없으면 fail-closed `404`입니다.
    """

    def __init__(
        self,
        *,
        job_repository: AsyncJobRepository,
        ocr_repository: OcrRepository,
        guide_repository: GuideRepository,
        chat_repository: ChatRepository,
    ) -> None:
        self.job_repository = job_repository
        self.ocr_repository = ocr_repository
        self.guide_repository = guide_repository
        self.chat_repository = chat_repository

    async def get_job_status(self, *, user: User, job_id: UUID) -> JobStatusResult:
        job = await self.job_repository.get_job(job_id=job_id)
        if job is None or job.user_id != user.id:
            raise _job_not_found_error()

        domain_reference = await self.job_repository.get_interim_domain_reference(job=job)
        if domain_reference is None:
            raise _job_not_found_error()
        domain_type, domain_id = domain_reference

        result_url = await self._resolve_owned_result_url(
            user=user,
            job=job,
            domain_type=domain_type,
            domain_id=domain_id,
        )

        retry_after_seconds = _compute_retry_after_seconds(job)
        data = JobStatusData(
            job_id=job.id,
            job_type=job.job_type,
            status=job.status,
            domain_type=domain_type,
            domain_id=domain_id,
            prescription_version_id=job.prescription_version_id,
            status_url=f"/api/v1/jobs/{job.id}",
            result_url=result_url,
            retry_after_seconds=retry_after_seconds,
            error=_build_error(job),
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        return JobStatusResult(data=data, retry_after_seconds=retry_after_seconds)

    async def rediscover_ocr_job(self, *, user: User, document_id: UUID) -> JobStatusResult:
        """async-job-v1.md "공통 화면 재접속 복구": 화면을 재진입해도 새 Job을 접수하지 않고
        가장 최근 Job의 polling을 재개할 수 있도록, 문서의 가장 최근 OCR Job을 찾아 그 Job의
        상태를 그대로 돌려줍니다(`get_job_status`와 동일한 응답)."""
        ocr_job = await self.ocr_repository.get_latest_job_for_document_owned(document_id=document_id, user_id=user.id)
        if ocr_job is None:
            raise _job_not_found_error()
        job_id = await self.job_repository.get_latest_job_id_for_domain(
            domain_type=DomainType.OCR_JOB, domain_id=ocr_job.id
        )
        if job_id is None:
            raise _job_not_found_error()
        return await self.get_job_status(user=user, job_id=job_id)

    async def rediscover_guide_job(self, *, user: User, prescription_id: UUID) -> JobStatusResult:
        """`rediscover_ocr_job`과 같은 목적으로, 처방의 가장 최근 Guide Job을 찾아 돌려줍니다.

        prescription_id당 non-terminal Guide Job 1개 제약(`409 GUIDE_JOB_IN_PROGRESS`)은
        Guide 접수(`POST /guides`)가 `accept_job()`에 실제로 연결될 때 함께 구현합니다 — 팀
        결정으로 그 연결 자체가 Publisher·Worker·Handler 준비 전까지 보류돼 있어(#148), 이
        rediscovery만 먼저 준비해둡니다.
        """
        guide = await self.guide_repository.get_latest_for_prescription_owned(
            prescription_id=prescription_id, user_id=user.id
        )
        if guide is None:
            raise _job_not_found_error()
        job_id = await self.job_repository.get_latest_job_id_for_domain(
            domain_type=DomainType.GUIDE, domain_id=guide.id
        )
        if job_id is None:
            raise _job_not_found_error()
        return await self.get_job_status(user=user, job_id=job_id)

    async def _resolve_owned_result_url(
        self,
        *,
        user: User,
        job: AiJob,
        domain_type: DomainType,
        domain_id: UUID,
    ) -> str | None:
        """클래스 docstring의 §6 이중 확인을 실행하는 자리입니다. `result_url` 자체는
        `job.status`가 `COMPLETED`일 때만 채우지만, 도메인 row 조회는 상태와 무관하게
        항상 수행합니다."""
        if domain_type is DomainType.OCR_JOB:
            ocr_job = await self.ocr_repository.get_job_owned(job_id=domain_id, user_id=user.id)
            if ocr_job is None:
                raise _job_not_found_error()
            return f"/api/v1/ocr-jobs/{domain_id}" if job.status is AiJobStatus.COMPLETED else None

        if domain_type is DomainType.GUIDE:
            guide = await self.guide_repository.get_owned(guide_id=domain_id, user_id=user.id)
            if guide is None:
                raise _job_not_found_error()
            return f"/api/v1/guides/{domain_id}" if job.status is AiJobStatus.COMPLETED else None

        # DomainType.CHAT_MESSAGE — domain_id는 ASSISTANT chat_message.id입니다. Chat의
        # result_url은 단건이 아니라 메시지 목록 조회라 chat_message.session_id가 필요합니다.
        chat_message = await self.chat_repository.get_message_owned(message_id=domain_id, user_id=user.id)
        if chat_message is None:
            raise _job_not_found_error()
        if job.status is not AiJobStatus.COMPLETED:
            return None
        return f"/api/v1/chat-sessions/{chat_message.session_id}/messages"


def _compute_retry_after_seconds(job: AiJob) -> int | None:
    if job.status is not AiJobStatus.RETRY_WAIT:
        return None
    remaining = (job.available_at - datetime.now(UTC)).total_seconds()
    return max(0, round(remaining))


def _build_error(job: AiJob) -> JobErrorData | None:
    if job.status is not AiJobStatus.FAILED:
        return None
    code = job.failure_code
    if code is None:
        # chk_ai_job_failed_code CHECK 제약이 FAILED에서 failure_code NOT NULL을 보장하므로
        # 정상 데이터에서는 오지 않지만, DTO 계약을 깨지 않기 위해 안전한 값으로 fail-closed합니다.
        return JobErrorData(code="INTERNAL_ERROR", message=_FAILURE_MESSAGES["INTERNAL_ERROR"])
    message = _FAILURE_MESSAGES.get(code, _FAILURE_MESSAGES["INTERNAL_ERROR"])
    return JobErrorData(code=code, message=message)
