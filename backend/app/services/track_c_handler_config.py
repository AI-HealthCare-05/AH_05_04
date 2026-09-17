"""Validate versioned Track C support rules and historical Plan snapshots.

No rule is active merely because its file exists. Callers must provide the
approved version and copy/rationale references before loading it.
"""

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.track_c import (
    BarrierCode,
    BarrierResponseStatus,
    SupportActionPlan,
    SupportActionPlanStatus,
    SupportCode,
)
from app.repositories.track_c_storage_repository import TrackCStorageRepository
from app.services.track_c_personalization import SUBREASONS, question_texts, validate_questions

SCHEMA_VERSION = "track-c-handler-config-v1"
COPY_SCHEMA_VERSION = "track-c-support-copy-v1"
ACTIVE_RULE_VERSION = "track-c-support-rule-2026-09-17.1"
ACTIVE_COPY_VERSION = "track-c-support-copy-ko-2026-09-17.1"
APPROVED_RULE_VERSIONS = frozenset(
    {"track-c-support-rule-2026-09-15.1", "track-c-support-rule-2026-09-16.1", ACTIVE_RULE_VERSION}
)
APPROVED_COPY_VERSIONS = frozenset(
    {"track-c-support-copy-ko-2026-09-15.1", "track-c-support-copy-ko-2026-09-16.1", ACTIVE_COPY_VERSION}
)
APPROVED_RATIONALE_CODES = frozenset(
    {
        "ROUTINE_REMINDER_SETUP_AVAILABLE",
        "ROUTINE_OR_TRAVEL_GUIDANCE_AVAILABLE",
        "ROUTINE_INSTRUCTION_REVIEW_AVAILABLE",
        "ROUTINE_PURPOSE_REVIEW_AVAILABLE",
        "ROUTINE_MEDICATION_CONCERN_GUIDANCE_AVAILABLE",
        "ROUTINE_ACCESS_SUPPORT_AVAILABLE",
    }
)
_CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config" / "track_c"
_RULES_DIR = _CONFIG_ROOT / "support-rules"
_COPY_DIR = _CONFIG_ROOT / "support-copy"
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_EXPECTED: dict[SupportCode, tuple[tuple[BarrierCode, ...], int]] = {
    SupportCode.REMINDER_SETUP: ((BarrierCode.FORGOT, BarrierCode.SCHEDULE_OR_TRAVEL), 10),
    SupportCode.ROUTINE_OR_TRAVEL_PLAN: ((BarrierCode.SCHEDULE_OR_TRAVEL, BarrierCode.FORGOT), 20),
    SupportCode.INSTRUCTION_REVIEW: ((BarrierCode.INSTRUCTIONS_UNCLEAR,), 10),
    SupportCode.PURPOSE_REVIEW: ((BarrierCode.NEED_DOUBT,), 10),
    SupportCode.MEDICATION_CONCERN_GUIDANCE: ((BarrierCode.MEDICATION_CONCERN,), 10),
    SupportCode.ACCESS_SUPPORT: ((BarrierCode.ACCESS_OR_COST,), 10),
}


class HandlerConfigError(ValueError):
    """Invalid or unapproved rules or historical snapshot; never include input data."""


def _base_snapshot_parameters(parameters: dict[str, Any], support_code: SupportCode) -> dict[str, Any]:
    actual = dict(parameters)
    subreason_code = actual.pop("subreason_code", None)
    selected_question_ids = actual.pop("selected_question_ids", [])
    selected_questions = actual.pop("selected_questions", [])
    if subreason_code is not None and not isinstance(subreason_code, str):
        raise HandlerConfigError("invalid historical subreason")
    if not isinstance(selected_question_ids, list) or not all(
        isinstance(question_id, str) for question_id in selected_question_ids
    ):
        raise HandlerConfigError("invalid historical question selection")
    if not isinstance(selected_questions, list) or any(
        not isinstance(item, dict)
        or set(item) != {"question_id", "text"}
        or not isinstance(item["question_id"], str)
        or not isinstance(item["text"], str)
        for item in selected_questions
    ):
        raise HandlerConfigError("invalid historical question snapshot")
    if [item["question_id"] for item in selected_questions] != selected_question_ids:
        raise HandlerConfigError("historical question snapshot mismatch")
    try:
        if subreason_code is not None and not any(subreason_code in values for values in SUBREASONS.values()):
            raise ValueError("unknown subreason")
        validate_questions(support_code, subreason_code, selected_question_ids)
    except ValueError as exc:
        raise HandlerConfigError("invalid historical personalization") from exc
    return actual


