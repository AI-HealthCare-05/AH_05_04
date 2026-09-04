"""SQLAlchemy 기반 OCR 실행 시작 상태 전이 저장소입니다."""

from datetime import datetime

from sqlalchemy import DateTime, String, column, func, table, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.schemas.messages import DomainType, JobType, WorkerMessage

_OCR_JOB = table(
    "ocr_job",
    column("id", String(36)),
    column("ai_job_id", String(36)),
    column("ocr_status", String(20)),
    column("started_at", DateTime(timezone=True)),
)


class SqlAlchemyOcrExecutionStarter:
    """OCR Job을 Provider 실행 전 PROCESSING 상태로 전환합니다.

    변경사항만 현재 transaction에 적재하며 직접 commit하지 않습니다.
    Consumer가 AI Job lease·attempt 변경과 함께 짧게 commit합니다.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(
        self,
        *,
        message: WorkerMessage,
        started_at: datetime,
    ) -> bool:
        """일치하는 OCR Job을 PROCESSING으로 전환합니다.

        최초 실행의 PENDING과 lease 만료 후 재시도되는 PROCESSING을 모두
        허용합니다. 기존 started_at은 최초 실행 시각으로 유지합니다.
        """

        if message.job_type is not JobType.OCR or message.domain_type is not DomainType.OCR_JOB:
            return False

        statement = (
            update(_OCR_JOB)
            .where(
                _OCR_JOB.c.id == str(message.domain_id),
                _OCR_JOB.c.ai_job_id == str(message.job_id),
                _OCR_JOB.c.ocr_status.in_(("PENDING", "PROCESSING")),
            )
            .values(
                ocr_status="PROCESSING",
                started_at=func.coalesce(_OCR_JOB.c.started_at, started_at),
            )
            .returning(_OCR_JOB.c.id)
        )

        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None
