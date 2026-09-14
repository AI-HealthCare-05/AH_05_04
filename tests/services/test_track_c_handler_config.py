"""Synthetic rules only: validation does not publish or activate a support rule."""

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.track_c import BarrierCode, SupportActionPlan, SupportCode
from app.services.track_c_handler_config import (
    HandlerConfigError,
    load_handler_config,
    parse_handler_config,
)

APPROVALS = {
    "approved_rule_versions": frozenset({"synthetic-v1", "synthetic-v2"}),
    "approved_copy_versions": frozenset({"synthetic-copy-v1"}),
    "approved_rationale_codes": frozenset({"SYNTHETIC_REASON"}),
}


def synthetic_rules(version: str = "synthetic-v1") -> dict:
    mappings = [
        (SupportCode.REMINDER_SETUP, [BarrierCode.FORGOT, BarrierCode.SCHEDULE_OR_TRAVEL], 10),
        (SupportCode.ROUTINE_OR_TRAVEL_PLAN, [BarrierCode.SCHEDULE_OR_TRAVEL, BarrierCode.FORGOT], 20),
        (SupportCode.INSTRUCTION_REVIEW, [BarrierCode.INSTRUCTIONS_UNCLEAR], 10),
        (SupportCode.PURPOSE_REVIEW, [BarrierCode.NEED_DOUBT], 10),
        (SupportCode.MEDICATION_CONCERN_GUIDANCE, [BarrierCode.MEDICATION_CONCERN], 10),
        (SupportCode.ACCESS_SUPPORT, [BarrierCode.ACCESS_OR_COST], 10),
    ]
    return {
        "schema_version": "track-c-handler-config-v1",
        "rule_version": version,
        "supports": [
            {
                "support_code": code.value,
                "barrier_codes": [barrier.value for barrier in barriers],
                "priority": priority,
                "copy_version": "synthetic-copy-v1",
                "rationale_code": "SYNTHETIC_REASON",
                "parameters": (
                    {"destination": "MEDICATION_SCHEDULE_SETUP"}
                    if code == SupportCode.REMINDER_SETUP
                    else {"content_key": code.value}
                ),
            }
            for code, barriers, priority in mappings
        ],
    }


def test_snapshot_contains_only_approved_fields_and_server_medication_id():
    config = parse_handler_config(synthetic_rules(), **APPROVALS)
    medication_id = uuid4()
    snapshot = config.snapshot(SupportCode.REMINDER_SETUP, medication_id=medication_id)
    assert snapshot == {
        "schema_version": "track-c-handler-config-v1",
        "rationale_code": "SYNTHETIC_REASON",
        "parameters": {
            "destination": "MEDICATION_SCHEDULE_SETUP",
            "prescription_version_medication_id": str(medication_id),
        },
    }
    assert config.snapshot(SupportCode.ACCESS_SUPPORT)["parameters"] == {"content_key": "ACCESS_SUPPORT"}
    with pytest.raises(HandlerConfigError, match="server medication"):
        config.snapshot(SupportCode.REMINDER_SETUP)
    with pytest.raises(HandlerConfigError, match="unexpected medication"):
        config.snapshot(SupportCode.ACCESS_SUPPORT, medication_id=medication_id)