@dataclass(frozen=True)
class SupportRule:
    support_code: SupportCode
    barrier_codes: tuple[BarrierCode, ...]
    priority: int
    copy_version: str
    rationale_code: str
    parameters: Mapping[str, str]


@dataclass(frozen=True)
class HandlerConfig:
    rule_version: str
    supports: Mapping[SupportCode, SupportRule]

    def snapshot(self, support_code: SupportCode, *, medication_id: UUID | None = None) -> dict[str, Any]:
        rule = self.supports.get(support_code)
        if rule is None:
            raise HandlerConfigError("support unavailable in approved rule")
        parameters: dict[str, str] = dict(rule.parameters)
        if support_code == SupportCode.REMINDER_SETUP:
            if medication_id is None:
                raise HandlerConfigError("server medication reference required")
            parameters["prescription_version_medication_id"] = str(medication_id)
        elif medication_id is not None:
            raise HandlerConfigError("unexpected medication reference")
        return {
            "schema_version": SCHEMA_VERSION,
            "rationale_code": rule.rationale_code,
            "parameters": parameters,
        }

    def restore(self, plan: SupportActionPlan, *, medication_id: UUID | None = None) -> dict[str, Any]:
        if plan.rule_version != self.rule_version or plan.support_code not in self.supports:
            raise HandlerConfigError("historical rule unavailable")
        rule = self.supports[plan.support_code]
        if plan.copy_version != rule.copy_version:
            raise HandlerConfigError("historical copy reference mismatch")
        snapshot = plan.action_config_snapshot
        if not isinstance(snapshot, dict) or set(snapshot) != {"schema_version", "rationale_code", "parameters"}:
            raise HandlerConfigError("invalid historical snapshot")
        if snapshot["schema_version"] != SCHEMA_VERSION or snapshot["rationale_code"] != rule.rationale_code:
            raise HandlerConfigError("invalid historical snapshot version or rationale")
        parameters = snapshot["parameters"]
        if not isinstance(parameters, dict):
            raise HandlerConfigError("invalid historical parameters")
        expected = dict(rule.parameters)
        if plan.support_code == SupportCode.REMINDER_SETUP:
            if medication_id is None:
                raise HandlerConfigError("server medication reference required")
            expected["prescription_version_medication_id"] = str(medication_id)
        elif medication_id is not None:
            raise HandlerConfigError("unexpected medication reference")
        actual = _base_snapshot_parameters(parameters, plan.support_code)
        if actual != expected:
            raise HandlerConfigError("historical parameters do not match approved rule or parent")
        return deepcopy(snapshot)


@dataclass(frozen=True)
class SupportCopy:
    support_code: SupportCode
    title: str
    body: str
    confirmation_prompt: str
    primary_label: str
    secondary_label: str


@dataclass(frozen=True)
class SupportCopyCatalog:
    copy_version: str
    locale: str
    supports: Mapping[SupportCode, SupportCopy]


