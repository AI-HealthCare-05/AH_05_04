from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from ai_worker.tasks.evaluation.canonical import (
    JsonValue,
    canonical_json_bytes,
    canonical_sha256,
    sha256_hex,
)
from ai_worker.tasks.evaluation.comparison import LoadedRunBundle, build_scope_comparisons
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import parse_json_object_bytes
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    ANS_BASE_TO_ANS_FINAL_DELTA_KEYS,
    ANS_BASE_TO_ANS_RAG_DELTA_KEYS,
    ANS_RAG_TO_ANS_FINAL_DELTA_KEYS,
    ANSWER_COMPARISON_SET_MANIFEST_ADAPTER,
    CANONICAL_ANSWER_COMPARISON_PAIRS,
    AnswerComparisonPairId,
    AnswerComparisonPairManifestEntry,
    AnswerComparisonSetManifest,
    AnswerVariantId,
    GitCommitSha,
)
from ai_worker.tasks.evaluation.schemas.artifacts import (
    ComparisonResult,
    ControlledVariableCheck,
    RagEvaluationRun,
)
from ai_worker.tasks.evaluation.schemas.common import (
    DecisionStatus,
    ExecutionStatus,
    ExperimentType,
    ImmutableReference,
    Sha256Hex,
)

_SHA_ADAPTER: TypeAdapter[str] = TypeAdapter(Sha256Hex)
_GIT_SHA_ADAPTER: TypeAdapter[str] = TypeAdapter(GitCommitSha)


def _validate_sha_binding(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _SHA_ADAPTER.validate_python(value)
    except (ValidationError, ValueError):
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID) from None


def _validate_commit_sha(value: str) -> str:
    try:
        return _GIT_SHA_ADAPTER.validate_python(value)
    except (ValidationError, ValueError):
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID) from None


ORDERED_ANSWER_CONTROLLED_VARIABLE_KEYS: tuple[str, ...] = (
    "CASE_SET",
    "DATASET",
    "PARTITION",
    "GOLD",
    "RUBRIC",
    "METRIC_POLICY",
    "INPUT_CONTEXT",
    "MODEL_CONFIGURATION",
    "PROMPT_STRUCTURE",
    "PARSER",
    "SEED",
    "SAMPLING_PARAMETERS",
    "TOKEN_LIMIT",
    "TIMEOUT",
)

ANSWER_COMPARISON_RELATIVE_PATHS: dict[str, str] = {
    AnswerComparisonPairId.ANS_BASE_ANS_RAG.value: "ans-base--ans-rag/comparison.json",
    AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value: "ans-rag--ans-final/comparison.json",
    AnswerComparisonPairId.ANS_BASE_ANS_FINAL.value: "ans-base--ans-final/comparison.json",
}

ANSWER_COMPARISON_SET_MANIFEST_FILENAME = "comparison-set-manifest.json"

_ALLOWED_DELTA_KEYS_BY_PAIR: dict[str, tuple[str, ...]] = {
    AnswerComparisonPairId.ANS_BASE_ANS_RAG.value: ANS_BASE_TO_ANS_RAG_DELTA_KEYS,
    AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value: ANS_RAG_TO_ANS_FINAL_DELTA_KEYS,
    AnswerComparisonPairId.ANS_BASE_ANS_FINAL.value: ANS_BASE_TO_ANS_FINAL_DELTA_KEYS,
}

_DELTA_KEY_TO_FIELD: dict[str, str] = {
    "RETRIEVAL_PIPELINE": "retrieval_pipeline_hash",
    "SOURCE_INDEX": "source_index_hash",
    "RUNTIME_BUNDLE": "runtime_bundle_hash",
    "RETRIEVED_EVIDENCE": "retrieved_evidence_hash",
    "FINAL_VALIDATOR": "final_validator_hash",
    "CITATION_GATE": "citation_gate_hash",
    "SAFETY_GATE": "safety_gate_hash",
    "RELEASE_GATE": "release_gate_hash",
}

