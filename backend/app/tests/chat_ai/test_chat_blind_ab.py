import hashlib
import json
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.chat_blind_ab import (
    BlindABVariant,
    artifact_json_bytes,
    build_judgment_template,
    load_blind_ab_experiment,
    run_blind_ab_evaluation,
    unblind_judgments,
)
from app.evaluation.chat_blind_ab_runner import _validate_canonical_config, validate_blind_ab_environment
from app.evaluation.chat_history import LiveEvaluationConfigurationError
from app.services.chat_ai.prompt import CHAT_SYSTEM_INSTRUCTIONS
from app.services.chat_ai.schemas import ProviderChatResponse

_REPOSITORY_ROOT = Path(__file__).parents[4]
_CONFIG_PATH = _REPOSITORY_ROOT / "evals" / "generation" / "chat-conversation-quality-blind-ab-v1.json"


class ScriptedUsageProvider:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = iter(outputs)
        self.calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> ProviderChatResponse:
        self.calls.append(kwargs)
        return ProviderChatResponse(
            content=next(self._outputs),
            model_name=f"{kwargs['model']}-snapshot",
            input_tokens=1,
            output_tokens=2,
            total_tokens=3,
        )


def _provider_outputs(dataset: dict[str, Any]) -> list[str]:
    outputs = [case["replay_outputs"][path] for case in dataset["cases"] for path in ("baseline", "history")]
    outputs.extend(
        "최대 history 합성 검증 답변입니다." for _ in range(int(dataset["max_history_fixture"]["sample_count"]))
    )
    sampling_case = next(case for case in dataset["cases"] if "live_sampling" in case)
    outputs.extend(
        "어느 약을 뜻하는지 약명, 제품명 또는 성분명을 알려주세요."
        for _ in range(int(sampling_case["live_sampling"]["sample_count"]) - 1)
    )
    return outputs


def test_canonical_blind_ab_config_pins_synthetic_dataset_and_prompt_snapshots() -> None:
    experiment, dataset = load_blind_ab_experiment(_CONFIG_PATH)

    assert experiment.experiment_id == "chat-conversation-quality-blind-ab-v1"
    assert experiment.dataset_id == "chat-v4-conversation-quality-eval-v1"
    assert dataset["data_classification"] == "SYNTHETIC"
    assert tuple(variant.prompt_version for variant in experiment.variants) == (
        "chat-prompt-v3",
        "chat-prompt-v4",
    )
    assert {variant.model for variant in experiment.variants} == {"gpt-4o"}
    assert all(
        hashlib.sha256((variant.prompt + "\n").encode()).hexdigest() == variant.prompt_sha256
        for variant in experiment.variants
    )
    assert experiment.variants[1].prompt == CHAT_SYSTEM_INSTRUCTIONS
    assert experiment.variants[1].prompt == experiment.variants[1].prompt_path.read_text().rstrip("\n")


def test_blind_ab_config_rejects_prompt_hash_drift(tmp_path: Path) -> None:
    config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    config["variants"][0]["prompt_sha256"] = "0" * 64
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="prompt snapshot hash mismatch"):
        load_blind_ab_experiment(config_path)


def test_live_runner_accepts_only_the_canonical_config_path(tmp_path: Path) -> None:
    _validate_canonical_config(_CONFIG_PATH)
    copied_config = tmp_path / _CONFIG_PATH.name
    copied_config.write_bytes(_CONFIG_PATH.read_bytes())

    with pytest.raises(LiveEvaluationConfigurationError, match="experiment config is not allowed"):
        _validate_canonical_config(copied_config)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"RUN_OPENAI_CHAT_BLIND_AB_EVAL": "1", "ENV": "staging", "OPENAI_API_KEY": "sk-synthetic"},
        {"RUN_OPENAI_CHAT_BLIND_AB_EVAL": "1", "ENV": "local", "OPENAI_API_KEY": "sk-not-configured"},
    ],
)
def test_blind_ab_live_run_requires_separate_local_opt_in(environment: dict[str, str]) -> None:
    with pytest.raises(LiveEvaluationConfigurationError, match="blind A/B evaluation is not enabled"):
        validate_blind_ab_environment(environment)


