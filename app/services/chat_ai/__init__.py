from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class ChatMedicationInput:
    medication_name: str
    dose_value: float | None
    dose_unit: str | None
    frequency_per_day: int | None
    timing_text: str | None


@dataclass(frozen=True)
class ChatReplyInput:
    prescription_id: UUID
    medications: list[ChatMedicationInput]
    content: str


@dataclass(frozen=True)
class ChatReplyOutput:
    content: str
    model_name: str
    prompt_version: str


class ChatServiceUnavailableError(Exception):
    """챗봇 LLM 호출 자체가 실패했을 때 발생합니다. (-> 503 SERVICE_UNAVAILABLE)"""


class ChatTimeoutError(Exception):
    """챗봇 응답 대기 시간이 초과됐을 때 발생합니다. (-> 504 GATEWAY_TIMEOUT)"""


class ChatEngine(Protocol):
    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput: ...


class NotConfiguredChatEngine:
    """
    TODO(정현우): 실시간 복약 챗봇 응답 Backend 계약 기준 OpenAI Responses API 연동.
    - 입력은 현재 질문(content)과 세션에 연결된 확정 처방·약물 정보(ChatReplyInput)만 사용합니다.
      MVP 범위에서는 이전 대화 문맥을 사용하지 않습니다.
    - 성공 시 ChatReplyOutput(content=답변, model_name=실제 사용 모델 ID, prompt_version=프롬프트 버전)을 반환합니다.
    - 호출 실패 시 ChatServiceUnavailableError, 타임아웃 시 ChatTimeoutError를 발생시켜야
      ChatService가 명세된 503/504 오류로 변환합니다.
    """

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        _ = chat_input
        raise NotImplementedError("ChatEngine 구현이 아직 연결되지 않았습니다.")
