"""SQLAlchemy 기반 OCR Worker 결과 저장소입니다."""

from datetime import datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Integer,
    Numeric,
    String,
    column,
    delete,
    insert,
    table,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.core.results import HandlerSuccess
from ai_worker.schemas.messages import JobType, WorkerMessage
from ai_worker.tasks.ocr.handler import OcrHandlerSuccess

_OCR_JOB = table(
    "ocr_job",
    column("id", String(36)),
    column("ai_job_id", String(36)),
    column("ocr_status", String(20)),
    column("engine_name", String(100)),
    column("model_version", String(100)),
    column("prompt_version", String(100)),
    column("completed_at", DateTime(timezone=True)),
    column("error_code", String(100)),
    column("error_message", String(500)),
)

_EXTRACTED_FIELD = table(
    "extracted_field",
    column("id", String(36)),
    column("ocr_job_id", String(36)),
    column("medication_index", Integer),
    column("field_type", String(30)),
    column("raw_value", String(1000)),
    column("confidence_score", Numeric(5, 4)),
    column("normalized_value", String(1000)),
    column("normalization_version", String(30)),
    column("confirmed_value", String(1000)),
    column("confirmation_status", String(20)),
    column("confirmed_at", DateTime(timezone=True)),
)


# 처방 확정 필수 필드 중 저장 계층이 방어적으로 채우는 대상입니다. backend/app/models/ocr.py의
# FieldType, backend/app/services/prescriptions.py의 _build_confirmed_data/_build_medication
# 검증 기준, Frontend PrescriptionReviewPage.tsx의 requiredMedicationFieldTypes와 반드시 같은
# 집합이어야 합니다. ai_worker는 backend ORM을 import하지 않는 별도 패키지라 값을
# 문자열로 다시 선언합니다 — 셋 중 하나를 바꾸면 나머지도 맞춰야 합니다.
#
# MEDICATION_NAME은 의도적으로 제외합니다. docs/contracts/current/ocr-medication-structuring.md
# 「부분 인식」이 명시하듯 두 구조화 경로 모두 약품 행을 인식하는 시점에 MEDICATION_NAME을
# 함께 만들고, grounding 실패 시 빈 필드 대체 없이 구조화 전체를 실패시킵니다. 즉
# medication_index는 있는데 MEDICATION_NAME row만 없는 상태는 발생하지 않으며, 계약도
# MEDICATION_NAME을 빈 필드로 만들지 않도록 명시적으로 금지합니다.
_REQUIRED_MEDICATION_FIELD_TYPES = (
    "DOSE_VALUE",
    "FREQUENCY_PER_DAY",
    "DURATION_DAYS",
)
_PRESCRIBED_DATE_FIELD_TYPE = "PRESCRIBED_DATE"


def _placeholder_field_row(*, ocr_job_id: str, medication_index: int, field_type: str) -> dict:
    return {
        "id": str(uuid4()),
        "ocr_job_id": ocr_job_id,
        "medication_index": medication_index,
        "field_type": field_type,
        "raw_value": None,
        "confidence_score": None,
        "normalized_value": None,
        "normalization_version": None,
        "confirmed_value": None,
        "confirmation_status": "UNCONFIRMED",
        "confirmed_at": None,
    }


