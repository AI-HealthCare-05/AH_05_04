"""내부 조사 포트가 제공한 증거로 검토 후보를 분류합니다. 삭제 권한을 발행하지 않습니다."""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol


class SurveyDecision(StrEnum):
    REVIEW_CANDIDATE = "REVIEW_CANDIDATE"
    PROTECTED = "PROTECTED"
    HOLD = "HOLD"


@dataclass(frozen=True)
class ObjectObservation:
    object_key: str = field(repr=False)
    checksum: str
    byte_size: int
    created_at: datetime | None = None
    source_owned: bool = False


@dataclass(frozen=True)
class Inventory:
    namespace: str = field(repr=False)
    objects: tuple[ObjectObservation, ...]
    complete: bool


@dataclass(frozen=True)
class ReferenceObservation:
    database_id: str = field(repr=False)
    direct_count: int | None
    # SQL count alone cannot prove external/downstream scope or environment binding.
    namespace: str | None = field(default=None, repr=False)
    scope_complete: bool = False
    downstream_count: int | None = None
    acquisition_idle: bool | None = None


@dataclass(frozen=True)
class SurveyScope:
    database_id: str = field(repr=False)
    namespace: str = field(repr=False)
    storage_backend: str
    policy_version: str


@dataclass(frozen=True)
class SurveyItem:
    object_ref: str
    decision: SurveyDecision
    reason: str


@dataclass(frozen=True)
class SurveyResult:
    items: tuple[SurveyItem, ...]
    complete: bool
    reason: str | None = None


class InventoryReader(Protocol):
    def read_inventory(self) -> Inventory: ...


class ReferenceReader(Protocol):
    async def inspect_references(self, *, storage_backend: str, object_key: str) -> ReferenceObservation: ...


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def classify(
    obj: ObjectObservation, refs: ReferenceObservation, scope: SurveyScope, now: datetime
) -> tuple[SurveyDecision, str]:
    if refs.database_id != scope.database_id:
        return SurveyDecision.HOLD, "DATABASE_MISMATCH"
    if type(refs.direct_count) is int and refs.direct_count > 0:
        return SurveyDecision.PROTECTED, "DIRECT_REFERENCE"
    if type(refs.downstream_count) is int and refs.downstream_count > 0:
        return SurveyDecision.PROTECTED, "DOWNSTREAM_REFERENCE"
    if refs.namespace != scope.namespace:
        return SurveyDecision.HOLD, "NAMESPACE_UNPROVEN"
    if not refs.scope_complete or any(
        type(count) is not int or count != 0 for count in (refs.direct_count, refs.downstream_count)
    ):
        return SurveyDecision.HOLD, "REFERENCE_SCOPE_INCOMPLETE"
    if refs.acquisition_idle is not True:
        return SurveyDecision.HOLD, "ACQUISITION_UNPROVEN"
    if not obj.source_owned:
        return SurveyDecision.HOLD, "OWNERSHIP_UNPROVEN"
    if obj.created_at is None or not _aware(obj.created_at) or obj.created_at > now:
        return SurveyDecision.HOLD, "CREATION_TIME_UNPROVEN"
    if now - obj.created_at <= timedelta(days=30):
        return SurveyDecision.HOLD, "GRACE_PERIOD"
    return SurveyDecision.REVIEW_CANDIDATE, "REQUIRES_BATCH_REVIEW"


async def survey_candidates(
    *, scope: SurveyScope, now: datetime, inventory: InventoryReader, references: ReferenceReader
) -> SurveyResult:
    if not _aware(now) or scope.policy_version != "source-artifact-retention-v1":
        return SurveyResult((), False, "POLICY_OR_TIME_INVALID")
    if not scope.database_id.strip() or not scope.namespace.strip() or scope.storage_backend != "LOCAL_PRIVATE":
        return SurveyResult((), False, "SCOPE_INVALID")
    try:
        listing = inventory.read_inventory()
    except Exception:
        return SurveyResult((), False, "INVENTORY_UNAVAILABLE")
    if listing.namespace != scope.namespace or not listing.complete:
        return SurveyResult((), False, "INVENTORY_INCOMPLETE")
    if any(
        not obj.object_key.strip()
        or re.fullmatch(r"[0-9a-f]{64}", obj.checksum) is None
        or type(obj.byte_size) is not int
        or obj.byte_size < 0
        for obj in listing.objects
    ):
        return SurveyResult((), False, "OBJECT_METADATA_INVALID")
    keys = [obj.object_key for obj in listing.objects]
    if len(set(keys)) != len(keys):
        return SurveyResult((), False, "DUPLICATE_OBJECT")
    results = []
    complete = True
    for obj in sorted(listing.objects, key=lambda item: item.object_key):
        # Do not return filesystem paths or exception text in the report.
        identity = f"{len(scope.namespace)}:{scope.namespace}{obj.object_key}"
        object_ref = hashlib.sha256(identity.encode()).hexdigest()
        try:
            refs = await references.inspect_references(storage_backend=scope.storage_backend, object_key=obj.object_key)
            decision, reason = classify(obj, refs, scope, now)
        except Exception:
            decision, reason = SurveyDecision.HOLD, "REFERENCE_UNAVAILABLE"
        complete = complete and decision is not SurveyDecision.HOLD
        results.append(SurveyItem(object_ref, decision, reason))
    return SurveyResult(tuple(results), complete)