_SUPPLEMENTAL_CONTROL_KEY_TO_FIELD: dict[str, str] = {
    "INPUT_CONTEXT": "input_context_hash",
    "PROMPT_STRUCTURE": "prompt_structure_hash",
    "PARSER": "parser_hash",
    "SEED": "seed_hash",
    "SAMPLING_PARAMETERS": "sampling_parameters_hash",
    "TOKEN_LIMIT": "token_limit_hash",
    "TIMEOUT": "timeout_hash",
}


@dataclass(frozen=True, slots=True)
class AnswerComparisonSupplementalControls:
    input_context_hash: str | None = None
    prompt_structure_hash: str | None = None
    parser_hash: str | None = None
    seed_hash: str | None = None
    sampling_parameters_hash: str | None = None
    token_limit_hash: str | None = None
    timeout_hash: str | None = None

    def __post_init__(self) -> None:
        _validate_sha_binding(self.input_context_hash)
        _validate_sha_binding(self.prompt_structure_hash)
        _validate_sha_binding(self.parser_hash)
        _validate_sha_binding(self.seed_hash)
        _validate_sha_binding(self.sampling_parameters_hash)
        _validate_sha_binding(self.token_limit_hash)
        _validate_sha_binding(self.timeout_hash)


@dataclass(frozen=True, slots=True)
class AnswerComparisonDeltaBindings:
    retrieval_pipeline_hash: str | None = None
    source_index_hash: str | None = None
    runtime_bundle_hash: str | None = None
    retrieved_evidence_hash: str | None = None
    final_validator_hash: str | None = None
    citation_gate_hash: str | None = None
    safety_gate_hash: str | None = None
    release_gate_hash: str | None = None

    def __post_init__(self) -> None:
        _validate_sha_binding(self.retrieval_pipeline_hash)
        _validate_sha_binding(self.source_index_hash)
        _validate_sha_binding(self.runtime_bundle_hash)
        _validate_sha_binding(self.retrieved_evidence_hash)
        _validate_sha_binding(self.final_validator_hash)
        _validate_sha_binding(self.citation_gate_hash)
        _validate_sha_binding(self.safety_gate_hash)
        _validate_sha_binding(self.release_gate_hash)


@dataclass(frozen=True, slots=True)
class AnswerDraftInputBinding:
    case_id: str
    draft_answer_sha256: str

    def __post_init__(self) -> None:
        _validate_sha_binding(self.draft_answer_sha256)


@dataclass(frozen=True, slots=True)
class AnswerComparisonControlBindings:
    case_set: str
    dataset: str
    partition: str
    gold: str
    rubric: str
    metric_policy: str
    input_context: str
    model_configuration: str
    prompt_structure: str
    parser: str
    seed: str
    sampling_parameters: str
    token_limit: str
    timeout: str


@dataclass(frozen=True, slots=True)
class AnswerComparisonRunInput:
    bundle: LoadedRunBundle
    supplemental_controls: AnswerComparisonSupplementalControls
    delta_bindings: AnswerComparisonDeltaBindings
    runner_commit_sha: str
    draft_answer_bindings: tuple[AnswerDraftInputBinding, ...] = ()

    def __post_init__(self) -> None:
        _validate_commit_sha(self.runner_commit_sha)
        case_ids = [item.case_id for item in self.draft_answer_bindings]
        if len(case_ids) != len(set(case_ids)):
            raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)
        if case_ids != sorted(case_ids, key=lambda s: s.encode("utf-16-be")):
            raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)


@dataclass(frozen=True, slots=True)
class AnswerComparisonSetBuildResult:
    manifest: AnswerComparisonSetManifest
    manifest_bytes: bytes
    comparisons: dict[str, ComparisonResult]
    comparison_files: dict[str, bytes]


def derive_known_run_controls(run: RagEvaluationRun) -> dict[str, str]:
    return {
        "CASE_SET": run.partition_manifest_hash,
        "DATASET": run.dataset_manifest_sha256,
        "PARTITION": canonical_sha256([partition.value for partition in run.evaluated_partitions]),
        "GOLD": canonical_sha256(
            {
                "resource_set_hash": run.resource_set_hash,
                "evidence_mapping_manifest_sha256": run.evidence_mapping_manifest_sha256,
            }
        ),
        "RUBRIC": run.critical_claim_rubric_ref.hash,
        "METRIC_POLICY": run.comparison_policy_ref.hash,
        "MODEL_CONFIGURATION": run.model_config_hash,
    }


