from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from openai import AsyncOpenAI

from app.apis.v1 import v1_routers
from app.core import config
from app.core.db.databases import close_database
from app.core.errors import register_exception_handlers
from app.core.no_store_middleware import NoStoreMiddleware
from app.core.validation_trace_middleware import RequestTraceMiddleware, ValidationTraceMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # guide_ai·chat_ai 연동에서 공용으로 사용할 AsyncOpenAI 클라이언트를 조립합니다.
    # 재시도는 asyncio.timeout으로 감싼 우리 쪽 타임아웃/에러 매핑이 전담하도록 SDK 자동 재시도를 끕니다.
    app.state.openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY, max_retries=0)
    try:
        yield
    finally:
        await app.state.openai_client.close()
        await close_database()


fastapi_app = FastAPI(
    default_response_class=ORJSONResponse,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


register_exception_handlers(fastapi_app)

fastapi_app.include_router(v1_routers)

# validation 거부 응답도 CORS와 no-store 경계를 통과하도록 FastAPI 바로 바깥에 둡니다.
validated_app = ValidationTraceMiddleware(
    fastapi_app,
    environment=config.ENV,
    validation_enabled=config.RELEASE_VALIDATION_ALLOWED,
)

# FastAPI의 바깥쪽 예외 처리 계층에서 반환되는 500 응답에도 CORS 헤더를 붙입니다.
cors_app = CORSMiddleware(
    app=NoStoreMiddleware(validated_app),
    allow_origins=config.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Retry-After: job_routers.build_job_status_response()가 RETRY_WAIT에서 설정하는 값을
    # cross-origin Frontend가 fetch()로 읽으려면 CORS expose 대상에 있어야 합니다 — 없으면
    # 브라우저가 응답 자체는 받아도 스크립트에서 헤더값을 읽지 못합니다.
    expose_headers=["X-Trace-Id", "Retry-After"],
)

# trace 경계는 CORS preflight를 포함한 모든 HTTP 응답을 감싸도록 가장 바깥에 둡니다.
app = RequestTraceMiddleware(cors_app)
