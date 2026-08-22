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
    ChatGenerationFailedError,
    ChatMedicationInput,
    ChatReplyInput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
)

_TIMEOUT_ERROR_MESSAGE = "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다."
_UNAVAILABLE_ERROR_MESSAGE = "OpenAI 서비스 호출에 실패했습니다."
_GENERATION_FAILED_ERROR_MESSAGE = "챗봇 응답 생성 처리 중 오류가 발생했습니다."


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
        engine: ChatEngine,
    ) -> None:
        self._engine = engine
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
        chat_session = await self._chat_repo.get_session_owned_for_update(session_id=session_id, user_id=user.id)
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

        medications = await self._prescription_repo.get_medications(prescription_id=chat_session.prescription_id)
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

        chat_input = ChatReplyInput(
            prescription_id=chat_session.prescription_id,
            medications=[
                ChatMedicationInput(
                    medication_name=medication.medication_name,
                    dose_value=medication.dose_value,
                    dose_unit=medication.dose_unit,
                    frequency_per_day=medication.frequency_per_day,
                    timing_text=medication.timing_text,
                    duration_days=medication.duration_days,
                )
                for medication in medications
            ],
            content=request.content,
        )

        assistant_message = await self._chat_repo.mark_generating(assistant_message)

        failure_metadata: tuple[str, str] | None = None
        api_error: ApiError | None = None
        result = None
        try:
            result = await self._engine.reply(chat_input)
        except ChatTimeoutError:
            failure_metadata = ("OPENAI_API_TIMEOUT", _TIMEOUT_ERROR_MESSAGE)
            api_error = ApiError(
                status_code=504,
                code="GATEWAY_TIMEOUT",
                message="외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
                details=[ErrorDetail(field="openai_api", reason="OPENAI_API_TIMEOUT")],
            )
        except ChatServiceUnavailableError:
            failure_metadata = ("OPENAI_API_ERROR", _UNAVAILABLE_ERROR_MESSAGE)
            api_error = ApiError(
                status_code=503,
                code="SERVICE_UNAVAILABLE",
                message="현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                details=[ErrorDetail(field="openai_api", reason="OPENAI_API_ERROR")],
            )
        except ChatGenerationFailedError:
            failure_metadata = ("OPENAI_RESPONSE_PROCESSING_FAILED", _GENERATION_FAILED_ERROR_MESSAGE)
            api_error = ApiError(
                status_code=500,
                code="AI_RESPONSE_FAILED",
                message="AI 답변 생성에 실패했습니다.",
                details=[ErrorDetail(field="assistant_message", reason="OPENAI_RESPONSE_PROCESSING_FAILED")],
            )
        except Exception:
            failure_metadata = ("OPENAI_RESPONSE_PROCESSING_FAILED", _GENERATION_FAILED_ERROR_MESSAGE)
            api_error = ApiError(
                status_code=500,
                code="AI_RESPONSE_FAILED",
                message="AI 답변 생성에 실패했습니다.",
                details=[ErrorDetail(field="assistant_message", reason="OPENAI_RESPONSE_PROCESSING_FAILED")],
            )

        if failure_metadata is not None and api_error is not None:
            error_code, error_message = failure_metadata
            await self._chat_repo.commit_failed_message_pair(
                assistant_message,
                error_code=error_code,
                error_message=error_message,
                completed_at=datetime.now(UTC),
            )
            raise api_error

        assert result is not None
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
