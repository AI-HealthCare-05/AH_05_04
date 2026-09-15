import hashlib
import json
import random
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evaluation.chat_history import ExecutionReport, run_live_evaluation
from app.services.chat_ai.client import ChatProvider
from app.services.chat_ai.schemas import ProviderChatResponse

_REPOSITORY_ROOT = Path(__file__).parents[3]
_PREFERENCE_VALUES = frozenset({"RESPONSE_1", "RESPONSE_2", "TIE"})


@dataclass(frozen=True)
class BlindABVariant:
    variant_id: str
    prompt_version: str
    source_commit: str
    prompt_path: Path
    prompt_sha256: str
    prompt: str
    model: str


@dataclass(frozen=True)
class BlindABExperiment:
    experiment_id: str
    dataset_path: Path
    dataset_id: str
    dataset_sha256: str
    review_paths: tuple[str, ...]
    human_review_dimensions: tuple[str, ...]
    max_output_tokens: int
    timeout_seconds: float
    variants: tuple[BlindABVariant, BlindABVariant]


class ConfiguredVariantProvider:
    def __init__(
        self,
        provider: ChatProvider,
        *,
        variant: BlindABVariant,
        max_output_tokens: int,
    ) -> None:
        self._provider = provider
        self._variant = variant
        self._max_output_tokens = max_output_tokens
        self.responses: list[ProviderChatResponse] = []

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderChatResponse:
        del model, instructions, max_output_tokens
        response = await self._provider.generate(
            model=self._variant.model,
            instructions=self._variant.prompt,
            input_json=input_json,
            max_output_tokens=self._max_output_tokens,
        )
        self.responses.append(response)
        return response


