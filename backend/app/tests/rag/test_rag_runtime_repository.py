from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.rag_evaluation import (
    EvaluationDecisionStatus,
    EvaluationExecutionStatus,
    EvaluationExperimentType,
    EvaluationVariantRole,
)
from app.models.rag_runtime import (
    RagRuntimeApprovalStatus,
    RagRuntimeBundleSource,
    RagRuntimeBundleStatus,
    RagRuntimeEnvironmentStatus,
    RagRuntimeEnvironmentTransition,
    RagRuntimeEnvironmentTransitionKind,
    RagRuntimeSourcePurpose,
)
from app.models.rag_source import RagSnapshotVerificationStatus
from app.repositories.rag_evaluation_repository import (
    EvalDatasetCreate,
    EvalExperimentCreate,
    EvalRunCreate,
    EvalVariantCreate,
    RagEvaluationRepository,
)
from app.repositories.rag_runtime_repository import (
    RagReleaseEvaluationApprovalCreate,
    RagRuntimeEnvironmentCreate,
    RagRuntimeEnvironmentTransitionCreate,
    RagRuntimeExecutionManifestCreate,
    RagRuntimeReleaseBundleCreate,
    RagRuntimeRepository,
    RuntimeEnvironmentTransitionConflictError,
    RuntimeEnvironmentTransitionInvalidError,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
from app.services.rag_runtime import RagRuntimeEnvironmentTransitionService
from app.tests.fixtures.source_snapshot import seed_snapshot
from rag_runtime.identification_preflight import (
    IdentificationSnapshotRef,
    MedicationIdentificationPreflightRequest,
    MedicationPreflightState,
    MedicationSnapshotRef,
    PreflightCurrentnessToken,
    PreflightDecision,
    PreflightStaleSignal,
    evaluate_medication_identification_preflight,
)


def _hash(char: str) -> str:
    return char * 64


async def _create_source_snapshot(session: AsyncSession):
    suffix = uuid4().hex[:10]
    repository = RagSourceCatalogRepository(session)
    source = await repository.create_source(
        RagSourceCreate(
            source_code=f"MFDS_RUNTIME_{suffix}",
            display_name="MFDS Runtime Source",
            owner_name="MFDS",
        )
    )
    endpoint = await repository.create_endpoint(
        RagSourceEndpointCreate(
            source_id=source.id,
            endpoint_code="PRODUCT_LIST",
            display_name="Product List",
        )
    )
    operation = await repository.create_operation(
        RagSourceOperationCreate(
            endpoint_id=endpoint.id,
            operation_code="LIST_PRODUCTS",
            display_name="List Products",
        )
    )
    return await seed_snapshot(
        repository,
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version=f"api:2026-09-08:{suffix}",
            raw_manifest_checksum=_hash("a"),
            canonical_checksum=_hash("b"),
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            verification_status=RagSnapshotVerificationStatus.CURRENT,
            collected_at=datetime.now(config.TIMEZONE),
            verified_at=datetime.now(config.TIMEZONE),
        ),
    )


async def _create_passed_eval_run(session: AsyncSession):
    suffix = uuid4().hex[:10]
    repository = RagEvaluationRepository(session)
    dataset = await repository.create_dataset(
        EvalDatasetCreate(
            dataset_key=f"runtime-eval-{suffix}",
            dataset_version="1.0.0",
            display_name="Runtime Evaluation Dataset",
            schema_set_id="rag-eval.schema-set",
            schema_set_version="1.0.0",
            schema_set_sha256=_hash("c"),
            manifest_hash=_hash("d"),
            source_classification="SYNTHETIC",
            case_count=0,
        )
    )
    experiment = await repository.create_experiment(
        EvalExperimentCreate(
            dataset_id=dataset.id,
            experiment_key=f"runtime-exp-{suffix}",
            experiment_version="1.0.0",
            experiment_type=EvaluationExperimentType.END_TO_END_RAG,
            policy_ref="docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md",
            policy_hash=_hash("e"),
        )
    )
    variant = await repository.create_variant(
        EvalVariantCreate(
            experiment_id=experiment.id,
            variant_key="candidate",
            variant_role=EvaluationVariantRole.CANDIDATE,
            config_hash=_hash("f"),
        )
    )
    return await repository.create_run(
        EvalRunCreate(
            run_key=f"runtime-run-{suffix}",
            dataset_id=dataset.id,
            experiment_id=experiment.id,
            variant_id=variant.id,
            experiment_type=EvaluationExperimentType.END_TO_END_RAG,
            execution_status=EvaluationExecutionStatus.COMPLETED,
            decision_status=EvaluationDecisionStatus.PASS,
            git_commit_sha="abcdef1",
            dataset_manifest_hash=dataset.manifest_hash,
            execution_manifest_hash=_hash("1"),
        )
    )


