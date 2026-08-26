import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.dtos.prescriptions import MedicationData, PrescriptionData
from app.models.ocr import ExtractedField, FieldType
from app.models.prescriptions import Medication, Prescription
from app.models.users import User
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.ocr_repository import OcrRepository
from app.repositories.prescription_repository import PrescriptionRepository

_MAX_MEDICATION_NAME_LENGTH = 255
# 복합제 및 농도 문자열을 포함할 수 있는 최대 길이입니다.
_MAX_STRENGTH_TEXT_LENGTH = 100
_MAX_DOSE_VALUE = Decimal("9999999.999")
_MAX_DOSE_SCALE = 3
_MAX_INTEGER_VALUE = 2_147_483_647
_MAX_DOSE_UNIT_LENGTH = 50
_MAX_TIMING_TEXT_LENGTH = 255


def _field_value(field: ExtractedField | None) -> str | None:
    # 사용자가 명시적으로 확인(PATCH /extracted-fields)하지 않은 OCR raw_value는 처방 확정에 쓰지 않습니다.
    # "OCR 결과는 사용자 확인 전까지 미확정 상태"라는 팀 규칙에 따라 raw_value로 대체하지 않고,
    # 확인되지 않은 필드는 없는 값으로 취급해 확정 대상에서 제외되도록 합니다.
    if field is None or field.confirmed_value is None:
        return None
    # UpdateExtractedFieldRequest의 min_length=1은 공백 문자열(" ")도 통과시키므로,
    # 여기서 공백만 있는 값을 빈 값과 동일하게 취급해 필수 항목 검증을 우회하지 못하게 합니다.
    stripped = field.confirmed_value.strip()
    return stripped or None


def _to_prescription_data(prescription: Prescription, medications: list[Medication]) -> PrescriptionData:
    return PrescriptionData(
        prescription_id=prescription.id,
        document_id=prescription.document_id,
        prescribed_date=prescription.prescribed_date,
        confirmed_at=prescription.confirmed_at,
        medications=[
            MedicationData(
                medication_name=medication.medication_name,
                strength_text=medication.strength_text,
                dose_value=(
                    float(medication.dose_value)
                    if medication.dose_value is not None
                    else None
                ),
                dose_unit=medication.dose_unit,
                frequency_per_day=medication.frequency_per_day,
                timing_text=medication.timing_text,
                duration_days=medication.duration_days,
                display_order=medication.display_order,
            )
            for medication in sorted(medications, key=lambda m: m.display_order)
        ],
    )