async def test_blind_ab_run_creates_balanced_packet_separate_assignment_and_usage() -> None:
    experiment, dataset = load_blind_ab_experiment(_CONFIG_PATH)
    providers: dict[str, ScriptedUsageProvider] = {}

    def provider_factory(variant: BlindABVariant) -> ScriptedUsageProvider:
        provider = ScriptedUsageProvider(_provider_outputs(dataset))
        providers[variant.variant_id] = provider
        return provider

    ticks = count()
    review_packet, assignment = await run_blind_ab_evaluation(
        experiment,
        dataset,
        provider_factory=provider_factory,
        clock=lambda: next(ticks) / 1000,
    )

    assert review_packet["variant_identity_status"] == "BLINDED"
    review_items = review_packet["items"]
    assert isinstance(review_items, list)
    assert len(review_items) == 54
    review_serialized = json.dumps(review_packet, ensure_ascii=False)
    assert "chat-prompt-v3-gpt-4o" not in review_serialized
    assert "chat-prompt-v4-gpt-4o" not in review_serialized
    assert "SYNTHETIC_NAME_SENTINEL_129" not in review_serialized
    assert "SYNTHETIC_CONTACT_SENTINEL_129" not in review_serialized

    judgment_template = build_judgment_template(
        review_packet,
        hashlib.sha256(artifact_json_bytes(review_packet)).hexdigest(),
    )
    template_serialized = json.dumps(judgment_template, ensure_ascii=False)
    judgment_items = judgment_template["items"]
    assert isinstance(judgment_items, list)
    assert len(judgment_items) == 54
    assert "chat-prompt-v3-gpt-4o" not in template_serialized
    assert "chat-prompt-v4-gpt-4o" not in template_serialized

    assert assignment["review_packet_sha256"] == hashlib.sha256(artifact_json_bytes(review_packet)).hexdigest()
    assert assignment["review_item_count"] == 54
    assert assignment["decision"] == {
        "status": "PENDING_BLIND_HUMAN_REVIEW",
        "selected_variant_id": None,
    }
    assignments = assignment["assignments"]
    assert isinstance(assignments, list)
    response_one_counts = {
        variant.variant_id: sum(item["response_1_variant_id"] == variant.variant_id for item in assignments)
        for variant in experiment.variants
    }
    assert set(response_one_counts.values()) == {27}

    variants = assignment["variants"]
    assert isinstance(variants, list)
    for variant in variants:
        assert variant["passed"] is True
        assert variant["token_usage"] == {
            "status": "RUN",
            "response_count": 113,
            "complete_usage_count": 113,
            "input_tokens": 113,
            "output_tokens": 226,
            "total_tokens": 339,
        }
        assert variant["cost"]["status"] == "NOT_CALCULATED"

    for variant in experiment.variants:
        calls = providers[variant.variant_id].calls
        assert len(calls) == 113
        assert {call["model"] for call in calls} == {variant.model}
        assert {call["instructions"] for call in calls} == {variant.prompt}
        assert {call["max_output_tokens"] for call in calls} == {800}


async def test_blind_ab_packet_redacts_sentinel_even_if_provider_repeats_it() -> None:
    experiment, dataset = load_blind_ab_experiment(_CONFIG_PATH)
    outputs = _provider_outputs(dataset)
    sentinel_case_index = next(index for index, case in enumerate(dataset["cases"]) if case.get("pii_sentinels"))
    outputs[sentinel_case_index * 2 + 1] = "SYNTHETIC_NAME_SENTINEL_129"

    ticks = count()
    review_packet, _assignment = await run_blind_ab_evaluation(
        experiment,
        dataset,
        provider_factory=lambda _variant: ScriptedUsageProvider(list(outputs)),
        clock=lambda: next(ticks) / 1000,
    )

    serialized = json.dumps(review_packet, ensure_ascii=False)
    assert "SYNTHETIC_NAME_SENTINEL_129" not in serialized
    assert "[SYNTHETIC_SENTINEL_REDACTED]" in serialized


def test_unblind_judgments_maps_preferences_without_selecting_a_winner() -> None:
    assignments = [
        {
            "item_id": "AB-001",
            "review_dimensions": ["naturalness"],
            "response_1_variant_id": "variant-a",
            "response_2_variant_id": "variant-b",
        },
        {
            "item_id": "AB-002",
            "review_dimensions": ["naturalness"],
            "response_1_variant_id": "variant-b",
            "response_2_variant_id": "variant-a",
        },
    ]
    assignment_artifact = {
        "experiment_id": "experiment",
        "review_packet_sha256": "a" * 64,
        "human_review_dimensions": ["naturalness"],
        "variants": [{"variant_id": "variant-a"}, {"variant_id": "variant-b"}],
        "assignments": assignments,
    }
    judgments = {
        "experiment_id": "experiment",
        "review_packet_sha256": "a" * 64,
        "reviewer": "ceohwj",
        "items": [
            {
                "item_id": "AB-001",
                "preference": "RESPONSE_1",
                "dimension_preferences": {"naturalness": "TIE"},
            },
            {
                "item_id": "AB-002",
                "preference": "RESPONSE_1",
                "dimension_preferences": {"naturalness": "RESPONSE_2"},
            },
        ],
    }

    result = unblind_judgments(assignment_artifact, judgments)

    assert result["overall_preference"] == {
        "variant_wins": {"variant-a": 1, "variant-b": 1},
        "ties": 0,
    }
    assert result["dimension_preference"] == {
        "naturalness": {"variant_wins": {"variant-a": 1, "variant-b": 0}, "ties": 1}
    }
    assert result["decision"] == {
        "status": "PENDING_RESPONSIBLE_REVIEWER_APPROVAL",
        "selected_variant_id": None,
    }


def test_unblind_judgments_rejects_incomplete_item_coverage() -> None:
    assignment_artifact = {
        "experiment_id": "experiment",
        "review_packet_sha256": "a" * 64,
        "human_review_dimensions": [],
        "variants": [{"variant_id": "variant-a"}, {"variant_id": "variant-b"}],
        "assignments": [
            {
                "item_id": "AB-001",
                "response_1_variant_id": "variant-a",
                "response_2_variant_id": "variant-b",
            }
        ],
    }

    with pytest.raises(ValueError, match="cover every blind review item"):
        unblind_judgments(
            assignment_artifact,
            {
                "experiment_id": "experiment",
                "review_packet_sha256": "a" * 64,
                "items": [],
            },
        )