async def test_runtime_bundle_repository_can_save_minimum_graph(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    eval_run = await _create_passed_eval_run(db_session)
    repository = RagRuntimeRepository(db_session)

    manifest = await repository.create_execution_manifest(
        RagRuntimeExecutionManifestCreate(
            manifest_key="rag-runtime",
            manifest_version="2026.09.08-001",
            manifest_hash=_hash("2"),
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
            worker_artifact_ref="worker:local:test",
        )
    )
    bundle = await repository.create_release_bundle(
        RagRuntimeReleaseBundleCreate(
            bundle_key="local-rag-runtime",
            bundle_version="2026.09.08-001",
            bundle_status=RagRuntimeBundleStatus.READY,
            execution_manifest_id=manifest.id,
            bundle_manifest_hash=_hash("3"),
            environment_code="local",
            catalog_version="catalog-1.0.0",
            catalog_manifest_hash=_hash("9"),
            candidate_index_ref="candidate-index:pending-fk",
            candidate_index_version="1.0.0",
            candidate_index_manifest_hash=_hash("4"),
            created_by="backend-test",
        )
    )
    # #164 저장 구조 자체를 확인하는 테스트다. member write 경로는 #175에서
    # build_runtime_bundle 안으로 닫혔으므로, 여기서는 ORM으로 직접 넣어 컬럼·제약만 검증한다.
    bundle_source = RagRuntimeBundleSource(
        bundle_id=bundle.id,
        source_snapshot_id=snapshot.id,
        source_purpose=RagRuntimeSourcePurpose.CATALOG,
        source_version=snapshot.source_version,
        canonical_checksum=snapshot.canonical_checksum,
        approval_version="approval-v1",
        scope_policy_hash=_hash("c"),
        freshness_policy_hash=_hash("d"),
    )
    db_session.add(bundle_source)
    await db_session.flush()
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code="local",
            environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
            active_bundle_id=bundle.id,
            active_bundle_manifest_hash=bundle.bundle_manifest_hash,
        )
    )
    approval = await repository.create_evaluation_approval(
        RagReleaseEvaluationApprovalCreate(
            bundle_id=bundle.id,
            bundle_manifest_hash=bundle.bundle_manifest_hash,
            eval_run_id=eval_run.id,
            eval_decision_status=EvaluationDecisionStatus.PASS,
            approval_scope="END_TO_END_RAG",
            approval_status=RagRuntimeApprovalStatus.APPROVED,
            approved_by="reviewer",
            approved_at=datetime.now(config.TIMEZONE),
        )
    )

    assert await repository.get_execution_manifest_by_hash(_hash("2")) == manifest
    assert (
        await repository.get_release_bundle_by_version(
            bundle_key="local-rag-runtime",
            bundle_version="2026.09.08-001",
        )
        == bundle
    )
    assert await repository.get_release_bundle_by_manifest_hash(_hash("3")) == bundle
    assert await repository.list_bundle_sources(bundle.id) == [bundle_source]
    assert await repository.get_environment_by_code("local") == environment
    assert await repository.list_bundle_evaluation_approvals(bundle.id) == [approval]


