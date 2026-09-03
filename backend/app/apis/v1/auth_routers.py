from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, status
from fastapi.responses import JSONResponse as Response

from app.core import config
from app.core.config import Env
from app.core.errors import ApiError, ErrorResponse
from app.dependencies.security import get_request_user, resolve_active_user_from_payload
from app.dependencies.services import get_auth_service, get_user_repository
from app.dtos.auth import LoginRequest, LoginResponse, LogoutResponse, SignUpRequest, TokenRefreshResponse
from app.models.users import User
from app.repositories.user_repository import UserRepository
from app.services.auth import AuthService
from app.services.jwt import JwtService

auth_router = APIRouter(prefix="/auth", tags=["auth"])


def should_use_secure_cookie(env: Env) -> bool:
    return env in {Env.STAGING, Env.PRODUCTION}


@auth_router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    request: SignUpRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    await auth_service.signup(request)
    return Response(content={"detail": "회원가입이 성공적으로 완료되었습니다."}, status_code=status.HTTP_201_CREATED)


@auth_router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def login(
    request: LoginRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    user = await auth_service.authenticate(request)
    tokens = await auth_service.login(user)
    resp = Response(
        content=LoginResponse(access_token=str(tokens["access_token"])).model_dump(), status_code=status.HTTP_200_OK
    )
    resp.set_cookie(
        key="refresh_token",
        value=str(tokens["refresh_token"]),
        httponly=True,
        secure=should_use_secure_cookie(config.ENV),
        domain=config.COOKIE_DOMAIN or None,
        expires=tokens["access_token"].payload["exp"],
    )
    return resp


@auth_router.get("/token/refresh", response_model=TokenRefreshResponse, status_code=status.HTTP_200_OK)
async def token_refresh(
    jwt_service: Annotated[JwtService, Depends(JwtService)],
    user_repository: Annotated[UserRepository, Depends(get_user_repository)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> Response:
    if not refresh_token:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message="로그인이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    verified_refresh_token = jwt_service.verify_jwt(refresh_token, token_type="refresh")
    await resolve_active_user_from_payload(payload=verified_refresh_token.payload, repository=user_repository)
    access_token = verified_refresh_token.access_token
    return Response(
        content=TokenRefreshResponse(access_token=str(access_token)).model_dump(), status_code=status.HTTP_200_OK
    )


@auth_router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "인증 정보가 없거나 유효하지 않습니다. `code`는 `UNAUTHORIZED` 또는 `INVALID_TOKEN`입니다.",
        },
    },
)
async def logout(
    user: Annotated[User, Depends(get_request_user)],
    user_repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> Response:
    await user_repository.increment_token_version(user)
    response = Response(
        content=LogoutResponse(detail="로그아웃되었습니다.").model_dump(), status_code=status.HTTP_200_OK
    )
    response.delete_cookie(
        key="refresh_token",
        domain=config.COOKIE_DOMAIN or None,
    )
    return response
