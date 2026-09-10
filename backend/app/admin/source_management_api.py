"""Opt-in management ASGI application; no route is mounted in app.main."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.source_management_contract import ManagementCommand, ManagementResult, TargetKind, UpdateCommand
from app.admin.source_management_service import ManagementActor, SourceManagementService, fail
from app.core import config
from app.core.db.databases import close_database, engine, get_db_session
from app.core.errors import register_exception_handlers
from app.dependencies.security import parse_token_user_id_and_version, security
from app.services.jwt import JwtService
from infra.python.source_management_role_policy import validate_management_connection


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not os.environ.get("SOURCE_MANAGEMENT_USER") or config.DB_USER != os.environ["SOURCE_MANAGEMENT_USER"]:
        raise RuntimeError("Use the isolated management database role")
    if any(
        os.environ.get(key)
        for key in ("SOURCE_WRITER_PASSWORD", "DB_APP_PASSWORD", "DB_ADMIN_PASSWORD", "DB_MIGRATION_PASSWORD")
    ):
        raise RuntimeError("Management process must have only its own database credential")
    async with engine.connect() as connection:
        await validate_management_connection(connection)
    try:
        yield
    finally:
        await close_database()


management_app = FastAPI(title="Source/Catalog management", lifespan=lifespan)
register_exception_handlers(management_app)


@management_app.middleware("http")
async def request_trace(request: Request, call_next):  # type: ignore[no-untyped-def]
    request.state.trace_id = str(uuid4())
    return await call_next(request)


async def management_actor(
    credential: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ManagementActor:
    if credential is None:
        raise fail("UNAUTHORIZED", 401)
    payload = JwtService().verify_jwt(token=credential.credentials, token_type="access").payload
    user_id, token_version = parse_token_user_id_and_version(payload)
    actor = ManagementActor(user_id, token_version)
    await SourceManagementService(session).authorize(actor)
    return actor


Session = Annotated[AsyncSession, Depends(get_db_session)]
Actor = Annotated[ManagementActor, Depends(management_actor)]


@management_app.get("/management/{kind}/{target_id}", response_model=ManagementResult)
async def inspect_target(kind: TargetKind, target_id: UUID, session: Session, actor: Actor) -> ManagementResult:
    return await SourceManagementService(session).inspect_target(actor, kind, target_id)


async def _mutate(
    session: AsyncSession, actor: ManagementActor, kind: TargetKind, target_id: UUID, command: ManagementCommand
) -> ManagementResult:
    try:
        return await SourceManagementService(session).mutate(actor, kind, target_id, command)
    except IntegrityError:
        await session.rollback()
        # Never expose DB details, request contents, or audit values in an error.
        raise fail("MANAGEMENT_CONSTRAINT_CONFLICT") from None


@management_app.patch("/management/{kind}/{target_id}", response_model=ManagementResult)
async def update_target(
    kind: TargetKind, target_id: UUID, command: UpdateCommand, session: Session, actor: Actor
) -> ManagementResult:
    return await _mutate(session, actor, kind, target_id, command)


@management_app.delete("/management/{kind}/{target_id}", response_model=ManagementResult)
async def delete_target(
    kind: TargetKind, target_id: UUID, command: ManagementCommand, session: Session, actor: Actor
) -> ManagementResult:
    return await _mutate(session, actor, kind, target_id, command)
