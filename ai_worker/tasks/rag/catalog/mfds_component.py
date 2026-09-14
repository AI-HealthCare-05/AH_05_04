"""MFDS 상세 행 변환. 호출자는 승인된 원료 매핑과 명시적인 순서를 제공한다."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from ai_worker.tasks.rag.catalog.build import CatalogComponentInput, CatalogIngredientInput
from ai_worker.tasks.rag.catalog.normalize import require_official_identity_text
from ai_worker.tasks.rag.catalog.types import CatalogComponentRole

_REQUIRED_FIELDS = ("ITEM_SEQ", "TAMT_SEQ", "MTRAL_SN", "MTRAL_CODE", "QNT", "INGD_UNIT_CD")


@dataclass(frozen=True, slots=True)
class MfdsComponentObservation:
    """Snapshot 내 관찰 행. 원료코드·순번을 전역 Identity로 선언하지 않는다."""

    item_seq: str
    tamt_seq: str
    mtral_sn: str
    material_code: str
    quantity: str
    unit: str
    record_json: bytes = field(repr=False)

    @property
    def source_record_key(self) -> str:
        return json.dumps(
            ["mfds-component-key-v1", self.item_seq, self.tamt_seq, self.mtral_sn],
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class MfdsComponentExclusion:
    reason: Literal[
        "EMPTY_COMPONENT_FIELDS",
        "MISSING_COMPONENT_QUANTITY",
        "INVALID_COMPONENT_KEY_FIELDS",
        "CONFLICTING_OBSERVATION",
    ]
    record_json: bytes = field(repr=False)


# 빈 주성분 행은 제공자가 성분을 비워 반환한 경우이며 원문 무결성 위반이 아니다.
# 아래 사유들은 계속 전체를 차단한다. 감사·후속 복구를 위해 분량만 누락된 경우와
# identity·join에 필요한 필드가 손상된 경우를 구분해 기록한다.
_BLOCKING_EXCLUSION_REASONS = frozenset(
    {"MISSING_COMPONENT_QUANTITY", "INVALID_COMPONENT_KEY_FIELDS", "CONFLICTING_OBSERVATION"}
)
# 분량만 비어 있고 키·성분코드·단위는 남아 있는 행. 공식 상세 화면에는 분량이 표시되므로
# 성분 없음이나 분량 미상 정상 성분으로 해석하지 않는다.
_QUANTITY_FIELD = "QNT"
_IDENTITY_FIELDS = tuple(name for name in _REQUIRED_FIELDS if name != _QUANTITY_FIELD)


@dataclass(frozen=True, slots=True)
class MfdsComponentInspection:
    """전달된 입력 전체의 적격성. Source Receipt·Catalog 완전성 판정은 아니다."""

    observations: tuple[MfdsComponentObservation, ...] = field(repr=False)
    exclusions: tuple[MfdsComponentExclusion, ...] = field(repr=False)
    input_count: int
    duplicate_count: int

    @property
    def empty_component_exclusions(self) -> tuple[MfdsComponentExclusion, ...]:
        return tuple(item for item in self.exclusions if item.reason == "EMPTY_COMPONENT_FIELDS")

    @property
    def missing_quantity_exclusions(self) -> tuple[MfdsComponentExclusion, ...]:
        """분량만 누락된 행. 차단 사유이지만 키 손상과 원인이 다르므로 따로 센다."""
        return tuple(item for item in self.exclusions if item.reason == "MISSING_COMPONENT_QUANTITY")

    @property
    def blocking_exclusions(self) -> tuple[MfdsComponentExclusion, ...]:
        return tuple(item for item in self.exclusions if item.reason in _BLOCKING_EXCLUSION_REASONS)

    @property
    def exclusion_counts_by_reason(self) -> dict[str, int]:
        """사유별 제외 건수. 어떤 행이 왜 막혔는지 감사할 수 있게 한다."""
        counts: dict[str, int] = {}
        for item in self.exclusions:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return counts

    @property
    def eligible_for_mapping(self) -> bool:
        """빈 주성분 행은 차단하지 않는다. 원문 무결성 위반은 계속 전체를 차단한다."""
        return bool(self.observations) and not self.blocking_exclusions

    @property
    def has_excluded_empty_components(self) -> bool:
        """빈 주성분 행이 제외돼 Catalog가 부분임을 나타낸다.

        해당 제품의 성분 없음이나 금기 없음을 뜻하지 않는다. 성분 기반 안전성 검사는
        구성원 0개를 판정 불가로 다루어야 하며 통과로 해석하지 않는다.
        """
        return bool(self.empty_component_exclusions)


def _record_json(record: Mapping[str, object]) -> bytes:
    try:
        if any(not isinstance(key, str) for key in record):
            raise ValueError
        return json.dumps(
            dict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("MFDS component record must be a valid JSON object") from None


def _observation(record: Mapping[str, object], record_json: bytes) -> MfdsComponentObservation:
    values = {
        name: require_official_identity_text(record.get(name), field_name=f"mfds_component.{name}")
        for name in _REQUIRED_FIELDS
    }
    return MfdsComponentObservation(
        item_seq=values["ITEM_SEQ"],
        tamt_seq=values["TAMT_SEQ"],
        mtral_sn=values["MTRAL_SN"],
        material_code=values["MTRAL_CODE"],
        quantity=values["QNT"],
        unit=values["INGD_UNIT_CD"],
        record_json=record_json,
    )


def _is_blank(record: Mapping[str, object], field_name: str) -> bool:
    value = record.get(field_name)
    return value is None or (isinstance(value, str) and not value.strip())


def _exclusion_reason(record: Mapping[str, object]) -> str:
    """구성원으로 승격하지 못한 행의 사유를 구분합니다.

    분량만 비어 있고 identity·join 필드가 남아 있으면 제공자 응답에서 값이 빠진 경우이며,
    공식 상세 화면에는 분량이 표시되는 사례가 확인됐습니다. 성분 없음이나 분량 미상 정상
    성분으로 해석하지 않고 전체 차단을 유지하되 별도 사유로 기록합니다.
    """
    try:
        require_official_identity_text(record.get("ITEM_SEQ"), field_name="mfds_component.ITEM_SEQ")
    except ValueError:
        return "INVALID_COMPONENT_KEY_FIELDS"

    if all(_is_blank(record, name) for name in (*_REQUIRED_FIELDS[1:], "CPNT_CTNT_CONT")):
        return "EMPTY_COMPONENT_FIELDS"

    if _is_blank(record, _QUANTITY_FIELD) and not any(_is_blank(record, name) for name in _IDENTITY_FIELDS):
        return "MISSING_COMPONENT_QUANTITY"

    return "INVALID_COMPONENT_KEY_FIELDS"


def inspect_mfds_component_rows(records: tuple[Mapping[str, object], ...]) -> MfdsComponentInspection:
    """빈 행·충돌을 기록한다. 빈 주성분 행은 제외로만 남기고 원문 무결성 위반만 전체를 차단한다."""
    observations: dict[str, MfdsComponentObservation] = {}
    exclusions: list[MfdsComponentExclusion] = []
    duplicate_count = 0
    for record in records:
        raw = _record_json(record)
        try:
            observation = _observation(record, raw)
        except ValueError:
            exclusions.append(MfdsComponentExclusion(_exclusion_reason(record), raw))
            continue
        previous = observations.get(observation.source_record_key)
        if previous is None:
            observations[observation.source_record_key] = observation
        elif previous.record_json == raw:
            duplicate_count += 1
        else:
            exclusions.append(MfdsComponentExclusion("CONFLICTING_OBSERVATION", raw))
    return MfdsComponentInspection(tuple(observations.values()), tuple(exclusions), len(records), duplicate_count)


def map_mfds_component(
    record: Mapping[str, object],
    *,
    ingredient: CatalogIngredientInput,
    expected_material_code: str,
    component_order: int,
) -> CatalogComponentInput:
    """누락 키·매핑 불일치를 거부하며 문자열 순번과 분량을 변환하지 않는다.

    이 함수는 매핑 승인 또는 Source Receipt 검증을 대신하지 않는다.
    ingredient의 Snapshot은 해당 상세 행을 보존한 검증된 Snapshot이어야 한다.
    """
    observation = _observation(record, _record_json(record))
    if observation.material_code != expected_material_code:
        raise ValueError("MFDS component material mapping mismatch")
    if type(component_order) is not int or component_order < 1:
        raise ValueError("MFDS component requires an explicit positive order")
    return CatalogComponentInput(
        source_snapshot_id=ingredient.source_snapshot_id,
        product_code_system="MFDS_ITEM_SEQ",
        product_canonical_code=observation.item_seq,
        ingredient_code_system=ingredient.code_system,
        ingredient_canonical_code=ingredient.canonical_code,
        component_role=CatalogComponentRole.ACTIVE_INGREDIENT,
        component_order=component_order,
        strength_value=observation.quantity,
        strength_unit=observation.unit,
        source_record_key=observation.source_record_key,
    )
