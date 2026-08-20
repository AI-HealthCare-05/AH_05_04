from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.dtos.chat import ChatMessageData, ChatRole, ChatSessionData, SendChatMessageData, SendChatMessageRequest
from app.models.chat import ChatGenerationStatus, ChatMessage, ChatSessionStatus
from app.models.chat import ChatRole as ModelChatRole
from app.models.users import User
from app.repositories.chat_repository import ChatRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.chat_ai import (
    ChatEngine,
    ChatMedicationInput,
    ChatReplyInput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
    NotConfiguredChatEngine,
)


def _to_message_data(message: ChatMessage) -> ChatMessageData:
    return ChatMessageData(
        message_id=message.id,
        role=ChatRole(message.role),
        content=message.content,
        generation_status=str(message.generation_status),
        created_at=message.created_at,
    )


class ChatService:
    def __init__(
        self,
        prescription_repository: PrescriptionRepository,
        chat_repository: ChatRepository,
        engine: ChatEngine | None = None,
    ) -> None:
        self._engine: ChatEngine = engine or NotConfiguredChatEngine()
        self._prescription_repo = prescription_repository
        self._chat_repo = chat_repository

    async def create_session(self, *, user: User, prescription_id: UUID) -> ChatSessionData:
        # 채팅 세션 생성 Backend 계약: 확정 처방을 기준으로 챗봇 세션을 만듭니다.
        prescription = await self._prescription_repo.get_owned(prescription_id=prescription_id, user_id=user.id)
        if prescription is None:
            raise ApiError(
                status_code=404,
                code="PRESCRIPTION_NOT_FOUND",
                message="처방 정보를 찾을 수 없습니다.",
                details=[ErrorDetail(field="prescription_id", reason="NOT_FOUND", rejected_value=str(prescription_id))],
            )

        chat_session = await self._chat_repo.create_session(prescription=prescription)
        return ChatSessionData(
            session_id=chat_session.id,
            prescription_id=prescription.id,
            session_status=str(chat_session.session_status),
            created_at=chat_session.created_at,
        )

    async def list_messages(self, *, user: User, session_id: UUID) -> list[ChatMessageData]:
        chat_session = await self._chat_repo.get_session_owned(session_id=session_id, user_id=user.id)
        if chat_session is None:
            raise ApiError(
                status_code=404,
                code="CHAT_SESSION_NOT_FOUND",
                message="대화 세션을 찾을 수 없습니다.",
                details=[ErrorDetail(field="session_id", reason="NOT_FOUND", rejected_value=str(session_id))],
            )
        messages = await self._chat_repo.list_messages(session=chat_session)
        return [_to_message_data(message) for message in messages]

    async def send_message(
        self,
        *,
        user: User,
        session_id: UUID,
        request: SendChatMessageRequest,
    ) -> SendChatMessageData:
        # 실시간 복약 챗봇 응답 Backend 계약: 사용자 질문 저장 → OpenAI 단일 응답 생성 → 저장.
        chat_session = await self._chat_repo.get_session_owned(session_id=session_id, user_id=user.id)
        if chat_session is None:
            raise ApiError(
                status_code=404,
                code="CHAT_SESSION_NOT_FOUND",
                message="대화 세션을 찾을 수 없습니다.",
                details=[ErrorDetail(field="session_id", reason="NOT_FOUND", rejected_value=str(session_id))],
            )

        if chat_session.session_status != ChatSessionStatus.ACTIVE:
            raise ApiError(
                status_code=409,
                code="CONFLICT",
                message="종료된 대화 세션에는 메시지를 추가할 수 없습니다.",
                details=[ErrorDetail(field="session_id", reason="CHAT_SESSION_CLOSED", rejected_value=str(session_id))],
            )

        next_seq = await self._chat_repo.next_seq(session=chat_session)
        user_message = await self._chat_repo.create_message(
            session=chat_session,
            message_seq=next_seq,
            role=ModelChatRole.USER,
            content=request.content,
            generation_status=ChatGenerationStatus.NOT_APPLICABLE,
        )

        assistant_message = await self._chat_repo.create_message(
            session=chat_session,
            message_seq=next_seq + 1,
            role=ModelChatRole.ASSISTANT,
            content=None,
            generation_status=ChatGenerationStatus.PENDING,
        )

        medications = await self._prescription_repo.get_medications(prescription_id=chat_session.prescription_id)
        chat_input = ChatReplyInput(
            prescription_id=chat_session.prescription_id,
            medications=[
                ChatMedicationInput(
                    medication_name=medication.medication_name,
                    dose_value=float(medication.dose_value) if medication.dose_value is not None else None,
                    dose_unit=medication.dose_unit,
                    frequency_per_day=medication.frequency_per_day,
                    timing_text=medication.timing_text,
                )
                for medication in medications
            ],
            content=request.content,
        )

        assistant_message = await self._chat_repo.mark_generating(assistant_message)

        try:
            result = await self._engine.reply(chat_input)
        except ChatServiceUnavailableError as err:
            await self._chat_repo.mark_failed(
                assistant_message,
                error_code="OPENAI_API_ERROR",
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=503,
                code="SERVICE_UNAVAILABLE",
                message="현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                details=[ErrorDetail(field="openai_api", reason="OPENAI_API_ERROR")],
            ) from err
        except ChatTimeoutError as err:
            await self._chat_repo.mark_failed(
                assistant_message,
                error_code="OPENAI_API_TIMEOUT",
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=504,
                code="GATEWAY_TIMEOUT",
                message="외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
                details=[ErrorDetail(field="openai_api", reason="OPENAI_API_TIMEOUT")],
            ) from err
        except Exception as err:
            await self._chat_repo.mark_failed(
                assistant_message,
                error_code="OPENAI_RESPONSE_PROCESSING_FAILED",
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=500,
                code="AI_RESPONSE_FAILED",
                message="AI 답변 생성에 실패했습니다.",
                details=[ErrorDetail(field="assistant_message", reason="OPENAI_RESPONSE_PROCESSING_FAILED")],
            ) from err

        completed_at = datetime.now(UTC)
        assistant_message = await self._chat_repo.mark_completed(
            assistant_message,
            content=result.content,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
            completed_at=completed_at,
        )
        await self._chat_repo.update_last_message_at(chat_session, last_message_at=completed_at)

        return SendChatMessageData(
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            session_id=chat_session.id,
            generation_status=str(assistant_message.generation_status),
            content=assistant_message.content,
            model_name=assistant_message.model_name,
            prompt_version=assistant_message.prompt_version,
            created_at=assistant_message.created_at,
            completed_at=assistant_message.completed_at,
        )