async def test_environment_active_bundle_hash_must_match_bundle(db_session: AsyncSession) -> None:
    repository = RagRuntimeRepository(db_session)
    manifest = await repository.create_execution_manifest(
        RagRuntimeExecutionManifestCreate(
            manifest_key=f"rag-runtime-{uuid4().hex[:8]}",
            manifest_version="1.0.0",
            manifest_hash=_hash("5"),
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
        )
    )
    bundle = await repository.create_release_bundle(
        RagRuntimeReleaseBundleCreate(
            bundle_key=f"local-rag-runtime-{uuid4().hex[:8]}",
            bundle_version="1.0.0",
            execution_manifest_id=manifest.id,
            bundle_manifest_hash=_hash("6"),
            environment_code="local",
            catalog_version="catalog-1.0.0",
            catalog_manifest_hash=_hash("9"),
        )
    )

    with pytest.raises(IntegrityError):
        await repository.create_environment(
            RagRuntimeEnvironmentCreate(
                environment_code=f"local-{uuid4().hex[:8]}",
                active_bundle_id=bundle.id,
                active_bundle_manifest_hash=_hash("7"),
            )
        )


async def test_approved_evaluation_requires_passed_eval_run(db_session: AsyncSession) -> None:
    eval_run = await _create_passed_eval_run(db_session)
    repository = RagRuntimeRepository(db_session)
    manifest = await repository.create_execution_manifest(
        RagRuntimeExecutionManifestCreate(
            manifest_key=f"rag-runtime-{uuid4().hex[:8]}",
            manifest_version="1.0.0",
            manifest_hash=_hash("8"),
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
        )
    )
    bundle = await repository.create_release_bundle(
        RagRuntimeReleaseBundleCreate(
            bundle_key=f"local-rag-runtime-{uuid4().hex[:8]}",
            bundle_version="1.0.0",
            execution_manifest_id=manifest.id,
            bundle_manifest_hash=_hash("9"),
            environment_code="local",
            catalog_version="catalog-1.0.0",
            catalog_manifest_hash=_hash("9"),
        )
    )

    with pytest.raises(IntegrityError):
        await repository.create_evaluation_approval(
            RagReleaseEvaluationApprovalCreate(
                bundle_id=bundle.id,
                bundle_manifest_hash=bundle.bundle_manifest_hash,
                eval_run_id=eval_run.id,
                eval_decision_status=EvaluationDecisionStatus.FAIL,
                approval_scope="END_TO_END_RAG",
                approval_status=RagRuntimeApprovalStatus.APPROVED,
                approved_by="reviewer",
                approved_at=datetime.now(config.TIMEZONE),
            )
        )


async def test_evaluation_approval_bundle_hash_must_match_bundle(db_session: AsyncSession) -> None:
    eval_run = await _create_passed_eval_run(db_session)
    repository = RagRuntimeRepository(db_session)
    manifest = await repository.create_execution_manifest(
        RagRuntimeExecutionManifestCreate(
            manifest_key=f"rag-runtime-{uuid4().hex[:8]}",
            manifest_version="1.0.0",
            manifest_hash=_hash("c"),
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
        )
    )
    bundle = await repository.create_release_bundle(
        RagRuntimeReleaseBundleCreate(
            bundle_key=f"local-rag-runtime-{uuid4().hex[:8]}",
            bundle_version="1.0.0",
            execution_manifest_id=manifest.id,
            bundle_manifest_hash=_hash("d"),
            environment_code="local",
            catalog_version="catalog-1.0.0",
            catalog_manifest_hash=_hash("9"),
        )
    )

    with pytest.raises(IntegrityError):
        await repository.create_evaluation_approval(
            RagReleaseEvaluationApprovalCreate(
                bundle_id=bundle.id,
                bundle_manifest_hash=_hash("e"),
                eval_run_id=eval_run.id,
                eval_decision_status=EvaluationDecisionStatus.PASS,
                approval_scope="END_TO_END_RAG",
                approval_status=RagRuntimeApprovalStatus.APPROVED,
                approved_by="reviewer",
                approved_at=datetime.now(config.TIMEZONE),
            )
        )


async def test_environment_code_allows_only_one_row_per_environment(db_session: AsyncSession) -> None:
    repository = RagRuntimeRepository(db_session)
    environment_code = f"local-{uuid4().hex[:8]}"
    await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=environment_code,
            environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
        )
    )

    with pytest.raises(IntegrityError):
        await repository.create_environment(
            RagRuntimeEnvironmentCreate(
                environment_code=environment_code,
                environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
            )
        )


