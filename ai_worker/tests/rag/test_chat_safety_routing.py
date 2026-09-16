from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path

import pytest

from ai_worker.tasks.rag.chat_safety_routing import (
    NODE_APPROVED_UNKNOWN_RISK_FALLBACK,
    NODE_APPROVED_URGENT_OR_EMERGENCY_GUIDANCE,
    NODE_CLASSIFY_QUESTION,
    NODE_SAFETY_TRIAGE,
    BlockedActionTerminalPlan,
    ChatSafetyRoutingDecision,
    ChatSafetyRoutingNodeId,
    ChatSafetyTriageTerminalPlan,
    ExecutionStatus,
    ReleaseDecision,
    ResolvedBlockedActionObservation,
    ResponseLevel,
    SafetyDisposition,
    plan_blocked_action,
    route_safety_triage,
)

_MODULE_PATH = Path(__file__).resolve().parents[2] / "tasks" / "rag" / "chat_safety_routing.py"


def test_chat_safety_routing_node_id_contains_exactly_four_nodes_for_this_slice() -> None:
    expected = {
        "safety_triage",
        "classify_question",
        "approved_urgent_or_emergency_guidance",
        "approved_unknown_risk_fallback",
    }
    actual = {node.value for node in ChatSafetyRoutingNodeId}
    assert actual == expected
    assert len(ChatSafetyRoutingNodeId) == 4
    assert ChatSafetyRoutingNodeId.SAFETY_TRIAGE.value == "safety_triage"
    assert ChatSafetyRoutingNodeId.CLASSIFY_QUESTION.value == "classify_question"
    assert (
        ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE.value == "approved_urgent_or_emergency_guidance"
    )
    assert ChatSafetyRoutingNodeId.APPROVED_UNKNOWN_RISK_FALLBACK.value == "approved_unknown_risk_fallback"


def test_response_level_contains_exactly_four_contract_values() -> None:
    expected = {"ROUTINE", "URGENT", "EMERGENCY", "UNKNOWN"}
    actual = {level.value for level in ResponseLevel}
    assert actual == expected
    assert len(ResponseLevel) == 4
    for name in expected:
        assert getattr(ResponseLevel, name).value == name


def test_safety_disposition_contains_exactly_five_contract_values() -> None:
    expected = {
        "NORMAL",
        "URGENT_ROUTED",
        "EMERGENCY_ROUTED",
        "UNKNOWN_RISK",
        "BLOCKED_ACTION",
    }
    actual = {disp.value for disp in SafetyDisposition}
    assert actual == expected
    assert len(SafetyDisposition) == 5


def test_blocked_action_is_not_a_response_level() -> None:
    assert not hasattr(ResponseLevel, "BLOCKED_ACTION")
    assert "BLOCKED_ACTION" not in {level.value for level in ResponseLevel}
    with pytest.raises(ValueError):
        ResponseLevel("BLOCKED_ACTION")


def test_fixed_graph_node_constants_match_contract_and_enum() -> None:
    assert NODE_SAFETY_TRIAGE is ChatSafetyRoutingNodeId.SAFETY_TRIAGE
    assert NODE_CLASSIFY_QUESTION is ChatSafetyRoutingNodeId.CLASSIFY_QUESTION
    assert NODE_APPROVED_URGENT_OR_EMERGENCY_GUIDANCE is ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE
    assert NODE_APPROVED_UNKNOWN_RISK_FALLBACK is ChatSafetyRoutingNodeId.APPROVED_UNKNOWN_RISK_FALLBACK


def test_route_safety_triage_routine_mapping() -> None:
    decision = route_safety_triage(ResponseLevel.ROUTINE)

    assert isinstance(decision, ChatSafetyRoutingDecision)
    assert decision.source_node_id is ChatSafetyRoutingNodeId.SAFETY_TRIAGE
    assert decision.response_level is ResponseLevel.ROUTINE
    assert decision.safety_disposition is SafetyDisposition.NORMAL
    assert decision.next_node_id is ChatSafetyRoutingNodeId.CLASSIFY_QUESTION
    assert decision.continue_general_rag is True
    assert decision.terminal_plan is None