def compute_comparison_sha256(comparison: ComparisonResult) -> str:
    payload = comparison.model_dump(mode="json")
    return sha256_hex(canonical_json_bytes(payload))


def compute_comparison_semantic_hash(comparison: ComparisonResult) -> str:
    payload = comparison.model_dump(mode="json")
    payload.pop("run_id", None)
    payload.pop("baseline_run_id", None)
    payload.pop("candidate_run_id", None)
    return canonical_sha256(payload)


def validate_answer_pair_identity(
    pair_id: str | AnswerComparisonPairId,
    baseline: AnswerComparisonRunInput,
    candidate: AnswerComparisonRunInput,
) -> None:
    pair_id_str = pair_id.value if isinstance(pair_id, AnswerComparisonPairId) else pair_id
    if pair_id_str not in CANONICAL_ANSWER_COMPARISON_PAIRS:
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)

    expected_baseline, expected_candidate = {
        AnswerComparisonPairId.ANS_BASE_ANS_RAG.value: (AnswerVariantId.ANS_BASE, AnswerVariantId.ANS_RAG),
        AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value: (AnswerVariantId.ANS_RAG, AnswerVariantId.ANS_FINAL),
        AnswerComparisonPairId.ANS_BASE_ANS_FINAL.value: (AnswerVariantId.ANS_BASE, AnswerVariantId.ANS_FINAL),
    }[pair_id_str]

    base_run = baseline.bundle.run
    cand_run = candidate.bundle.run

    allowed_exp_types = {ExperimentType.ANSWER_GROUNDING_SAFETY, ExperimentType.END_TO_END_RAG}
    if (
        base_run.experiment_type not in allowed_exp_types
        or cand_run.experiment_type not in allowed_exp_types
        or base_run.experiment_type != cand_run.experiment_type
        or base_run.experiment_id != cand_run.experiment_id
        or base_run.run_id == cand_run.run_id
        or base_run.variant_id != expected_baseline.value
        or cand_run.variant_id != expected_candidate.value
        or base_run.answer_variant_manifest_hash is None
        or cand_run.answer_variant_manifest_hash is None
        or tuple(p.value for p in base_run.evaluated_partitions) != ("DEV",)
        or tuple(p.value for p in cand_run.evaluated_partitions) != ("DEV",)
        or base_run.execution_status is not ExecutionStatus.COMPLETED
        or cand_run.execution_status is not ExecutionStatus.COMPLETED
    ):
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)


def _check_cases_match(
    base_cases: tuple[Any, ...],
    cand_cases: tuple[Any, ...],
) -> bool:
    return (
        bool(base_cases)
        and len(base_cases) == len(cand_cases)
        and [c.case_id for c in base_cases] == [c.case_id for c in cand_cases]
        and all(b.input_sha256 == c.input_sha256 for b, c in zip(base_cases, cand_cases, strict=True))
    )


def _check_drafts_match(
    pair_id_str: str,
    baseline: AnswerComparisonRunInput,
    candidate: AnswerComparisonRunInput,
) -> bool:
    if pair_id_str != AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value:
        return True
    cand_drafts = candidate.draft_answer_bindings
    base_cases = baseline.bundle.cases
    cand_cases = candidate.bundle.cases
    if not cand_drafts or len(cand_drafts) != len(cand_cases):
        return False
    if [d.case_id for d in cand_drafts] != [c.case_id for c in cand_cases]:
        return False
    draft_dict = {d.case_id: d.draft_answer_sha256 for d in cand_drafts}
    return all(c.answer_sha256 is not None and draft_dict.get(c.case_id) == c.answer_sha256 for c in base_cases)