async def test_environment_transition_preserves_activation_history(db_session: AsyncSession) -> None:
    repository = RagRuntimeRepository(db_session)
    manifest = await repository.create_execution_manifest(
        RagRuntimeExecutionManifestCreate(
            manifest_key=f"rag-runtime-{uuid4().hex[:8]}",
            manifest_version="1.0.0",
            manifest_hash=_hash("a"),
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
        )
    )
    bundle = await repository.create_release_bundle(
        RagRuntimeReleaseBundleCreate(
            bundle_key=f"local-rag-runtime-{uuid4().hex[:8]}",
            bundle_version="1.0.0",
            execution_manifest_id=manifest.id,
            bundle_manifest_hash=_hash("b"),
            bundle_status=RagRuntimeBundleStatus.READY,
            governance_revision_ref="governance:test-a",
            environment_code="local",
            catalog_version="catalog-1.0.0",
            catalog_manifest_hash=_hash("9"),
        )
    )
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=f"local-{uuid4().hex[:8]}",
            environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
            governance_revision_ref="governance:test-a",
        )
    )

    transition = await RagRuntimeEnvironmentTransitionService(repository).transition(
        RagRuntimeEnvironmentTransitionCreate(
            environment_id=environment.id,
            transition_kind=RagRuntimeEnvironmentTransitionKind.PLANNED_ACTIVATION,
            expected_environment_revision=1,
            expected_safety_epoch=1,
            expected_active_bundle_id=None,
            expected_active_bundle_manifest_hash=None,
            expected_governance_revision_ref="governance:test-a",
            target_bundle_id=bundle.id,
            target_bundle_manifest_hash=bundle.bundle_manifest_hash,
            guard_decision_ref="guard-decision:test",
            created_by="backend-test",
        )
    )

    assert environment.environment_status is RagRuntimeEnvironmentStatus.ACTIVE
    assert environment.active_bundle_id == bundle.id
    assert environment.active_bundle_manifest_hash == bundle.bundle_manifest_hash
    assert environment.environment_revision == transition.environment_revision == 2
    assert transition.from_bundle_id is None and transition.to_bundle_id == bundle.id
    assert await repository.list_environment_transitions(environment.id) == [transition]

    prescription_id, version_id, medication_id = (str(uuid4()) for _ in range(3))
    pinned_bundle_id = str(uuid4())
    preflight = evaluate_medication_identification_preflight(
        MedicationIdentificationPreflightRequest(
            currentness=PreflightCurrentnessToken(
                prescription_id=prescription_id,
                pinned_prescription_version_id=version_id,
                observed_active_prescription_version_id=version_id,
                pinned_runtime_release_bundle_id=pinned_bundle_id,
                observed_active_runtime_release_bundle_id=str(environment.active_bundle_id),
                ownership_verified=True,
            ),
            medications=(
                MedicationSnapshotRef(
                    prescription_version_medication_id=medication_id,
                    prescription_version_id=version_id,
                    display_order=1,
                ),
            ),
            identifications=(
                IdentificationSnapshotRef(
                    prescription_version_medication_id=medication_id,
                    state=MedicationPreflightState.MATCHED,
                    prescription_version_id=version_id,
                    identification_id=str(uuid4()),
                    code_system="SYNTHETIC-CODE-SYSTEM",
                    canonical_code="SYNTHETIC-0001",
                    runtime_release_bundle_id=pinned_bundle_id,
                ),
            ),
        )
    )
    assert preflight.decision is PreflightDecision.STALE_FALLBACK
    assert preflight.stale_signals == (PreflightStaleSignal.RUNTIME_RELEASE_STALE,)

    wrong_governance_bundle = await repository.create_release_bundle(
        RagRuntimeReleaseBundleCreate(
            bundle_key=f"local-rag-runtime-{uuid4().hex[:8]}",
            bundle_version="1.0.0",
            execution_manifest_id=manifest.id,
            bundle_manifest_hash=_hash("c"),
            bundle_status=RagRuntimeBundleStatus.READY,
            governance_revision_ref="governance:test-b",
        )
    )
    with pytest.raises(RuntimeEnvironmentTransitionConflictError):
        await RagRuntimeEnvironmentTransitionService(repository).transition(
            RagRuntimeEnvironmentTransitionCreate(
                environment_id=environment.id,
                transition_kind=RagRuntimeEnvironmentTransitionKind.PLANNED_ACTIVATION,
                expected_environment_revision=2,
                expected_safety_epoch=1,
                expected_active_bundle_id=bundle.id,
                expected_active_bundle_manifest_hash=bundle.bundle_manifest_hash,
                expected_governance_revision_ref="governance:test-a",
                target_bundle_id=wrong_governance_bundle.id,
                target_bundle_manifest_hash=wrong_governance_bundle.bundle_manifest_hash,
                guard_decision_ref="guard-decision:wrong-governance",
                created_by="backend-test",
            )
        )
    assert environment.environment_revision == 2

    suspended = await RagRuntimeEnvironmentTransitionService(repository).transition(
        RagRuntimeEnvironmentTransitionCreate(
            environment_id=environment.id,
            transition_kind=RagRuntimeEnvironmentTransitionKind.SUSPEND,
            expected_environment_revision=2,
            expected_safety_epoch=1,
            expected_active_bundle_id=bundle.id,
            expected_active_bundle_manifest_hash=bundle.bundle_manifest_hash,
            expected_governance_revision_ref="governance:test-a",
            guard_decision_ref="guard-decision:suspend",
            transition_reason_code="SAFETY_HOLD",
            created_by="backend-test",
        )
    )
    assert environment.environment_status is RagRuntimeEnvironmentStatus.SUSPENDED
    assert environment.active_bundle_id == bundle.id
    assert suspended.environment_revision == 3

    resumed = await RagRuntimeEnvironmentTransitionService(repository).transition(
        RagRuntimeEnvironmentTransitionCreate(
            environment_id=environment.id,
            transition_kind=RagRuntimeEnvironmentTransitionKind.RESUME,
            expected_environment_revision=3,
            expected_safety_epoch=1,
            expected_active_bundle_id=bundle.id,
            expected_active_bundle_manifest_hash=bundle.bundle_manifest_hash,
            expected_governance_revision_ref="governance:test-a",
            guard_decision_ref="guard-decision:resume",
            created_by="backend-test",
        )
    )
    assert environment.environment_status is RagRuntimeEnvironmentStatus.ACTIVE
    assert resumed.environment_revision == environment.environment_revision == 4
    history = await repository.list_environment_transitions(environment.id)
    assert [item.environment_revision for item in history] == [2, 3, 4]
    assert [item.transition_kind for item in history] == [
        RagRuntimeEnvironmentTransitionKind.PLANNED_ACTIVATION,
        RagRuntimeEnvironmentTransitionKind.SUSPEND,
        RagRuntimeEnvironmentTransitionKind.RESUME,
    ]

    emergency_hold = await RagRuntimeEnvironmentTransitionService(repository).transition(
        RagRuntimeEnvironmentTransitionCreate(
            environment_id=environment.id,
            transition_kind=RagRuntimeEnvironmentTransitionKind.EMERGENCY_ROLLBACK,
            expected_environment_revision=4,
            expected_safety_epoch=1,
            expected_active_bundle_id=bundle.id,
            expected_active_bundle_manifest_hash=bundle.bundle_manifest_hash,
            expected_governance_revision_ref="governance:test-a",
            guard_decision_ref="guard-decision:emergency-hold",
            transition_reason_code="NO_ELIGIBLE_ROLLBACK_BUNDLE",
            created_by="backend-test",
        )
    )
    assert environment.environment_status is RagRuntimeEnvironmentStatus.SUSPENDED
    assert environment.active_bundle_id == bundle.id
    assert emergency_hold.environment_revision == environment.environment_revision == 5


