"""Deterministic, synchronous, persistence-free Chat Safety routing.

This module implements the pure deterministic mapping from a Safety Triage
ResponseLevel to a ChatSafetyRoutingDecision (a routing plan) and its next
isolated Graph node and downstream ChatSafetyTriageTerminalPlan.

Important lifecycle boundaries:
- route_safety_triage returns a routing plan.
- It does not generate guidance or fallback content.
- It does not execute the Finalizer or release gate.
- It does not persist or publish a result.

It also plans downstream handling for blocked actions via plan_blocked_action
using a typed, content-free ResolvedBlockedActionObservation representing an
upstream approved policy decision. This module does not implement prohibited-action
detection or policy classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChatSafetyRoutingNodeId(StrEnum):
    SAFETY_TRIAGE = "safety_triage"
    CLASSIFY_QUESTION = "classify_question"
    APPROVED_URGENT_OR_EMERGENCY_GUIDANCE = "approved_urgent_or_emergency_guidance"
    APPROVED_UNKNOWN_RISK_FALLBACK = "approved_unknown_risk_fallback"


NODE_SAFETY_TRIAGE = ChatSafetyRoutingNodeId.SAFETY_TRIAGE
NODE_CLASSIFY_QUESTION = ChatSafetyRoutingNodeId.CLASSIFY_QUESTION
NODE_APPROVED_URGENT_OR_EMERGENCY_GUIDANCE = ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE
NODE_APPROVED_UNKNOWN_RISK_FALLBACK = ChatSafetyRoutingNodeId.APPROVED_UNKNOWN_RISK_FALLBACK


class ResponseLevel(StrEnum):
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"
    UNKNOWN = "UNKNOWN"


class SafetyDisposition(StrEnum):
    NORMAL = "NORMAL"
    URGENT_ROUTED = "URGENT_ROUTED"
    EMERGENCY_ROUTED = "EMERGENCY_ROUTED"
    UNKNOWN_RISK = "UNKNOWN_RISK"
    BLOCKED_ACTION = "BLOCKED_ACTION"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    NO_RESULT = "NO_RESULT"
    TIMED_OUT = "TIMED_OUT"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class ReleaseDecision(StrEnum):
    PASS = "PASS"
    LIMITED = "LIMITED"
    REJECTED = "REJECTED"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class ChatSafetyTriageTerminalPlan:
    expected_execution_status: ExecutionStatus
    expected_release_decision: ReleaseDecision
    requires_approved_response: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.expected_execution_status, ExecutionStatus):
            raise TypeError("Expected ExecutionStatus for expected_execution_status")
        if not isinstance(self.expected_release_decision, ReleaseDecision):
            raise TypeError("Expected ReleaseDecision for expected_release_decision")
        if type(self.requires_approved_response) is not bool or not self.requires_approved_response:
            raise ValueError("requires_approved_response must be True")
        if (
            self.expected_execution_status,
            self.expected_release_decision,
        ) not in {
            (ExecutionStatus.SUCCEEDED, ReleaseDecision.PASS),
            (ExecutionStatus.NO_RESULT, ReleaseDecision.REJECTED),
        }:
            raise ValueError("Contradictory terminal axis combination")


_PLAN_SUCCEEDED_PASS = ChatSafetyTriageTerminalPlan(
    expected_execution_status=ExecutionStatus.SUCCEEDED,
    expected_release_decision=ReleaseDecision.PASS,
    requires_approved_response=True,
)
_PLAN_NO_RESULT_REJECTED = ChatSafetyTriageTerminalPlan(
    expected_execution_status=ExecutionStatus.NO_RESULT,
    expected_release_decision=ReleaseDecision.REJECTED,
    requires_approved_response=True,
)

_VALID_ROUTING_COMBINATIONS = {
    (
        ResponseLevel.ROUTINE,
        SafetyDisposition.NORMAL,
        ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
        True,
        None,
    ),
    (
        ResponseLevel.URGENT,
        SafetyDisposition.URGENT_ROUTED,
        ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE,
        False,
        _PLAN_SUCCEEDED_PASS,
    ),
    (
        ResponseLevel.EMERGENCY,
        SafetyDisposition.EMERGENCY_ROUTED,
        ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE,
        False,
        _PLAN_SUCCEEDED_PASS,
    ),
    (
        ResponseLevel.UNKNOWN,
        SafetyDisposition.UNKNOWN_RISK,
        ChatSafetyRoutingNodeId.APPROVED_UNKNOWN_RISK_FALLBACK,
        False,
        _PLAN_NO_RESULT_REJECTED,
    ),
}


@dataclass(frozen=True, slots=True)
class ChatSafetyRoutingDecision:
    source_node_id: ChatSafetyRoutingNodeId
    response_level: ResponseLevel
    safety_disposition: SafetyDisposition
    next_node_id: ChatSafetyRoutingNodeId
    continue_general_rag: bool
    terminal_plan: ChatSafetyTriageTerminalPlan | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_node_id, ChatSafetyRoutingNodeId):
            raise TypeError("Expected ChatSafetyRoutingNodeId for source_node_id")
        if self.source_node_id is not ChatSafetyRoutingNodeId.SAFETY_TRIAGE:
            raise ValueError("source_node_id must be ChatSafetyRoutingNodeId.SAFETY_TRIAGE")
        if not isinstance(self.next_node_id, ChatSafetyRoutingNodeId):
            raise TypeError("Expected ChatSafetyRoutingNodeId for next_node_id")
        if not isinstance(self.response_level, ResponseLevel):
            raise TypeError("Expected ResponseLevel for response_level")
        if not isinstance(self.safety_disposition, SafetyDisposition):
            raise TypeError("Expected SafetyDisposition for safety_disposition")
        if type(self.continue_general_rag) is not bool:
            raise TypeError("Expected bool for continue_general_rag")
        if self.terminal_plan is not None and type(self.terminal_plan) is not ChatSafetyTriageTerminalPlan:
            raise TypeError("Expected ChatSafetyTriageTerminalPlan or None for terminal_plan")

        key = (
            self.response_level,
            self.safety_disposition,
            self.next_node_id,
            self.continue_general_rag,
            self.terminal_plan,
        )
        if key not in _VALID_ROUTING_COMBINATIONS:
            raise ValueError("Invalid routing decision combination")


@dataclass(frozen=True, slots=True)
class ResolvedBlockedActionObservation:
    response_level: ResponseLevel
    safety_disposition: SafetyDisposition = SafetyDisposition.BLOCKED_ACTION

    def __post_init__(self) -> None:
        if not isinstance(self.response_level, ResponseLevel):
            raise TypeError("Expected ResponseLevel for response_level")
        if self.safety_disposition is not SafetyDisposition.BLOCKED_ACTION:
            raise ValueError("safety_disposition must be SafetyDisposition.BLOCKED_ACTION")


@dataclass(frozen=True, slots=True)
class BlockedActionTerminalPlan:
    response_level: ResponseLevel
    safety_disposition: SafetyDisposition = SafetyDisposition.BLOCKED_ACTION
    expected_execution_status: ExecutionStatus = ExecutionStatus.SUCCEEDED
    expected_release_decision: ReleaseDecision = ReleaseDecision.LIMITED
    requires_approved_response: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.response_level, ResponseLevel):
            raise TypeError("Expected ResponseLevel for response_level")
        if self.safety_disposition is not SafetyDisposition.BLOCKED_ACTION:
            raise ValueError("safety_disposition must be SafetyDisposition.BLOCKED_ACTION")
        if self.expected_execution_status is not ExecutionStatus.SUCCEEDED:
            raise ValueError("expected_execution_status must be ExecutionStatus.SUCCEEDED")
        if self.expected_release_decision is not ReleaseDecision.LIMITED:
            raise ValueError("expected_release_decision must be ReleaseDecision.LIMITED")
        if type(self.requires_approved_response) is not bool or not self.requires_approved_response:
            raise ValueError("requires_approved_response must be True")


def route_safety_triage(
    response_level: ResponseLevel,
) -> ChatSafetyRoutingDecision:
    if not isinstance(response_level, ResponseLevel):
        raise TypeError("Expected ResponseLevel for response_level")

    match response_level:
        case ResponseLevel.ROUTINE:
            return ChatSafetyRoutingDecision(
                source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
                response_level=ResponseLevel.ROUTINE,
                safety_disposition=SafetyDisposition.NORMAL,
                next_node_id=ChatSafetyRoutingNodeId.CLASSIFY_QUESTION,
                continue_general_rag=True,
                terminal_plan=None,
            )
        case ResponseLevel.URGENT:
            return ChatSafetyRoutingDecision(
                source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
                response_level=ResponseLevel.URGENT,
                safety_disposition=SafetyDisposition.URGENT_ROUTED,
                next_node_id=ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE,
                continue_general_rag=False,
                terminal_plan=_PLAN_SUCCEEDED_PASS,
            )
        case ResponseLevel.EMERGENCY:
            return ChatSafetyRoutingDecision(
                source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
                response_level=ResponseLevel.EMERGENCY,
                safety_disposition=SafetyDisposition.EMERGENCY_ROUTED,
                next_node_id=ChatSafetyRoutingNodeId.APPROVED_URGENT_OR_EMERGENCY_GUIDANCE,
                continue_general_rag=False,
                terminal_plan=_PLAN_SUCCEEDED_PASS,
            )
        case ResponseLevel.UNKNOWN:
            return ChatSafetyRoutingDecision(
                source_node_id=ChatSafetyRoutingNodeId.SAFETY_TRIAGE,
                response_level=ResponseLevel.UNKNOWN,
                safety_disposition=SafetyDisposition.UNKNOWN_RISK,
                next_node_id=ChatSafetyRoutingNodeId.APPROVED_UNKNOWN_RISK_FALLBACK,
                continue_general_rag=False,
                terminal_plan=_PLAN_NO_RESULT_REJECTED,
            )


def plan_blocked_action(
    observation: ResolvedBlockedActionObservation,
) -> BlockedActionTerminalPlan:
    if type(observation) is not ResolvedBlockedActionObservation:
        raise TypeError("Expected ResolvedBlockedActionObservation for observation")

    return BlockedActionTerminalPlan(
        response_level=observation.response_level,
        safety_disposition=SafetyDisposition.BLOCKED_ACTION,
        expected_execution_status=ExecutionStatus.SUCCEEDED,
        expected_release_decision=ReleaseDecision.LIMITED,
        requires_approved_response=True,
    )


__all__ = [
    "NODE_APPROVED_UNKNOWN_RISK_FALLBACK",
    "NODE_APPROVED_URGENT_OR_EMERGENCY_GUIDANCE",
    "NODE_CLASSIFY_QUESTION",
    "NODE_SAFETY_TRIAGE",
    "BlockedActionTerminalPlan",
    "ChatSafetyRoutingDecision",
    "ChatSafetyRoutingNodeId",
    "ChatSafetyTriageTerminalPlan",
    "ExecutionStatus",
    "ReleaseDecision",
    "ResolvedBlockedActionObservation",
    "ResponseLevel",
    "SafetyDisposition",
    "plan_blocked_action",
    "route_safety_triage",
]
