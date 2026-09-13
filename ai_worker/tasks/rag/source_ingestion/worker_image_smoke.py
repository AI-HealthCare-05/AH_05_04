"""Worker 이미지의 Source Snapshot DB 연결을 검증합니다."""

import asyncio
from uuid import UUID

from ai_worker.adapters.sqlalchemy_source_snapshot_repository import (
    SqlAlchemySourceSnapshotRepository,
)
from ai_worker.core import get_config
from ai_worker.core.config import Config
from ai_worker.core.runtime_assembly import create_session_factory, create_worker_engine

_MISSING_OPERATION_ID = UUID(int=0)


async def verify_source_snapshot_adapter(config: Config) -> None:
    """Worker runtime 설정으로 연결하고 Source Snapshot 테이블을 읽습니다."""

    engine = create_worker_engine(config)
    try:
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            repository = SqlAlchemySourceSnapshotRepository(session)
            await repository.get_latest_snapshot(operation_id=_MISSING_OPERATION_ID)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(verify_source_snapshot_adapter(get_config()))
    print("PASS source snapshot adapter import and database connection")


if __name__ == "__main__":
    main()
