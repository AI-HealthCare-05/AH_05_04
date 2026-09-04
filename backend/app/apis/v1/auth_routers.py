from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, status
from fastapi.responses import JSONResponse as Response
from fastapi.security import HTTPAuthorizationCredentials

from app.core import config
from app.core.config import Env
from app.core.errors import ApiError, ErrorResponse
from app.dependencies.security import (
    resolve_active_user_from_payload,
    resolve_logout_user,
    security,
)
from app.dependencies.services import get_auth_service, get_user_repository
from app.dtos.auth import LoginRequest, LoginResponse, LogoutResponse, SignUpRequest, TokenRefreshResponse
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
        # `expires`에 정수를 그대로 넘기면 Python `http.cookies`가 이를 절대 epoch이 아니라
        # "지금부터 몇 초 후"로 해석해(`http.cookies._getdate`) 실제 만료가 의도보다 훨씬
        # 뒤로 밀립니다(4차 리뷰가 지적한 access_token.exp 오사용을 refresh_token.exp로만
        # 바꿔도 이 문제는 그대로 남습니다). `datetime`으로 변환해 절대 시각으로 넘겨야 합니다.
        expires=datetime.fromtimestamp(tokens["refresh_token"].payload["exp"], tz=UTC),
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
            "description": "인증 정보가 없거나 유효하지 않습니다. `code`는 `UNAUTHORIZED`·`INVALID_TOKEN`·`EXPIRED_TOKEN`입니다.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": (
                "이 라우트가 실제로 422를 반환할 입력은 없지만, `refresh_token` Cookie parameter가 있어 "
                "FastAPI가 기본 `HTTPValidationError`를 자동 문서화합니다. 만약 422가 발생하면 전역 "
                "`RequestValidationError` 핸들러가 만드는 `ErrorResponse`(`code=VALIDATION_FAILED`)가 "
                "실제 응답입니다(#148/#250 리뷰와 같은 종류의 문서·실제 응답 불일치)."
            ),
        },
    },
)
async def logout(
    request: Request,
    credential: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    user_repository: Annotated[UserRepository, Depends(get_user_repository)],
    jwt_service: Annotated[JwtService, Depends(JwtService)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> Response:
    # PD-206 리뷰: access token이 만료된 상태로 로그아웃해도 세션을 확실히 끊어야 합니다.
    # resolve_logout_user()가 만료된 access token을 유효한 refresh token으로 fallback
    # 확인하고, 인증 성공·실패와 무관하게 이 라우트는 항상 refresh_token 쿠키를 삭제합니다 —
    # 그렇지 않으면 브라우저에 남은 refresh_token으로 세션이 재발급될 수 있습니다.
    try:
        user = await resolve_logout_user(
            credential=credential,
            refresh_token=refresh_token,
            repository=user_repository,
            jwt_service=jwt_service,
        )
    except ApiError as exc:
        error_response = Response(
            content=ErrorResponse(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                trace_id=request.state.trace_id,
            ).model_dump(mode="json"),
            status_code=exc.status_code,
            headers=exc.headers,
        )
        error_response.delete_cookie(key="refresh_token", domain=config.COOKIE_DOMAIN or None)
        return error_response

    await user_repository.increment_token_version(user)
    response = Response(
        content=LogoutResponse(detail="로그아웃되었습니다.").model_dump(), status_code=status.HTTP_200_OK
    )
    response.delete_cookie(
        key="refresh_token",
        domain=config.COOKIE_DOMAIN or None,
    )
    return response
