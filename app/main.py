import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from openai import AsyncOpenAI

from app.apis.v1 import v1_routers
from app.core import config
from app.core.db.databases import close_database
from app.core.errors import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 정현우님(guide_ai)·챗봇(chat_ai) 연동에서 공용으로 사용할 AsyncOpenAI 클라이언트를 조립합니다.
    # 재시도는 asyncio.timeout으로 감싼 우리 쪽 타임아웃/에러 매핑이 전담하도록 SDK 자동 재시도를 끕니다.
    app.state.openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY, max_retries=0)
    try:
        yield
    finally:
        await app.state.openai_client.close()
        await close_database()


app = FastAPI(
    default_response_class=ORJSONResponse,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


@app.middleware("http")
async def add_trace_id(request: Request, call_next):
    # 요청별 고유 trace_id를 request.state에 저장해 에러 응답·로그에서 재사용합니다.
    request.state.trace_id = uuid.uuid4().hex
    return await call_next(request)


register_exception_handlers(app)

app.include_router(v1_routers)

# FastAPI의 바깥쪽 예외 처리 계층에서 반환되는 500 응답에도 CORS 헤더를 붙입니다.
# 내부 FastAPI 앱을 먼저 구성한 뒤 마지막에 CORS 미들웨어로 감싸야 합니다.
fastapi_app = app
app = CORSMiddleware(
    app=fastapi_app,
    allow_origins=config.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 기존 테스트와 의존성 오버라이드가 내부 FastAPI 앱과 같은 딕셔너리를 사용하도록 합니다.
app.dependency_overrides = fastapi_app.dependency_overrides