def _check_delta_bindings(
    pair_id_str: str,
    baseline: AnswerComparisonRunInput,
    candidate: AnswerComparisonRunInput,
) -> tuple[bool, bool]:
    """Returns (delta_binding_missing, unauthorized_delta_changed)."""
    for attr in _DELTA_KEY_TO_FIELD.values():
        if getattr(baseline.delta_bindings, attr) is None or getattr(candidate.delta_bindings, attr) is None:
            return True, False

    allowed_deltas = frozenset(_ALLOWED_DELTA_KEYS_BY_PAIR[pair_id_str])
    all_8_delta_keys = set(_DELTA_KEY_TO_FIELD.keys())
    non_allowed_deltas = all_8_delta_keys - allowed_deltas

    for key in non_allowed_deltas:
        attr = _DELTA_KEY_TO_FIELD[key]
        if getattr(baseline.delta_bindings, attr) != getattr(candidate.delta_bindings, attr):
            return False, True

    return False, False


def _build_controlled_checks(
    baseline: AnswerComparisonRunInput,
    candidate: AnswerComparisonRunInput,
    base_known: Mapping[str, str],
    cand_known: Mapping[str, str],
) -> tuple[ControlledVariableCheck, ...]:
    checks: list[ControlledVariableCheck] = []
    for key in ORDERED_ANSWER_CONTROLLED_VARIABLE_KEYS:
        if key in base_known:
            b_val = base_known[key]
            c_val = cand_known[key]
        else:
            attr = _SUPPLEMENTAL_CONTROL_KEY_TO_FIELD[key]
            b_val = cast(str, getattr(baseline.supplemental_controls, attr))
            c_val = cast(str, getattr(candidate.supplemental_controls, attr))
        checks.append(
            ControlledVariableCheck(
                variable_key=key,
                baseline_value_hash=b_val,
                candidate_value_hash=c_val,
                matched=b_val == c_val,
            )
        )
    return tuple(checks)


def build_answer_pair_comparison(
    pair_id: str | AnswerComparisonPairId,
    baseline: AnswerComparisonRunInput,
    candidate: AnswerComparisonRunInput,
) -> ComparisonResult:
    pair_id_str = pair_id.value if isinstance(pair_id, AnswerComparisonPairId) else pair_id
    validate_answer_pair_identity(pair_id_str, baseline, candidate)

    base_run = baseline.bundle.run
    cand_run = candidate.bundle.run

    cases_match = _check_cases_match(baseline.bundle.cases, candidate.bundle.cases)
    drafts_match = _check_drafts_match(pair_id_str, baseline, candidate)
    delta_binding_missing, unauthorized_delta_changed = _check_delta_bindings(pair_id_str, baseline, candidate)

    base_known = derive_known_run_controls(base_run)
    cand_known = derive_known_run_controls(cand_run)

    supp_missing = any(
        getattr(baseline.supplemental_controls, attr) is None or getattr(candidate.supplemental_controls, attr) is None
        for attr in _SUPPLEMENTAL_CONTROL_KEY_TO_FIELD.values()
    )

    if supp_missing or delta_binding_missing:
        return ComparisonResult(
            schema_id="rag-eval.comparison",
            schema_version="1.0.0",
            run_id=cand_run.run_id,
            experiment_id=cand_run.experiment_id,
            baseline_run_id=base_run.run_id,
            baseline_run_hash=baseline.bundle.semantic_hash,
            candidate_run_id=cand_run.run_id,
            candidate_run_hash=candidate.bundle.semantic_hash,
            controlled_variable_checks=(),
            scope_comparisons=(),
            execution_status=ExecutionStatus.INVALID,
            decision_status=None,
        )

    controlled_checks = _build_controlled_checks(baseline, candidate, base_known, cand_known)
    all_controls_matched = all(c.matched for c in controlled_checks)

    scopes = build_scope_comparisons(baseline.bundle.metrics, candidate.bundle.metrics)

    is_valid = all_controls_matched and not unauthorized_delta_changed and cases_match and drafts_match and bool(scopes)

    if not is_valid:
        if all_controls_matched:
            scopes = ()
        return ComparisonResult(
            schema_id="rag-eval.comparison",
            schema_version="1.0.0",
            run_id=cand_run.run_id,
            experiment_id=cand_run.experiment_id,
            baseline_run_id=base_run.run_id,
            baseline_run_hash=baseline.bundle.semantic_hash,
            candidate_run_id=cand_run.run_id,
            candidate_run_hash=candidate.bundle.semantic_hash,
            controlled_variable_checks=controlled_checks,
            scope_comparisons=scopes,
            execution_status=ExecutionStatus.INVALID,
            decision_status=None,
        )

    return ComparisonResult(
        schema_id="rag-eval.comparison",
        schema_version="1.0.0",
        run_id=cand_run.run_id,
        experiment_id=cand_run.experiment_id,
        baseline_run_id=base_run.run_id,
        baseline_run_hash=baseline.bundle.semantic_hash,
        candidate_run_id=cand_run.run_id,
        candidate_run_hash=candidate.bundle.semantic_hash,
        controlled_variable_checks=controlled_checks,
        scope_comparisons=scopes,
        execution_status=ExecutionStatus.COMPLETED,
        decision_status=DecisionStatus.INCONCLUSIVE,
    )


