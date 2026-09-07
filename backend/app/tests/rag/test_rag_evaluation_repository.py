from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_evaluation import (
    EvaluationDatasetPartition,
    EvaluationDecisionStatus,
    EvaluationExecutionStatus,
    EvaluationExperimentType,
    EvaluationFailureScope,
    EvaluationMetricScope,
    EvaluationVariantRole,
)
from app.repositories.rag_evaluation_repository import (
    EvalCaseCreate,
    EvalCaseResultCreate,
    EvalDatasetCreate,
    EvalExperimentCreate,
    EvalFailureCreate,
    EvalMetricCreate,
    EvalRunCreate,
    EvalVariantCreate,
    RagEvaluationRepository,
)


async def test_rag_evaluation_repository_can_save_and_read_minimum_graph(db_session: AsyncSession) -> None:
    repository = RagEvaluationRepository(db_session)
    suffix = uuid4().hex[:10]

    dataset = await repository.create_dataset(
        EvalDatasetCreate(
            dataset_key=f"synthetic-eval-{suffix}",
            dataset_version="1.0.0",
            display_name="Synthetic Evaluation Dataset",
            schema_set_id="rag-eval.schema-set",
            schema_set_version="1.2.0",
            schema_set_sha256="a" * 64,
            manifest_hash=f"{suffix:0<64}"[:64],
            source_classification="SYNTHETIC",
            case_count=1,
        )
    )
    eval_case = await repository.create_case(
        EvalCaseCreate(
            dataset_id=dataset.id,
            case_key="case-001",
            case_version="1.0.0",
            partition=EvaluationDatasetPartition.DEV,
            experiment_type=EvaluationExperimentType.END_TO_END_RAG,
            input_hash="b" * 64,
            expected_scope_codes=["ROUTINE_MEDICATION_GUIDE"],
        )
    )
    experiment = await repository.create_experiment(
        EvalExperimentCreate(
            dataset_id=dataset.id,
            experiment_key=f"experiment-{suffix}",
            experiment_version="1.0.0",
            experiment_type=EvaluationExperimentType.END_TO_END_RAG,
            policy_ref="docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md",
            policy_hash="c" * 64,
        )
    )
    variant = await repository.create_variant(
        EvalVariantCreate(
            experiment_id=experiment.id,
            variant_key="candidate",
            variant_role=EvaluationVariantRole.CANDIDATE,
            config_hash="d" * 64,
        )
    )
    run = await repository.create_run(
        EvalRunCreate(
            run_key=f"run-{suffix}",
            dataset_id=dataset.id,
            experiment_id=experiment.id,
            variant_id=variant.id,
            execution_status=EvaluationExecutionStatus.COMPLETED,
            decision_status=EvaluationDecisionStatus.PASS,
            git_commit_sha="abcdef1",
            dataset_manifest_hash=dataset.manifest_hash,
        )
    )
    case_result = await repository.create_case_result(
        EvalCaseResultCreate(
            run_id=run.id,
            case_id=eval_case.id,
            dataset_id=dataset.id,
            execution_status=EvaluationExecutionStatus.COMPLETED,
            decision_status=EvaluationDecisionStatus.PASS,
            result_summary_hash="e" * 64,
            non_sensitive_summary={"case_key": eval_case.case_key},
        )
    )
    metric = await repository.create_metric(
        EvalMetricCreate(
            run_id=run.id,
            metric_scope=EvaluationMetricScope.RUN,
            metric_key="retrieval_recall_at_5",
            metric_version="1.0.0",
            metric_area="retrieval",
            numerator=9,
            denominator=10,
            score=Decimal("0.90000000"),
        )
    )
    failure = await repository.create_failure(
        EvalFailureCreate(
            case_result_id=case_result.id,
            failure_scope=EvaluationFailureScope.CASE,
            failure_code="NO_APPROVED_EVIDENCE",
            failure_area="citation",
            context_hash="f" * 64,
        )
    )

    assert (
        await repository.get_dataset_by_version(
            dataset_key=dataset.dataset_key, dataset_version=dataset.dataset_version
        )
        == dataset
    )
    assert await repository.get_dataset_by_manifest_hash(manifest_hash=dataset.manifest_hash) == dataset
    assert await repository.get_case_by_key(dataset_id=dataset.id, case_key=eval_case.case_key) == eval_case
    assert (
        await repository.get_experiment_by_version(
            experiment_key=experiment.experiment_key, experiment_version=experiment.experiment_version
        )
        == experiment
    )
    assert await repository.get_variant_by_key(experiment_id=experiment.id, variant_key=variant.variant_key) == variant
    assert (
        await repository.get_variant_by_config_hash(experiment_id=experiment.id, config_hash=variant.config_hash)
        == variant
    )
    assert await repository.get_run_by_key(run_key=run.run_key) == run
    assert await repository.get_case_result(run_id=run.id, case_id=eval_case.id) == case_result
    assert (
        await repository.get_run_metric(
            run_id=run.id, metric_key=metric.metric_key, metric_version=metric.metric_version
        )
        == metric
    )
    assert failure.case_result_id == case_result.id
