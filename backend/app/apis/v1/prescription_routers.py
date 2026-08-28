from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse as Response

from app.dependencies.security import get_request_user
from app.dependencies.services import get_chat_service, get_prescription_service
from app.dtos.chat import ChatSessionResponse
from app.dtos.prescriptions import PrescriptionResponse
from app.models.users import User
from app.services.chat import ChatService
from app.services.prescriptions import PrescriptionService

prescription_router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])


@prescription_router.get(
    "/{prescription_id}",
    response_model=PrescriptionResponse,
    status_code=status.HTTP_200_OK,
)
async def get_prescription_detail(
    prescription_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    prescription_service: Annotated[PrescriptionService, Depends(get_prescription_service)],
) -> Response:
    # 처방 상세 조회 Backend 계약: 확정 처방과 소속 약물 상세 정보를 조회합니다.
    # Cache-Control: no-store는 NoStoreMiddleware가 /api/v1/* 전체에 일괄 적용합니다.
    result = await prescription_service.get_prescription_detail(user=user, prescription_id=prescription_id)

    return Response(
        content=PrescriptionResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )


@prescription_router.post(
    "/{prescription_id}/chat-sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_session(
    prescription_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
) -> Response:
    # 채팅 세션 생성 Backend 계약: 확정 처방을 기준으로 챗봇 세션을 생성합니다.
    result = await chat_service.create_session(user=user, prescription_id=prescription_id)

    return Response(
        content=ChatSessionResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED,
    )
