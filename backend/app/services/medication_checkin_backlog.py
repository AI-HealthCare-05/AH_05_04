from uuid import UUID

from app.core.errors import ApiError
from app.dtos.medication_checkin_backlog import UnconfirmedCheckinItem, UnconfirmedCheckinPage
from app.repositories.medication_checkin_repository import MedicationCheckinRepository


class MedicationCheckinBacklogService:
    def __init__(self, repository: MedicationCheckinRepository) -> None:
        self.repository = repository

    async def list_owned(self, *, user_id: UUID, limit: int = 20, cursor: UUID | None = None) -> UnconfirmedCheckinPage:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = await self.repository.list_unconfirmed_page_owned(user_id=user_id, limit=limit, cursor=cursor)
        if rows is None:
            raise ApiError(
                status_code=404, code="CHECKIN_CURSOR_NOT_FOUND", message="미확인 기록 조회 위치를 찾을 수 없습니다."
            )
        items = [
            UnconfirmedCheckinItem(
                checkin_id=checkin.id,
                occurrence_id=occurrence.id,
                prescription_id=version.prescription_id,
                prescription_version_id=version.id,
                prescription_version_medication_id=medication.id,
                medication_name=medication.medication_name,
                strength_text=medication.strength_text,
                scheduled_local_date=occurrence.scheduled_local_date,
                scheduled_at=occurrence.scheduled_at,
                confirmation_deadline_at=occurrence.confirmation_deadline_at,
                revision=checkin.revision,
            )
            for checkin, occurrence, medication, version in rows[:limit]
        ]
        return UnconfirmedCheckinPage(items=items, next_cursor=items[-1].checkin_id if len(rows) > limit else None)