class PrescriptionService:
    def __init__(
        self,
        document_repository: MedicalDocumentRepository,
        ocr_repository: OcrRepository,
        prescription_repository: PrescriptionRepository,
    ) -> None:
        self._document_repo = document_repository
        self._ocr_repo = ocr_repository
        self._prescription_repo = prescription_repository

    async def confirm_prescription(self, *, user: User, document_id: UUID) -> PrescriptionData:
        # 처방 최종 확정 Backend 계약(1차 구현 ERD): 확정 시점에 PRESCRIPTION·MEDICATION을 생성합니다.
        # "사용자 확인 전 정보를 확정 정보로 쓰지 않는다"는 의료 안전 원칙에 따라 단순화 대상에서 제외합니다.
        document = await self._document_repo.get_owned(document_id=document_id, user=user)
        if document is None:
            raise ApiError(
                status_code=404,
                code="MEDICAL_DOCUMENT_NOT_FOUND",
                message="의료문서를 찾을 수 없습니다.",
                details=[ErrorDetail(field="document_id", reason="NOT_FOUND", rejected_value=str(document_id))],
            )

        existing = await self._prescription_repo.get_by_document(document=document)
        if existing is not None:
            raise ApiError(
                status_code=409,
                code="PRESCRIPTION_ALREADY_CONFIRMED",
                message="이미 확정된 처방 정보입니다.",
                details=[ErrorDetail(field="document_id", reason="ALREADY_CONFIRMED")],
            )

        # MVP에서는 문서에 연결된 최신 COMPLETED OCR 작업을 사용합니다.
        # 검수 작업을 명시적으로 식별하는 job_id 연결은 Post-MVP 범위입니다.
        job = await self._ocr_repo.get_latest_completed_job(document=document)
        if job is None:
            raise ApiError(
                status_code=409,
                code="OCR_JOB_NOT_COMPLETED",
                message="OCR 처리가 완료된 결과가 없어 처방을 확정할 수 없습니다.",
                details=[ErrorDetail(field="document_id", reason="OCR_JOB_NOT_COMPLETED")],
            )

        prescribed_date, medications = self._build_confirmed_data(list(job.extracted_fields))

        confirmed_at = datetime.now(UTC)
        prescription = await self._prescription_repo.create_with_medications(
            document=document,
            source_ocr_job=job,
            prescribed_date=prescribed_date,
            confirmed_at=confirmed_at,
            medications=medications,
        )
        created_medications = await self._prescription_repo.get_medications(prescription_id=prescription.id)
        return _to_prescription_data(prescription, created_medications)

    async def get_prescription_detail(self, *, user: User, prescription_id: UUID) -> PrescriptionData:
        prescription = await self._prescription_repo.get_owned(prescription_id=prescription_id, user_id=user.id)
        if prescription is None:
            raise ApiError(
                status_code=404,
                code="PRESCRIPTION_NOT_FOUND",
                message="처방 정보를 찾을 수 없습니다.",
                details=[ErrorDetail(field="prescription_id", reason="NOT_FOUND", rejected_value=str(prescription_id))],
            )
        return _to_prescription_data(prescription, list(prescription.medications))

    @staticmethod
    def _build_confirmed_data(
        extracted_fields: list[ExtractedField],
    ) -> tuple[date, list[dict]]:
        by_index: dict[int, dict[FieldType, ExtractedField]] = defaultdict(dict)
        for field in extracted_fields:
            by_index[field.medication_index][FieldType(field.field_type)] = field

        missing: list[ErrorDetail] = []
        invalid: list[ErrorDetail] = []

        prescribed_date = _parse_prescribed_date(
            _field_value(by_index.get(0, {}).get(FieldType.PRESCRIBED_DATE)),
            missing=missing,
            invalid=invalid,
        )

        medication_indexes = sorted(index for index in by_index if index != 0)
        if not medication_indexes:
            missing.append(ErrorDetail(field="medications", reason="REQUIRED"))
        else:
            # medication_index가 중간에 비면 OCR 결과에서 약물이 누락된 것으로 처리합니다.
            expected_indexes = set(range(1, medication_indexes[-1] + 1))
            missing_indexes = sorted(expected_indexes.difference(medication_indexes))
            missing.extend(ErrorDetail(field=f"medications[{index}]", reason="REQUIRED") for index in missing_indexes)

        medications: list[dict] = []
        for index in medication_indexes:
            medication = _build_medication(index, by_index[index], missing=missing, invalid=invalid)
            if medication is not None:
                medications.append(medication)

        if missing:
            # 필수값 누락과 형식 오류가 함께 있으면 필수값 누락을 대표 code로 두되,
            # 사용자가 한 번에 수정할 수 있도록 형식 오류 details도 같은 응답에 포함합니다.
            raise ApiError(
                status_code=422,
                code="PRESCRIPTION_REQUIRED_FIELD_MISSING",
                message="처방 확정에 필요한 항목이 누락되었습니다.",
                details=[*missing, *invalid],
            )
        if invalid:
            raise ApiError(
                status_code=422,
                code="VALIDATION_FAILED",
                message="입력값을 확인해 주세요.",
                details=invalid,
            )

        assert prescribed_date is not None  # missing이 비어 있으면 파싱에 성공한 상태입니다.
        return prescribed_date, medications


def _to_decimal(value: str | None) -> Decimal | None:
    if not value or not value.strip():
        return None
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation:
        return None
    if not parsed.is_finite() or parsed <= 0 or parsed > _MAX_DOSE_VALUE:
        return None
    # normalize()로 뒤에 붙은 불필요한 0(예: 0.5000)을 제거한 뒤 실제 소수 자릿수를 계산합니다.
    exponent = parsed.normalize().as_tuple().exponent
    if not isinstance(exponent, int) or max(-exponent, 0) > _MAX_DOSE_SCALE:
        return None
    return parsed


def _to_int(value: str | None) -> int | None:
    if not value or not value.strip() or re.fullmatch(r"[0-9]+", value.strip()) is None:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if 0 < parsed <= _MAX_INTEGER_VALUE else None


def _parse_prescribed_date(
    value: str | None,
    *,
    missing: list[ErrorDetail],
    invalid: list[ErrorDetail],
) -> date | None:
    if not value:
        missing.append(ErrorDetail(field="prescribed_date", reason="REQUIRED"))
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        invalid.append(ErrorDetail(field="prescribed_date", reason="INVALID_FORMAT", rejected_value=value))
        return None


