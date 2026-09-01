from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_documents import MedicalDocument
from app.models.users import User


class DocumentLockTimeoutError(Exception):
    """문서 row lock을 제한 시간 안에 획득하지 못한 경우입니다.

    같은 문서에 대한 처방 확정 또는 extracted-field PATCH가
    이미 진행 중이라는 뜻이며, 서비스 계층에서 409로 변환합니다.
    """


class MedicalDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user: User,
        original_file_name: str,
        object_key: str,
        file_mime_type: str,
        file_size_bytes: int,
    ) -> MedicalDocument:
        document = MedicalDocument(
            user_id=user.id,
            original_file_name=original_file_name,
            object_key=object_key,
            file_mime_type=file_mime_type,
            file_size_bytes=file_size_bytes,
        )
        self.session.add(document)
        await self.session.flush()
        await self.session.refresh(
            document,
            attribute_names=["uploaded_at"],
        )
        return document

    async def get_owned(
        self,
        *,
        document_id: UUID,
        user: User,
    ) -> MedicalDocument | None:
        result = await self.session.execute(
            select(MedicalDocument).where(
                MedicalDocument.id == document_id,
                MedicalDocument.user_id == user.id,
            )
        )
        return result.scalar_one_or_none()

    async def get_owned_for_update(
        self,
        *,
        document_id: UUID,
        user: User,
    ) -> MedicalDocument | None:
        # 처방 확정과 extracted-field PATCH를 직렬화하는 lock anchor입니다.
        # 확정 시점에는 PRESCRIPTION row가 아직 없어 잠글 대상이 없으므로,
        # 두 경로가 공통으로 도달하는 부모 MEDICAL_DOCUMENT row를 잠급니다.
        #
        # 전역 lock 순서(PRESCRIPTION → CHAT_SESSION → AI_JOB → 도메인 row → OUTBOX)에서
        # MEDICAL_DOCUMENT는 PRESCRIPTION의 상위이므로 항상 가장 먼저 잠가야
        # Post-MVP-1의 처방 버전 transaction과 역순 잠금이 생기지 않습니다.
        #
        # 무한 대기로 connection pool이 고갈되지 않도록 transaction 범위 lock_timeout을 둡니다.
        # SET LOCAL이므로 현재 transaction이 끝나면 자동으로 원복됩니다.
        await self.session.execute(text("SET LOCAL lock_timeout = '3s'"))

        try:
            result = await self.session.execute(
                # selectinload를 함께 쓰면 FOR UPDATE가 outer join과 충돌할 수 있어
                # 이 메서드는 관계를 eager loading 하지 않습니다.
                select(MedicalDocument)
                .where(
                    MedicalDocument.id == document_id,
                    MedicalDocument.user_id == user.id,
                )
                .with_for_update()
            )
        except DBAPIError as error:
            # PostgreSQL 55P03 lock_not_available만 동시 요청 충돌로 처리하고
            # 나머지 DB 오류는 원래 계층에서 처리하도록 그대로 전파합니다.
            if getattr(error.orig, "sqlstate", None) == "55P03":
                raise DocumentLockTimeoutError from None
            raise

        return result.scalar_one_or_none()

    async def update_object_key(
        self,
        document: MedicalDocument,
        object_key: str,
    ) -> MedicalDocument:
        document.object_key = object_key
        await self.session.flush()
        return document