def test_route_safety_triage_urgent_mapping() -> None:
    decision = route_safety_triage(ResponseLevel.URGENT)

    assert isinstance(decision, ChatSafetyRoutingDecision)
    assert decision.source_node_id is ChatSafetyRoutingNodeId.SAFETY_TRIAGE
    assert decision.response_level is ResponseLevel.URGENT
    assert decision.safety_disposition is SafetyDisposition.URGENT_ROUTED
    assert decision.next_node_id is ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE
    assert decision.continue_general_rag is False
    assert decision.terminal_plan == ChatSafetyTriageTerminalPlan(
        expected_execution_status=ExecutionStatus.SUCCEEDED,
        expected_release_decision=ReleaseDecision.PASS,
        requires_approved_response=True,
    )


def test_route_safety_triage_emergency_mapping() -> None:
    decision = route_safety_triage(ResponseLevel.EMERGENCY)

    assert isinstance(decision, ChatSafetyRoutingDecision)
    assert decision.source_node_id is ChatSafetyRoutingNodeId.SAFETY_TRIAGE
    assert decision.response_level is ResponseLevel.EMERGENCY
    assert decision.safety_disposition is SafetyDisposition.EMERGENCY_ROUTED
    assert decision.next_node_id is ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE
    assert decision.continue_general_rag is False
    assert decision.terminal_plan == ChatSafetyTriageTerminalPlan(
        expected_execution_status=ExecutionStatus.SUCCEEDED,
        expected_release_decision=ReleaseDecision.PASS,
        requires_approved_response=True,
    )


def test_urgent_and_emergency_share_approved_guidance_node_but_preserve_distinct_states() -> None:
    urgent = route_safety_triage(ResponseLevel.URGENT)
    emergency = route_safety_triage(ResponseLevel.EMERGENCY)

    assert (
        urgent.next_node_id == emergency.next_node_id == ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE
    )
    assert urgent.response_level is not emergency.response_level
    assert urgent.safety_disposition is not emergency.safety_disposition
    assert urgent.safety_disposition is SafetyDisposition.URGENT_ROUTED
    assert emergency.safety_disposition is SafetyDisposition.EMERGENCY_ROUTED


def test_route_safety_triage_unknown_mapping() -> None:
    decision = route_safety_triage(ResponseLevel.UNKNOWN)

    assert isinstance(decision, ChatSafetyRoutingDecision)
    assert decision.source_node_id is ChatSafetyRoutingNodeId.SAFETY_TRIAGE
    assert decision.response_level is ResponseLevel.UNKNOWN
    assert decision.safety_disposition is SafetyDisposition.UNKNOWN_RISK
    assert decision.next_node_id is ChatSafetyRoutingNodeId.APPROVED_UNKNOWN_RISK_FALLBACK
    assert decision.continue_general_rag is False
    assert decision.terminal_plan == ChatSafetyTriageTerminalPlan(
        expected_execution_status=ExecutionStatus.NO_RESULT,
        expected_release_decision=ReleaseDecision.REJECTED,
        requires_approved_response=True,
    )


def test_routine_has_no_terminal_plan() -> None:
    routine = route_safety_triage(ResponseLevel.ROUTINE)
    assert routine.terminal_plan is None


def test_triage_never_produces_blocked_action_disposition() -> None:
    for level in ResponseLevel:
        decision = route_safety_triage(level)
        assert decision.safety_disposition is not SafetyDisposition.BLOCKED_ACTION


