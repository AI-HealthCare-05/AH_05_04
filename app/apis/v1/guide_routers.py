from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse as Response

from app.dependencies.security import get_request_user
from app.dependencies.services import get_guide_service
from app.dtos.guides import CreateGuideRequest, GuideResponse
from app.models.users import User
from app.services.guides import GuideService

guide_router = APIRouter(tags=["guides"])


@guide_router.post(
    "/guides",
    response_model=GuideResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_guide(
    request: CreateGuideRequest,
    user: Annotated[User, Depends(get_request_user)],
    guide_service: Annotated[GuideService, Depends(get_guide_service)],
) -> Response:
    # 복약 가이드 생성 Backend 계약(one-cycle, 동기):
    # 같은 요청 안에서 OpenAI 가이드 생성과 GUIDE 저장을 완료하고 201로 응답합니다.
    result = await guide_service.create_guide(user=user, request=request)

    return Response(
        content=GuideResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED,
        headers={"Cache-Control": "no-store"},
    )


@guide_router.get(
    "/guides/{guide_id}",
    response_model=GuideResponse,
    status_code=status.HTTP_200_OK,
)
async def get_guide_detail(
    guide_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    guide_service: Annotated[GuideService, Depends(get_guide_service)],
) -> Response:
    # 지원 API: 새로고침·재조회용. one-cycle 최초 생성 흐름에는 필요하지 않습니다.
    result = await guide_service.get_guide_detail(user=user, guide_id=guide_id)

    return Response(
        content=GuideResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
        headers={"Cache-Control": "no-store"},
    )
