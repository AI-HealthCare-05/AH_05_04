from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import ORJSONResponse as Response

from app.dependencies.security import get_request_user
from app.dependencies.services import (
    get_ocr_consent_service,
    get_user_consent_service,
    get_user_manage_service,
)
from app.dtos.user_consents import GrantOcrConsentRequest, OcrConsentResponse
from app.dtos.users import (
    UserConsentListResponse,
    UserConsentResponse,
    UserConsentUpdateRequest,
    UserInfoResponse,
    UserUpdateRequest,
)
from app.models.user_consents import ConsentPurpose
from app.models.users import User
from app.services.user_consents import OcrConsentService
from app.services.users import UserConsentService, UserManageService

user_router = APIRouter(prefix="/users", tags=["users"])


@user_router.get("/me/consents/OCR", response_model=OcrConsentResponse)
async def get_my_ocr_consent(
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[OcrConsentService, Depends(get_ocr_consent_service)],
) -> OcrConsentResponse:
    return OcrConsentResponse(data=await service.get_state(user=user))


@user_router.post("/me/consents/OCR", response_model=OcrConsentResponse)
async def grant_my_ocr_consent(
    request: GrantOcrConsentRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[OcrConsentService, Depends(get_ocr_consent_service)],
) -> OcrConsentResponse:
    return OcrConsentResponse(data=await service.grant(user=user, policy_version=request.policy_version))


@user_router.delete("/me/consents/OCR", response_model=OcrConsentResponse)
async def withdraw_my_ocr_consent(
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[OcrConsentService, Depends(get_ocr_consent_service)],
) -> OcrConsentResponse:
    return OcrConsentResponse(data=await service.withdraw(user=user))


@user_router.get("/me", response_model=UserInfoResponse, status_code=status.HTTP_200_OK)
async def user_me_info(
    user: Annotated[User, Depends(get_request_user)],
) -> Response:
    return Response(UserInfoResponse.model_validate(user).model_dump(), status_code=status.HTTP_200_OK)


@user_router.patch("/me", response_model=UserInfoResponse, status_code=status.HTTP_200_OK)
async def update_user_me_info(
    update_data: UserUpdateRequest,
    user: Annotated[User, Depends(get_request_user)],
    user_manage_service: Annotated[UserManageService, Depends(get_user_manage_service)],
) -> Response:
    updated_user = await user_manage_service.update_user(user=user, data=update_data)
    return Response(UserInfoResponse.model_validate(updated_user).model_dump(), status_code=status.HTTP_200_OK)


@user_router.get("/me/consents", response_model=UserConsentListResponse, status_code=status.HTTP_200_OK)
async def list_user_consents(
    user: Annotated[User, Depends(get_request_user)],
    consent_service: Annotated[UserConsentService, Depends(get_user_consent_service)],
) -> Response:
    result = await consent_service.list_user_consents(user=user)
    return Response(result.model_dump(mode="json"), status_code=status.HTTP_200_OK)


@user_router.put(
    "/me/consents/{purpose}",
    response_model=UserConsentResponse,
    status_code=status.HTTP_200_OK,
)
async def update_user_consent(
    purpose: ConsentPurpose,
    request: UserConsentUpdateRequest,
    user: Annotated[User, Depends(get_request_user)],
    consent_service: Annotated[UserConsentService, Depends(get_user_consent_service)],
) -> Response:
    result = await consent_service.set_user_consent(user=user, purpose=purpose, request=request)
    return Response(result.model_dump(mode="json"), status_code=status.HTTP_200_OK)
