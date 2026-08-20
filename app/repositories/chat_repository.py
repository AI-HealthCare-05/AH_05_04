from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.chat import ChatGenerationStatus, ChatMessage, ChatRole, ChatSession
from app.models.medical_documents import MedicalDocument
from app.models.prescriptions import Prescription


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(self, *, prescription: Prescription) -> ChatSession:
        chat_session = ChatSession(prescription_id=prescription.id)
        self.session.add(chat_session)
        await self.session.flush()
        await self.session.refresh(chat_session, attribute_names=["created_at", "last_message_at"])
        return chat_session

    async def get_session_owned(self, *, session_id: UUID, user_id: UUID) -> ChatSession | None:
        result = await self.session.execute(
            select(ChatSession)
            .options(selectinload(ChatSession.prescription).selectinload(Prescription.document))
            .where(ChatSession.id == session_id)
        )
        chat_session = result.scalar_one_or_none()
        if chat_session is None or chat_session.prescription.document.user_id != user_id:
            return None
        return chat_session

    async def get_session_owned_for_update(self, *, session_id: UUID, user_id: UUID) -> ChatSession | None:
        ownership_exists = exists(
            select(1)
            .select_from(Prescription)
            .join(MedicalDocument, MedicalDocument.id == Prescription.document_id)
            .where(
                Prescription.id == ChatSession.prescription_id,
                MedicalDocument.user_id == user_id,
            )
        )
        result = await self.session.execute(
            select(ChatSession)
            .where(
                ChatSession.id == session_id,
                ownership_exists,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_messages(self, *, session: ChatSession) -> list[ChatMessage]:
        result = await self.session.execute(
            select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.message_seq)
        )
        return list(result.scalars().all())

    async def next_seq(self, *, session: ChatSession) -> int:
        result = await self.session.execute(
            select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.message_seq.desc())
        )
        last = result.scalars().first()
        return (last.message_seq + 1) if last else 1

    async def create_message(
        self,
        *,
        session: ChatSession,
        message_seq: int,
        role: ChatRole,
        content: str | None,
        generation_status: ChatGenerationStatus,
    ) -> ChatMessage:
        message = ChatMessage(
            session_id=session.id,
            message_seq=message_seq,
            role=role,
            content=content,
            generation_status=generation_status,
        )
        self.session.add(message)
        await self.session.flush()
        await self.session.refresh(message, attribute_names=["created_at"])
        return message

    async def mark_generating(self, message: ChatMessage) -> ChatMessage:
        message.generation_status = ChatGenerationStatus.GENERATING
        await self.session.flush()
        return message

    async def mark_completed(
        self,
        message: ChatMessage,
        *,
        content: str,
        model_name: str,
        prompt_version: str,
        completed_at: datetime,
    ) -> ChatMessage:
        message.content = content
        message.model_name = model_name
        message.prompt_version = prompt_version
        message.generation_status = ChatGenerationStatus.COMPLETED
        message.completed_at = completed_at
        await self.session.flush()
        return message

    async def commit_failed_message_pair(
        self,
        message: ChatMessage,
        *,
        error_code: str,
        error_message: str,
        completed_at: datetime,
    ) -> ChatMessage:
        """Mark failure and commit the entire current pending transaction containing the message pair."""

        message.generation_status = ChatGenerationStatus.FAILED
        message.error_code = error_code
        message.error_message = error_message
        message.completed_at = completed_at
        message.content = None
        message.model_name = None
        message.prompt_version = None
        await self.session.commit()
        return message

    async def update_last_message_at(self, session: ChatSession, *, last_message_at: datetime) -> ChatSession:
        session.last_message_at = last_message_at
        await self.session.flush()
        return session
