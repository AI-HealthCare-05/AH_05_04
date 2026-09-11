"""Catalog v2 저장·복원 adapter. Python 검증과 명시적 transaction으로 DB 결속을 보호합니다."""

import dataclasses
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Integer, LargeBinary, Numeric, String, column, func, select, table, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker
from sqlalchemy.sql.selectable import TableClause

from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.catalog.approval import CatalogApprovalVerifier
from ai_worker.tasks.rag.catalog.build import CatalogMembers
from ai_worker.tasks.rag.catalog.export import CatalogExportArtifacts
from ai_worker.tasks.rag.catalog.restore import restore_catalog_export_bytes, restore_current_catalog_storage
from ai_worker.tasks.rag.catalog.storage import CatalogStoragePlan, prepare_catalog_storage
from ai_worker.tasks.rag.catalog.types import CandidateEntityType, ProductIdentity

_SOURCE_SNAPSHOT = table(
    "rag_source_snapshot",
    column("id", String(36)),
    column("source_version", String(200)),
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
    column("release_profile", String(255)),
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
_CATALOG_SET = table(
    "rag_catalog_set",
    column("id", String(36)),
    column("catalog_version", String(100)),
    column("schema_version", String(100)),
    column("normalization_version", String(100)),
    column("manifest_spec_version", String(100)),
    column("envelope_hash", String(64)),
    column("manifest_json", LargeBinary),
)
_CATALOG_SET_SOURCE = table(
    "rag_catalog_set_source",
    column("set_id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_version", String(255)),
)
_CATALOG_SET_MEMBER = table(
    "rag_catalog_set_member",
    column("set_id", String(36)),
    column("member_kind", String(30)),
    column("member_ref", String(100)),
    column("source_snapshot_id", String(36)),
    column("product_id", String(36)),
    column("ingredient_id", String(36)),
    column("component_id", String(36)),
    column("alias_id", String(36)),
    column("search_entry_id", String(36)),
)
_CATALOG_SET_HASH = table(
    "rag_catalog_set_hash",
    column("set_id", String(36)),
    column("hash_kind", String(30)),
    column("schema_version", String(100)),
    column("contract_spec_version", String(100)),
    column("digest", String(64)),
    column("target", String(50)),
    column("canonical_bytes", LargeBinary),
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
    set_id: UUID | None = None


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

    def __init__(self, session: AsyncSession, *, read_only: bool = False) -> None:
        self._session = session
        self._read_only = read_only

    async def bind(self, plan: CatalogStoragePlan) -> CatalogDatabaseBindings:
        source_snapshot_ids = await self._bind_source_refs(plan)
        identity_ids = await self._upsert_identities(plan)
        return CatalogDatabaseBindings(source_snapshot_ids, identity_ids)

    async def stage_compatible_members(self, plan: CatalogStoragePlan) -> CatalogDatabaseStageResult:
        """한 savepoint에서 구성원과 Set을 검증·적재합니다. commit은 adapter가 소유합니다."""
        if self._read_only:
            raise CatalogDatabaseBindingError()
        try:
            async with self._session.begin_nested():
                staged = await self._bind_members(plan)
                set_id = await self._stage_set(plan, staged)
                return dataclasses.replace(staged, set_id=set_id)
        except CatalogDatabaseBindingError:
            raise
        except (SQLAlchemyError, KeyError, TypeError, ValueError, ArithmeticError):
            raise CatalogDatabaseBindingError() from None

    async def _bind_members(self, plan: CatalogStoragePlan) -> CatalogDatabaseStageResult:
        bindings = await self.bind(plan)
        records = {row.member_ref: _record(row.canonical_record) for row in plan.rows}
        product_ids = await self._stage_products(plan, records, bindings)
        ingredient_ids = await self._stage_ingredients(plan, records, bindings)
        alias_ids = await self._stage_aliases(plan, records, bindings)
        component_ids = await self._stage_components(plan, records, bindings, product_ids, ingredient_ids)
        search_entry_ids = await self._stage_search_entries(plan, records, bindings, product_ids, alias_ids)
        return CatalogDatabaseStageResult(
            bindings, product_ids, ingredient_ids, alias_ids, component_ids, search_entry_ids
        )

    async def read_set(self, set_id: UUID) -> CatalogStoragePlan:
        """보존 bytes와 모든 실제 행/FK를 읽기 전용으로 대조합니다. 누락 행을 생성하지 않습니다."""
        if not self._read_only:
            raise CatalogDatabaseBindingError()
        try:
            stored = (
                await self._session.execute(
                    select(_CATALOG_SET.c.manifest_json).where(_CATALOG_SET.c.id == str(set_id))
                )
            ).scalar_one()
            jsonl = (
                await self._session.execute(
                    select(_CATALOG_SET_HASH.c.canonical_bytes).where(
                        _CATALOG_SET_HASH.c.set_id == str(set_id),
                        _CATALOG_SET_HASH.c.hash_kind == "EXPORT_CHECKSUM",
                    )
                )
            ).scalar_one()
            artifacts = restore_catalog_export_bytes(catalog_jsonl=bytes(jsonl), manifest_json=bytes(stored))
            catalog = artifacts.catalog
            members = CatalogMembers(
                products=catalog.products,
                ingredients=catalog.ingredients,
                components=catalog.components,
                aliases=catalog.aliases,
                search_entries=catalog.search_entries,
            )
            plan = prepare_catalog_storage(members=members, artifacts=artifacts)
            staged = await self._bind_members(plan)
            await self.verify_set(set_id, plan, staged)
            return plan
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
        )
        if not self._read_only:
            statement = statement.with_for_update(of=_SOURCE_SNAPSHOT)
        rows = (await self._session.execute(statement)).mappings().all()
        if len(rows) != len(requested):
            raise CatalogDatabaseBindingError()

        bound: dict[str, UUID] = {}
        for row in rows:
            snapshot_id = _uuid(row["id"])
            requested_ref = requested.get(snapshot_id)
            if requested_ref is None or requested_ref[1] != row["source_version"]:
                raise CatalogDatabaseBindingError()
            receipt = await SqlAlchemySourceSnapshotRepository(self._session).get_snapshot_receipt(
                snapshot_id=snapshot_id
            )
            if (
                receipt is None
                or receipt.source_snapshot_id != snapshot_id
                or receipt.source_version != requested_ref[1]
            ):
                raise CatalogDatabaseBindingError()
            try:
                receipt.validate_provenance()
            except (ValueError, TypeError, AttributeError):
                raise CatalogDatabaseBindingError() from None
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
        for identity in () if self._read_only else ordered:
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
        if not self._read_only:
            await self._session.execute(
                insert(target).values(id=str(uuid4()), **database_values).on_conflict_do_nothing()
            )
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

    async def _insert_exact(
        self,
        target: TableClause,
        *,
        values: dict[str, object],
        key_columns: tuple[str, ...],
    ) -> None:
        database_values = {name: str(value) if isinstance(value, UUID) else value for name, value in values.items()}
        await self._session.execute(insert(target).values(**database_values).on_conflict_do_nothing())
        statement = select(target).where(*(target.c[name] == database_values[name] for name in key_columns))
        rows = (await self._session.execute(statement)).mappings().all()
        if len(rows) != 1:
            raise CatalogDatabaseBindingError()
        if any(rows[0][name] != expected for name, expected in database_values.items()):
            raise CatalogDatabaseBindingError()

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
                "release_profile": _optional_text(record, "release_profile"),
            }
            result[row.member_ref] = await self._upsert_row(
                _COMPONENT,
                values=values,
                key_columns=("product_id", "display_order"),
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
            await self._require_search_entry_eligible(values)
            result[row.member_ref] = await self._upsert_row(
                _SEARCH_ENTRY,
                values=values,
                key_columns=("product_id", "entry_type", "normalized_text"),
            )
        return result

    async def _require_search_entry_eligible(self, values: dict[str, object]) -> None:
        product_query = select(_PRODUCT.c.normalized_product_name, _PRODUCT.c.product_status).where(
            _PRODUCT.c.id == str(values["product_id"]),
            _PRODUCT.c.entity_identity_id == str(values["product_identity_id"]),
            _PRODUCT.c.identity_entity_type == "PRODUCT",
        )
        if not self._read_only:
            product_query = product_query.with_for_update(of=_PRODUCT)
        product_rows = (await self._session.execute(product_query)).mappings().all()
        if len(product_rows) != 1 or product_rows[0]["product_status"] != "ACTIVE":
            raise CatalogDatabaseBindingError()

        entry_type = values["entry_type"]
        alias_id = values["alias_id"]
        normalized_text = values["normalized_text"]
        if entry_type == "PRODUCT_NAME":
            if alias_id is not None or normalized_text != product_rows[0]["normalized_product_name"]:
                raise CatalogDatabaseBindingError()
            return
        if entry_type != "APPROVED_ALIAS" or alias_id is None:
            raise CatalogDatabaseBindingError()

        alias_query = select(
            _ALIAS.c.normalized_alias_text, _ALIAS.c.review_status, _ALIAS.c.record_status, _ALIAS.c.is_effective
        ).where(
            _ALIAS.c.id == str(alias_id),
            _ALIAS.c.target_identity_id == str(values["product_identity_id"]),
            _ALIAS.c.target_type == "PRODUCT",
        )
        if not self._read_only:
            alias_query = alias_query.with_for_update(of=_ALIAS)
        alias_rows = (await self._session.execute(alias_query)).mappings().all()
        if len(alias_rows) != 1:
            raise CatalogDatabaseBindingError()
        alias = alias_rows[0]
        if (
            alias["review_status"] != "APPROVED"
            or alias["record_status"] != "ACTIVE"
            or alias["is_effective"] is not True
            or normalized_text != alias["normalized_alias_text"]
        ):
            raise CatalogDatabaseBindingError()

    async def _stage_set(self, plan: CatalogStoragePlan, staged: CatalogDatabaseStageResult) -> UUID:
        manifest = _record(plan.manifest_json)
        envelope = tuple(item for item in plan.hashes if item.kind == "CATALOG_ENVELOPE")
        if len(envelope) != 1 or _text(manifest, "catalog_manifest_hash") != envelope[0].digest:
            raise CatalogDatabaseBindingError()
        await self._session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(envelope[0].digest, 0))))
        existing = (
            await self._session.execute(
                select(_CATALOG_SET.c.id).where(
                    _CATALOG_SET.c.schema_version == _text(manifest, "schema_version"),
                    _CATALOG_SET.c.manifest_spec_version == envelope[0].contract_spec_version,
                    _CATALOG_SET.c.envelope_hash == envelope[0].digest,
                )
            )
        ).all()
        if len(existing) > 1:
            raise CatalogDatabaseBindingError()
        set_id = await self._upsert_row(
            _CATALOG_SET,
            values={
                "catalog_version": plan.catalog_version,
                "schema_version": _text(manifest, "schema_version"),
                "normalization_version": _text(manifest, "normalization_version"),
                "manifest_spec_version": envelope[0].contract_spec_version,
                "envelope_hash": envelope[0].digest,
                "manifest_json": plan.manifest_json,
            },
            key_columns=("schema_version", "manifest_spec_version", "envelope_hash"),
        )
        if existing:
            await self.verify_set(set_id, plan, staged)
            return set_id
        for source_ref in plan.source_refs:
            await self._insert_exact(
                _CATALOG_SET_SOURCE,
                values={
                    "set_id": set_id,
                    "source_snapshot_id": staged.bindings.source_snapshot_ids[source_ref.snapshot_id],
                    "source_version": source_ref.source_version,
                },
                key_columns=("set_id", "source_snapshot_id"),
            )

        member_ids = {
            "PRODUCT": ("product_id", staged.product_ids),
            "INGREDIENT": ("ingredient_id", staged.ingredient_ids),
            "COMPONENT": ("component_id", staged.component_ids),
            "ALIAS": ("alias_id", staged.alias_ids),
            "SEARCH_ENTRY": ("search_entry_id", staged.search_entry_ids),
        }
        for row in plan.rows:
            target_column, ids = member_ids[row.kind]
            values: dict[str, object] = {
                "set_id": set_id,
                "member_kind": row.kind,
                "member_ref": row.member_ref,
                "source_snapshot_id": staged.bindings.source_snapshot_ids[row.source_ref.snapshot_id],
                "product_id": None,
                "ingredient_id": None,
                "component_id": None,
                "alias_id": None,
                "search_entry_id": None,
            }
            values[target_column] = ids[row.member_ref]
            await self._insert_exact(
                _CATALOG_SET_MEMBER,
                values=values,
                key_columns=("set_id", "member_kind", "member_ref"),
            )
        for hash_material in plan.hashes:
            await self._insert_exact(
                _CATALOG_SET_HASH,
                values={
                    "set_id": set_id,
                    "hash_kind": hash_material.kind,
                    "schema_version": hash_material.schema_version,
                    "contract_spec_version": hash_material.contract_spec_version,
                    "digest": hash_material.digest,
                    "target": hash_material.target,
                    "canonical_bytes": hash_material.canonical_bytes,
                },
                key_columns=("set_id", "hash_kind"),
            )
        await self.verify_set(set_id, plan, staged)
        return set_id

    async def verify_set(
        self,
        set_id: UUID,
        plan: CatalogStoragePlan,
        staged: CatalogDatabaseStageResult,
    ) -> None:
        manifest = _record(plan.manifest_json)
        envelope = tuple(item for item in plan.hashes if item.kind == "CATALOG_ENVELOPE")
        if len(envelope) != 1:
            raise CatalogDatabaseBindingError()
        statement = select(_CATALOG_SET).where(_CATALOG_SET.c.id == str(set_id))
        rows = (await self._session.execute(statement)).mappings().all()
        if len(rows) != 1:
            raise CatalogDatabaseBindingError()
        stored = rows[0]
        expected = {
            "catalog_version": plan.catalog_version,
            "schema_version": _text(manifest, "schema_version"),
            "normalization_version": _text(manifest, "normalization_version"),
            "manifest_spec_version": envelope[0].contract_spec_version,
            "envelope_hash": _text(manifest, "catalog_manifest_hash"),
            "manifest_json": plan.manifest_json,
        }
        if any(stored[name] != value for name, value in expected.items()):
            raise CatalogDatabaseBindingError()
        source_rows = (
            (
                await self._session.execute(
                    select(_CATALOG_SET_SOURCE).where(_CATALOG_SET_SOURCE.c.set_id == str(set_id))
                )
            )
            .mappings()
            .all()
        )
        actual_sources = {(str(row["source_snapshot_id"]), str(row["source_version"])) for row in source_rows}
        expected_sources = {
            (str(staged.bindings.source_snapshot_ids[item.snapshot_id]), item.source_version)
            for item in plan.source_refs
        }
        if actual_sources != expected_sources or len(source_rows) != len(expected_sources):
            raise CatalogDatabaseBindingError()

        member_ids = {
            "PRODUCT": ("product_id", staged.product_ids),
            "INGREDIENT": ("ingredient_id", staged.ingredient_ids),
            "COMPONENT": ("component_id", staged.component_ids),
            "ALIAS": ("alias_id", staged.alias_ids),
            "SEARCH_ENTRY": ("search_entry_id", staged.search_entry_ids),
        }
        target_columns = ("product_id", "ingredient_id", "component_id", "alias_id", "search_entry_id")
        expected_members: set[tuple[object, ...]] = set()
        for item in plan.rows:
            target_column, ids = member_ids[item.kind]
            targets = tuple(str(ids[item.member_ref]) if name == target_column else None for name in target_columns)
            expected_members.add(
                (
                    item.kind,
                    item.member_ref,
                    str(staged.bindings.source_snapshot_ids[item.source_ref.snapshot_id]),
                    *targets,
                )
            )
        member_rows = (
            (
                await self._session.execute(
                    select(_CATALOG_SET_MEMBER).where(_CATALOG_SET_MEMBER.c.set_id == str(set_id))
                )
            )
            .mappings()
            .all()
        )
        actual_members = {
            (
                str(row["member_kind"]),
                str(row["member_ref"]),
                str(row["source_snapshot_id"]),
                *(None if row[name] is None else str(row[name]) for name in target_columns),
            )
            for row in member_rows
        }
        if actual_members != expected_members or len(member_rows) != len(expected_members):
            raise CatalogDatabaseBindingError()

        hash_rows = (
            (await self._session.execute(select(_CATALOG_SET_HASH).where(_CATALOG_SET_HASH.c.set_id == str(set_id))))
            .mappings()
            .all()
        )
        actual_hashes = {
            (
                str(row["hash_kind"]),
                str(row["schema_version"]),
                str(row["contract_spec_version"]),
                str(row["digest"]),
                str(row["target"]),
                bytes(row["canonical_bytes"]),
            )
            for row in hash_rows
        }
        expected_hashes = {
            (
                item.kind,
                item.schema_version,
                item.contract_spec_version,
                item.digest,
                item.target,
                item.canonical_bytes,
            )
            for item in plan.hashes
        }
        if actual_hashes != expected_hashes or len(hash_rows) != len(expected_hashes):
            raise CatalogDatabaseBindingError()


