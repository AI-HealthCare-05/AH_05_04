from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

GUIDE_RETRIEVAL_BINDING_PROJECTION_VERSION = "guide-retrieval-binding-manifest-v1"
GUIDE_RETRIEVAL_FILTER_ARTIFACT_CODE = "guide-retrieval-filter-snapshot"
GUIDE_RETRIEVAL_FILTER_ARTIFACT_VERSION = "1.0.0"

type JsonScalar = None | bool | int | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_MODES = frozenset({"LEXICAL_ONLY", "DENSE_ONLY", "HYBRID_RRF"})


class GuideRetrievalBindingValidationError(ValueError):
    """The Guide retrieval authority is incomplete or not canonical."""


@dataclass(frozen=True, slots=True)
class GuideRetrievalArtifactRef:
    artifact_code: str
    version: str
    content_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.artifact_code, "artifact_code")
        _require_text(self.version, "version")
        _require_sha256(self.content_sha256, "content_sha256")

    def to_projection(self) -> dict[str, JsonValue]:
        return {
            "artifact_code": self.artifact_code,
            "content_sha256": self.content_sha256,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class GuideRetrievalSourceMember:
    source_snapshot_id: UUID
    source_purpose: str
    source_version: str
    canonical_checksum: str
    approval_version: str
    scope_policy_hash: str
    freshness_policy_hash: str
    required: bool
    selected_for_operation: bool

    def __post_init__(self) -> None:
        _require_uuid(self.source_snapshot_id, "source_snapshot_id")
        _require_text(self.source_purpose, "source_purpose")
        _require_text(self.source_version, "source_version")
        _require_sha256(self.canonical_checksum, "canonical_checksum")
        _require_text(self.approval_version, "approval_version")
        _require_sha256(self.scope_policy_hash, "scope_policy_hash")
        _require_sha256(self.freshness_policy_hash, "freshness_policy_hash")
        if type(self.required) is not bool or type(self.selected_for_operation) is not bool:
            raise GuideRetrievalBindingValidationError("source selection flags must be bool")

    def to_projection(self) -> dict[str, JsonValue]:
        return {
            "approval_version": self.approval_version,
            "canonical_checksum": self.canonical_checksum,
            "freshness_policy_hash": self.freshness_policy_hash,
            "required": self.required,
            "scope_policy_hash": self.scope_policy_hash,
            "selected_for_operation": self.selected_for_operation,
            "source_purpose": self.source_purpose,
            "source_snapshot_id": str(self.source_snapshot_id),
            "source_version": self.source_version,
        }


@dataclass(frozen=True, slots=True)
class GuideRetrievalMemberBinding:
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID

    def __post_init__(self) -> None:
        _require_uuid(self.source_snapshot_id, "source_snapshot_id")
        _require_uuid(self.source_snapshot_member_id, "source_snapshot_member_id")

    def to_projection(self) -> dict[str, JsonValue]:
        return {
            "source_snapshot_id": str(self.source_snapshot_id),
            "source_snapshot_member_id": str(self.source_snapshot_member_id),
        }


@dataclass(frozen=True, slots=True)
class GuideRetrievalBindingManifest:
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    knowledge_index_id: UUID
    evidence_index_ref: GuideRetrievalArtifactRef
    member_bindings: tuple[GuideRetrievalMemberBinding, ...]
    retrieval_configuration: dict[str, JsonValue]
    filter_snapshot_ref: GuideRetrievalArtifactRef
    source_manifest_hash: str
    manifest_hash: str

    def __post_init__(self) -> None:
        _require_uuid(self.runtime_release_bundle_id, "runtime_release_bundle_id")
        _require_sha256(self.runtime_release_bundle_manifest_hash, "runtime_release_bundle_manifest_hash")
        _require_uuid(self.runtime_execution_manifest_id, "runtime_execution_manifest_id")
        _require_sha256(self.runtime_execution_manifest_hash, "runtime_execution_manifest_hash")
        _require_uuid(self.knowledge_index_id, "knowledge_index_id")
        _validate_member_bindings(self.member_bindings)
        validate_retrieval_configuration_projection(self.retrieval_configuration)
        expected_filter_ref = compute_filter_snapshot_ref(
            knowledge_index_id=self.knowledge_index_id,
            evidence_index_ref=self.evidence_index_ref,
            member_bindings=self.member_bindings,
        )
        if self.filter_snapshot_ref != expected_filter_ref:
            raise GuideRetrievalBindingValidationError("filter_snapshot_ref does not match the pinned member scope")
        _require_sha256(self.source_manifest_hash, "source_manifest_hash")
        _require_sha256(self.manifest_hash, "manifest_hash")
        if self.manifest_hash != compute_guide_retrieval_binding_manifest_hash(self):
            raise GuideRetrievalBindingValidationError("manifest_hash does not match the canonical projection")


def canonical_source_manifest_hash(source_members: tuple[GuideRetrievalSourceMember, ...]) -> str:
    if not source_members:
        raise GuideRetrievalBindingValidationError("source manifest requires at least one pinned source")
    identities = [(member.source_purpose, member.source_snapshot_id) for member in source_members]
    if len(identities) != len(set(identities)):
        raise GuideRetrievalBindingValidationError("source manifest members must be unique")
    projection: dict[str, JsonValue] = {
        "projection_version": "guide-retrieval-source-manifest-v1",
        "sources": [
            member.to_projection()
            for member in sorted(
                source_members,
                key=lambda item: (item.source_purpose.encode("utf-8"), item.source_snapshot_id.bytes),
            )
        ],
    }
    return _canonical_sha256(projection)


def compute_filter_snapshot_ref(
    *,
    knowledge_index_id: UUID,
    evidence_index_ref: GuideRetrievalArtifactRef,
    member_bindings: tuple[GuideRetrievalMemberBinding, ...],
) -> GuideRetrievalArtifactRef:
    _require_uuid(knowledge_index_id, "knowledge_index_id")
    _validate_member_bindings(member_bindings)
    projection: dict[str, JsonValue] = {
        "evidence_index_ref": evidence_index_ref.to_projection(),
        "knowledge_index_id": str(knowledge_index_id),
        "member_bindings": [item.to_projection() for item in member_bindings],
        "projection_version": "guide-retrieval-filter-snapshot-v1",
    }
    return GuideRetrievalArtifactRef(
        artifact_code=GUIDE_RETRIEVAL_FILTER_ARTIFACT_CODE,
        version=GUIDE_RETRIEVAL_FILTER_ARTIFACT_VERSION,
        content_sha256=_canonical_sha256(projection),
    )


def build_guide_retrieval_binding_manifest(
    *,
    runtime_release_bundle_id: UUID,
    runtime_release_bundle_manifest_hash: str,
    runtime_execution_manifest_id: UUID,
    runtime_execution_manifest_hash: str,
    knowledge_index_id: UUID,
    evidence_index_ref: GuideRetrievalArtifactRef,
    member_bindings: tuple[GuideRetrievalMemberBinding, ...],
    retrieval_configuration: dict[str, JsonValue],
    source_members: tuple[GuideRetrievalSourceMember, ...],
) -> GuideRetrievalBindingManifest:
    source_manifest_hash = canonical_source_manifest_hash(source_members)
    filter_snapshot_ref = compute_filter_snapshot_ref(
        knowledge_index_id=knowledge_index_id,
        evidence_index_ref=evidence_index_ref,
        member_bindings=member_bindings,
    )
    retrieval_configuration = _validated_json_object(retrieval_configuration)
    manifest_hash = _manifest_projection_hash(
        runtime_release_bundle_id=runtime_release_bundle_id,
        runtime_release_bundle_manifest_hash=runtime_release_bundle_manifest_hash,
        runtime_execution_manifest_id=runtime_execution_manifest_id,
        runtime_execution_manifest_hash=runtime_execution_manifest_hash,
        knowledge_index_id=knowledge_index_id,
        evidence_index_ref=evidence_index_ref,
        member_bindings=member_bindings,
        retrieval_configuration=retrieval_configuration,
        filter_snapshot_ref=filter_snapshot_ref,
        source_manifest_hash=source_manifest_hash,
    )
    return GuideRetrievalBindingManifest(
        runtime_release_bundle_id=runtime_release_bundle_id,
        runtime_release_bundle_manifest_hash=runtime_release_bundle_manifest_hash,
        runtime_execution_manifest_id=runtime_execution_manifest_id,
        runtime_execution_manifest_hash=runtime_execution_manifest_hash,
        knowledge_index_id=knowledge_index_id,
        evidence_index_ref=evidence_index_ref,
        member_bindings=member_bindings,
        retrieval_configuration=retrieval_configuration,
        filter_snapshot_ref=filter_snapshot_ref,
        source_manifest_hash=source_manifest_hash,
        manifest_hash=manifest_hash,
    )


def compute_guide_retrieval_binding_manifest_hash(manifest: GuideRetrievalBindingManifest) -> str:
    return _manifest_projection_hash(
        runtime_release_bundle_id=manifest.runtime_release_bundle_id,
        runtime_release_bundle_manifest_hash=manifest.runtime_release_bundle_manifest_hash,
        runtime_execution_manifest_id=manifest.runtime_execution_manifest_id,
        runtime_execution_manifest_hash=manifest.runtime_execution_manifest_hash,
        knowledge_index_id=manifest.knowledge_index_id,
        evidence_index_ref=manifest.evidence_index_ref,
        member_bindings=manifest.member_bindings,
        retrieval_configuration=manifest.retrieval_configuration,
        filter_snapshot_ref=manifest.filter_snapshot_ref,
        source_manifest_hash=manifest.source_manifest_hash,
    )


def _manifest_projection_hash(
    *,
    runtime_release_bundle_id: UUID,
    runtime_release_bundle_manifest_hash: str,
    runtime_execution_manifest_id: UUID,
    runtime_execution_manifest_hash: str,
    knowledge_index_id: UUID,
    evidence_index_ref: GuideRetrievalArtifactRef,
    member_bindings: tuple[GuideRetrievalMemberBinding, ...],
    retrieval_configuration: dict[str, JsonValue],
    filter_snapshot_ref: GuideRetrievalArtifactRef,
    source_manifest_hash: str,
) -> str:
    projection: dict[str, JsonValue] = {
        "evidence_index_ref": evidence_index_ref.to_projection(),
        "filter_snapshot_ref": filter_snapshot_ref.to_projection(),
        "knowledge_index_id": str(knowledge_index_id),
        "member_bindings": [item.to_projection() for item in member_bindings],
        "projection_version": GUIDE_RETRIEVAL_BINDING_PROJECTION_VERSION,
        "retrieval_configuration": retrieval_configuration,
        "runtime_execution_manifest_hash": runtime_execution_manifest_hash,
        "runtime_execution_manifest_id": str(runtime_execution_manifest_id),
        "runtime_release_bundle_id": str(runtime_release_bundle_id),
        "runtime_release_bundle_manifest_hash": runtime_release_bundle_manifest_hash,
        "source_manifest_hash": source_manifest_hash,
    }
    return _canonical_sha256(projection)


def validate_retrieval_configuration_projection(projection: object) -> str:
    value = _validated_json_object(projection)
    expected_keys = {
        "algorithm_id",
        "artifact_ref",
        "dense_config",
        "dense_limit",
        "exact_limit",
        "execution_mode",
        "expected_query_embedding_adapter_ref",
        "fts_limit",
        "future_reranker_input_limit",
        "hybrid_limit",
        "lexical_config",
        "lexical_limit",
        "observed_score_projection",
        "observed_score_quantum",
        "observed_score_rounding",
        "rrf_k",
        "stable_coordinate_fields",
        "tie_break",
        "transaction_access",
        "transaction_isolation",
        "trigram_limit",
    }
    _require_exact_keys(value, expected_keys, "retrieval_configuration")
    artifact_ref = _artifact_ref_from_projection(value["artifact_ref"], "artifact_ref")
    lexical_hash = _validate_subconfiguration(value["lexical_config"], _LEXICAL_FIELDS, "lexical_config")
    dense_value = value["dense_config"]
    dense_hash = None if dense_value is None else _validate_subconfiguration(dense_value, _DENSE_FIELDS, "dense_config")
    adapter_value = value["expected_query_embedding_adapter_ref"]
    adapter_projection = (
        None
        if adapter_value is None
        else _artifact_ref_from_projection(adapter_value, "expected_query_embedding_adapter_ref").to_projection()
    )
    execution_mode = value["execution_mode"]
    if type(execution_mode) is not str or execution_mode not in _EXECUTION_MODES:
        raise GuideRetrievalBindingValidationError("execution_mode is invalid")
    for field in (
        "rrf_k",
        "exact_limit",
        "trigram_limit",
        "fts_limit",
        "lexical_limit",
        "dense_limit",
        "hybrid_limit",
        "future_reranker_input_limit",
    ):
        _require_positive_int(value[field], field)
    stable_fields = value["stable_coordinate_fields"]
    if type(stable_fields) is not list or not stable_fields:
        raise GuideRetrievalBindingValidationError("stable_coordinate_fields must be a non-empty list")
    for item in stable_fields:
        _require_text(item, "stable_coordinate_fields entry")
    for field in (
        "algorithm_id",
        "tie_break",
        "observed_score_projection",
        "observed_score_quantum",
        "observed_score_rounding",
        "transaction_access",
        "transaction_isolation",
    ):
        _require_text(value[field], field)
    canonical: dict[str, JsonValue] = {
        "algorithm_id": value["algorithm_id"],
        "dense_config_hash": dense_hash,
        "dense_limit": value["dense_limit"],
        "exact_limit": value["exact_limit"],
        "execution_mode": execution_mode,
        "expected_query_embedding_adapter_ref": adapter_projection,
        "fts_limit": value["fts_limit"],
        "future_reranker_input_limit": value["future_reranker_input_limit"],
        "hybrid_limit": value["hybrid_limit"],
        "lexical_config_hash": lexical_hash,
        "lexical_limit": value["lexical_limit"],
        "observed_score_projection": value["observed_score_projection"],
        "observed_score_quantum": value["observed_score_quantum"],
        "observed_score_rounding": value["observed_score_rounding"],
        "rrf_k": value["rrf_k"],
        "stable_coordinate_fields": stable_fields,
        "tie_break": value["tie_break"],
        "transaction_access": value["transaction_access"],
        "transaction_isolation": value["transaction_isolation"],
        "trigram_limit": value["trigram_limit"],
    }
    config_hash = _canonical_sha256(canonical)
    if artifact_ref.content_sha256 != config_hash:
        raise GuideRetrievalBindingValidationError("retrieval configuration artifact hash is invalid")
    return config_hash


def parse_member_bindings(value: object) -> tuple[GuideRetrievalMemberBinding, ...]:
    if type(value) is not list or not value:
        raise GuideRetrievalBindingValidationError("member_bindings must be a non-empty list")
    bindings: list[GuideRetrievalMemberBinding] = []
    for item in value:
        projection = _validated_json_object(item)
        _require_exact_keys(projection, {"source_snapshot_id", "source_snapshot_member_id"}, "member binding")
        snapshot_id = projection["source_snapshot_id"]
        member_id = projection["source_snapshot_member_id"]
        if type(snapshot_id) is not str or type(member_id) is not str:
            raise GuideRetrievalBindingValidationError("member binding UUID is invalid")
        try:
            bindings.append(
                GuideRetrievalMemberBinding(
                    source_snapshot_id=UUID(snapshot_id),
                    source_snapshot_member_id=UUID(member_id),
                )
            )
        except (TypeError, ValueError):
            raise GuideRetrievalBindingValidationError("member binding UUID is invalid") from None
    result = tuple(bindings)
    _validate_member_bindings(result)
    return result


_LEXICAL_FIELDS = frozenset(
    {
        "exact_limit",
        "exact_strategy",
        "fts_limit",
        "fts_query_constructor",
        "fts_regconfig",
        "fts_score_function",
        "fts_vector_expression",
        "query_normalization",
        "trigram_limit",
        "trigram_match_operator",
        "trigram_score_function",
        "trigram_threshold",
    }
)
_DENSE_FIELDS = frozenset({"cutoff_policy", "dense_limit", "distance_metric", "minimum_similarity"})


def _validate_subconfiguration(value: object, fields: frozenset[str], field_name: str) -> str:
    projection = _validated_json_object(value)
    _require_exact_keys(projection, set(fields) | {"artifact_ref"}, field_name)
    artifact_ref = _artifact_ref_from_projection(projection["artifact_ref"], f"{field_name}.artifact_ref")
    canonical = {key: projection[key] for key in fields}
    for key, item in canonical.items():
        if key.endswith("_limit"):
            _require_positive_int(item, f"{field_name}.{key}")
        elif item is not None:
            _require_text(item, f"{field_name}.{key}")
    config_hash = _canonical_sha256(canonical)
    if artifact_ref.content_sha256 != config_hash:
        raise GuideRetrievalBindingValidationError(f"{field_name} artifact hash is invalid")
    return config_hash


def _artifact_ref_from_projection(value: object, field_name: str) -> GuideRetrievalArtifactRef:
    projection = _validated_json_object(value)
    _require_exact_keys(projection, {"artifact_code", "content_sha256", "version"}, field_name)
    artifact_code = projection["artifact_code"]
    version = projection["version"]
    content_sha256 = projection["content_sha256"]
    try:
        return GuideRetrievalArtifactRef(
            artifact_code=artifact_code if isinstance(artifact_code, str) else "",
            version=version if isinstance(version, str) else "",
            content_sha256=content_sha256 if isinstance(content_sha256, str) else "",
        )
    except (TypeError, GuideRetrievalBindingValidationError):
        raise GuideRetrievalBindingValidationError(f"{field_name} is invalid") from None


def _validate_member_bindings(member_bindings: object) -> None:
    if type(member_bindings) is not tuple or not member_bindings:
        raise GuideRetrievalBindingValidationError("member_bindings must be a non-empty tuple")
    if any(type(item) is not GuideRetrievalMemberBinding for item in member_bindings):
        raise GuideRetrievalBindingValidationError("member_bindings contains an invalid item")
    identities = [(item.source_snapshot_id, item.source_snapshot_member_id) for item in member_bindings]
    if len(identities) != len(set(identities)):
        raise GuideRetrievalBindingValidationError("member_bindings must be unique")
    if identities != sorted(identities, key=lambda item: (item[0].bytes, item[1].bytes)):
        raise GuideRetrievalBindingValidationError("member_bindings must be UUID-byte sorted")


def _require_exact_keys(value: dict[str, JsonValue], expected: set[str], field_name: str) -> None:
    if set(value) != expected:
        raise GuideRetrievalBindingValidationError(f"{field_name} keys are invalid")


def _require_uuid(value: object, field_name: str) -> None:
    if type(value) is not UUID:
        raise GuideRetrievalBindingValidationError(f"{field_name} must be a UUID")


def _require_text(value: object, field_name: str) -> None:
    if type(value) is not str or not value or value != value.strip() or not unicodedata.is_normalized("NFC", value):
        raise GuideRetrievalBindingValidationError(f"{field_name} must be canonical nonblank text")


def _require_sha256(value: object, field_name: str) -> None:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise GuideRetrievalBindingValidationError(f"{field_name} must be a lowercase SHA-256")


def _require_positive_int(value: object, field_name: str) -> None:
    if type(value) is not int or value <= 0:
        raise GuideRetrievalBindingValidationError(f"{field_name} must be a positive integer")


def _validated_json_object(value: object) -> dict[str, JsonValue]:
    validated = _validated_json_value(value)
    if type(validated) is not dict:
        raise GuideRetrievalBindingValidationError("value must be a JSON object")
    return validated


def _validated_json_value(value: object) -> JsonValue:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -(2**53) + 1 <= value <= (2**53) - 1:
            raise GuideRetrievalBindingValidationError("JSON integer is outside the safe range")
        return value
    if type(value) is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise GuideRetrievalBindingValidationError("JSON string contains an invalid surrogate")
        return value
    if type(value) is list:
        return [_validated_json_value(item) for item in value]
    if type(value) is dict:
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise GuideRetrievalBindingValidationError("JSON object key must be a string")
            result[key] = _validated_json_value(item)
        return result
    raise GuideRetrievalBindingValidationError("value is not canonical JSON")


def _canonical_sha256(value: JsonValue) -> str:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "GUIDE_RETRIEVAL_BINDING_PROJECTION_VERSION",
    "GuideRetrievalArtifactRef",
    "GuideRetrievalBindingManifest",
    "GuideRetrievalBindingValidationError",
    "GuideRetrievalMemberBinding",
    "GuideRetrievalSourceMember",
    "JsonValue",
    "build_guide_retrieval_binding_manifest",
    "canonical_source_manifest_hash",
    "compute_filter_snapshot_ref",
    "compute_guide_retrieval_binding_manifest_hash",
    "parse_member_bindings",
    "validate_retrieval_configuration_projection",
]
