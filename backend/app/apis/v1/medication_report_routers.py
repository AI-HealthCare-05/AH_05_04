from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.errors import ErrorResponse
from app.dependencies.security import get_request_user
from app.dependencies.services import get_medication_report_service
from app.dtos.medication_reports import MedicationReportResponse, MedicationReportView
from app.models.users import User
from app.services.medication_reports import MedicationReportService

medication_report_router = APIRouter(tags=["medication-reports"])


@medication_report_router.get(
    "/medication-reports",
    response_model=MedicationReportResponse,
    operation_id="medication-reports.get",
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def get_medication_report(
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[MedicationReportService, Depends(get_medication_report_service)],
    period_days: Annotated[int, Query(json_schema_extra={"enum": [7, 30]})],
    end_date: Annotated[date | None, Query()] = None,
    # Missed-dose reasons and consultation questions are returned only for the
    # clinic view, so the default report never carries them.
    view: Annotated[MedicationReportView | None, Query()] = None,
) -> MedicationReportResponse:
    return await service.report(user_id=user.id, period_days=period_days, end_date=end_date, view=view)
