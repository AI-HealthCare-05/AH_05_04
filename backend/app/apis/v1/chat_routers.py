from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse as Response

from app.dependencies.security import get_request_user
from app.dependencies.services import get_chat_service
from app.dtos.chat import (
    ChatMessageListData,
    ChatMessageListResponse,
    SendChatMessageRequest,
    SendChatMessageResponse,
)
from app.models.users import User
from app.services.chat import ChatService

chat_router = APIRouter(prefix="/chat-sessions", tags=["chat"])


@chat_router.get(
    "/{session_id}/messages",
    response_model=ChatMessageListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_chat_messages(
    session_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
) -> Response:
    # 채팅 메시지 목록 조회 Backend 계약: 세션의 사용자 질문과 AI 응답 이력을 조회합니다.
    # Cache-Control: no-store는 NoStoreMiddleware가 /api/v1/* 전체에 일괄 적용합니다.
    messages = await chat_service.list_messages(user=user, session_id=session_id)
    return Response(
        content=ChatMessageListResponse(data=ChatMessageListData(session_id=session_id, messages=messages)).model_dump(
            mode="json"
        ),
        status_code=status.HTTP_200_OK,
    )


@chat_router.post(
    "/{session_id}/messages",
    response_model=SendChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_chat_message(
    session_id: UUID,
    request: SendChatMessageRequest,
    user: Annotated[User, Depends(get_request_user)],
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
) -> Response:
    # 실시간 복약 챗봇 응답 Backend 계약: 사용자 질문을 저장하고 같은 요청 안에서 AI 응답을 생성합니다.
    result = await chat_service.send_message(user=user, session_id=session_id, request=request)
    return Response(
        content=SendChatMessageResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED,
    )