def _fill_missing_required_fields(field_rows: list[dict], *, ocr_job_id: str) -> list[dict]:
    """#294: OCR이 필수 필드를 인식하지 못하면 Frontend가 검수 입력 컨트롤을 만들 근거(row)
    자체가 없어 처방 확정·가이드 생성이 막힌다. 이미 감지된 medication_index에 대해서만
    누락된 필수 필드를 raw_value=null인 placeholder row로 채운다.

    정본은 구조화 계층(backend/app/services/ocr_ai/validator.py,
    ocr_runtime/prescription_ocr_structurer.py)이다 — 두 경로 모두 감지된 약품 행에 대해
    누락 필드를 이미 빈 검수 필드로 채운다. 여기서는 회귀 방지를 위한 방어 계층으로만
    같은 필드를 다시 채운다. PRESCRIBED_DATE(medication_index=0)는 예외로, 규칙 기반
    경로(prescription_ocr_structurer.py)가 날짜를 전혀 인식하지 못하면 row 자체를 만들지
    않으므로(#294가 실제로 재현된 시나리오) 이 저장 계층이 유일한 방어선이다. medication이
    하나도 감지되지 않은 경우는 여기서 새 medication index를 만들어내지 않는다 — 전체
    약물 누락은 prescriptions.py의 별도 gap 검증 영역이다.
    """
    present = {(row["medication_index"], row["field_type"]) for row in field_rows}
    medication_indexes = {row["medication_index"] for row in field_rows if row["medication_index"] != 0}

    filled = list(field_rows)
    if (0, _PRESCRIBED_DATE_FIELD_TYPE) not in present:
        filled.append(
            _placeholder_field_row(ocr_job_id=ocr_job_id, medication_index=0, field_type=_PRESCRIBED_DATE_FIELD_TYPE)
        )
    for medication_index in medication_indexes:
        for field_type in _REQUIRED_MEDICATION_FIELD_TYPES:
            if (medication_index, field_type) not in present:
                filled.append(
                    _placeholder_field_row(
                        ocr_job_id=ocr_job_id, medication_index=medication_index, field_type=field_type
                    )
                )
    return filled


class CompletionClock(Protocol):
    """결과 완료 시각을 주입하기 위한 clock 계약입니다."""

    def __call__(self) -> datetime:
        """timezone-aware 완료 시각을 반환합니다."""
        ...


class SqlAlchemyOcrResultStore:
    """OCR 결과를 현재 session transaction에 적재합니다.

    commit과 rollback은 #141 Consumer 실행 계층이 담당합니다.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: CompletionClock,
    ) -> None:
        self._session = session
        self._clock = clock

    async def save(
        self,
        *,
        message: WorkerMessage,
        result: HandlerSuccess,
    ) -> None:
        """검증된 OCR 결과를 적재하되 직접 commit하지 않습니다."""

        if not isinstance(result, OcrHandlerSuccess):
            raise ValueError("OCR Handler 결과가 아닙니다.")

        if (
            result.event_id != message.event_id
            or result.job_id != message.job_id
            or result.handler_type is not JobType.OCR
            or result.domain_id != message.domain_id
        ):
            raise ValueError("OCR 결과 식별자가 일치하지 않습니다.")

        completed_at = self._clock()

        update_statement = (
            update(_OCR_JOB)
            .where(
                _OCR_JOB.c.id == str(message.domain_id),
                _OCR_JOB.c.ai_job_id == str(message.job_id),
                _OCR_JOB.c.ocr_status == "PROCESSING",
            )
            .values(
                ocr_status="COMPLETED",
                engine_name=result.engine_name,
                model_version=result.model_version,
                prompt_version=result.prompt_version,
                completed_at=completed_at,
                error_code=None,
                error_message=None,
            )
            .returning(_OCR_JOB.c.id)
        )
        update_result = await self._session.execute(update_statement)

        if update_result.scalar_one_or_none() is None:
            raise ValueError("저장 가능한 OCR Job을 찾을 수 없습니다.")

        await self._session.execute(
            delete(_EXTRACTED_FIELD).where(
                _EXTRACTED_FIELD.c.ocr_job_id == str(message.domain_id),
            )
        )

        field_rows = _fill_missing_required_fields(
            [
                {
                    "id": str(uuid4()),
                    "ocr_job_id": str(message.domain_id),
                    "medication_index": field.medication_index,
                    "field_type": field.field_type,
                    "raw_value": field.raw_value,
                    "confidence_score": field.confidence_score,
                    "normalized_value": field.normalized_value,
                    "normalization_version": field.normalization_version,
                    "confirmed_value": None,
                    "confirmation_status": "UNCONFIRMED",
                    "confirmed_at": None,
                }
                for field in result.fields
            ],
            ocr_job_id=str(message.domain_id),
        )

        if not field_rows:
            return

        await self._session.execute(
            insert(_EXTRACTED_FIELD),
            field_rows,
        )