def test_resolved_blocked_action_observation_field_allowlist_and_safety() -> None:
    assert is_dataclass(ResolvedBlockedActionObservation)
    assert {f.name for f in fields(ResolvedBlockedActionObservation)} == {
        "response_level",
        "safety_disposition",
    }

    obs = ResolvedBlockedActionObservation(response_level=ResponseLevel.ROUTINE)
    assert obs.response_level is ResponseLevel.ROUTINE
    assert obs.safety_disposition is SafetyDisposition.BLOCKED_ACTION
    assert not hasattr(obs, "content")
    assert not hasattr(obs, "text")
    assert not hasattr(obs, "reason")
    assert not hasattr(obs, "message")
    assert not hasattr(obs, "node_id")


def test_resolved_blocked_action_observation_rejects_invalid_values() -> None:
    with pytest.raises(TypeError):
        ResolvedBlockedActionObservation(response_level="ROUTINE")  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        ResolvedBlockedActionObservation(
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
        )


def test_plan_blocked_action_requires_typed_observation() -> None:
    for level in ResponseLevel:
        obs = ResolvedBlockedActionObservation(response_level=level)
        plan = plan_blocked_action(obs)
        assert isinstance(plan, BlockedActionTerminalPlan)
        assert plan.response_level is level
        assert plan.safety_disposition is SafetyDisposition.BLOCKED_ACTION
        assert plan.expected_execution_status is ExecutionStatus.SUCCEEDED
        assert plan.expected_release_decision is ReleaseDecision.LIMITED
        assert plan.requires_approved_response is True


def test_plan_blocked_action_rejects_bare_response_level_or_raw_input() -> None:
    with pytest.raises(TypeError):
        plan_blocked_action(ResponseLevel.ROUTINE)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        plan_blocked_action("ROUTINE")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        plan_blocked_action(None)  # type: ignore[arg-type]


def test_blocked_action_terminal_plan_has_no_content_or_graph_node() -> None:
    blocked_fields = {f.name for f in fields(BlockedActionTerminalPlan)}
    assert "content" not in blocked_fields
    assert "next_node_id" not in blocked_fields
    assert "source_node_id" not in blocked_fields
    assert "node_id" not in blocked_fields
    assert "fallback_code" not in blocked_fields

    sample = plan_blocked_action(ResolvedBlockedActionObservation(response_level=ResponseLevel.ROUTINE))
    assert not hasattr(sample, "content")
    assert not hasattr(sample, "next_node_id")
    assert not hasattr(sample, "source_node_id")
    assert not hasattr(sample, "node_id")
    assert not hasattr(sample, "fallback_code")


def test_blocked_action_terminal_plan_rejects_invalid_direct_construction() -> None:
    with pytest.raises(ValueError):
        BlockedActionTerminalPlan(
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
        )

    with pytest.raises(ValueError):
        BlockedActionTerminalPlan(
            response_level=ResponseLevel.ROUTINE,
            expected_execution_status=ExecutionStatus.NO_RESULT,
        )

    with pytest.raises(ValueError):
        BlockedActionTerminalPlan(
            response_level=ResponseLevel.ROUTINE,
            expected_release_decision=ReleaseDecision.PASS,
        )

    with pytest.raises(ValueError):
        BlockedActionTerminalPlan(
            response_level=ResponseLevel.ROUTINE,
            requires_approved_response=False,
        )

    with pytest.raises(TypeError):
        BlockedActionTerminalPlan(
            response_level="ROUTINE",  # type: ignore[arg-type]
        )


def test_exact_dataclass_field_allowlists() -> None:
    assert is_dataclass(ChatSafetyRoutingDecision)
    assert is_dataclass(ChatSafetyTriageTerminalPlan)
    assert is_dataclass(BlockedActionTerminalPlan)
    assert is_dataclass(ResolvedBlockedActionObservation)

    assert {f.name for f in fields(ChatSafetyRoutingDecision)} == {
        "source_node_id",
        "response_level",
        "safety_disposition",
        "next_node_id",
        "continue_general_rag",
        "terminal_plan",
    }

    assert {f.name for f in fields(ChatSafetyTriageTerminalPlan)} == {
        "expected_execution_status",
        "expected_release_decision",
        "requires_approved_response",
    }

    assert {f.name for f in fields(BlockedActionTerminalPlan)} == {
        "response_level",
        "safety_disposition",
        "expected_execution_status",
        "expected_release_decision",
        "requires_approved_response",
    }

    assert {f.name for f in fields(ResolvedBlockedActionObservation)} == {
        "response_level",
        "safety_disposition",
    }


