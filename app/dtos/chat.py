from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class ChatRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class ChatSessionData(BaseModel):
    session_id: UUID
    prescription_id: UUID
    session_status: str
    created_at: datetime


class ChatSessionResponse(BaseModel):
    data: ChatSessionData


class ChatMessageData(BaseModel):
    message_id: UUID
    role: ChatRole
    content: str | None = None
    generation_status: str
    created_at: datetime


class ChatMessageListData(BaseModel):
    session_id: UUID
    messages: list[ChatMessageData] = Field(default_factory=list)


class ChatMessageListResponse(BaseModel):
    data: ChatMessageListData


class SendChatMessageRequest(BaseModel):
    content: str = Field(min_length=1)


# 실시간 복약 챗봇 응답 Backend 계약: USER·ASSISTANT 메시지 생성 결과를 하나의 평면 구조로 반환합니다.
class SendChatMessageData(BaseModel):
    user_message_id: UUID
    assistant_message_id: UUID
    session_id: UUID
    generation_status: str
    content: str | None
    model_name: str | None
    prompt_version: str | None
    created_at: datetime
    completed_at: datetime | None


class SendChatMessageResponse(BaseModel):
    data: SendChatMessageData
