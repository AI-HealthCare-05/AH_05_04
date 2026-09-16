import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.dtos.track_c_support import CreateSupportActionPlanRequest, SupportActionPlanResponse, SupportOfferResponse
from app.services.track_c_handler_config import load_active_handler_config, load_active_support_copy_catalog


def test_frontend_fixture_matches_current_dtos_and_approved_assets() -> None:
    fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures/post_mvp_1/track_c/support-plan-v1.json").read_text()
    )
    assert fixture["classification"] == "SYNTHETIC"
    offer = SupportOfferResponse.model_validate(fixture["single_offer"]).data
    assert len(offer.supports) == 1
    assert offer.reason_code is None
    empty = SupportOfferResponse.model_validate(fixture["declined_offer"]).data
    assert empty.supports == []
    assert empty.reason_code == "NO_ELIGIBLE_SUPPORT"
    request = CreateSupportActionPlanRequest.model_validate(fixture["create_request"])
    plan = SupportActionPlanResponse.model_validate(fixture["create_response"]).data
    item = offer.supports[0]
    config = load_active_handler_config()
    copy = load_active_support_copy_catalog().supports[item.support_code]
    assert request.support_code == item.support_code == plan.support_code
    assert request.rule_version == config.rule_version == item.rule_version == plan.rule_version
    assert (
        request.copy_version
        == config.supports[item.support_code].copy_version
        == item.copy_version
        == plan.copy_version
    )
    assert plan.action_config_snapshot == item.action_config
    assert item.support_copy.title == copy.title
    assert item.support_copy.body == copy.body
    assert item.support_copy.confirmation_prompt == copy.confirmation_prompt
    assert item.support_copy.primary_label == copy.primary_label
    assert item.support_copy.secondary_label == copy.secondary_label
    assert request.confirmed is True
    assert plan.status == "ACTIVE"
    invalid = {**fixture["single_offer"]["data"], "supports": [item, item]}
    with pytest.raises(ValidationError):
        SupportOfferResponse.model_validate({"data": invalid})