def build_answer_comparison_set(
    *,
    ans_base: AnswerComparisonRunInput,
    ans_rag: AnswerComparisonRunInput,
    ans_final: AnswerComparisonRunInput,
    gold_manifest_ref: ImmutableReference,
) -> AnswerComparisonSetBuildResult:
    base_run = ans_base.bundle.run
    rag_run = ans_rag.bundle.run
    final_run = ans_final.bundle.run

    if (
        base_run.experiment_id != rag_run.experiment_id
        or base_run.experiment_id != final_run.experiment_id
        or base_run.dataset_code != rag_run.dataset_code
        or base_run.dataset_code != final_run.dataset_code
        or base_run.dataset_version != rag_run.dataset_version
        or base_run.dataset_version != final_run.dataset_version
        or base_run.dataset_manifest_sha256 != rag_run.dataset_manifest_sha256
        or base_run.dataset_manifest_sha256 != final_run.dataset_manifest_sha256
        or base_run.critical_claim_rubric_ref != rag_run.critical_claim_rubric_ref
        or base_run.critical_claim_rubric_ref != final_run.critical_claim_rubric_ref
        or base_run.comparison_policy_ref != rag_run.comparison_policy_ref
        or base_run.comparison_policy_ref != final_run.comparison_policy_ref
    ):
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)

    comp_base_rag = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)
    comp_rag_final = build_answer_pair_comparison(AnswerComparisonPairId.ANS_RAG_ANS_FINAL, ans_rag, ans_final)
    comp_base_final = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_FINAL, ans_base, ans_final)

    pair_entries = (
        AnswerComparisonPairManifestEntry(
            pair_id=AnswerComparisonPairId.ANS_BASE_ANS_RAG.value,
            baseline_variant=AnswerVariantId.ANS_BASE,
            candidate_variant=AnswerVariantId.ANS_RAG,
            baseline_run_id=base_run.run_id,
            candidate_run_id=rag_run.run_id,
            baseline_answer_variant_manifest_hash=cast(str, base_run.answer_variant_manifest_hash),
            candidate_answer_variant_manifest_hash=cast(str, rag_run.answer_variant_manifest_hash),
            baseline_runner_commit_sha=ans_base.runner_commit_sha,
            candidate_runner_commit_sha=ans_rag.runner_commit_sha,
            relative_path=ANSWER_COMPARISON_RELATIVE_PATHS[AnswerComparisonPairId.ANS_BASE_ANS_RAG.value],
            comparison_sha256=compute_comparison_sha256(comp_base_rag),
            comparison_semantic_hash=compute_comparison_semantic_hash(comp_base_rag),
            allowed_delta_keys=ANS_BASE_TO_ANS_RAG_DELTA_KEYS,
        ),
        AnswerComparisonPairManifestEntry(
            pair_id=AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value,
            baseline_variant=AnswerVariantId.ANS_RAG,
            candidate_variant=AnswerVariantId.ANS_FINAL,
            baseline_run_id=rag_run.run_id,
            candidate_run_id=final_run.run_id,
            baseline_answer_variant_manifest_hash=cast(str, rag_run.answer_variant_manifest_hash),
            candidate_answer_variant_manifest_hash=cast(str, final_run.answer_variant_manifest_hash),
            baseline_runner_commit_sha=ans_rag.runner_commit_sha,
            candidate_runner_commit_sha=ans_final.runner_commit_sha,
            relative_path=ANSWER_COMPARISON_RELATIVE_PATHS[AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value],
            comparison_sha256=compute_comparison_sha256(comp_rag_final),
            comparison_semantic_hash=compute_comparison_semantic_hash(comp_rag_final),
            allowed_delta_keys=ANS_RAG_TO_ANS_FINAL_DELTA_KEYS,
        ),
        AnswerComparisonPairManifestEntry(
            pair_id=AnswerComparisonPairId.ANS_BASE_ANS_FINAL.value,
            baseline_variant=AnswerVariantId.ANS_BASE,
            candidate_variant=AnswerVariantId.ANS_FINAL,
            baseline_run_id=base_run.run_id,
            candidate_run_id=final_run.run_id,
            baseline_answer_variant_manifest_hash=cast(str, base_run.answer_variant_manifest_hash),
            candidate_answer_variant_manifest_hash=cast(str, final_run.answer_variant_manifest_hash),
            baseline_runner_commit_sha=ans_base.runner_commit_sha,
            candidate_runner_commit_sha=ans_final.runner_commit_sha,
            relative_path=ANSWER_COMPARISON_RELATIVE_PATHS[AnswerComparisonPairId.ANS_BASE_ANS_FINAL.value],
            comparison_sha256=compute_comparison_sha256(comp_base_final),
            comparison_semantic_hash=compute_comparison_semantic_hash(comp_base_final),
            allowed_delta_keys=ANS_BASE_TO_ANS_FINAL_DELTA_KEYS,
        ),
    )

    dataset_manifest_ref = ImmutableReference(
        id=base_run.dataset_code,
        version=base_run.dataset_version,
        hash=base_run.dataset_manifest_sha256,
    )

    manifest_payload: dict[str, JsonValue] = {
        "schema_id": "rag-eval.answer-comparison-set-manifest",
        "schema_version": "1.0.0",
        "experiment_id": base_run.experiment_id,
        "dataset_manifest_ref": dataset_manifest_ref.model_dump(mode="json"),
        "partition": "DEV",
        "gold_manifest_ref": gold_manifest_ref.model_dump(mode="json"),
        "critical_claim_rubric_ref": base_run.critical_claim_rubric_ref.model_dump(mode="json"),
        "metric_policy_ref": base_run.comparison_policy_ref.model_dump(mode="json"),
        "pairs": [entry.model_dump(mode="json") for entry in pair_entries],
    }
    manifest_sha256 = canonical_sha256(manifest_payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    manifest_payload["manifest_sha256"] = manifest_sha256

    manifest = AnswerComparisonSetManifest.model_validate(manifest_payload)
    manifest_bytes = canonical_json_bytes(manifest_payload)

    comparisons = {
        AnswerComparisonPairId.ANS_BASE_ANS_RAG.value: comp_base_rag,
        AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value: comp_rag_final,
        AnswerComparisonPairId.ANS_BASE_ANS_FINAL.value: comp_base_final,
    }
    comparison_files = {
        ANSWER_COMPARISON_RELATIVE_PATHS[AnswerComparisonPairId.ANS_BASE_ANS_RAG.value]: canonical_json_bytes(
            comp_base_rag.model_dump(mode="json")
        ),
        ANSWER_COMPARISON_RELATIVE_PATHS[AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value]: canonical_json_bytes(
            comp_rag_final.model_dump(mode="json")
        ),
        ANSWER_COMPARISON_RELATIVE_PATHS[AnswerComparisonPairId.ANS_BASE_ANS_FINAL.value]: canonical_json_bytes(
            comp_base_final.model_dump(mode="json")
        ),
    }

    return AnswerComparisonSetBuildResult(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        comparisons=comparisons,
        comparison_files=comparison_files,
    )


def _parse_and_validate_manifest_envelope(manifest_bytes: bytes) -> AnswerComparisonSetManifest:
    try:
        manifest_payload = parse_json_object_bytes(manifest_bytes)
        expected_hash = manifest_payload.get("manifest_sha256")
        if (
            not isinstance(expected_hash, str)
            or canonical_sha256(manifest_payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
            != expected_hash
        ):
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
        return ANSWER_COMPARISON_SET_MANIFEST_ADAPTER.validate_python(manifest_payload)
    except EvaluationValidationError:
        raise
    except (ValidationError, ValueError):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None


def _validate_single_pair_entry(
    pair_entry: AnswerComparisonPairManifestEntry,
    raw_bytes: bytes,
    b_input: AnswerComparisonRunInput,
    c_input: AnswerComparisonRunInput,
    manifest_experiment_id: str,
) -> None:
    if sha256_hex(raw_bytes) != pair_entry.comparison_sha256:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    try:
        comp_payload = parse_json_object_bytes(raw_bytes)
        comp = ComparisonResult.model_validate(comp_payload)
    except (ValidationError, ValueError):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None

    if compute_comparison_semantic_hash(comp) != pair_entry.comparison_semantic_hash:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    b_run = b_input.bundle.run
    c_run = c_input.bundle.run
    if (
        pair_entry.baseline_run_id != b_run.run_id
        or pair_entry.candidate_run_id != c_run.run_id
        or comp.baseline_run_id != b_run.run_id
        or comp.candidate_run_id != c_run.run_id
        or comp.run_id != c_run.run_id
        or pair_entry.baseline_answer_variant_manifest_hash != b_run.answer_variant_manifest_hash
        or pair_entry.candidate_answer_variant_manifest_hash != c_run.answer_variant_manifest_hash
        or pair_entry.baseline_runner_commit_sha != b_input.runner_commit_sha
        or pair_entry.candidate_runner_commit_sha != c_input.runner_commit_sha
        or manifest_experiment_id != comp.experiment_id
        or manifest_experiment_id != b_run.experiment_id
    ):
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)


def validate_answer_comparison_set_bundle(
    manifest_bytes: bytes,
    comparison_files: Mapping[str, bytes],
    *,
    ans_base: AnswerComparisonRunInput,
    ans_rag: AnswerComparisonRunInput,
    ans_final: AnswerComparisonRunInput,
    gold_manifest_ref: ImmutableReference,
) -> AnswerComparisonSetManifest:
    manifest = _parse_and_validate_manifest_envelope(manifest_bytes)

    pair_ids = tuple(pair.pair_id for pair in manifest.pairs)
    if pair_ids != CANONICAL_ANSWER_COMPARISON_PAIRS:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)

    expected_paths = {pair.relative_path for pair in manifest.pairs}
    actual_paths = set(comparison_files.keys())
    if not expected_paths.issubset(actual_paths):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING)
    if not actual_paths.issubset(expected_paths):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)

    pair_inputs = {
        AnswerComparisonPairId.ANS_BASE_ANS_RAG.value: (ans_base, ans_rag),
        AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value: (ans_rag, ans_final),
        AnswerComparisonPairId.ANS_BASE_ANS_FINAL.value: (ans_base, ans_final),
    }

    for pair_entry in manifest.pairs:
        raw_bytes = comparison_files[pair_entry.relative_path]
        b_input, c_input = pair_inputs[pair_entry.pair_id]
        _validate_single_pair_entry(pair_entry, raw_bytes, b_input, c_input, manifest.experiment_id)

    base_run = ans_base.bundle.run
    if (
        manifest.dataset_manifest_ref.hash != base_run.dataset_manifest_sha256
        or manifest.dataset_manifest_ref.id != base_run.dataset_code
        or manifest.dataset_manifest_ref.version != base_run.dataset_version
        or manifest.gold_manifest_ref != gold_manifest_ref
        or manifest.critical_claim_rubric_ref != base_run.critical_claim_rubric_ref
        or manifest.metric_policy_ref != base_run.comparison_policy_ref
    ):
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)

    return manifest
