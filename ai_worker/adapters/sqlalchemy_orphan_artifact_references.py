"""#613 cleanup 대상의 전체 Source DB 참조를 조회합니다."""

from sqlalchemy import String, column, func, select, table, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.source_cleanup.orphan_artifact import ArtifactReferences, CleanupTarget

_ARTIFACT = table(
    "rag_source_ingestion_artifact",
    column("id", String(36)),
    column("ingestion_run_id", String(36)),
    column("storage_backend", String(50)),
    column("object_key", String(500)),
    column("raw_checksum", String(64)),
)
_RUN = table("rag_source_ingestion_run", column("id", String(36)), column("snapshot_id", String(36)))
_MEMBER = table(
    "rag_source_snapshot_member",
    column("source_snapshot_id", String(36)),
    column("ingestion_artifact_id", String(36)),
)


async def lock_source_artifact_mutation(session: AsyncSession) -> None:
    """Artifact 보존 transaction과 cleanup을 같은 application lock으로 직렬화합니다."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('source-artifact-cleanup-613'))"))


class SqlAlchemyOrphanArtifactReferenceInspector:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def inspect(self, target: CleanupTarget) -> ArtifactReferences:
        matching = (_ARTIFACT.c.storage_backend == "LOCAL_PRIVATE") & (_ARTIFACT.c.object_key == target.object_key)
        rows = (
            await self._session.execute(
                select(_ARTIFACT.c.id, _ARTIFACT.c.ingestion_run_id, _ARTIFACT.c.raw_checksum).where(matching)
            )
        ).all()
        artifact_ids = tuple(str(row.id) for row in rows)
        run_ids = tuple({str(row.ingestion_run_id) for row in rows})
        checksum_conflicts = sum(row.raw_checksum != target.checksum for row in rows)
        member_count = 0
        snapshot_count = 0
        if artifact_ids:
            member_count = int(
                await self._session.scalar(
                    select(func.count()).select_from(_MEMBER).where(_MEMBER.c.ingestion_artifact_id.in_(artifact_ids))
                )
                or 0
            )
        if run_ids:
            snapshot_count = int(
                await self._session.scalar(
                    select(func.count())
                    .select_from(_RUN)
                    .where(_RUN.c.id.in_(run_ids), _RUN.c.snapshot_id.is_not(None))
                )
                or 0
            )
        return ArtifactReferences(len(rows), len(run_ids), member_count, snapshot_count, checksum_conflicts)
