"""AI Worker 실패를 연결된 OCR Job의 안전한 공개 실패로 투영합니다."""

from datetime import datetime

from sqlalchemy import DateTime, String, column, table, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.core.retry import FailureCode

_OCR_JOB = table(
    "ocr_job",
    column("id", String(36)),
    column("ai_job_id", String(36)),
    column("ocr_status", String(20)),
    column("completed_at", DateTime(timezone=True)),
    column("error_code", String(100)),
    column("error_message", String(500)),
)

_OCR_FAILURE_BY_WORKER_FAILURE: dict[FailureCode, tuple[str, str]] = {
    "TIMEOUT": (
        "OCR_PROVIDER_TIMEOUT",
        "OCR 서비스 응답 시간이 초과되었습니다.",
    ),
    "DEPENDENCY_UNAVAILABLE": (
        "OCR_PROVIDER_UNAVAILABLE",
        "OCR 제공자 호출에 실패했습니다.",
    ),
    "INVALID_INPUT": (
        "OCR_PROCESSING_FAILED",
        "OCR 처리 중 오류가 발생했습니다.",
    ),
    "UNSUPPORTED_SCHEMA": (
        "OCR_PROCESSING_FAILED",
        "OCR 처리 중 오류가 발생했습니다.",
    ),
    "SAFETY_VALIDATION_FAILED": (
        "OCR_PROCESSING_FAILED",
        "OCR 처리 중 오류가 발생했습니다.",
    ),
    "RETRY_EXHAUSTED": (
        "OCR_PROCESSING_FAILED",
        "OCR 처리 중 오류가 발생했습니다.",
    ),
    "INTERNAL_ERROR": (
        "OCR_PROCESSING_FAILED",
        "OCR 처리 중 오류가 발생했습니다.",
    ),
}


async def mark_linked_ocr_job_failed(
    session: AsyncSession,
    *,
    ai_job_id: str,
    failure_code: FailureCode,
    completed_at: datetime,
) -> bool:
    """연결된 PROCESSING OCR Job을 같은 transaction에서 종료합니다."""

    error_code, error_message = _OCR_FAILURE_BY_WORKER_FAILURE[failure_code]
    statement = (
        update(_OCR_JOB)
        .where(
            _OCR_JOB.c.ai_job_id == ai_job_id,
            _OCR_JOB.c.ocr_status == "PROCESSING",
        )
        .values(
            ocr_status="FAILED",
            completed_at=completed_at,
            error_code=error_code,
            error_message=error_message,
        )
        .returning(_OCR_JOB.c.id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def mark_ocr_consent_blocked(
    session: AsyncSession, *, ai_job_id: str, reason: str, completed_at: datetime
) -> bool:
    codes = {
        "WITHDRAWN": ("CONSENT_WITHDRAWN", "동의가 철회되어 처리가 중단되었습니다."),
        "LOOKUP_FAILED": ("CONSENT_LOOKUP_FAILED", "동의를 확인할 수 없어 처리가 중단되었습니다."),
        "POLICY_VERSION_MISMATCH": ("CONSENT_POLICY_MISMATCH", "현재 정책에 대한 동의가 필요합니다."),
        "MISSING_CONSENT": ("CONSENT_REQUIRED", "처방전 처리 동의가 필요합니다."),
        "ACCOUNT_NOT_ACTIVE": ("CONSENT_SUBJECT_INVALID", "처리 권한을 확인할 수 없습니다."),
        "OWNER_MISMATCH": ("CONSENT_SUBJECT_INVALID", "처리 권한을 확인할 수 없습니다."),
        "PURPOSE_MISMATCH": ("CONSENT_REQUIRED", "처방전 처리 동의가 필요합니다."),
        "INVALID_CONSENT": ("CONSENT_REQUIRED", "처방전 처리 동의가 필요합니다."),
    }
    code, message = codes[reason]
    result = await session.execute(
        update(_OCR_JOB)
        .where(_OCR_JOB.c.ai_job_id == ai_job_id, _OCR_JOB.c.ocr_status == "PROCESSING")
        .values(ocr_status="FAILED", completed_at=completed_at, error_code=code, error_message=message)
        .returning(_OCR_JOB.c.id)
    )
    return result.scalar_one_or_none() is not None
