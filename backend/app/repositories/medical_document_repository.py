from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_documents import MedicalDocument
from app.models.users import User
from app.repositories.profile_ownership import get_self_profile_id, owned_by_self


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
        profile_id = await get_self_profile_id(self.session, user_id=user.id)
        if profile_id is None:
            raise RuntimeError(f"User {user.id} has no SELF profile; cannot create medical_document.")
        document = MedicalDocument(
            uploaded_by=user.id,
            profile_id=profile_id,
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
                owned_by_self(MedicalDocument.profile_id, user.id),
            )
        )
        return result.scalar_one_or_none()

    async def update_object_key(
        self,
        document: MedicalDocument,
        object_key: str,
    ) -> MedicalDocument:
        document.object_key = object_key
        await self.session.flush()
        return document