def test_plans_and_decisions_are_frozen_and_slotted() -> None:
    plan = ChatSafetyTriageTerminalPlan(
        expected_execution_status=ExecutionStatus.SUCCEEDED,
        expected_release_decision=ReleaseDecision.PASS,
        requires_approved_response=True,
    )
    with pytest.raises(FrozenInstanceError):
        plan.expected_execution_status = ExecutionStatus.NO_RESULT  # type: ignore[misc]

    decision = route_safety_triage(ResponseLevel.ROUTINE)
    with pytest.raises(FrozenInstanceError):
        decision.continue_general_rag = False  # type: ignore[misc]

    blocked = plan_blocked_action(ResolvedBlockedActionObservation(response_level=ResponseLevel.ROUTINE))
    with pytest.raises(FrozenInstanceError):
        blocked.requires_approved_response = False  # type: ignore[misc]

    obs = ResolvedBlockedActionObservation(response_level=ResponseLevel.ROUTINE)
    with pytest.raises(FrozenInstanceError):
        obs.response_level = ResponseLevel.URGENT  # type: ignore[misc]

    assert not hasattr(plan, "__dict__")
    assert not hasattr(decision, "__dict__")
    assert not hasattr(blocked, "__dict__")
    assert not hasattr(obs, "__dict__")


def test_no_public_raw_text_input_accepted() -> None:
    with pytest.raises(TypeError):
        route_safety_triage("ROUTINE")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        route_safety_triage("EMERGENCY")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        route_safety_triage("arbitrary patient question")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        route_safety_triage(None)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        route_safety_triage(123)  # type: ignore[arg-type]


def test_arbitrary_graph_node_strings_are_rejected() -> None:
    with pytest.raises(TypeError):
        ChatSafetyRoutingDecision(
            source_node_id="safety_triage",  # type: ignore[arg-type]
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=None,
        )

    with pytest.raises(TypeError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id="classify_question",  # type: ignore[arg-type]
            continue_general_rag=True,
            terminal_plan=None,
        )

    with pytest.raises(ValueError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=None,
        )


def test_unknown_with_continue_general_rag_true_is_rejected() -> None:
    with pytest.raises(ValueError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.UNKNOWN,
            safety_disposition=SafetyDisposition.UNKNOWN_RISK,
            next_node_id=ChatSafetyRoutingNodeId.APPROVED_UNKNOWN_RISK_FALLBACK,
            continue_general_rag=True,
            terminal_plan=ChatSafetyTriageTerminalPlan(
                expected_execution_status=ExecutionStatus.NO_RESULT,
                expected_release_decision=ReleaseDecision.REJECTED,
                requires_approved_response=True,
            ),
        )


def test_routine_with_terminal_plan_is_rejected() -> None:
    with pytest.raises(ValueError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=ChatSafetyTriageTerminalPlan(
                expected_execution_status=ExecutionStatus.SUCCEEDED,
                expected_release_decision=ReleaseDecision.PASS,
                requires_approved_response=True,
            ),
        )


def test_urgent_without_approved_guidance_node_is_rejected() -> None:
    with pytest.raises(ValueError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.URGENT,
            safety_disposition=SafetyDisposition.URGENT_ROUTED,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=False,
            terminal_plan=ChatSafetyTriageTerminalPlan(
                expected_execution_status=ExecutionStatus.SUCCEEDED,
                expected_release_decision=ReleaseDecision.PASS,
                requires_approved_response=True,
            ),
        )


