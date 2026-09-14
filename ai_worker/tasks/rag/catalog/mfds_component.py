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


@dataclass(frozen=True, slots=True)
class MfdsComponentInspection:
    """전달된 입력 전체의 적격성. Source Receipt·Catalog 완전성 판정은 아니다."""

    observations: tuple[MfdsComponentObservation, ...] = field(repr=False)
    exclusions: tuple[MfdsComponentExclusion, ...] = field(repr=False)
    input_count: int
    duplicate_count: int

    @property
    def eligible_for_mapping(self) -> bool:
        return bool(self.observations) and not self.exclusions


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
    """빈 행·충돌을 기록하며, 제외 행이 있으면 전체 입력을 부적격으로 표시한다."""
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