async def test_environment_transition_stale_revision_is_fail_closed(db_session: AsyncSession) -> None:
    repository = RagRuntimeRepository(db_session)
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=f"local-{uuid4().hex[:8]}",
            environment_status=RagRuntimeEnvironmentStatus.ACTIVE,
        )
    )
    command = RagRuntimeEnvironmentTransitionCreate(
        environment_id=environment.id,
        transition_kind=RagRuntimeEnvironmentTransitionKind.SUSPEND,
        expected_environment_revision=2,
        expected_safety_epoch=1,
        expected_active_bundle_id=None,
        expected_active_bundle_manifest_hash=None,
        expected_governance_revision_ref=None,
        guard_decision_ref="guard-decision:stale",
        transition_reason_code="SAFETY_HOLD",
        created_by="backend-test",
    )
    with pytest.raises(RuntimeEnvironmentTransitionConflictError):
        await RagRuntimeEnvironmentTransitionService(repository).transition(command)
    assert environment.environment_status is RagRuntimeEnvironmentStatus.ACTIVE
    assert environment.environment_revision == 1
    assert await repository.list_environment_transitions(environment.id) == []


async def test_environment_and_transition_roll_back_together_after_flush_failure(db_session: AsyncSession) -> None:
    repository = RagRuntimeRepository(db_session)
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=f"local-{uuid4().hex[:8]}",
            environment_status=RagRuntimeEnvironmentStatus.ACTIVE,
        )
    )
    command = RagRuntimeEnvironmentTransitionCreate(
        environment_id=environment.id,
        transition_kind=RagRuntimeEnvironmentTransitionKind.SUSPEND,
        expected_environment_revision=1,
        expected_safety_epoch=1,
        expected_active_bundle_id=None,
        expected_active_bundle_manifest_hash=None,
        expected_governance_revision_ref=None,
        guard_decision_ref="guard-decision:rollback",
        transition_reason_code="SAFETY_HOLD",
        created_by="backend-test",
    )
    original_flush = db_session.flush

    async def fail_after_transition_flush(*args, **kwargs):
        has_transition = any(isinstance(row, RagRuntimeEnvironmentTransition) for row in db_session.new)
        await original_flush(*args, **kwargs)
        if has_transition:
            raise RuntimeError("synthetic transition audit failure")

    with patch.object(db_session, "flush", side_effect=fail_after_transition_flush):
        with pytest.raises(RuntimeError, match="transition audit failure"):
            await RagRuntimeEnvironmentTransitionService(repository).transition(command)
    await db_session.refresh(environment)
    assert environment.environment_status is RagRuntimeEnvironmentStatus.ACTIVE
    assert environment.environment_revision == 1
    assert await repository.list_environment_transitions(environment.id) == []
    transition = await RagRuntimeEnvironmentTransitionService(repository).transition(command)
    assert transition.environment_revision == environment.environment_revision == 2