def test_requires_approved_response_false_cannot_construct_terminal_plan() -> None:
    with pytest.raises(ValueError):
        ChatSafetyTriageTerminalPlan(
            expected_execution_status=ExecutionStatus.SUCCEEDED,
            expected_release_decision=ReleaseDecision.PASS,
            requires_approved_response=False,
        )


def test_contradictory_terminal_axis_combinations_are_rejected() -> None:
    with pytest.raises(ValueError):
        ChatSafetyTriageTerminalPlan(
            expected_execution_status=ExecutionStatus.SUCCEEDED,
            expected_release_decision=ReleaseDecision.REJECTED,
        )

    with pytest.raises(ValueError):
        ChatSafetyTriageTerminalPlan(
            expected_execution_status=ExecutionStatus.NO_RESULT,
            expected_release_decision=ReleaseDecision.PASS,
        )

    with pytest.raises(ValueError):
        ChatSafetyTriageTerminalPlan(
            expected_execution_status=ExecutionStatus.SUCCEEDED,
            expected_release_decision=ReleaseDecision.LIMITED,
        )

    with pytest.raises(ValueError):
        ChatSafetyTriageTerminalPlan(
            expected_execution_status=ExecutionStatus.TIMED_OUT,
            expected_release_decision=ReleaseDecision.REJECTED,
        )


def test_continue_general_rag_rejects_int_one() -> None:
    with pytest.raises(TypeError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=1,  # type: ignore[arg-type]
            terminal_plan=None,
        )


def test_continue_general_rag_rejects_int_zero() -> None:
    with pytest.raises(TypeError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.URGENT,
            safety_disposition=SafetyDisposition.URGENT_ROUTED,
            next_node_id=ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE,
            continue_general_rag=0,  # type: ignore[arg-type]
            terminal_plan=ChatSafetyTriageTerminalPlan(
                expected_execution_status=ExecutionStatus.SUCCEEDED,
                expected_release_decision=ReleaseDecision.PASS,
                requires_approved_response=True,
            ),
        )


def test_continue_general_rag_rejects_string() -> None:
    with pytest.raises(TypeError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag="true",  # type: ignore[arg-type]
            terminal_plan=None,
        )


def test_terminal_plan_rejects_empty_dict() -> None:
    with pytest.raises(TypeError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan={},  # type: ignore[arg-type]
        )


def test_terminal_plan_rejects_empty_list() -> None:
    with pytest.raises(TypeError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=[],  # type: ignore[arg-type]
        )


def test_terminal_plan_with_custom_hash_eq_rejected_before_either_executes() -> None:
    class ExplodingHashProxy:
        def __hash__(self) -> int:
            raise AssertionError("__hash__ must not be called")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("__eq__ must not be called")

    proxy = ExplodingHashProxy()
    with pytest.raises(TypeError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=proxy,  # type: ignore[arg-type]
        )


def test_terminal_plan_subclass_is_rejected() -> None:
    class SubTerminalPlan(ChatSafetyTriageTerminalPlan):
        pass

    sub_plan = SubTerminalPlan(
        expected_execution_status=ExecutionStatus.SUCCEEDED,
        expected_release_decision=ReleaseDecision.PASS,
        requires_approved_response=True,
    )
    with pytest.raises(TypeError):
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.URGENT,
            safety_disposition=SafetyDisposition.URGENT_ROUTED,
            next_node_id=ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE,
            continue_general_rag=False,
            terminal_plan=sub_plan,
        )


def test_error_messages_do_not_contain_supplied_sensitive_sentinel() -> None:
    sentinel = "PATIENT_SSN_999_88_7777_SECRET"

    with pytest.raises(TypeError) as exc_info:
        route_safety_triage(sentinel)  # type: ignore[arg-type]
    assert sentinel not in str(exc_info.value)

    with pytest.raises(TypeError) as exc_info:
        ChatSafetyRoutingDecision(
            source_node_id=sentinel,  # type: ignore[arg-type]
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=None,
        )
    assert sentinel not in str(exc_info.value)

    with pytest.raises(TypeError) as exc_info:
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=sentinel,  # type: ignore[arg-type]
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=None,
        )
    assert sentinel not in str(exc_info.value)

    with pytest.raises(TypeError) as exc_info:
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=sentinel,  # type: ignore[arg-type]
        )
    assert sentinel not in str(exc_info.value)


