"""Source Artifact 전체 직접 참조만 조회합니다. 0건을 전체 무참조 증명으로 승격하지 않습니다."""

from sqlalchemy import String, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.source_cleanup.survey import ReferenceObservation

_ARTIFACT = table("rag_source_ingestion_artifact", column("storage_backend", String), column("object_key", String))


class SqlAlchemySourceCleanupReader:
    def __init__(self, session: AsyncSession, *, database_id: str) -> None:
        self._session = session
        self._database_id = database_id

    async def inspect_references(self, *, storage_backend: str, object_key: str) -> ReferenceObservation:
        count = await self._session.scalar(
            select(func.count())
            .select_from(_ARTIFACT)
            .where(_ARTIFACT.c.storage_backend == storage_backend, _ARTIFACT.c.object_key == object_key)
        )
        # Namespace, external references and active writers need separate verified evidence.
        return ReferenceObservation(self._database_id, count)
