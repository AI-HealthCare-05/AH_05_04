import { apiRequest } from './client'

export type ChatRole = 'USER' | 'ASSISTANT'
export type ChatGenerationStatus =
  | 'NOT_APPLICABLE'
  | 'PENDING'
  | 'GENERATING'
  | 'COMPLETED'
  | 'FAILED'

export type ChatSessionResponse = {
  data: {
    session_id: string
    prescription_id: string
    session_status: string
    created_at: string
  }
}

export type ChatMessageData = {
  message_id: string
  role: ChatRole
  content: string | null
  generation_status: ChatGenerationStatus
  created_at: string
}

export type ChatMessageListResponse = {
  data: {
    session_id: string
    messages: ChatMessageData[]
  }
}

export type SendChatMessageResponse = {
  data: {
    user_message_id: string
    assistant_message_id: string
    session_id: string
    generation_status: ChatGenerationStatus
    content: string | null
    model_name: string | null
    prompt_version: string | null
    created_at: string
    completed_at: string | null
  }
}

export async function createChatSession(
  prescriptionId: string,
): Promise<ChatSessionResponse> {
  return apiRequest<ChatSessionResponse>(
    `/api/v1/prescriptions/${prescriptionId}/chat-sessions`,
    { method: 'POST' },
  )
}

export async function getChatMessages(
  sessionId: string,
): Promise<ChatMessageListResponse> {
  return apiRequest<ChatMessageListResponse>(
    `/api/v1/chat-sessions/${sessionId}/messages`,
  )
}

export async function sendChatMessage(
  sessionId: string,
  content: string,
): Promise<SendChatMessageResponse> {
  return apiRequest<SendChatMessageResponse>(
    `/api/v1/chat-sessions/${sessionId}/messages`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    },
  )
}