def test_error_messages_do_not_contain_dynamically_named_type_sentinel() -> None:
    sentinel = "PATIENT_SSN_999_88_7777_SECRET"
    malicious = type(sentinel, (), {})()

    with pytest.raises(TypeError) as exc_info:
        route_safety_triage(malicious)  # type: ignore[arg-type]
    assert sentinel not in str(exc_info.value)
    assert str(exc_info.value) == "Expected ResponseLevel for response_level"

    with pytest.raises(TypeError) as exc_info:
        ChatSafetyRoutingDecision(
            source_node_id=malicious,  # type: ignore[arg-type]
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=None,
        )
    assert sentinel not in str(exc_info.value)
    assert str(exc_info.value) == "Expected ChatSafetyRoutingNodeId for source_node_id"

    with pytest.raises(TypeError) as exc_info:
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=malicious,  # type: ignore[arg-type]
            continue_general_rag=True,
            terminal_plan=None,
        )
    assert sentinel not in str(exc_info.value)
    assert str(exc_info.value) == "Expected ChatSafetyRoutingNodeId for next_node_id"

    with pytest.raises(TypeError) as exc_info:
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=malicious,  # type: ignore[arg-type]
            terminal_plan=None,
        )
    assert sentinel not in str(exc_info.value)
    assert str(exc_info.value) == "Expected bool for continue_general_rag"

    with pytest.raises(TypeError) as exc_info:
        ChatSafetyRoutingDecision(
            source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
            response_level=ResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
            continue_general_rag=True,
            terminal_plan=malicious,  # type: ignore[arg-type]
        )
    assert sentinel not in str(exc_info.value)
    assert str(exc_info.value) == "Expected ChatSafetyTriageTerminalPlan or None for terminal_plan"

    with pytest.raises(TypeError) as exc_info:
        ChatSafetyTriageTerminalPlan(
            expected_execution_status=malicious,  # type: ignore[arg-type]
            expected_release_decision=ReleaseDecision.PASS,
            requires_approved_response=True,
        )
    assert sentinel not in str(exc_info.value)
    assert str(exc_info.value) == "Expected ExecutionStatus for expected_execution_status"

    with pytest.raises(TypeError) as exc_info:
        ChatSafetyTriageTerminalPlan(
            expected_execution_status=ExecutionStatus.SUCCEEDED,
            expected_release_decision=malicious,  # type: ignore[arg-type]
            requires_approved_response=True,
        )
    assert sentinel not in str(exc_info.value)
    assert str(exc_info.value) == "Expected ReleaseDecision for expected_release_decision"

    with pytest.raises(TypeError) as exc_info:
        ResolvedBlockedActionObservation(response_level=malicious)  # type: ignore[arg-type]
    assert sentinel not in str(exc_info.value)
    assert str(exc_info.value) == "Expected ResponseLevel for response_level"

    with pytest.raises(TypeError) as exc_info:
        plan_blocked_action(malicious)  # type: ignore[arg-type]
    assert sentinel not in str(exc_info.value)
    assert str(exc_info.value) == "Expected ResolvedBlockedActionObservation for observation"


def test_production_module_imports_are_strictly_standard_library() -> None:
    assert _MODULE_PATH.exists(), f"Production file {_MODULE_PATH} must exist"
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"), filename=str(_MODULE_PATH))

    allowed_stdlib_modules = {"__future__", "dataclasses", "enum"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                assert root_pkg in allowed_stdlib_modules, f"Disallowed import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "Relative imports are not permitted in this isolated slice"
            assert node.module is not None
            root_pkg = node.module.split(".")[0]
            assert root_pkg in allowed_stdlib_modules, f"Disallowed from-import: {node.module}"