class SqlAlchemyCatalogBuildRepository:
    """현재 v2 Catalog Set 전체 transaction을 소유하는 PostgreSQL adapter입니다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_build(self, *, members: CatalogMembers, artifacts: CatalogExportArtifacts) -> None:
        plan = prepare_catalog_storage(members=members, artifacts=artifacts)
        try:
            async with self._session_factory() as session:
                if isinstance(session.bind, AsyncConnection) and session.bind.in_transaction():
                    raise CatalogDatabaseBindingError()
                async with session.begin():
                    staged = await SqlAlchemyCatalogWriteSupport(session).stage_compatible_members(plan)
                    if staged.set_id is None:
                        raise CatalogDatabaseBindingError()
                    set_id = staged.set_id
            confirmed = await self._read_plan(set_id)
            if confirmed != plan:
                raise CatalogDatabaseBindingError()
        except SQLAlchemyError:
            raise CatalogDatabaseBindingError() from None

    async def load_build(
        self, set_id: UUID, *, approval_verifier: CatalogApprovalVerifier | None
    ) -> CatalogExportArtifacts:
        """전체 v2 artifacts를 Candidate에 인계합니다. 저장 당시 승인만으로 소비를 허용하지 않습니다."""
        plan = await self._read_plan(set_id)
        return await restore_current_catalog_storage(plan, approval_verifier=approval_verifier)

    async def _read_plan(self, set_id: UUID) -> CatalogStoragePlan:
        try:
            async with self._session_factory() as session:
                if isinstance(session.bind, AsyncConnection) and session.bind.in_transaction():
                    raise CatalogDatabaseBindingError()
                async with session.begin():
                    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                    return await SqlAlchemyCatalogWriteSupport(session, read_only=True).read_set(set_id)
        except SQLAlchemyError:
            raise CatalogDatabaseBindingError() from None