@pytest.mark.parametrize("support_code", list(SupportCode))
def test_each_synthetic_support_round_trips_without_extra_patient_or_schedule_fields(support_code: SupportCode):
    config = parse_handler_config(synthetic_rules(), **APPROVALS)
    medication_id = uuid4()
    bound_id = medication_id if support_code == SupportCode.REMINDER_SETUP else None
    snapshot = config.snapshot(support_code, medication_id=bound_id)
    plan = SupportActionPlan(
        support_code=support_code,
        rule_version=config.rule_version,
        copy_version=config.supports[support_code].copy_version,
        action_config_snapshot=snapshot,
    )
    assert config.restore(plan, medication_id=bound_id) == snapshot
    assert set(snapshot) == {"schema_version", "rationale_code", "parameters"}
    assert set(snapshot["parameters"]) == (
        {"destination", "prescription_version_medication_id"}
        if support_code == SupportCode.REMINDER_SETUP
        else {"content_key"}
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(schema_version="other"),
        lambda data: data.update(rule_version="../../bad"),
        lambda data: data["supports"].pop(),
        lambda data: data["supports"].append(deepcopy(data["supports"][0])),
        lambda data: data["supports"][0].update(priority=True),
        lambda data: data["supports"][0].update(barrier_codes=["ACCESS_OR_COST"]),
        lambda data: data["supports"][0].update(copy_version="missing"),
        lambda data: data["supports"][0].update(rationale_code="missing"),
        lambda data: data["supports"][0]["parameters"].update(medication_name="patient text"),
        lambda data: data["supports"][0]["parameters"].update(prescription_version_medication_id=str(uuid4())),
        lambda data: data["supports"][1]["parameters"].update(url="https://example.test"),
        lambda data: data["supports"][1]["parameters"].update(content_key="other"),
        lambda data: data["supports"][0].update(extra="other"),
    ],
)
def test_invalid_or_unapproved_rules_are_blocked(change):
    data = synthetic_rules()
    change(data)
    with pytest.raises(HandlerConfigError):
        parse_handler_config(data, **APPROVALS)


def test_file_requires_explicit_approval_and_rejects_duplicate_keys(tmp_path: Path):
    path = tmp_path / "synthetic-v1.json"
    path.write_text(json.dumps(synthetic_rules()), encoding="utf-8")
    assert load_handler_config(tmp_path, "synthetic-v1", **APPROVALS).rule_version == "synthetic-v1"
    with pytest.raises(HandlerConfigError, match="unapproved rule file"):
        load_handler_config(tmp_path, "synthetic-v1", **{**APPROVALS, "approved_rule_versions": frozenset()})
    with pytest.raises(HandlerConfigError, match="unapproved rule file"):
        load_handler_config(tmp_path, "../synthetic-v1", **APPROVALS)
    path.write_text('{"schema_version":"track-c-handler-config-v1","schema_version":"other"}', encoding="utf-8")
    with pytest.raises(HandlerConfigError, match="duplicate JSON field"):
        load_handler_config(tmp_path, "synthetic-v1", **APPROVALS)


def test_restore_rejects_legacy_and_tampered_history_without_backfill():
    config = parse_handler_config(synthetic_rules(), **APPROVALS)
    medication_id = uuid4()
    plan = SupportActionPlan(
        support_code=SupportCode.REMINDER_SETUP,
        rule_version="synthetic-v1",
        copy_version="synthetic-copy-v1",
        action_config_snapshot=config.snapshot(SupportCode.REMINDER_SETUP, medication_id=medication_id),
    )
    assert config.restore(plan, medication_id=medication_id) == plan.action_config_snapshot
    for snapshot in ({}, {**plan.action_config_snapshot, "patient": "text"}):
        plan.action_config_snapshot = snapshot
        with pytest.raises(HandlerConfigError):
            config.restore(plan, medication_id=medication_id)
    plan.action_config_snapshot = config.snapshot(SupportCode.REMINDER_SETUP, medication_id=medication_id)
    for key, bad_value in (
        ("schema_version", "future"),
        ("rationale_code", "OTHER_REASON"),
        ("parameters", {"destination": "MEDICATION_SCHEDULE_SETUP"}),
    ):
        plan.action_config_snapshot[key] = bad_value
        with pytest.raises(HandlerConfigError):
            config.restore(plan, medication_id=medication_id)
        plan.action_config_snapshot = config.snapshot(SupportCode.REMINDER_SETUP, medication_id=medication_id)
    plan.copy_version = "other-copy"
    with pytest.raises(HandlerConfigError, match="copy reference"):
        config.restore(plan, medication_id=medication_id)
    plan.copy_version = "synthetic-copy-v1"
    with pytest.raises(HandlerConfigError, match="parent"):
        config.restore(plan, medication_id=uuid4())
    plan.rule_version = "synthetic-v2"
    with pytest.raises(HandlerConfigError, match="historical rule"):
        config.restore(plan, medication_id=medication_id)
