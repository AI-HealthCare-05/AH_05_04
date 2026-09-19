"""Approved Phase B Answer Runtime Authority Binding recipes and projections."""

from collections.abc import Mapping
from decimal import Decimal
from typing import cast

from ai_worker.tasks.evaluation.answer_comparison import (
    AnswerComparisonDeltaBindings,
    AnswerComparisonSupplementalControls,
)
from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.comparison import LoadedRunBundle
from ai_worker.tasks.evaluation.config import ActualRetrievalModelConfig, DevExecutionRequest
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    AnswerRuntimeAuthorityBindingManifest,
    AnswerVariantId,
)
from ai_worker.tasks.evaluation.schemas.artifacts import RagEvaluationRun
from ai_worker.tasks.evaluation.schemas.common import MAX_SAFE_INTEGER
from ai_worker.tasks.rag.evidence_retrieval import is_valid_immutable_artifact_ref
from ai_worker.tasks.rag.evidence_search import VersionedEvidenceRetrievalConfiguration
from ai_worker.tasks.rag.guideline_card import GuidelineGenerationProvenance
from rag_runtime.request_guard_runtime_binding import RequestGuardRuntimeBindingObservation

_DELTA_AXES = frozenset(
    {
        "RETRIEVAL_PIPELINE",
        "SOURCE_INDEX",
        "RUNTIME_BUNDLE",
        "RETRIEVED_EVIDENCE",
        "FINAL_VALIDATOR",
        "CITATION_GATE",
        "SAFETY_GATE",
        "RELEASE_GATE",
    }
)


def _invalid() -> EvaluationValidationError:
    return EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)


def compute_answer_runtime_binding_manifest_sha256(payload: Mapping[str, JsonValue]) -> str:
    return canonical_sha256(
        cast(JsonValue, dict(payload)),
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )


def compute_not_applied_binding_hash(axis: str) -> str:
    if axis not in _DELTA_AXES:
        raise _invalid()
    return canonical_sha256(
        {
            "axis": axis,
            "binding_state": "NOT_APPLIED",
            "projection_version": "answer-authority-binding-v1",
        }
    )


def compute_input_context_binding_hash(bundle: LoadedRunBundle) -> str:
    cases = sorted(bundle.cases, key=lambda case: case.case_id.encode("utf-16-be"))
    return canonical_sha256(
        {
            "cases": [{"case_id": case.case_id, "input_sha256": case.input_sha256} for case in cases],
            "projection_version": "answer-input-context-v1",
        }
    )


def compute_prompt_structure_binding_hash(provenance: GuidelineGenerationProvenance) -> str:
    if (
        type(provenance) is not GuidelineGenerationProvenance
        or not is_valid_immutable_artifact_ref(provenance.prompt_ref)
        or provenance.prompt_ref.artifact_code != "guideline-prompt"
    ):
        raise _invalid()
    return provenance.prompt_ref.content_sha256


def compute_parser_binding_hash(provenance: GuidelineGenerationProvenance) -> str:
    if (
        type(provenance) is not GuidelineGenerationProvenance
        or not is_valid_immutable_artifact_ref(provenance.parser_ref)
        or provenance.parser_ref.artifact_code != "guideline-parser"
    ):
        raise _invalid()
    return provenance.parser_ref.content_sha256


def compute_seed_binding_hash(request: DevExecutionRequest) -> str:
    if type(request) is not DevExecutionRequest:
        raise _invalid()
    return canonical_sha256({"projection_version": "answer-seed-v1", "seed": request.seed})


def compute_sampling_parameters_binding_hash() -> str:
    return canonical_sha256(
        {
            "projection_version": "answer-sampling-parameters-v1",
            "temperature": "0",
        }
    )


def compute_token_limit_binding_hash(max_output_tokens: int) -> str:
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= MAX_SAFE_INTEGER:
        raise _invalid()
    return canonical_sha256(
        {
            "max_output_tokens": max_output_tokens,
            "projection_version": "answer-token-limit-v1",
        }
    )


