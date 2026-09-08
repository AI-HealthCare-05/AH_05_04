from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse as Response

from app.core import config
from app.core.errors import ApiError, ErrorDetail
from app.dependencies.security import get_request_user
from app.dependencies.services import get_chat_service, get_guide_service, get_prescription_service
from app.dtos.chat import ChatSessionResponse
from app.dtos.guides import GuideResponse
from app.dtos.prescriptions import CorrectPrescriptionRequest, PrescriptionResponse
from app.models.users import User
from app.services.chat import ChatService
from app.services.guides import GuideService
from app.services.prescriptions import PrescriptionService

prescription_router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])


@prescription_router.get(
    "/latest",
    response_model=PrescriptionResponse,
    status_code=status.HTTP_200_OK,
)
async def get_latest_prescription(
    user: Annotated[User, Depends(get_request_user)],
    prescription_service: Annotated[PrescriptionService, Depends(get_prescription_service)],
) -> Response:
    # 재접속 복구 Backend 계약(#295): 로그아웃·재로그인 등으로 Frontend가 prescription_id를
    # 잃어도, 이 사용자의 가장 최근 확정 처방을 다시 조회할 수 있게 합니다. `/{prescription_id}`
    # 보다 먼저 등록해야 "latest"가 UUID 경로 변수로 잘못 매칭되지 않습니다.
    result = await prescription_service.get_latest_prescription(user=user)

    return Response(
        content=PrescriptionResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )


@prescription_router.patch(
    "/{prescription_id}",
    response_model=PrescriptionResponse,
    status_code=status.HTTP_200_OK,
)
async def correct_prescription(
    prescription_id: UUID,
    request: CorrectPrescriptionRequest,
    user: Annotated[User, Depends(get_request_user)],
    prescription_service: Annotated[PrescriptionService, Depends(get_prescription_service)],
) -> Response:
    if not config.PRESCRIPTION_CORRECTION_ENABLED:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            message="처방 정정 기능은 아직 공개되지 않았습니다.",
            details=[ErrorDetail(field="prescription", reason="PRESCRIPTION_CORRECTION_DISABLED")],
        )
    result = await prescription_service.correct_prescription(
        user=user,
        prescription_id=prescription_id,
        request=request,
    )
    return Response(
        content=PrescriptionResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )


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


# GET /{prescription_id}/guides (Job 상태 기반 재접속 복구): Guide 생성이 JobIntakeService.accept_job()에
# 연결되기 전까지는(#219/#232/#233) 실제 사용자가 생성한 어떤 Guide Job도 AiJob 매핑을 가질 수
# 없어 정상 200 경로가 존재하지 않습니다. 라우트 등록과 docs/api.md 현재 API 목록 등재를
# 접수 연결 시점까지 보류합니다(#148 세 번째 리뷰). 서비스 로직(JobStatusService.rediscover_guide_job)과
# 그 테스트는 남겨 두어 연결 시점에 라우트만 다시 추가하면 되도록 합니다.
#
# 아래 단수형 GET /{prescription_id}/guide는 위와 다른 목적입니다(#295) — Guide 생성이 아직
# 동기(one-cycle)라 AiJob 매핑 자체가 없으므로, Job 상태와 무관하게 소유권만으로 가장 최근
# Guide를 바로 돌려줍니다. one-cycle 흐름이 유지되는 동안의 임시 rediscovery이며, 위 Job 기반
# 경로가 연결되면 그쪽으로 대체될 수 있습니다.


@prescription_router.get(
    "/{prescription_id}/guide",
    response_model=GuideResponse,
    status_code=status.HTTP_200_OK,
)
async def get_latest_guide_for_prescription(
    prescription_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    guide_service: Annotated[GuideService, Depends(get_guide_service)],
) -> Response:
    # 재접속 복구 Backend 계약(#295): 로그아웃·재로그인 등으로 Frontend가 guide_id를 잃어도
    # 처방 소유권 기준으로 가장 최근 Guide를 다시 조회할 수 있게 합니다.
    result = await guide_service.get_latest_guide_for_prescription(
        user=user,
        prescription_id=prescription_id,
    )

    return Response(
        content=GuideResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )


@prescription_router.get(
    "/{prescription_id}/chat-session",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def get_latest_chat_session_for_prescription(
    prescription_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
) -> Response:
    # 재접속 복구 Backend 계약(#295): 로그아웃·재로그인 등으로 Frontend가 session_id를
    # 잃어도 처방 소유권 기준으로 기존 활성 Chat session을 다시 찾을 수 있게 합니다.
    result = await chat_service.get_latest_session_for_prescription(user=user, prescription_id=prescription_id)

    return Response(
        content=ChatSessionResponse(data=result).model_dump(mode="json"),
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
