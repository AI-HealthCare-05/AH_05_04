"""공유 처방 소비 경계. 단일 DB 조회의 동일 snapshot에서 전체 내용을 검증합니다."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorDetail
from app.models.prescriptions import PrescriptionVersion, PrescriptionVersionMedication
from provider_contracts.prescription_integrity import MEDICATION_CONTENT_FIELDS, verify_prescription_fingerprint


def unavailable_version() -> ApiError:
    return ApiError(
        status_code=409,
        code="PRESCRIPTION_VERSION_UNAVAILABLE",
        message="처방 버전 정보를 사용할 수 없습니다.",
        details=[ErrorDetail(field="active_version_id", reason="INVALID_VERSION_GRAPH")],
    )


def verify_loaded_version(version: PrescriptionVersion, medications: Sequence[PrescriptionVersionMedication]) -> None:
    try:
        verify_prescription_fingerprint(
            version.prescribed_date,
            [
                {key: getattr(item, key) for key in (*MEDICATION_CONTENT_FIELDS, "medication_count")}
                for item in medications
            ],
            medication_count=version.medication_count,
            content_hash=version.content_hash,
        )
    except ValueError:
        raise unavailable_version() from None


async def require_verified_version(session: AsyncSession, version_id: UUID) -> None:
    rows = (
        (
            await session.execute(
                select(
                    PrescriptionVersion.prescribed_date,
                    PrescriptionVersion.medication_count.label("expected_count"),
                    PrescriptionVersion.content_hash,
                    PrescriptionVersionMedication.id.label("medication_id"),
                    PrescriptionVersionMedication.medication_count,
                    *(getattr(PrescriptionVersionMedication, key) for key in MEDICATION_CONTENT_FIELDS),
                )
                .outerjoin(
                    PrescriptionVersionMedication,
                    PrescriptionVersionMedication.prescription_version_id == PrescriptionVersion.id,
                )
                .where(PrescriptionVersion.id == version_id)
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        raise unavailable_version()
    try:
        verify_prescription_fingerprint(
            rows[0]["prescribed_date"],
            [dict(row) for row in rows if row["medication_id"] is not None],
            medication_count=rows[0]["expected_count"],
            content_hash=rows[0]["content_hash"],
        )
    except ValueError:
        raise unavailable_version() from None
