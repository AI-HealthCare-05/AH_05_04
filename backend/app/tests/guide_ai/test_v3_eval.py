import json
from pathlib import Path

import pytest

from app.services.guide_ai.exceptions import GuideGenerationSafetyError
from app.services.guide_ai.schemas import (
    GeneratedGuideDraft,
    GeneratedMedicationGuidance,
    GuideGuidanceIntent,
)
from app.services.guide_ai.validators import validate_generated_draft

_DATASET_PATH = Path(__file__).parents[4] / "evals" / "generation" / "guide-v3-eval-v1.json"


def test_guide_v3_eval_v1_accepts_only_expected_synthetic_outputs() -> None:
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))

    assert dataset["dataset_id"] == "guide-v3-eval-v1"
    assert dataset["data_classification"] == "SYNTHETIC"
    accepted_by_intent = {intent: 0 for intent in GuideGuidanceIntent}

    for case in dataset["cases"]:
        expected_intent = GuideGuidanceIntent(case["expected_intent"])
        draft = GeneratedGuideDraft(
            medications=[
                GeneratedMedicationGuidance(
                    source_index=0,
                    guidance_intent=GuideGuidanceIntent(case["output_intent"]),
                    guidance=case["guidance"],
                )
            ],
            general_notice=case["general_notice"],
        )
        expected_rule = case["expected_rule"]

        if expected_rule is None:
            validate_generated_draft(draft, expected_intents={0: expected_intent})
            accepted_by_intent[expected_intent] += 1
            continue

        with pytest.raises(GuideGenerationSafetyError) as exc_info:
            validate_generated_draft(draft, expected_intents={0: expected_intent})
        assert exc_info.value.rule_id == expected_rule, case["case_id"]

    assert accepted_by_intent == {
        GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING: 3,
        GuideGuidanceIntent.FOLLOW_CONFIRMED_SCHEDULE: 3,
    }
