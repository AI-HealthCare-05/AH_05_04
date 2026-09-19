"""Real PostgreSQL integration coverage for #806 request-bound runtime authority."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_request_guard_runtime_binding import (
    SqlAlchemyRequestGuardRuntimeBindingReader,
)
from ai_worker.tasks.rag.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingAssemblyError,
    build_origin_request_guard_binding,
    build_runtime_authorization_binding,
)
from app.core import config
from app.models.rag_request_authority import RagRequestGuardAuthority
from app.models.rag_runtime import (
    RagRuntimeBundleStatus,
    RagRuntimeExecutionManifest,
    RagRuntimeReleaseBundle,
)
from app.models.users import User
from app.repositories.rag_request_guard_runtime_binding_repository import (
    RagRequestGuardRuntimeBindingRepository,
)
from rag_runtime.request_authority import (
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    compute_request_guard_authority_ref,
)
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    canonical_scope_manifest_hash,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode

PROJECT_ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

USER_ID = UUID("80630000-0000-4000-8000-000000000001")
MANIFEST_ID = UUID("80630000-0000-4000-8000-000000000002")
BUNDLE_ID = UUID("80630000-0000-4000-8000-000000000003")
REQUEST_ID = UUID("80630000-0000-4000-8000-000000000004")
SECOND_REQUEST_ID = UUID("80630000-0000-4000-8000-000000000005")
OPERATION = "GUIDE_SYNC_ANSWER"
SCOPES = ("GUIDE", "PATIENT_CITATION")


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch: pytest.MonkeyPatch) -> AsyncEngine:
    name = "request_binding_806_" + uuid4().hex
    original = config.database_url
    cluster = create_async_engine(original, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine = create_async_engine(make_url(original).set(database=name), hide_parameters=True)
    try:
        async with cluster.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        monkeypatch.setattr(config, "DB_NAME", name)
        await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
        yield engine
    finally:
        await engine.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await cluster.dispose()


async def _seed_authorities(
    engine: AsyncEngine,
) -> tuple[async_sessionmaker[AsyncSession], RequestAuthorityArtifactRef]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    legacy_ref = compute_request_guard_authority_ref(
        user_id=USER_ID,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
    )
    async with sessions() as session:
        session.add(
            User(
                id=USER_ID,
                email=f"request-binding-806-{uuid4().hex[:8]}@example.com",
                hashed_password="synthetic-hash",
                name="synthetic-request-binding",
            )
        )
        await session.flush()
        session.add(
            RagRuntimeExecutionManifest(
                id=MANIFEST_ID,
                manifest_key="synthetic-request-binding",
                manifest_version="1",
                manifest_hash="a" * 64,
                schema_version="1",
                git_commit_sha="8060000",
            )
        )
        await session.flush()
        session.add(
            RagRuntimeReleaseBundle(
                id=BUNDLE_ID,
                bundle_key="synthetic-request-binding",
                bundle_version="1",
                bundle_status=RagRuntimeBundleStatus.BUILDING,
                execution_manifest_id=MANIFEST_ID,
                bundle_manifest_hash="b" * 64,
                environment_code=RuntimeEnvironmentCode.PRODUCTION.value,
                catalog_version="catalog-1",
                catalog_manifest_hash="c" * 64,
            )
        )
        await session.flush()
        session.add(
            RagRequestGuardAuthority(
                id=uuid4(),
                artifact_code=legacy_ref.artifact_code,
                artifact_version=legacy_ref.version,
                artifact_content_sha256=legacy_ref.content_sha256,
                user_id=USER_ID,
                request_operation_code=OPERATION,
                decision_stage=RequestAuthorityDecisionStage.REQUEST.value,
            )
        )
        await session.commit()
    return sessions, legacy_ref


def _observation(
    legacy_ref,
    *,
    request_id: UUID = REQUEST_ID,
    outcome: RequestAuthorityDecisionOutcome = RequestAuthorityDecisionOutcome.PASS,
) -> RequestGuardRuntimeBindingObservation:
    return RequestGuardRuntimeBindingObservation(
        request_guard_decision_id=request_id,
        actual_decision_outcome=outcome,
        user_id=USER_ID,
        request_operation_code=OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
        environment=RuntimeEnvironmentCode.PRODUCTION,
        bundle_id=BUNDLE_ID,
        bundle_manifest_hash="b" * 64,
        request_scope_codes=SCOPES,
        scope_manifest_hash=canonical_scope_manifest_hash(SCOPES),
        legacy_request_authority_ref=legacy_ref,
    )


async def test_committed_pass_is_exactly_read_and_projects_existing_bindings(database: AsyncEngine) -> None:
    sessions, legacy_ref = await _seed_authorities(database)
    observation = _observation(legacy_ref)

    async with sessions() as session:
        reference = await RagRequestGuardRuntimeBindingRepository(session).record(observation)
        await session.commit()

    persisted = await SqlAlchemyRequestGuardRuntimeBindingReader(sessions).read_exact(reference)
    assert persisted is not None
    assert persisted == observation
    assert build_runtime_authorization_binding(persisted)
    origin = build_origin_request_guard_binding(persisted)
    assert origin.guard_ref.content_sha256 == reference.content_sha256
    assert origin.decision.value == "PASS"


async def test_same_user_and_operation_different_requests_have_distinct_refs(database: AsyncEngine) -> None:
    sessions, legacy_ref = await _seed_authorities(database)
    async with sessions() as session:
        first = await RagRequestGuardRuntimeBindingRepository(session).record(_observation(legacy_ref))
        second = await RagRequestGuardRuntimeBindingRepository(session).record(
            _observation(legacy_ref, request_id=SECOND_REQUEST_ID)
        )
        await session.commit()

    assert first != second


async def test_committed_fail_is_historical_but_cannot_build_citation_bindings(database: AsyncEngine) -> None:
    sessions, legacy_ref = await _seed_authorities(database)
    observation = _observation(legacy_ref, outcome=RequestAuthorityDecisionOutcome.FAIL)
    async with sessions() as session:
        reference = await RagRequestGuardRuntimeBindingRepository(session).record(observation)
        await session.commit()

    persisted = await SqlAlchemyRequestGuardRuntimeBindingReader(sessions).read_exact(reference)
    assert persisted is not None
    assert persisted == observation
    with pytest.raises(RequestGuardRuntimeBindingAssemblyError):
        build_runtime_authorization_binding(persisted)
    with pytest.raises(RequestGuardRuntimeBindingAssemblyError):
        build_origin_request_guard_binding(persisted)
