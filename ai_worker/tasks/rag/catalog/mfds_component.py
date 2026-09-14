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
    reason: Literal["EMPTY_COMPONENT_FIELDS", "INVALID_COMPONENT_FIELDS", "CONFLICTING_OBSERVATION"]
    record_json: bytes = field(repr=False)


# 빈 주성분 행은 제공자가 성분을 비워 반환한 경우이며 원문 무결성 위반이 아니다.
# 아래 두 사유는 원문이 깨졌거나 같은 키의 원문이 서로 다른 경우이므로 계속 전체를 차단한다.
_BLOCKING_EXCLUSION_REASONS = frozenset({"INVALID_COMPONENT_FIELDS", "CONFLICTING_OBSERVATION"})


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
    def blocking_exclusions(self) -> tuple[MfdsComponentExclusion, ...]:
        return tuple(item for item in self.exclusions if item.reason in _BLOCKING_EXCLUSION_REASONS)

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
            component_fields = (*_REQUIRED_FIELDS[1:], "CPNT_CTNT_CONT")
            empty = all(
                record.get(name) is None or isinstance(record.get(name), str) and not str(record[name]).strip()
                for name in component_fields
            )
            try:
                require_official_identity_text(record.get("ITEM_SEQ"), field_name="mfds_component.ITEM_SEQ")
            except ValueError:
                empty = False
            exclusions.append(
                MfdsComponentExclusion("EMPTY_COMPONENT_FIELDS" if empty else "INVALID_COMPONENT_FIELDS", raw)
            )
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