def _validate_numeric[T](
    *,
    index: int,
    field_type: FieldType,
    fields_by_type: dict[FieldType, ExtractedField],
    parser: Callable[[str], T | None],
    missing: list[ErrorDetail],
    invalid: list[ErrorDetail],
) -> T | None:
    field_name = f"medications[{index}].{field_type.value.lower()}"
    raw_value = _field_value(fields_by_type.get(field_type))
    if not raw_value:
        missing.append(ErrorDetail(field=field_name, reason="REQUIRED"))
        return None
    value = parser(raw_value)
    if value is None:
        invalid.append(ErrorDetail(field=field_name, reason="INVALID_FORMAT", rejected_value=raw_value))
    return value


def _validate_optional_text_length(
    *,
    index: int,
    field_type: FieldType,
    fields_by_type: dict[FieldType, ExtractedField],
    max_length: int,
    invalid: list[ErrorDetail],
) -> str | None:
    value = _field_value(fields_by_type.get(field_type))
    if value is not None and len(value) > max_length:
        invalid.append(
            ErrorDetail(
                field=f"medications[{index}].{field_type.value.lower()}",
                reason="MAX_LENGTH_EXCEEDED",
                rejected_value=value,
            )
        )
        return None
    return value


def _build_medication(
    index: int,
    fields_by_type: dict[FieldType, ExtractedField],
    *,
    missing: list[ErrorDetail],
    invalid: list[ErrorDetail],
) -> dict | None:
    # Frontend 처방 검수 화면(PrescriptionReviewPage)의 requiredMedicationFieldTypes와 동일한 기준입니다.
    name = _field_value(fields_by_type.get(FieldType.MEDICATION_NAME))
    if not name:
        missing.append(ErrorDetail(field=f"medications[{index}].medication_name", reason="REQUIRED"))
    elif len(name) > _MAX_MEDICATION_NAME_LENGTH:
        invalid.append(
            ErrorDetail(
                field=f"medications[{index}].medication_name",
                reason="MAX_LENGTH_EXCEEDED",
                rejected_value=name,
            )
        )
    # 제품 함량은 없는 처방전도 있으므로 선택 필드입니다.
    # 다만 존재하면 사용자가 확인한 값만 확정 처방에 사용합니다.
    strength_text = _validate_optional_text_length(
        index=index,
        field_type=FieldType.MEDICATION_STRENGTH,
        fields_by_type=fields_by_type,
        max_length=_MAX_STRENGTH_TEXT_LENGTH,
        invalid=invalid,
    )

    dose_value = _validate_numeric(
        index=index,
        field_type=FieldType.DOSE_VALUE,
        fields_by_type=fields_by_type,
        parser=_to_decimal,
        missing=missing,
        invalid=invalid,
    )
    frequency_per_day = _validate_numeric(
        index=index,
        field_type=FieldType.FREQUENCY_PER_DAY,
        fields_by_type=fields_by_type,
        parser=_to_int,
        missing=missing,
        invalid=invalid,
    )
    duration_days = _validate_numeric(
        index=index,
        field_type=FieldType.DURATION_DAYS,
        fields_by_type=fields_by_type,
        parser=_to_int,
        missing=missing,
        invalid=invalid,
    )

    dose_unit = _validate_optional_text_length(
        index=index,
        field_type=FieldType.DOSE_UNIT,
        fields_by_type=fields_by_type,
        max_length=_MAX_DOSE_UNIT_LENGTH,
        invalid=invalid,
    )
    timing_text = _validate_optional_text_length(
        index=index,
        field_type=FieldType.TIMING,
        fields_by_type=fields_by_type,
        max_length=_MAX_TIMING_TEXT_LENGTH,
        invalid=invalid,
    )

    if (
        not name
        or len(name) > _MAX_MEDICATION_NAME_LENGTH
        or dose_value is None
        or frequency_per_day is None
        or duration_days is None
    ):
        return None

    return {
        # 화면 표시용 이름입니다. 후속 성분명 매핑에서도 덮어쓰지 않습니다.
        "medication_name": name,

        # 제품 함량은 1회 복용량과 별도로 저장합니다.
        "strength_text": strength_text,
        "dose_value": dose_value,
        "dose_unit": dose_unit,
        "frequency_per_day": frequency_per_day,
        "timing_text": timing_text,
        "duration_days": duration_days,
        "display_order": index,
    }
