"""Catalog PostgreSQL adapter가 사용할 Source·Identity 결속 단계입니다.

이 모듈은 CatalogBuildRepository 구현체가 아닙니다. 호출자가 연 transaction 안에서
Source reference를 대조하고 안정 Identity를 준비하며 commit하지 않습니다.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Integer, Numeric, String, column, select, table, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import TableClause

from ai_worker.tasks.rag.catalog.storage import CatalogStoragePlan
from ai_worker.tasks.rag.catalog.types import CandidateEntityType, ProductIdentity

_SOURCE_SNAPSHOT = table(
    "rag_source_snapshot",
    column("id", String(36)),
    column("source_version", String(255)),
)
_ENTITY_IDENTITY = table(
    "rag_entity_identity",
    column("id", String(36)),
    column("entity_type", String(20)),
    column("code_system", String(50)),
    column("canonical_code", String(100)),
)
_PRODUCT = table(
    "rag_medication_product",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("entity_identity_id", String(36)),
    column("identity_entity_type", String(20)),
    column("source_record_key", String(255)),
    column("code_system", String(50)),
    column("canonical_code", String(100)),
    column("product_name", String(255)),
    column("normalized_product_name", String(255)),
    column("strength_text", String(100)),
    column("dosage_form", String(100)),
    column("manufacturer_name", String(255)),
    column("product_status", String(50)),
)
_INGREDIENT = table(
    "rag_medication_ingredient",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("entity_identity_id", String(36)),
    column("identity_entity_type", String(20)),
    column("source_record_key", String(255)),
    column("ingredient_code_system", String(50)),
    column("ingredient_code", String(100)),
    column("ingredient_name", String(255)),
    column("normalized_ingredient_name", String(255)),
)
_ALIAS = table(
    "rag_medication_alias",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("target_identity_id", String(36)),
    column("target_type", String(20)),
    column("alias_text", String(255)),
    column("normalized_alias_text", String(255)),
    column("alias_source", String(100)),
    column("review_status", String(20)),
    column("record_status", String(20)),
    column("is_effective", Boolean),
)
_COMPONENT = table(
    "rag_medication_product_component",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("product_id", String(36)),
    column("ingredient_id", String(36)),
    column("component_role", String(30)),
    column("amount_value", Numeric(12, 4)),
    column("amount_unit", String(50)),
    column("amount_text", String(100)),
    column("display_order", Integer),
)
_SEARCH_ENTRY = table(
    "rag_medication_search_entry",
    column("id", String(36)),
    column("entry_type", String(30)),
    column("product_id", String(36)),
    column("product_identity_id", String(36)),
    column("identity_entity_type", String(20)),
    column("alias_id", String(36)),
    column("normalized_text", String(255)),
)


class CatalogDatabaseBindingError(ValueError):
    """입력 원문이나 식별값을 노출하지 않는 DB 결속 오류입니다."""

    def __init__(self) -> None:
        super().__init__("Catalog database binding failed")


@dataclass(frozen=True, slots=True)
class CatalogDatabaseBindings:
    source_snapshot_ids: dict[str, UUID]
    identity_ids: dict[ProductIdentity, UUID]


@dataclass(frozen=True, slots=True)
class CatalogDatabaseStageResult:
    """현재 최소 DB에 결속된 구성원 ID입니다. Publication 완료를 뜻하지 않습니다."""

    bindings: CatalogDatabaseBindings
    product_ids: dict[str, UUID] = field(default_factory=dict)
    ingredient_ids: dict[str, UUID] = field(default_factory=dict)
    alias_ids: dict[str, UUID] = field(default_factory=dict)
    component_ids: dict[str, UUID] = field(default_factory=dict)
    search_entry_ids: dict[str, UUID] = field(default_factory=dict)


def _uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise CatalogDatabaseBindingError() from None


def _record(value: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise CatalogDatabaseBindingError() from None
    if not isinstance(decoded, dict):
        raise CatalogDatabaseBindingError()
    return decoded


def _text(record: dict[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogDatabaseBindingError()
    return value


def _optional_text(record: dict[str, object], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CatalogDatabaseBindingError()
    return value


def _identity(record: dict[str, object]) -> ProductIdentity:
    value = record.get("identity")
    if not isinstance(value, dict):
        raise CatalogDatabaseBindingError()
    try:
        entity_type = CandidateEntityType(_text(value, "entity_type"))
    except ValueError:
        raise CatalogDatabaseBindingError() from None
    return ProductIdentity(entity_type, _text(value, "code_system"), _text(value, "canonical_code"))


def _amount(record: dict[str, object]) -> Decimal:
    try:
        value = Decimal(_text(record, "strength_value"))
    except InvalidOperation:
        raise CatalogDatabaseBindingError() from None
    exponent = value.as_tuple().exponent
    if (
        not value.is_finite()
        or not isinstance(exponent, int)
        or value < 0
        or exponent < -4
        or value > Decimal("99999999.9999")
    ):
        raise CatalogDatabaseBindingError()
    return value


class SqlAlchemyCatalogWriteSupport:
    """전체 Catalog 저장 transaction 내부에서만 사용하는 선행 결속 단계입니다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bind(self, plan: CatalogStoragePlan) -> CatalogDatabaseBindings:
        source_snapshot_ids = await self._bind_source_refs(plan)
        identity_ids = await self._upsert_identities(plan)
        return CatalogDatabaseBindings(source_snapshot_ids, identity_ids)

    async def stage_compatible_members(self, plan: CatalogStoragePlan) -> CatalogDatabaseStageResult:
        """현재 최소 schema가 표현할 수 있는 구성원을 savepoint 안에 적재합니다.

        Catalog build/Set/manifest와 D-02 실행 참조는 저장하지 않으므로 이 메서드의
        성공을 build 저장 또는 publication 완료로 취급하면 안 됩니다.
        """

        try:
            async with self._session.begin_nested():
                bindings = await self.bind(plan)
                records = {row.member_ref: _record(row.canonical_record) for row in plan.rows}
                product_ids = await self._stage_products(plan, records, bindings)
                ingredient_ids = await self._stage_ingredients(plan, records, bindings)
                alias_ids = await self._stage_aliases(plan, records, bindings)
                component_ids = await self._stage_components(plan, records, bindings, product_ids, ingredient_ids)
                search_entry_ids = await self._stage_search_entries(
                    plan,
                    records,
                    bindings,
                    product_ids,
                    alias_ids,
                )
                return CatalogDatabaseStageResult(
                    bindings,
                    product_ids,
                    ingredient_ids,
                    alias_ids,
                    component_ids,
                    search_entry_ids,
                )
        except CatalogDatabaseBindingError:
            raise
        except (SQLAlchemyError, KeyError, TypeError, ValueError, ArithmeticError):
            raise CatalogDatabaseBindingError() from None

    async def _bind_source_refs(self, plan: CatalogStoragePlan) -> dict[str, UUID]:
        requested: dict[UUID, tuple[str, str]] = {}
        for source_ref in plan.source_refs:
            snapshot_id = _uuid(source_ref.snapshot_id)
            if snapshot_id in requested:
                raise CatalogDatabaseBindingError()
            requested[snapshot_id] = (source_ref.snapshot_id, source_ref.source_version)

        statement = (
            select(_SOURCE_SNAPSHOT.c.id, _SOURCE_SNAPSHOT.c.source_version)
            .where(_SOURCE_SNAPSHOT.c.id.in_(tuple(str(value) for value in requested)))
            .order_by(_SOURCE_SNAPSHOT.c.id)
            .with_for_update(of=_SOURCE_SNAPSHOT)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        if len(rows) != len(requested):
            raise CatalogDatabaseBindingError()

        bound: dict[str, UUID] = {}
        for row in rows:
            snapshot_id = _uuid(row["id"])
            requested_ref = requested.get(snapshot_id)
            if requested_ref is None or requested_ref[1] != row["source_version"]:
                raise CatalogDatabaseBindingError()
            bound[requested_ref[0]] = snapshot_id
        return bound

    async def _upsert_identities(self, plan: CatalogStoragePlan) -> dict[ProductIdentity, UUID]:
        ordered = tuple(
            sorted(
                plan.identities,
                key=lambda value: (value.entity_type.value, value.code_system, value.canonical_code),
            )
        )
        if not ordered:
            return {}
        for identity in ordered:
            await self._session.execute(
                insert(_ENTITY_IDENTITY)
                .values(
                    id=str(uuid4()),
                    entity_type=identity.entity_type.value,
                    code_system=identity.code_system,
                    canonical_code=identity.canonical_code,
                )
                .on_conflict_do_nothing(index_elements=["entity_type", "code_system", "canonical_code"])
            )

        statement = select(
            _ENTITY_IDENTITY.c.id,
            _ENTITY_IDENTITY.c.entity_type,
            _ENTITY_IDENTITY.c.code_system,
            _ENTITY_IDENTITY.c.canonical_code,
        ).where(
            tuple_(
                _ENTITY_IDENTITY.c.entity_type,
                _ENTITY_IDENTITY.c.code_system,
                _ENTITY_IDENTITY.c.canonical_code,
            ).in_(
                tuple(
                    (identity.entity_type.value, identity.code_system, identity.canonical_code) for identity in ordered
                )
            )
        )
        rows = (await self._session.execute(statement)).mappings().all()
        by_key = {
            (str(row["entity_type"]), str(row["code_system"]), str(row["canonical_code"])): _uuid(row["id"])
            for row in rows
        }
        if len(by_key) != len(ordered):
            raise CatalogDatabaseBindingError()
        return {
            identity: by_key[(identity.entity_type.value, identity.code_system, identity.canonical_code)]
            for identity in ordered
        }

    async def _upsert_row(
        self,
        target: TableClause,
        *,
        values: dict[str, object],
        key_columns: tuple[str, ...],
    ) -> UUID:
        table_columns = target.c
        database_values = {name: str(value) if isinstance(value, UUID) else value for name, value in values.items()}
        await self._session.execute(insert(target).values(id=str(uuid4()), **database_values).on_conflict_do_nothing())
        statement = select(target).where(*(table_columns[name] == database_values[name] for name in key_columns))
        rows = (await self._session.execute(statement)).mappings().all()
        if len(rows) != 1:
            raise CatalogDatabaseBindingError()
        stored = rows[0]
        for name, expected in database_values.items():
            actual = stored[name]
            if isinstance(expected, Decimal):
                if Decimal(actual) != expected:
                    raise CatalogDatabaseBindingError()
            elif actual != expected:
                raise CatalogDatabaseBindingError()
        return _uuid(stored["id"])

    async def _stage_products(
        self,
        plan: CatalogStoragePlan,
        records: dict[str, dict[str, object]],
        bindings: CatalogDatabaseBindings,
    ) -> dict[str, UUID]:
        result: dict[str, UUID] = {}
        for row in (item for item in plan.rows if item.kind == "PRODUCT"):
            record = records[row.member_ref]
            identity = _identity(record)
            values: dict[str, object] = {
                "source_snapshot_id": bindings.source_snapshot_ids[_text(record, "source_snapshot_id")],
                "entity_identity_id": bindings.identity_ids[identity],
                "identity_entity_type": identity.entity_type.value,
                "source_record_key": _text(record, "source_record_key"),
                "code_system": identity.code_system,
                "canonical_code": identity.canonical_code,
                "product_name": _text(record, "product_name"),
                "normalized_product_name": _text(record, "normalized_product_name"),
                "strength_text": _optional_text(record, "strength_text"),
                "dosage_form": _optional_text(record, "dosage_form"),
                "manufacturer_name": _optional_text(record, "manufacturer_name"),
                "product_status": _text(record, "status"),
            }
            result[row.member_ref] = await self._upsert_row(
                _PRODUCT,
                values=values,
                key_columns=("source_snapshot_id", "code_system", "canonical_code"),
            )
        return result

    async def _stage_ingredients(
        self,
        plan: CatalogStoragePlan,
        records: dict[str, dict[str, object]],
        bindings: CatalogDatabaseBindings,
    ) -> dict[str, UUID]:
        result: dict[str, UUID] = {}
        for row in (item for item in plan.rows if item.kind == "INGREDIENT"):
            record = records[row.member_ref]
            if _text(record, "status") != "ACTIVE":
                raise CatalogDatabaseBindingError()
            identity = _identity(record)
            values: dict[str, object] = {
                "source_snapshot_id": bindings.source_snapshot_ids[_text(record, "source_snapshot_id")],
                "entity_identity_id": bindings.identity_ids[identity],
                "identity_entity_type": identity.entity_type.value,
                "source_record_key": _text(record, "source_record_key"),
                "ingredient_code_system": identity.code_system,
                "ingredient_code": identity.canonical_code,
                "ingredient_name": _text(record, "ingredient_name"),
                "normalized_ingredient_name": _text(record, "normalized_ingredient_name"),
            }
            result[row.member_ref] = await self._upsert_row(
                _INGREDIENT,
                values=values,
                key_columns=("source_snapshot_id", "entity_identity_id"),
            )
        return result

    async def _stage_aliases(
        self,
        plan: CatalogStoragePlan,
        records: dict[str, dict[str, object]],
        bindings: CatalogDatabaseBindings,
    ) -> dict[str, UUID]:
        result: dict[str, UUID] = {}
        for row in (item for item in plan.rows if item.kind == "ALIAS"):
            record = records[row.member_ref]
            identity = _identity(record)
            effective = record.get("is_effective")
            if type(effective) is not bool:
                raise CatalogDatabaseBindingError()
            values: dict[str, object] = {
                "source_snapshot_id": bindings.source_snapshot_ids[_text(record, "source_snapshot_id")],
                "target_identity_id": bindings.identity_ids[identity],
                "target_type": identity.entity_type.value,
                "alias_text": _text(record, "alias_text"),
                "normalized_alias_text": _text(record, "normalized_alias"),
                "alias_source": _text(record, "alias_source"),
                "review_status": _text(record, "review_status"),
                "record_status": _text(record, "status"),
                "is_effective": effective,
            }
            result[row.member_ref] = await self._upsert_row(
                _ALIAS,
                values=values,
                key_columns=("target_identity_id", "source_snapshot_id", "normalized_alias_text", "alias_source"),
            )
        return result

    async def _stage_components(
        self,
        plan: CatalogStoragePlan,
        records: dict[str, dict[str, object]],
        bindings: CatalogDatabaseBindings,
        product_ids: dict[str, UUID],
        ingredient_ids: dict[str, UUID],
    ) -> dict[str, UUID]:
        result: dict[str, UUID] = {}
        for row in (item for item in plan.rows if item.kind == "COMPONENT"):
            record = records[row.member_ref]
            if record.get("release_profile") is not None:
                raise CatalogDatabaseBindingError()
            display_order = record.get("component_order")
            if type(display_order) is not int or display_order < 1:
                raise CatalogDatabaseBindingError()
            values: dict[str, object] = {
                "source_snapshot_id": bindings.source_snapshot_ids[_text(record, "source_snapshot_id")],
                "product_id": product_ids[_text(record, "product_ref")],
                "ingredient_id": ingredient_ids[_text(record, "ingredient_ref")],
                "component_role": _text(record, "component_role"),
                "amount_value": _amount(record),
                "amount_unit": _text(record, "strength_unit"),
                "amount_text": None,
                "display_order": display_order,
            }
            result[row.member_ref] = await self._upsert_row(
                _COMPONENT,
                values=values,
                key_columns=("product_id", "ingredient_id", "component_role"),
            )
        return result

    async def _stage_search_entries(
        self,
        plan: CatalogStoragePlan,
        records: dict[str, dict[str, object]],
        bindings: CatalogDatabaseBindings,
        product_ids: dict[str, UUID],
        alias_ids: dict[str, UUID],
    ) -> dict[str, UUID]:
        result: dict[str, UUID] = {}
        for row in (item for item in plan.rows if item.kind == "SEARCH_ENTRY"):
            record = records[row.member_ref]
            identity = _identity(record)
            alias_ref = record.get("alias_ref")
            if alias_ref is not None and not isinstance(alias_ref, str):
                raise CatalogDatabaseBindingError()
            values: dict[str, object] = {
                "entry_type": _text(record, "entry_type"),
                "product_id": product_ids[_text(record, "product_ref")],
                "product_identity_id": bindings.identity_ids[identity],
                "identity_entity_type": identity.entity_type.value,
                "alias_id": None if alias_ref is None else alias_ids[alias_ref],
                "normalized_text": _text(record, "normalized_text"),
            }
            result[row.member_ref] = await self._upsert_row(
                _SEARCH_ENTRY,
                values=values,
                key_columns=("product_id", "entry_type", "normalized_text"),
            )
        return result
