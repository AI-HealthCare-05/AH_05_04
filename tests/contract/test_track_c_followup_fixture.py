"""Frontend handoff examples must remain consumable by the Backend DTOs."""

import json
from pathlib import Path

from app.dtos.track_c_support import (
    ActionPlanFollowupEnvelope,
    ActionPlanFollowupReadEnvelope,
    SubmitActionPlanFollowupRequest,
)


def test_followup_handoff_fixture_matches_revision_and_response_contract():
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/post_mvp_1/track_c/followup-v1.json").read_text(encoding="utf-8")
    )
    assert ActionPlanFollowupReadEnvelope.model_validate(fixture["empty"]).data is None
    initial_request = SubmitActionPlanFollowupRequest.model_validate(fixture["initial_request"])
    correction_request = SubmitActionPlanFollowupRequest.model_validate(fixture["correction_request"])
    initial = ActionPlanFollowupEnvelope.model_validate(fixture["initial_response"]).data
    corrected = ActionPlanFollowupEnvelope.model_validate(fixture["corrected_response"]).data
    assert initial_request.expected_revision == 0
    assert initial.response == initial_request.response
    assert correction_request.expected_revision == initial.revision == 1
    assert corrected.response == correction_request.response
    assert corrected.revision == initial.revision + 1
    assert initial.followup_id == corrected.followup_id
    assert str(initial.support_action_plan_id) == str(corrected.support_action_plan_id) == fixture["plan_id"]
    assert initial.created_at == corrected.created_at
    assert initial.updated_at < corrected.updated_at
