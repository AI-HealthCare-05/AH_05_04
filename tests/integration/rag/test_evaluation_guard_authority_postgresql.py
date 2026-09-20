"""Real PostgreSQL integration coverage for #162 Canonical Evaluation Guard Authority Reader."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_evaluation_guard_authority import (
    EvaluationGuardAuthorityReadError,
    SqlAlchemyEvaluationGuardAuthorityReader,
)
from ai_worker.tasks.rag.runtime_bundle_builder import (
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberIdentity,
    RuntimeBundleCanonicalConfiguration,
    RuntimeBundleCitationApprovalPinIdentity,
    RuntimeBundleMemberPurpose,
    RuntimeBundleSourceMemberIdentity,
    RuntimeExecutionManifestInput,
    canonical_execution_manifest_hash,
    canonical_runtime_bundle_manifest_hash,
)
from app.models.rag_runtime import (
    RagRuntimeBundleCitationApproval,
    RagRuntimeBundleSource,
    RagRuntimeBundleStatus,
    RagRuntimeEnvironment,
    RagRuntimeEnvironmentStatus,
    RagRuntimeExecutionManifest,
    RagRuntimeReleaseBundle,
    RagRuntimeSourcePurpose,
)
from app.models.users import User
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
from app.repositories.rag_source_use_approval_repository import (
    RagSourceUseApprovalRepository,
    SourceUseApprovalCreate,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import SourceUsePurpose

PROJECT_ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

ENVIRONMENT_CODE = "LOCAL"
GOVERNANCE_REV = "GOV-REV-162-INTEG"
MANIFEST_ID = UUID("16200000-0000-4000-8000-000000000003")
BUNDLE_ID = UUID("16200000-0000-4000-8000-000000000004")


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    try:
        from app.core import config

        original = config.database_url
        name = "eval_guard_162_" + uuid4().hex
        cluster = create_async_engine(original, isolation_level="AUTOCOMMIT", hide_parameters=True)
        engine = create_async_engine(make_url(original).set(database=name), hide_parameters=True)
        async with cluster.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:
        pytest.skip(f"PostgreSQL server not available for integration testing: {exc}")

    try:
        monkeypatch.setattr(config, "DB_NAME", name)
        await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
        yield engine
    finally:
        await engine.dispose()
        try:
            async with cluster.connect() as connection:
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        except Exception:
            pass
        await cluster.dispose()


async def _seed_database(
    engine: AsyncEngine,
    *,
    bundle_status: RagRuntimeBundleStatus = RagRuntimeBundleStatus.BUILDING,
    tamper_bundle_hash: bool = False,
) -> tuple[async_sessionmaker, str, str]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    suffix = uuid4().hex[:8]

    async with sessions() as session:
        actor = User(
            email=f"actor-162-{suffix}@example.com",
            hashed_password="synthetic-hash",
            name="Actor 162",
        )
        session.add(actor)
        await session.flush()

        catalog_repo = RagSourceCatalogRepository(session)
        source = await catalog_repo.create_source(
            RagSourceCreate(source_code=f"MFDS_{suffix}", display_name="MFDS Integration Source")
        )
        endpoint = await catalog_repo.create_endpoint(
            RagSourceEndpointCreate(source_id=source.id, endpoint_code="PRODUCT_LIST", display_name="Product List")
        )
        operation = await catalog_repo.create_operation(
            RagSourceOperationCreate(
                endpoint_id=endpoint.id,
                operation_code="LIST_PRODUCTS",
                display_name="List Products",
            )
        )
        snapshot = await catalog_repo.create_snapshot(
            RagSourceSnapshotCreate(
                operation_id=operation.id,
                source_version=f"api:2026-09-20:{suffix}",
                raw_manifest_checksum="1" * 64,
                canonical_checksum="1" * 64,
                schema_version="schema-v1",
                parser_version="parser-v1",
                normalization_version="normalization-v1",
                canonicalization_spec_version="canonical-v1",
                record_count=1,
                rejected_record_count=0,
                collected_at=now,
            )
        )

        approval_repo = RagSourceUseApprovalRepository(session)
        approval = await approval_repo.create_approval(
            SourceUseApprovalCreate(
                source_snapshot_id=snapshot.id,
                source_code=source.source_code,
                source_version=snapshot.source_version,
                environment=RuntimeEnvironmentCode.LOCAL,
                purpose=SourceUsePurpose.PATIENT_CITATION,
                approval_version="1.0.0",
                valid_from=now - timedelta(hours=1),
                expires_at=now + timedelta(days=30),
                actor_id=actor.id,
                evidence_ref="evidence://162/integration",
            )
        )

        manifest_input = RuntimeExecutionManifestInput(
            manifest_key="eval-manifest-162",
            manifest_version="1.0.0",
            schema_version="runtime-manifest-v1",
            git_commit_sha="a" * 40,
            worker_artifact_ref="worker-ref-1",
            model_ref="gpt-4o",
            prompt_ref="prompt-ref-1",
            parser_ref="parser-ref-1",
            resolver_ref="resolver-ref-1",
            guard_policy_ref="guard-policy-1",
        )
        manifest_hash = canonical_execution_manifest_hash(manifest_input)

        source_member = RuntimeBundleSourceMemberIdentity(
            source_snapshot_id=str(snapshot.id),
            source_purpose=RuntimeBundleMemberPurpose.CATALOG,
            source_version=snapshot.source_version,
            canonical_checksum=snapshot.canonical_checksum,
            approval_version="1.0.0",
            scope_policy_hash="2" * 64,
            freshness_policy_hash="3" * 64,
            required=True,
            selected_for_operation=True,
        )

        citation_pin = RuntimeBundleCitationApprovalPinIdentity(
            source_snapshot_id=str(snapshot.id),
            source_use_approval_id=str(approval.id),
            source_code=source.source_code,
            source_version=snapshot.source_version,
            approval_version="1.0.0",
            environment=ENVIRONMENT_CODE,
            purpose=SourceUsePurpose.PATIENT_CITATION,
        )

        artifact_members = (
            RuntimeBundleArtifactMemberIdentity(
                artifact_kind=RuntimeBundleArtifactKind.CANDIDATE_INDEX,
                artifact_ref="idx-ref-1",
                artifact_version="1.0.0",
                manifest_hash="4" * 64,
            ),
            RuntimeBundleArtifactMemberIdentity(
                artifact_kind=RuntimeBundleArtifactKind.RULE_SET,
                artifact_ref="rule-ref-1",
                artifact_version="1.0.0",
                manifest_hash=None,
            ),
        )

        computed_bundle_hash = canonical_runtime_bundle_manifest_hash(
            RuntimeBundleCanonicalConfiguration(
                environment_code=ENVIRONMENT_CODE,
                execution_manifest_hash=manifest_hash,
                catalog_version="cat-1.0.0",
                catalog_manifest_hash="5" * 64,
                source_members=(source_member,),
                artifact_members=artifact_members,
                citation_approval_pins=(citation_pin,),
            )
        )

        stored_bundle_hash = "9" * 64 if tamper_bundle_hash else computed_bundle_hash

        manifest = RagRuntimeExecutionManifest(
            id=MANIFEST_ID,
            manifest_key=manifest_input.manifest_key,
            manifest_version=manifest_input.manifest_version,
            manifest_hash=manifest_hash,
            schema_version=manifest_input.schema_version,
            git_commit_sha=manifest_input.git_commit_sha,
            worker_artifact_ref=manifest_input.worker_artifact_ref,
            model_ref=manifest_input.model_ref,
            prompt_ref=manifest_input.prompt_ref,
            parser_ref=manifest_input.parser_ref,
            resolver_ref=manifest_input.resolver_ref,
            guard_policy_ref=manifest_input.guard_policy_ref,
        )
        session.add(manifest)
        await session.flush()

        bundle = RagRuntimeReleaseBundle(
            id=BUNDLE_ID,
            bundle_key="eval-bundle-162",
            bundle_version="1.0.0",
            bundle_status=bundle_status,
            execution_manifest_id=MANIFEST_ID,
            bundle_manifest_hash=stored_bundle_hash,
            environment_code=ENVIRONMENT_CODE,
            catalog_version="cat-1.0.0",
            catalog_manifest_hash="5" * 64,
            candidate_index_ref="idx-ref-1",
            candidate_index_version="1.0.0",
            candidate_index_manifest_hash="4" * 64,
            rule_set_ref="rule-ref-1",
            rule_set_version="1.0.0",
            governance_revision_ref=GOVERNANCE_REV,
        )
        session.add(bundle)
        await session.flush()

        b_source = RagRuntimeBundleSource(
            bundle_id=BUNDLE_ID,
            source_snapshot_id=snapshot.id,
            source_version=snapshot.source_version,
            canonical_checksum=snapshot.canonical_checksum,
            approval_version="1.0.0",
            scope_policy_hash="2" * 64,
            freshness_policy_hash="3" * 64,
            source_purpose=RagRuntimeSourcePurpose.CATALOG,
            required=True,
            selected_for_operation=True,
        )
        session.add(b_source)

        b_pin = RagRuntimeBundleCitationApproval(
            bundle_id=BUNDLE_ID,
            bundle_manifest_hash=stored_bundle_hash,
            source_snapshot_id=snapshot.id,
            source_use_approval_id=approval.id,
            source_code=source.source_code,
            source_version=snapshot.source_version,
            approval_version="1.0.0",
            environment=ENVIRONMENT_CODE,
            purpose=SourceUsePurpose.PATIENT_CITATION.value,
        )
        session.add(b_pin)

        env = RagRuntimeEnvironment(
            environment_code=ENVIRONMENT_CODE,
            environment_status=RagRuntimeEnvironmentStatus.ACTIVE,
            active_bundle_id=None,
            active_bundle_manifest_hash=None,
            environment_revision=10,
            safety_epoch=4,
            governance_revision_ref=GOVERNANCE_REV,
        )
        session.add(env)

        await session.commit()

    return sessions, manifest_hash, computed_bundle_hash


async def test_read_candidate_start_from_persisted_postgresql(database: AsyncEngine) -> None:
    sessions, expected_manifest_hash, expected_bundle_hash = await _seed_database(database)
    reader = SqlAlchemyEvaluationGuardAuthorityReader(sessions)

    obs = await reader.read_candidate_start(
        bundle_id=BUNDLE_ID,
        bundle_manifest_hash=expected_bundle_hash,
    )

    assert obs is not None
    assert obs.bundle_id == BUNDLE_ID
    assert obs.bundle_manifest_hash == expected_bundle_hash
    assert obs.bundle_status == "BUILDING"
    assert obs.environment_code == "LOCAL"
    assert obs.runtime_execution_manifest_id == MANIFEST_ID
    assert obs.runtime_execution_manifest_hash == expected_manifest_hash
    assert obs.governance_revision_ref == GOVERNANCE_REV
    assert obs.environment_revision == 10
    assert obs.safety_epoch == 4


async def test_read_environment_fence_from_persisted_postgresql(database: AsyncEngine) -> None:
    sessions, _, _ = await _seed_database(database)
    reader = SqlAlchemyEvaluationGuardAuthorityReader(sessions)

    fence = await reader.read_environment_fence(ENVIRONMENT_CODE)
    assert fence is not None
    assert fence.environment_code == ENVIRONMENT_CODE
    assert fence.environment_revision == 10
    assert fence.safety_epoch == 4
    assert fence.governance_revision_ref == GOVERNANCE_REV


async def test_read_candidate_start_fails_when_bundle_manifest_hash_corrupted(database: AsyncEngine) -> None:
    sessions, _, _ = await _seed_database(database, tamper_bundle_hash=True)
    reader = SqlAlchemyEvaluationGuardAuthorityReader(sessions)

    with pytest.raises(
        EvaluationGuardAuthorityReadError, match="Runtime Bundle rows do not match stored bundle_manifest_hash"
    ):
        await reader.read_candidate_start(
            bundle_id=BUNDLE_ID,
            bundle_manifest_hash="9" * 64,
        )


async def test_read_candidate_start_fails_when_bundle_status_not_building(database: AsyncEngine) -> None:
    sessions, _, bundle_hash = await _seed_database(database, bundle_status=RagRuntimeBundleStatus.READY)
    reader = SqlAlchemyEvaluationGuardAuthorityReader(sessions)

    with pytest.raises(EvaluationGuardAuthorityReadError, match="must be BUILDING"):
        await reader.read_candidate_start(
            bundle_id=BUNDLE_ID,
            bundle_manifest_hash=bundle_hash,
        )
