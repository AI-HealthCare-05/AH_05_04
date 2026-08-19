from collections import defaultdict
from datetime import UTC, date, datetime
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.dtos.prescriptions import MedicationData, PrescriptionData
from app.models.ocr import ExtractedField, FieldType
from app.models.prescriptions import Medication, Prescription
from app.models.users import User
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.ocr_repository import OcrRepository
from app.repositories.prescription_repository import PrescriptionRepository


def _field_value(field: ExtractedField | None) -> str | None:
    # 사용자가 명시적으로 확인(PATCH /extracted-fields)하지 않은 OCR raw_value는 처방 확정에 쓰지 않습니다.
    # "OCR 결과는 사용자 확인 전까지 미확정 상태"라는 팀 규칙에 따라 raw_value로 대체하지 않고,
    # 확인되지 않은 필드는 없는 값으로 취급해 확정 대상에서 제외되도록 합니다.
    if field is None:
        return None
    return field.confirmed_value


def _to_prescription_data(prescription: Prescription, medications: list[Medication]) -> PrescriptionData:
    return PrescriptionData(
        prescription_id=prescription.id,
        document_id=prescription.document_id,
        prescribed_date=prescription.prescribed_date,
        confirmed_at=prescription.confirmed_at,
        medications=[
            MedicationData(
                medication_name=medication.medication_name,
                dose_value=float(medication.dose_value) if medication.dose_value is not None else None,
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

        job = await self._ocr_repo.get_latest_completed_job(document=document)
        if job is None:
            raise ApiError(
                status_code=409,
                code="OCR_NOT_COMPLETED",
                message="OCR 처리가 완료된 결과가 없어 처방을 확정할 수 없습니다.",
                details=[ErrorDetail(field="document_id", reason="OCR_NOT_COMPLETED")],
            )

        prescribed_date, medications = self._build_confirmed_data(list(job.extracted_fields))
        if prescribed_date is None or not medications:
            raise ApiError(
                status_code=422,
                code="PRESCRIPTION_REQUIRED_FIELD_MISSING",
                message="처방 확정에 필요한 항목이 누락되었습니다.",
                details=[ErrorDetail(field="extracted_fields", reason="REQUIRED")],
            )

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
    ) -> tuple[date | None, list[dict]]:
        by_index: dict[int, dict[FieldType, ExtractedField]] = defaultdict(dict)
        for field in extracted_fields:
            by_index[field.medication_index][FieldType(field.field_type)] = field

        prescribed_date: date | None = None
        prescription_level = by_index.get(0, {})
        raw_date = _field_value(prescription_level.get(FieldType.PRESCRIBED_DATE))
        if raw_date:
            try:
                prescribed_date = date.fromisoformat(raw_date)
            except ValueError:
                prescribed_date = None

        medications: list[dict] = []
        for index, fields_by_type in sorted(by_index.items()):
            if index == 0:
                continue
            name = _field_value(fields_by_type.get(FieldType.MEDICATION_NAME))
            if not name:
                continue
            medications.append(
                {
                    "medication_name": name,
                    "dose_value": _to_float(_field_value(fields_by_type.get(FieldType.DOSE_VALUE))),
                    "dose_unit": _field_value(fields_by_type.get(FieldType.DOSE_UNIT)),
                    "frequency_per_day": _to_int(_field_value(fields_by_type.get(FieldType.FREQUENCY_PER_DAY))),
                    "timing_text": _field_value(fields_by_type.get(FieldType.TIMING)),
                    "duration_days": _to_int(_field_value(fields_by_type.get(FieldType.DURATION_DAYS))),
                    "display_order": index,
                }
            )

        return prescribed_date, medications


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None