def _exact_object(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise HandlerConfigError("invalid rule fields")
    return value


def _approved_string(value: Any, approved: frozenset[str]) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 100 or value not in approved:
        raise HandlerConfigError("unapproved or invalid reference")
    return value


def _display_string(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= maximum:
        raise HandlerConfigError("invalid support copy")
    return value


def _parse_support(
    value: Any, *, approved_copy_versions: frozenset[str], approved_rationale_codes: frozenset[str]
) -> SupportRule:
    item = _exact_object(
        value,
        {"support_code", "barrier_codes", "priority", "copy_version", "rationale_code", "parameters"},
    )
    try:
        code = SupportCode(item["support_code"])
    except (ValueError, TypeError) as exc:
        raise HandlerConfigError("unknown support code") from exc
    barriers, priority = _EXPECTED[code]
    raw_barriers = item["barrier_codes"]
    if not isinstance(raw_barriers, list) or not all(isinstance(value, str) for value in raw_barriers):
        raise HandlerConfigError("invalid barrier mapping")
    if len(raw_barriers) != len(barriers) or set(raw_barriers) != set(barriers):
        raise HandlerConfigError("invalid barrier mapping")
    if type(item["priority"]) is not int or item["priority"] != priority:
        raise HandlerConfigError("invalid priority")
    copy_version = _approved_string(item["copy_version"], approved_copy_versions)
    rationale_code = _approved_string(item["rationale_code"], approved_rationale_codes)
    params = item["parameters"]
    if code == SupportCode.REMINDER_SETUP:
        params = _exact_object(params, {"destination"})
        if params["destination"] != "MEDICATION_SCHEDULE_SETUP":
            raise HandlerConfigError("invalid reminder destination")
    else:
        params = _exact_object(params, {"content_key"})
        if params["content_key"] != code.value:
            raise HandlerConfigError("invalid content reference")
    return SupportRule(code, barriers, priority, copy_version, rationale_code, MappingProxyType(dict(params)))


def parse_handler_config(
    data: Any,
    *,
    approved_rule_versions: frozenset[str],
    approved_copy_versions: frozenset[str],
    approved_rationale_codes: frozenset[str],
) -> HandlerConfig:
    root = _exact_object(data, {"schema_version", "rule_version", "supports"})
    if root["schema_version"] != SCHEMA_VERSION:
        raise HandlerConfigError("unsupported schema version")
    rule_version = _approved_string(root["rule_version"], approved_rule_versions)
    if not _VERSION.fullmatch(rule_version):
        raise HandlerConfigError("invalid rule version")
    entries = root["supports"]
    if not isinstance(entries, list) or len(entries) != len(SupportCode):
        raise HandlerConfigError("all support definitions required")
    supports: dict[SupportCode, SupportRule] = {}
    for item in entries:
        rule = _parse_support(
            item,
            approved_copy_versions=approved_copy_versions,
            approved_rationale_codes=approved_rationale_codes,
        )
        if rule.support_code in supports:
            raise HandlerConfigError("duplicate support code")
        supports[rule.support_code] = rule
    return HandlerConfig(rule_version, MappingProxyType(supports))


def parse_support_copy_catalog(
    data: Any,
    *,
    approved_copy_versions: frozenset[str],
) -> SupportCopyCatalog:
    root = _exact_object(data, {"schema_version", "copy_version", "locale", "supports"})
    if root["schema_version"] != COPY_SCHEMA_VERSION or root["locale"] != "ko-KR":
        raise HandlerConfigError("unsupported copy schema or locale")
    copy_version = _approved_string(root["copy_version"], approved_copy_versions)
    entries = root["supports"]
    if not isinstance(entries, list) or len(entries) != len(SupportCode):
        raise HandlerConfigError("all support copy definitions required")
    supports: dict[SupportCode, SupportCopy] = {}
    for value in entries:
        item = _exact_object(value, {"support_code", "title", "body", "confirmation"})
        try:
            support_code = SupportCode(item["support_code"])
        except (ValueError, TypeError) as exc:
            raise HandlerConfigError("unknown support copy code") from exc
        if support_code in supports:
            raise HandlerConfigError("duplicate support copy code")
        confirmation = _exact_object(
            item["confirmation"],
            {"prompt", "primary_label", "secondary_label"},
        )
        supports[support_code] = SupportCopy(
            support_code=support_code,
            title=_display_string(item["title"], maximum=100),
            body=_display_string(item["body"], maximum=500),
            confirmation_prompt=_display_string(confirmation["prompt"], maximum=200),
            primary_label=_display_string(confirmation["primary_label"], maximum=50),
            secondary_label=_display_string(confirmation["secondary_label"], maximum=50),
        )
    return SupportCopyCatalog(copy_version, root["locale"], MappingProxyType(supports))


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HandlerConfigError("duplicate JSON field")
        result[key] = value
    return result


def load_handler_config(
    rules_dir: Path,
    rule_version: str,
    *,
    approved_rule_versions: frozenset[str],
    approved_copy_versions: frozenset[str],
    approved_rationale_codes: frozenset[str],
) -> HandlerConfig:
    if rule_version not in approved_rule_versions or not _VERSION.fullmatch(rule_version):
        raise HandlerConfigError("unapproved rule file")
    path = rules_dir / f"{rule_version}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except HandlerConfigError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise HandlerConfigError("rule file unreadable or invalid") from exc
    config = parse_handler_config(
        data,
        approved_rule_versions=approved_rule_versions,
        approved_copy_versions=approved_copy_versions,
        approved_rationale_codes=approved_rationale_codes,
    )
    if config.rule_version != rule_version:
        raise HandlerConfigError("rule file version mismatch")
    return config


def load_support_copy_catalog(
    copy_dir: Path,
    copy_version: str,
    *,
    approved_copy_versions: frozenset[str],
) -> SupportCopyCatalog:
    if copy_version not in approved_copy_versions or not _VERSION.fullmatch(copy_version):
        raise HandlerConfigError("unapproved copy file")
    path = copy_dir / f"{copy_version}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except HandlerConfigError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise HandlerConfigError("copy file unreadable or invalid") from exc
    catalog = parse_support_copy_catalog(data, approved_copy_versions=approved_copy_versions)
    if catalog.copy_version != copy_version:
        raise HandlerConfigError("copy file version mismatch")
    return catalog


def load_active_support_assets() -> tuple[HandlerConfig, SupportCopyCatalog]:
    """Return active assets only after both files and their references validate."""
    config = load_handler_config(
        _RULES_DIR,
        ACTIVE_RULE_VERSION,
        approved_rule_versions=APPROVED_RULE_VERSIONS,
        approved_copy_versions=APPROVED_COPY_VERSIONS,
        approved_rationale_codes=APPROVED_RATIONALE_CODES,
    )
    catalog = load_support_copy_catalog(
        _COPY_DIR,
        ACTIVE_COPY_VERSION,
        approved_copy_versions=APPROVED_COPY_VERSIONS,
    )
    if {rule.copy_version for rule in config.supports.values()} != {catalog.copy_version}:
        raise HandlerConfigError("active rule and copy versions do not match")
    return config, catalog


def load_historical_plan_copy(plan: SupportActionPlan) -> SupportCopy:
    config = load_handler_config(
        _RULES_DIR,
        plan.rule_version,
        approved_rule_versions=APPROVED_RULE_VERSIONS,
        approved_copy_versions=APPROVED_COPY_VERSIONS,
        approved_rationale_codes=APPROVED_RATIONALE_CODES,
    )
    rule = config.supports.get(plan.support_code)
    if rule is None or rule.copy_version != plan.copy_version:
        raise HandlerConfigError("historical plan copy reference mismatch")
    return load_support_copy_catalog(
        _COPY_DIR,
        plan.copy_version,
        approved_copy_versions=APPROVED_COPY_VERSIONS,
    ).supports[plan.support_code]


def load_active_handler_config() -> HandlerConfig:
    config, _ = load_active_support_assets()
    return config


def load_active_support_copy_catalog() -> SupportCopyCatalog:
    _, catalog = load_active_support_assets()
    return catalog


async def save_action_plan_snapshot(
    session: AsyncSession,
    *,
    user_id: UUID,
    barrier_id: UUID,
    support_code: SupportCode,
    config: HandlerConfig,
    subreason_code: str | None = None,
    selected_question_ids: tuple[str, ...] = (),
) -> SupportActionPlan:
    """Stage a validated Plan in the caller's transaction.

    #194 must check current Safety/Check-in, active Plan and idempotency before
    calling this internal storage operation; it is not a public creation API.
    """
    repository = TrackCStorageRepository(session)
    parent = await repository.get_barrier_medication_owned(barrier_id=barrier_id, user_id=user_id)
    if parent is None:
        raise HandlerConfigError("barrier unavailable")
    barrier, medication_id = parent
    rule = config.supports.get(support_code)
    if (
        rule is None
        or barrier.response_status != BarrierResponseStatus.ANSWERED
        or barrier.barrier_code not in rule.barrier_codes
    ):
        raise HandlerConfigError("support does not match barrier")
    snapshot = config.snapshot(
        support_code,
        medication_id=medication_id if support_code == SupportCode.REMINDER_SETUP else None,
    )
    snapshot["parameters"]["subreason_code"] = subreason_code
    snapshot["parameters"]["selected_question_ids"] = list(selected_question_ids)
    snapshot["parameters"]["selected_questions"] = [
        {"question_id": question_id, "text": text} for question_id, text in question_texts(selected_question_ids)
    ]
    plan = SupportActionPlan(
        barrier_response_id=barrier_id,
        support_code=support_code,
        rule_version=config.rule_version,
        copy_version=rule.copy_version,
        action_config_snapshot=snapshot,
        status=SupportActionPlanStatus.ACTIVE,
    )
    session.add(plan)
    await session.flush()
    return plan


async def restore_action_plan_snapshot(
    session: AsyncSession,
    *,
    user_id: UUID,
    plan_id: UUID,
    historical_config: HandlerConfig,
) -> dict[str, Any]:
    """Read only the owned, stored historical version; never use active rules."""
    repository = TrackCStorageRepository(session)
    plan = await repository.get_action_plan_owned(plan_id=plan_id, user_id=user_id)
    if plan is None:
        raise HandlerConfigError("plan unavailable")
    parent = await repository.get_barrier_medication_owned(barrier_id=plan.barrier_response_id, user_id=user_id)
    if parent is None:
        raise HandlerConfigError("plan parent unavailable")
    medication_id = parent[1] if plan.support_code == SupportCode.REMINDER_SETUP else None
    return historical_config.restore(plan, medication_id=medication_id)