def compute_timeout_binding_hash(timeout_seconds: int | float | Decimal) -> str:
    if type(timeout_seconds) not in (int, float, Decimal):
        raise _invalid()
    try:
        canonical_timeout = Decimal(str(timeout_seconds))
    except Exception:
        raise _invalid() from None
    if not canonical_timeout.is_finite() or canonical_timeout <= 0:
        raise _invalid()
    return canonical_sha256(
        {
            "projection_version": "answer-timeout-v1",
            "timeout_seconds": str(canonical_timeout),
        }
    )


def compute_retrieval_pipeline_binding_hash(
    variant_id: AnswerVariantId,
    retrieval_config: VersionedEvidenceRetrievalConfiguration | None = None,
) -> str:
    if type(variant_id) is not AnswerVariantId:
        raise _invalid()
    if variant_id is AnswerVariantId.ANS_BASE:
        if retrieval_config is not None:
            raise _invalid()
        return compute_not_applied_binding_hash("RETRIEVAL_PIPELINE")
    if type(retrieval_config) is not VersionedEvidenceRetrievalConfiguration or not retrieval_config.is_hash_valid():
        raise _invalid()
    return retrieval_config.artifact_ref.content_sha256


def compute_source_index_binding_hash(
    variant_id: AnswerVariantId,
    model_config: ActualRetrievalModelConfig | None = None,
) -> str:
    if type(variant_id) is not AnswerVariantId:
        raise _invalid()
    if variant_id is AnswerVariantId.ANS_BASE:
        if model_config is not None:
            raise _invalid()
        return compute_not_applied_binding_hash("SOURCE_INDEX")
    if type(model_config) is not ActualRetrievalModelConfig:
        raise _invalid()
    return model_config.knowledge_index_ref.hash


def compute_runtime_bundle_binding_hash(
    variant_id: AnswerVariantId,
    observation: RequestGuardRuntimeBindingObservation | None = None,
) -> str:
    if type(variant_id) is not AnswerVariantId:
        raise _invalid()
    if variant_id is AnswerVariantId.ANS_BASE:
        if observation is not None:
            raise _invalid()
        return compute_not_applied_binding_hash("RUNTIME_BUNDLE")
    if type(observation) is not RequestGuardRuntimeBindingObservation:
        raise _invalid()
    return canonical_sha256(
        {
            "bundle_id": str(observation.bundle_id),
            "bundle_manifest_hash": observation.bundle_manifest_hash,
            "environment": observation.environment.value,
            "projection_version": "answer-runtime-bundle-binding-v1",
        }
    )


def validate_answer_runtime_binding_manifest_for_run(
    manifest: AnswerRuntimeAuthorityBindingManifest,
    run: RagEvaluationRun,
    expected_variant: AnswerVariantId,
) -> None:
    if (
        type(expected_variant) is not AnswerVariantId
        or manifest.experiment_id != run.experiment_id
        or manifest.run_id != run.run_id
        or manifest.variant_id is not expected_variant
        or run.variant_id != expected_variant.value
    ):
        raise _invalid()


def project_answer_runtime_binding_manifest(
    manifest: AnswerRuntimeAuthorityBindingManifest,
) -> tuple[AnswerComparisonSupplementalControls, AnswerComparisonDeltaBindings]:
    supplemental = manifest.supplemental_controls
    delta = manifest.delta_bindings
    return (
        AnswerComparisonSupplementalControls(
            input_context_hash=supplemental.input_context_hash,
            prompt_structure_hash=supplemental.prompt_structure_hash,
            parser_hash=supplemental.parser_hash,
            seed_hash=supplemental.seed_hash,
            sampling_parameters_hash=supplemental.sampling_parameters_hash,
            token_limit_hash=supplemental.token_limit_hash,
            timeout_hash=supplemental.timeout_hash,
        ),
        AnswerComparisonDeltaBindings(
            retrieval_pipeline_hash=delta.retrieval_pipeline_hash,
            source_index_hash=delta.source_index_hash,
            runtime_bundle_hash=delta.runtime_bundle_hash,
            retrieved_evidence_hash=delta.retrieved_evidence_hash,
            final_validator_hash=delta.final_validator_hash,
            citation_gate_hash=delta.citation_gate_hash,
            safety_gate_hash=delta.safety_gate_hash,
            release_gate_hash=delta.release_gate_hash,
        ),
    )
