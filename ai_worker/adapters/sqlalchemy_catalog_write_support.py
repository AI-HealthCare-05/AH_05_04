"""Catalog PostgreSQL adapter가 사용할 Source·Identity 결속 단계입니다.

이 모듈은 CatalogBuildRepository 구현체가 아닙니다. 호출자가 연 transaction 안에서
Source reference를 대조하고 안정 Identity를 준비하며 commit하지 않습니다.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import String, column, select, table, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.catalog.storage import CatalogStoragePlan
from ai_worker.tasks.rag.catalog.types import ProductIdentity

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


class CatalogDatabaseBindingError(ValueError):
    """입력 원문이나 식별값을 노출하지 않는 DB 결속 오류입니다."""

    def __init__(self) -> None:
        super().__init__("Catalog database binding failed")


@dataclass(frozen=True, slots=True)
class CatalogDatabaseBindings:
    source_snapshot_ids: dict[str, UUID]
    identity_ids: dict[ProductIdentity, UUID]


def _uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise CatalogDatabaseBindingError() from None


class SqlAlchemyCatalogWriteSupport:
    """전체 Catalog 저장 transaction 내부에서만 사용하는 선행 결속 단계입니다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bind(self, plan: CatalogStoragePlan) -> CatalogDatabaseBindings:
        source_snapshot_ids = await self._bind_source_refs(plan)
        identity_ids = await self._upsert_identities(plan)
        return CatalogDatabaseBindings(source_snapshot_ids, identity_ids)

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
