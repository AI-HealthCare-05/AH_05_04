from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.common import (
    ActorRef,
    CanonicalUuid,
    ImmutableReference,
    Partition,
    ResourcePath,
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
    UtcTimestamp,
)

_GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _validate_git_sha(value: str) -> str:
    if _GIT_SHA_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a lowercase Git commit SHA")
    return value


GitCommitSha = Annotated[
    str,
    StringConstraints(strict=True, pattern=_GIT_SHA_PATTERN.pattern),
    AfterValidator(_validate_git_sha),
]


def _enum_from_wire(enum_type: type[StrEnum], value: object) -> object:
    return enum_type(value) if isinstance(value, str) else value


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _utf16_key(value: str) -> bytes:
    return value.encode("utf-16-be")


class AnswerVariantId(StrEnum):
    ANS_BASE = "ANS-BASE"
    ANS_RAG = "ANS-RAG"
    ANS_FINAL = "ANS-FINAL"


class AnswerClaimCorrectnessLabel(StrEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"


class AnswerRelevanceLabel(StrEnum):
    RELEVANT = "RELEVANT"
    IRRELEVANT = "IRRELEVANT"


class AnswerComparisonPairId(StrEnum):
    ANS_BASE_ANS_RAG = "ANS-BASE--ANS-RAG"
    ANS_RAG_ANS_FINAL = "ANS-RAG--ANS-FINAL"
    ANS_BASE_ANS_FINAL = "ANS-BASE--ANS-FINAL"


AnswerVariantIdValue = Annotated[
    AnswerVariantId,
    BeforeValidator(lambda value: _enum_from_wire(AnswerVariantId, value)),
]

AnswerClaimCorrectnessLabelValue = Annotated[
    AnswerClaimCorrectnessLabel,
    BeforeValidator(lambda value: _enum_from_wire(AnswerClaimCorrectnessLabel, value)),
]

AnswerRelevanceLabelValue = Annotated[
    AnswerRelevanceLabel,
    BeforeValidator(lambda value: _enum_from_wire(AnswerRelevanceLabel, value)),
]

PartitionValue = Annotated[Partition, BeforeValidator(lambda value: _enum_from_wire(Partition, value))]

ANS_BASE_TO_ANS_RAG_DELTA_KEYS: tuple[str, ...] = (
    "RETRIEVAL_PIPELINE",
    "RETRIEVED_EVIDENCE",
    "RUNTIME_BUNDLE",
    "SOURCE_INDEX",
)

ANS_RAG_TO_ANS_FINAL_DELTA_KEYS: tuple[str, ...] = (
    "CITATION_GATE",
    "FINAL_VALIDATOR",
    "RELEASE_GATE",
    "SAFETY_GATE",
)

ANS_BASE_TO_ANS_FINAL_DELTA_KEYS: tuple[str, ...] = (
    "CITATION_GATE",
    "FINAL_VALIDATOR",
    "RELEASE_GATE",
    "RETRIEVAL_PIPELINE",
    "RETRIEVED_EVIDENCE",
    "RUNTIME_BUNDLE",
    "SAFETY_GATE",
    "SOURCE_INDEX",
)

CANONICAL_ANSWER_COMPARISON_PAIRS: tuple[str, ...] = (
    AnswerComparisonPairId.ANS_BASE_ANS_RAG.value,
    AnswerComparisonPairId.ANS_RAG_ANS_FINAL.value,
    AnswerComparisonPairId.ANS_BASE_ANS_FINAL.value,
)

_EXPECTED_PAIR_SPECS: dict[str, tuple[AnswerVariantId, AnswerVariantId, tuple[str, ...]]] = {
    AnswerComparisonPairId.ANS_BASE_ANS_RAG: (
        AnswerVariantId.ANS_BASE,
        AnswerVariantId.ANS_RAG,
        ANS_BASE_TO_ANS_RAG_DELTA_KEYS,
    ),
    AnswerComparisonPairId.ANS_RAG_ANS_FINAL: (
        AnswerVariantId.ANS_RAG,
        AnswerVariantId.ANS_FINAL,
        ANS_RAG_TO_ANS_FINAL_DELTA_KEYS,
    ),
    AnswerComparisonPairId.ANS_BASE_ANS_FINAL: (
        AnswerVariantId.ANS_BASE,
        AnswerVariantId.ANS_FINAL,
        ANS_BASE_TO_ANS_FINAL_DELTA_KEYS,
    ),
}


class AnswerClaimJudgment(StrictContractModel):
    claim_id: StableId
    label: AnswerClaimCorrectnessLabelValue


class AnswerHumanJudgmentRecord(StrictContractModel):
    judgment_id: StableId
    judgment_version: SemanticVersion
    run_id: CanonicalUuid
    case_id: StableId
    answer_variant_id: AnswerVariantIdValue
    input_sha256: Sha256Hex
    answer_sha256: Sha256Hex
    dataset_manifest_sha256: Sha256Hex
    critical_claim_rubric_ref: ImmutableReference
    claim_judgments: Annotated[
        tuple[AnswerClaimJudgment, ...],
        BeforeValidator(_tuple_from_wire),
    ]
    relevance: AnswerRelevanceLabelValue
    reviewer: ActorRef
    reviewed_at: UtcTimestamp
    approval_evidence_ref: ImmutableReference
    record_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_record(self) -> AnswerHumanJudgmentRecord:
        claim_ids = [claim.claim_id for claim in self.claim_judgments]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values in claim_judgments must be unique")
        if claim_ids != sorted(claim_ids, key=_utf16_key):
            raise ValueError("claim_judgments must be in canonical claim_id order")
        payload = self.model_dump(mode="json")
        if self.record_sha256 != canonical_sha256(payload, excluded_top_level_keys=frozenset({"record_sha256"})):
            raise ValueError("record_sha256 mismatch")
        return self


class AnswerHumanJudgmentArtifact(StrictContractModel):
    schema_id: Literal["rag-eval.answer-human-judgment"] = "rag-eval.answer-human-judgment"
    schema_version: Literal["1.0.0"] = "1.0.0"
    judgment_set_id: StableId
    judgment_set_version: SemanticVersion
    run_id: CanonicalUuid
    answer_variant_id: AnswerVariantIdValue
    answer_variant_manifest_hash: Sha256Hex
    dataset_manifest_sha256: Sha256Hex
    critical_claim_rubric_ref: ImmutableReference
    approval_evidence_ref: ImmutableReference
    records: Annotated[
        tuple[AnswerHumanJudgmentRecord, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    artifact_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_artifact(self) -> AnswerHumanJudgmentArtifact:
        case_ids = [record.case_id for record in self.records]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id values in records must be unique")
        if case_ids != sorted(case_ids, key=_utf16_key):
            raise ValueError("records must be in canonical case_id order")

        for record in self.records:
            if record.run_id != self.run_id:
                raise ValueError("record run_id must match artifact run_id")
            if record.answer_variant_id != self.answer_variant_id:
                raise ValueError("record answer_variant_id must match artifact answer_variant_id")
            if record.dataset_manifest_sha256 != self.dataset_manifest_sha256:
                raise ValueError("record dataset_manifest_sha256 must match artifact dataset_manifest_sha256")
            if record.critical_claim_rubric_ref != self.critical_claim_rubric_ref:
                raise ValueError("record critical_claim_rubric_ref must match artifact critical_claim_rubric_ref")
            if record.approval_evidence_ref != self.approval_evidence_ref:
                raise ValueError("record approval_evidence_ref must match artifact approval_evidence_ref")

        payload = self.model_dump(mode="json")
        if self.artifact_sha256 != canonical_sha256(payload, excluded_top_level_keys=frozenset({"artifact_sha256"})):
            raise ValueError("artifact_sha256 mismatch")
        return self


class AnswerHumanJudgmentApproval(StrictContractModel):
    schema_id: Literal["rag-eval.answer-human-judgment-approval"] = "rag-eval.answer-human-judgment-approval"
    schema_version: Literal["1.0.0"] = "1.0.0"
    approval_id: StableId
    approval_version: SemanticVersion
    judgment_artifact_sha256: Sha256Hex
    approval_status: Literal["APPROVED"] = "APPROVED"
    approved_by: ActorRef
    approved_at: UtcTimestamp
    approval_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_approval(self) -> AnswerHumanJudgmentApproval:
        payload = self.model_dump(mode="json")
        if self.approval_sha256 != canonical_sha256(payload, excluded_top_level_keys=frozenset({"approval_sha256"})):
            raise ValueError("approval_sha256 mismatch")
        return self


class AnswerComparisonPairManifestEntry(StrictContractModel):
    pair_id: Literal["ANS-BASE--ANS-RAG", "ANS-RAG--ANS-FINAL", "ANS-BASE--ANS-FINAL"]
    baseline_variant: AnswerVariantIdValue
    candidate_variant: AnswerVariantIdValue
    baseline_run_id: CanonicalUuid
    candidate_run_id: CanonicalUuid
    baseline_answer_variant_manifest_hash: Sha256Hex
    candidate_answer_variant_manifest_hash: Sha256Hex
    baseline_runner_commit_sha: GitCommitSha
    candidate_runner_commit_sha: GitCommitSha
    relative_path: ResourcePath
    comparison_sha256: Sha256Hex
    comparison_semantic_hash: Sha256Hex
    allowed_delta_keys: Annotated[
        tuple[str, ...],
        BeforeValidator(_tuple_from_wire),
    ]

    @model_validator(mode="after")
    def validate_pair_entry(self) -> AnswerComparisonPairManifestEntry:
        expected = _EXPECTED_PAIR_SPECS.get(self.pair_id)
        if expected is None:
            raise ValueError(f"unknown pair_id: {self.pair_id}")
        expected_baseline, expected_candidate, expected_deltas = expected
        if self.baseline_variant != expected_baseline:
            raise ValueError(f"baseline_variant for {self.pair_id} must be {expected_baseline.value}")
        if self.candidate_variant != expected_candidate:
            raise ValueError(f"candidate_variant for {self.pair_id} must be {expected_candidate.value}")
        if self.allowed_delta_keys != expected_deltas:
            raise ValueError(f"allowed_delta_keys mismatch for {self.pair_id}")
        return self


class AnswerComparisonSetManifest(StrictContractModel):
    schema_id: Literal["rag-eval.answer-comparison-set-manifest"] = "rag-eval.answer-comparison-set-manifest"
    schema_version: Literal["1.0.0"] = "1.0.0"
    experiment_id: StableId
    dataset_manifest_ref: ImmutableReference
    partition: PartitionValue
    gold_manifest_ref: ImmutableReference
    critical_claim_rubric_ref: ImmutableReference
    metric_policy_ref: ImmutableReference
    pairs: Annotated[
        tuple[AnswerComparisonPairManifestEntry, ...],
        BeforeValidator(_tuple_from_wire),
    ]
    manifest_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_manifest(self) -> AnswerComparisonSetManifest:
        pair_ids = tuple(pair.pair_id for pair in self.pairs)
        if pair_ids != CANONICAL_ANSWER_COMPARISON_PAIRS:
            raise ValueError(
                f"pairs must contain exactly the 3 canonical pairs in order: {CANONICAL_ANSWER_COMPARISON_PAIRS}"
            )
        relative_paths = [pair.relative_path for pair in self.pairs]
        if len(relative_paths) != len(set(relative_paths)):
            raise ValueError("relative_path across comparison pairs must be unique")
        payload = self.model_dump(mode="json")
        if self.manifest_sha256 != canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"})):
            raise ValueError("manifest_sha256 mismatch")
        return self


ANSWER_HUMAN_JUDGMENT_ADAPTER: TypeAdapter[AnswerHumanJudgmentArtifact] = TypeAdapter(AnswerHumanJudgmentArtifact)
ANSWER_HUMAN_JUDGMENT_APPROVAL_ADAPTER: TypeAdapter[AnswerHumanJudgmentApproval] = TypeAdapter(
    AnswerHumanJudgmentApproval
)
ANSWER_COMPARISON_SET_MANIFEST_ADAPTER: TypeAdapter[AnswerComparisonSetManifest] = TypeAdapter(
    AnswerComparisonSetManifest
)


def _parse_hashed_model[T: BaseModel](raw_bytes: bytes, model: type[T], hash_field: str) -> T:
    from ai_worker.tasks.evaluation.loaders import parse_json_object_bytes

    try:
        payload = parse_json_object_bytes(raw_bytes)
        expected_hash = payload.get(hash_field)
        if (
            not isinstance(expected_hash, str)
            or canonical_sha256(payload, excluded_top_level_keys=frozenset({hash_field})) != expected_hash
        ):
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
        validated = model.model_validate(payload)
    except EvaluationValidationError:
        raise
    except (ValidationError, ValueError):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None
    canonical_payload = cast(dict[str, JsonValue], validated.model_dump(mode="json"))
    try:
        validate_privacy_boundary(canonical_payload)
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN:
            raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_VALUE_DETECTED, error.safe_path) from None
        raise
    return validated


def parse_answer_human_judgment_bytes(raw_bytes: bytes) -> AnswerHumanJudgmentArtifact:
    return _parse_hashed_model(raw_bytes, AnswerHumanJudgmentArtifact, "artifact_sha256")


def parse_answer_human_judgment_approval_bytes(raw_bytes: bytes) -> AnswerHumanJudgmentApproval:
    return _parse_hashed_model(raw_bytes, AnswerHumanJudgmentApproval, "approval_sha256")


def parse_answer_comparison_set_manifest_bytes(raw_bytes: bytes) -> AnswerComparisonSetManifest:
    return _parse_hashed_model(raw_bytes, AnswerComparisonSetManifest, "manifest_sha256")