async def test_environment_transition_requires_guard_and_authenticated_actor(db_session: AsyncSession) -> None:
    repository = RagRuntimeRepository(db_session)
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=f"local-{uuid4().hex[:8]}",
            environment_status=RagRuntimeEnvironmentStatus.ACTIVE,
        )
    )
    with pytest.raises(RuntimeEnvironmentTransitionInvalidError):
        await RagRuntimeEnvironmentTransitionService(repository).transition(
            RagRuntimeEnvironmentTransitionCreate(
                environment_id=environment.id,
                transition_kind=RagRuntimeEnvironmentTransitionKind.SUSPEND,
                expected_environment_revision=1,
                expected_safety_epoch=1,
                expected_active_bundle_id=None,
                expected_active_bundle_manifest_hash=None,
                expected_governance_revision_ref=None,
                guard_decision_ref=" ",
                transition_reason_code="SAFETY_HOLD",
                created_by=None,
            )
        )


async def _freshness_resources(session):
    repository = RagRuntimeRepository(session)
    manifest = await repository.create_execution_manifest(
        RagRuntimeExecutionManifestCreate(
            manifest_key=f"fresh-{uuid4().hex}",
            manifest_version="1",
            manifest_hash=uuid4().hex * 2,
            schema_version="1",
            git_commit_sha="synthetic",
        )
    )
    bundle = await repository.create_release_bundle(
        RagRuntimeReleaseBundleCreate(
            bundle_key=f"fresh-{uuid4().hex}",
            bundle_version="1",
            execution_manifest_id=manifest.id,
            bundle_manifest_hash=uuid4().hex * 2,
            bundle_status=RagRuntimeBundleStatus.READY,
        )
    )
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=f"fresh-{uuid4().hex}", environment_status=RagRuntimeEnvironmentStatus.SUSPENDED
        )
    )
    command = RagRuntimeEnvironmentTransitionCreate(
        environment_id=environment.id,
        transition_kind=RagRuntimeEnvironmentTransitionKind.PLANNED_ACTIVATION,
        expected_environment_revision=1,
        expected_safety_epoch=1,
        expected_active_bundle_id=None,
        expected_active_bundle_manifest_hash=None,
        expected_governance_revision_ref=None,
        target_bundle_id=bundle.id,
        target_bundle_manifest_hash=bundle.bundle_manifest_hash,
        guard_decision_ref="synthetic-guard",
        transition_reason_code="SYNTHETIC_REVIEW",
        created_by="synthetic-actor",
    )
    return environment, bundle, command


