from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
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

register_exception_handlers(app)

app.include_router(v1_routers)
