from datetime import datetime
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
    RagRuntimeBundleStatus,
    RagRuntimeEnvironmentStatus,
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
    RagRuntimeBundleSourceCreate,
    RagRuntimeEnvironmentCreate,
    RagRuntimeEnvironmentTransitionCreate,
    RagRuntimeExecutionManifestCreate,
    RagRuntimeReleaseBundleCreate,
    RagRuntimeRepository,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
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
    return await repository.create_snapshot(
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
        )
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
            candidate_index_ref="candidate-index:pending-fk",
            candidate_index_manifest_hash=_hash("4"),
            created_by="backend-test",
        )
    )
    bundle_source = await repository.create_bundle_source(
        RagRuntimeBundleSourceCreate(
            bundle_id=bundle.id,
            source_snapshot_id=snapshot.id,
            source_purpose=RagRuntimeSourcePurpose.CATALOG,
        )
    )
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
        )
    )

    with pytest.raises(IntegrityError):
        await repository.create_evaluation_approval(
            RagReleaseEvaluationApprovalCreate(
                bundle_id=bundle.id,
                eval_run_id=eval_run.id,
                eval_decision_status=EvaluationDecisionStatus.FAIL,
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
        )
    )
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=f"local-{uuid4().hex[:8]}",
            environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
        )
    )

    transition = await repository.create_environment_transition(
        RagRuntimeEnvironmentTransitionCreate(
            environment_id=environment.id,
            transition_kind=RagRuntimeEnvironmentTransitionKind.PLANNED_ACTIVATION,
            to_bundle_id=bundle.id,
            to_bundle_manifest_hash=bundle.bundle_manifest_hash,
            environment_revision=1,
            safety_epoch=1,
            guard_decision_ref="guard-decision:test",
            created_by="backend-test",
        )
    )

    assert await repository.list_environment_transitions(environment.id) == [transition]