@pytest.mark.parametrize("changed", ["bundle", "environment"])
async def test_transition_refreshes_preloaded_objects_after_another_transaction(changed):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.rag_runtime import RagRuntimeEnvironment, RagRuntimeReleaseBundle
    from app.tests.conftest import test_engine

    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions.begin() as setup:
        environment, bundle, command = await _freshness_resources(setup)
    async with sessions.begin() as stale:
        old_environment = await stale.get(RagRuntimeEnvironment, environment.id)
        old_bundle = await stale.get(RagRuntimeReleaseBundle, bundle.id)
        async with sessions.begin() as concurrent:
            if changed == "bundle":
                row = await concurrent.get(RagRuntimeReleaseBundle, bundle.id)
                row.bundle_status = RagRuntimeBundleStatus.FAILED
            else:
                row = await concurrent.get(RagRuntimeEnvironment, environment.id)
                row.environment_revision += 1
        assert old_bundle.bundle_status is RagRuntimeBundleStatus.READY
        assert old_environment.environment_revision == 1
        error = (
            RuntimeEnvironmentTransitionInvalidError
            if changed == "bundle"
            else RuntimeEnvironmentTransitionConflictError
        )
        with pytest.raises(error):
            await RagRuntimeEnvironmentTransitionService(RagRuntimeRepository(stale)).transition(command)
    async with sessions() as check:
        final = await check.get(RagRuntimeEnvironment, environment.id)
        assert final.environment_status is RagRuntimeEnvironmentStatus.SUSPENDED
        assert final.active_bundle_id is None
        assert not list(
            await check.scalars(
                select(RagRuntimeEnvironmentTransition).where(
                    RagRuntimeEnvironmentTransition.environment_id == environment.id
                )
            )
        )


@pytest.mark.parametrize("kind", [*RagRuntimeEnvironmentTransitionKind, "UNSUPPORTED_FUTURE_TRANSITION"])
async def test_transition_branches_cover_every_supported_kind_and_reject_unknown(db_session, kind):
    from dataclasses import replace

    environment, bundle, command = await _freshness_resources(db_session)
    if kind == RagRuntimeEnvironmentTransitionKind.SUSPEND:
        environment.environment_status = RagRuntimeEnvironmentStatus.ACTIVE
    if kind == RagRuntimeEnvironmentTransitionKind.RESUME:
        environment.active_bundle_id = bundle.id
        environment.active_bundle_manifest_hash = bundle.bundle_manifest_hash
    command = replace(command, transition_kind=kind)
    if kind in {RagRuntimeEnvironmentTransitionKind.SUSPEND, RagRuntimeEnvironmentTransitionKind.RESUME}:
        command = replace(command, target_bundle_id=None, target_bundle_manifest_hash=None)
    repository = RagRuntimeRepository(db_session)
    if kind == "UNSUPPORTED_FUTURE_TRANSITION":
        with pytest.raises(RuntimeEnvironmentTransitionInvalidError):
            RagRuntimeEnvironmentTransitionService._validate_command(command)
        with pytest.raises(RuntimeEnvironmentTransitionInvalidError):
            await repository._resolve_transition_target(environment, command)
        with pytest.raises(RuntimeEnvironmentTransitionInvalidError):
            repository._next_environment_status(environment, command, bundle)
    else:
        RagRuntimeEnvironmentTransitionService._validate_command(command)
        target = await repository._resolve_transition_target(environment, command)
        status = repository._next_environment_status(environment, command, target)
        expected = (
            RagRuntimeEnvironmentStatus.SUSPENDED
            if kind is RagRuntimeEnvironmentTransitionKind.SUSPEND
            else RagRuntimeEnvironmentStatus.ACTIVE
        )
        assert status is expected