def normalized_sha256(content: bytes) -> str:
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def artifact_json_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _repository_path(raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("Experiment paths must be non-empty strings")
    path = (_REPOSITORY_ROOT / raw_path).resolve()
    if not path.is_relative_to(_REPOSITORY_ROOT):
        raise ValueError("Experiment paths must stay inside the repository")
    return path


def _load_dataset_reference(dataset_ref: dict[str, Any]) -> tuple[Path, str, str, dict[str, Any]]:
    dataset_path = _repository_path(dataset_ref.get("path"))
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    dataset_sha256 = str(dataset_ref.get("sha256", ""))
    dataset_id = str(dataset_ref.get("dataset_id", ""))
    if (
        normalized_sha256(dataset_bytes) != dataset_sha256
        or not isinstance(dataset, dict)
        or dataset.get("dataset_id") != dataset_id
        or dataset.get("data_classification") != "SYNTHETIC"
    ):
        raise ValueError("Blind A/B dataset reference is not the approved synthetic fixture")
    return dataset_path, dataset_id, dataset_sha256, dataset


def _load_variant(raw_variant: object) -> BlindABVariant:
    if not isinstance(raw_variant, dict):
        raise ValueError("Blind A/B variant must be an object")
    prompt_path = _repository_path(raw_variant.get("prompt_path"))
    prompt_bytes = prompt_path.read_bytes()
    prompt_sha256 = str(raw_variant.get("prompt_sha256", ""))
    if normalized_sha256(prompt_bytes) != prompt_sha256:
        raise ValueError("Blind A/B prompt snapshot hash mismatch")
    variant = BlindABVariant(
        variant_id=str(raw_variant.get("variant_id", "")),
        prompt_version=str(raw_variant.get("prompt_version", "")),
        source_commit=str(raw_variant.get("source_commit", "")),
        prompt_path=prompt_path,
        prompt_sha256=prompt_sha256,
        prompt=prompt_bytes.replace(b"\r\n", b"\n").decode("utf-8").rstrip("\n"),
        model=str(raw_variant.get("model", "")),
    )
    if not variant.variant_id or not variant.prompt_version or len(variant.source_commit) != 40 or not variant.model:
        raise ValueError("Blind A/B variant identity is incomplete")
    return variant


def _controlled_settings(settings: dict[str, Any], dataset: dict[str, Any]) -> tuple[int, float]:
    if settings.get("store") is not False or settings.get("temperature") is not None:
        raise ValueError("Blind A/B provider storage and temperature controls changed")
    max_output_tokens = settings.get("max_output_tokens")
    timeout_seconds = settings.get("timeout_seconds")
    if type(max_output_tokens) is not int or max_output_tokens <= 0:
        raise ValueError("Blind A/B max_output_tokens must be positive")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ValueError("Blind A/B timeout_seconds must be positive")
    model_settings = dataset.get("model_settings", {})
    if (
        model_settings.get("max_output_tokens") != max_output_tokens
        or model_settings.get("timeout_seconds") != timeout_seconds
        or model_settings.get("temperature") is not None
    ):
        raise ValueError("Blind A/B controlled settings must match the canonical dataset")
    return max_output_tokens, float(timeout_seconds)


def load_blind_ab_experiment(config_path: Path) -> tuple[BlindABExperiment, dict[str, Any]]:
    raw_config = json.loads(config_path.read_bytes())
    if not isinstance(raw_config, dict) or raw_config.get("schema_version") != 1:
        raise ValueError("Unsupported blind A/B experiment schema")
    experiment_id = raw_config.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise ValueError("Blind A/B experiment_id is required")
    if raw_config.get("environment") != "LOCAL" or raw_config.get("provider_invocation") is not True:
        raise ValueError("Blind A/B experiment must be an explicit Local provider run")

    dataset_ref = raw_config.get("dataset")
    settings = raw_config.get("controlled_settings")
    raw_variants = raw_config.get("variants")
    if not isinstance(dataset_ref, dict) or not isinstance(settings, dict):
        raise ValueError("Blind A/B experiment dataset and settings are required")
    if not isinstance(raw_variants, list) or len(raw_variants) != 2:
        raise ValueError("Blind A/B experiment requires exactly two variants")
    dataset_path, dataset_id, dataset_sha256, dataset = _load_dataset_reference(dataset_ref)
    variants = [_load_variant(raw_variant) for raw_variant in raw_variants]
    if len({variant.variant_id for variant in variants}) != 2:
        raise ValueError("Blind A/B variant ids must be unique")

    review_paths = tuple(raw_config.get("review_paths", ()))
    dimensions = tuple(raw_config.get("human_review_dimensions", ()))
    if set(review_paths) != {"baseline", "history"} or not dimensions:
        raise ValueError("Blind A/B review paths and dimensions are incomplete")
    if "blind_seed" in raw_config:
        raise ValueError("Blind A/B seed must not be public")

    max_output_tokens, timeout_seconds = _controlled_settings(settings, dataset)

    return (
        BlindABExperiment(
            experiment_id=experiment_id,
            dataset_path=dataset_path,
            dataset_id=dataset_id,
            dataset_sha256=dataset_sha256,
            review_paths=review_paths,
            human_review_dimensions=dimensions,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            variants=(variants[0], variants[1]),
        ),
        dataset,
    )


def _token_usage(responses: list[ProviderChatResponse]) -> dict[str, int | str]:
    complete = [
        response
        for response in responses
        if response.input_tokens is not None
        and response.output_tokens is not None
        and response.total_tokens is not None
    ]
    if not complete:
        return {
            "status": "NOT_AVAILABLE",
            "response_count": len(responses),
            "complete_usage_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
    return {
        "status": "RUN" if len(complete) == len(responses) else "PARTIAL",
        "response_count": len(responses),
        "complete_usage_count": len(complete),
        "input_tokens": sum(response.input_tokens or 0 for response in complete),
        "output_tokens": sum(response.output_tokens or 0 for response in complete),
        "total_tokens": sum(response.total_tokens or 0 for response in complete),
    }


def _sanitize(value: Any, sentinels: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        for sentinel in sentinels:
            value = value.replace(sentinel, "[SYNTHETIC_SENTINEL_REDACTED]")
        return value
    if isinstance(value, list):
        return [_sanitize(item, sentinels) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize(item, sentinels) for key, item in value.items()}
    return value


def _variant_summary(
    variant: BlindABVariant,
    report: ExecutionReport,
    responses: list[ProviderChatResponse],
) -> dict[str, object]:
    return {
        "variant_id": variant.variant_id,
        "prompt_version": variant.prompt_version,
        "source_commit": variant.source_commit,
        "prompt_sha256": variant.prompt_sha256,
        "model": variant.model,
        "provider_model_names": sorted({response.model_name for response in responses}),
        "passed": report.passed,
        "metrics": report.evaluation.metrics,
        "live_gate_evaluation": report.live_gate_evaluation,
        "latency": {
            "baseline_p95_ms": report.observations["baseline_p95_ms"],
            "history_p95_ms": report.observations["history_p95_ms"],
            "max_history_p95_ms": report.observations["max_history_p95_ms"],
        },
        "token_usage": _token_usage(responses),
        "cost": {
            "status": "NOT_CALCULATED",
            "reason": "No approved versioned provider rate card is configured.",
        },
    }


async def run_blind_ab_evaluation(
    experiment: BlindABExperiment,
    dataset: dict[str, Any],
    *,
    provider_factory: Callable[[BlindABVariant], ChatProvider],
    clock: Callable[[], float],
) -> tuple[dict[str, object], dict[str, object]]:
    blind_seed = secrets.randbits(256)
    rng = random.Random(blind_seed)
    execution_order = list(experiment.variants)
    rng.shuffle(execution_order)
    runs: dict[str, tuple[ExecutionReport, ConfiguredVariantProvider]] = {}
    for variant in execution_order:
        provider = ConfiguredVariantProvider(
            provider_factory(variant),
            variant=variant,
            max_output_tokens=experiment.max_output_tokens,
        )
        report = await run_live_evaluation(dataset, provider=provider, clock=clock)
        runs[variant.variant_id] = (report, provider)

    item_specs = [
        (case_index, path) for case_index, _case in enumerate(dataset["cases"]) for path in experiment.review_paths
    ]
    rng.shuffle(item_specs)
    response_one_positions = [0, 1] * (len(item_specs) // 2)
    rng.shuffle(response_one_positions)
    sentinels = tuple(
        sentinel for dataset_case in dataset["cases"] for sentinel in dataset_case.get("pii_sentinels", ())
    )
    review_items: list[dict[str, object]] = []
    assignments: list[dict[str, object]] = []
    for item_index, (case_index, path) in enumerate(item_specs, start=1):
        case = dataset["cases"][case_index]
        response_index = case_index * 2 + (0 if path == "baseline" else 1)
        first_variant_index = response_one_positions[item_index - 1]
        ordered_variants = (
            experiment.variants[first_variant_index],
            experiment.variants[1 - first_variant_index],
        )
        item_id = f"AB-{item_index:03d}"
        responses = {}
        review_dimensions = sorted(case.get("quality_expectations", {})) if path == "history" else []
        assignment: dict[str, object] = {
            "item_id": item_id,
            "review_dimensions": review_dimensions,
        }
        for response_number, variant in enumerate(ordered_variants, start=1):
            variant_responses = runs[variant.variant_id][1].responses
            responses[f"response_{response_number}"] = _sanitize(
                variant_responses[response_index].content,
                sentinels,
            )
            assignment[f"response_{response_number}_variant_id"] = variant.variant_id
        review_items.append(
            {
                "item_id": item_id,
                "case_id": case["case_id"],
                "scenario_type": case["scenario_type"],
                "path": path,
                "review_dimensions": review_dimensions,
                "question": _sanitize(case["question"], sentinels),
                "history": _sanitize(case["history"] if path == "history" else [], sentinels),
                "medications": _sanitize(case["medications"], sentinels),
                "responses": responses,
            }
        )
        assignments.append(assignment)

    review_packet: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": experiment.experiment_id,
        "dataset_id": experiment.dataset_id,
        "data_classification": "SYNTHETIC",
        "variant_identity_status": "BLINDED",
        "human_review_dimensions": list(experiment.human_review_dimensions),
        "preference_values": sorted(_PREFERENCE_VALUES),
        "items": review_items,
    }
    review_packet_sha256 = normalized_sha256(artifact_json_bytes(review_packet))
    assignment_artifact: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": experiment.experiment_id,
        "status": "RUN",
        "blind_seed": blind_seed,
        "commitment_nonce": secrets.token_hex(32),
        "dataset": {
            "dataset_id": experiment.dataset_id,
            "sha256": experiment.dataset_sha256,
        },
        "review_packet_sha256": review_packet_sha256,
        "review_item_count": len(review_items),
        "execution_order": [variant.variant_id for variant in execution_order],
        "variants": [
            _variant_summary(
                variant,
                runs[variant.variant_id][0],
                runs[variant.variant_id][1].responses,
            )
            for variant in experiment.variants
        ],
        "human_review_dimensions": list(experiment.human_review_dimensions),
        "assignments": assignments,
        "human_review": {
            "status": "NOT_RUN",
            "responsible_reviewer": "ceohwj",
        },
        "decision": {
            "status": "PENDING_BLIND_HUMAN_REVIEW",
            "selected_variant_id": None,
        },
    }
    commitment = assignment_commitment(assignment_artifact)
    review_packet["assignment_commitment_sha256"] = commitment
    assignment_artifact["assignment_commitment_sha256"] = commitment
    assignment_artifact["review_packet_sha256"] = normalized_sha256(artifact_json_bytes(review_packet))
    return review_packet, assignment_artifact


def assignment_commitment(assignment_artifact: dict[str, Any]) -> str:
    # Exclude the packet hash to avoid a cycle: the packet contains this commitment.
    payload = {
        key: value
        for key, value in assignment_artifact.items()
        if key not in {"assignment_commitment_sha256", "review_packet_sha256"}
    }
    return normalized_sha256(artifact_json_bytes(payload))


def build_judgment_template(
    review_packet: dict[str, Any],
    review_packet_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": review_packet["experiment_id"],
        "review_packet_sha256": review_packet_sha256,
        "assignment_commitment_sha256": review_packet["assignment_commitment_sha256"],
        "reviewer": None,
        "items": [
            {
                "item_id": item["item_id"],
                "preference": None,
                "dimension_preferences": {dimension: None for dimension in item.get("review_dimensions", ())},
            }
            for item in review_packet["items"]
        ],
    }


def _validated_judgment_items(
    assignment_artifact: dict[str, Any],
    judgments: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if judgments.get("experiment_id") != assignment_artifact.get("experiment_id"):
        raise ValueError("Judgment experiment_id does not match")
    if judgments.get("review_packet_sha256") != assignment_artifact.get("review_packet_sha256"):
        raise ValueError("Judgment review packet hash does not match")
    commitment = assignment_commitment(assignment_artifact)
    if (
        judgments.get("assignment_commitment_sha256") != commitment
        or assignment_artifact.get("assignment_commitment_sha256") != commitment
    ):
        raise ValueError("Judgment assignment commitment does not match")
    assignments = {assignment["item_id"]: assignment for assignment in assignment_artifact.get("assignments", [])}
    raw_items = judgments.get("items")
    if not isinstance(raw_items, list) or len(raw_items) != len(assignments):
        raise ValueError("Judgments must cover every blind review item exactly once")
    if any(not isinstance(raw_item, dict) for raw_item in raw_items):
        raise ValueError("Each judgment must be an object")
    return assignments, raw_items


def _preferred_variant(preference: object, assignment: dict[str, Any]) -> str | None:
    if preference not in _PREFERENCE_VALUES:
        raise ValueError("Judgment preference is invalid")
    if preference == "TIE":
        return None
    variant_id = assignment.get(f"{str(preference).lower()}_variant_id")
    if not isinstance(variant_id, str):
        raise ValueError("Blind assignment is invalid")
    return variant_id


def _record_dimension_preferences(
    raw_dimensions: object,
    *,
    assignment: dict[str, Any],
    allowed_dimensions: set[str],
    variant_ids: tuple[str, ...],
    dimension_wins: dict[str, dict[str, int]],
    dimension_ties: dict[str, int],
) -> None:
    if not isinstance(raw_dimensions, dict):
        raise ValueError("dimension_preferences must be an object")
    expected_dimensions = set(assignment.get("review_dimensions", ()))
    if set(raw_dimensions) != expected_dimensions:
        raise ValueError("Dimension judgments must cover every applicable dimension")
    for dimension, preference in raw_dimensions.items():
        if dimension not in allowed_dimensions:
            raise ValueError("Judgment dimension is invalid")
        preferred_variant = _preferred_variant(preference, assignment)
        dimension_wins.setdefault(dimension, {variant_id: 0 for variant_id in variant_ids})
        dimension_ties.setdefault(dimension, 0)
        if preferred_variant is None:
            dimension_ties[dimension] += 1
        else:
            dimension_wins[dimension][preferred_variant] += 1


def unblind_judgments(
    assignment_artifact: dict[str, Any],
    judgments: dict[str, Any],
) -> dict[str, object]:
    assignments, raw_items = _validated_judgment_items(assignment_artifact, judgments)
    seen: set[str] = set()
    variant_ids = tuple(variant["variant_id"] for variant in assignment_artifact.get("variants", []))
    overall_wins = {variant_id: 0 for variant_id in variant_ids}
    allowed_dimensions = set(assignment_artifact.get("human_review_dimensions", ()))
    ties = 0
    dimension_wins: dict[str, dict[str, int]] = {}
    dimension_ties: dict[str, int] = {}
    for raw_item in raw_items:
        item_id = raw_item.get("item_id")
        if not isinstance(item_id, str) or item_id in seen or item_id not in assignments:
            raise ValueError("Judgment item ids must be unique and complete")
        seen.add(item_id)
        assignment = assignments[item_id]
        preferred_variant = _preferred_variant(raw_item.get("preference"), assignment)
        if preferred_variant is None:
            ties += 1
        else:
            overall_wins[preferred_variant] += 1
        _record_dimension_preferences(
            raw_item.get("dimension_preferences", {}),
            assignment=assignment,
            allowed_dimensions=allowed_dimensions,
            variant_ids=variant_ids,
            dimension_wins=dimension_wins,
            dimension_ties=dimension_ties,
        )

    if seen != set(assignments):
        raise ValueError("Judgments must cover every blind review item exactly once")
    return {
        "schema_version": 1,
        "experiment_id": assignment_artifact["experiment_id"],
        "review_packet_sha256": assignment_artifact["review_packet_sha256"],
        "assignment_commitment_sha256": assignment_artifact["assignment_commitment_sha256"],
        "reviewer": judgments.get("reviewer"),
        "overall_preference": {"variant_wins": overall_wins, "ties": ties},
        "dimension_preference": {
            dimension: {"variant_wins": wins, "ties": dimension_ties[dimension]}
            for dimension, wins in sorted(dimension_wins.items())
        },
        "automated_results": assignment_artifact["variants"],
        "decision": {
            "status": "PENDING_RESPONSIBLE_REVIEWER_APPROVAL",
            "selected_variant_id": None,
        },
    }
