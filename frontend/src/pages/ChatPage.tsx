import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  createChatSession,
  getChatMessages,
  sendChatMessage,
  type ChatMessageData,
} from '../api/chat'
import { ApiError } from '../api/client'
import { Button, Card, MobileShell, StatusBadge } from '../design-system/components'
import '../design-system/prototype.css'
import './ChatPage.css'

const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const sessionCreationRequests = new Map<
  string,
  ReturnType<typeof createChatSession>
>()

function getSessionStorageKey(prescriptionId: string) {
  return `dosey_chat_session:${prescriptionId}`
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback
}

function createChatSessionOnce(prescriptionId: string) {
  const pendingRequest = sessionCreationRequests.get(prescriptionId)
  if (pendingRequest) return pendingRequest

  const request = createChatSession(prescriptionId).finally(() => {
    if (sessionCreationRequests.get(prescriptionId) === request) {
      sessionCreationRequests.delete(prescriptionId)
    }
  })
  sessionCreationRequests.set(prescriptionId, request)
  return request
}

function ChatPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const prescriptionId = searchParams.get('prescription_id')?.trim() ?? ''
  const activePrescriptionRef = useRef(prescriptionId)
  const initializationRequestRef = useRef(0)
  const sendRequestRef = useRef(0)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const [statePrescriptionId, setStatePrescriptionId] = useState(prescriptionId)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessageData[]>([])
  const [draft, setDraft] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [isSending, setIsSending] = useState(false)
  const [requiresLogin, setRequiresLogin] = useState(false)

  activePrescriptionRef.current = prescriptionId

  const initializeChat = useCallback(async () => {
    const requestedPrescriptionId = prescriptionId
    const requestId = ++initializationRequestRef.current
    const isCurrentRequest = () =>
      initializationRequestRef.current === requestId &&
      activePrescriptionRef.current === requestedPrescriptionId

    setStatePrescriptionId(requestedPrescriptionId)
    setSessionId(null)
    setMessages([])
    setDraft('')
    setErrorMessage('')
    setIsSending(false)
    setRequiresLogin(false)

    if (!uuidPattern.test(prescriptionId)) {
      setIsLoading(false)
      return
    }

    try {
      setIsLoading(true)
      setErrorMessage('')
      setMessages([])

      const storageKey = getSessionStorageKey(prescriptionId)
      const storedSessionId = sessionStorage.getItem(storageKey)
      let activeSessionId = storedSessionId

      if (!activeSessionId) {
        const sessionResponse = await createChatSessionOnce(prescriptionId)
        if (!isCurrentRequest()) return
        activeSessionId = sessionResponse.data.session_id
        sessionStorage.setItem(storageKey, activeSessionId)
      }

      let historyResponse
      try {
        historyResponse = await getChatMessages(activeSessionId)
        if (!isCurrentRequest()) return
      } catch (error) {
        if (!isCurrentRequest()) return
        if (!(storedSessionId && error instanceof ApiError && error.status === 404)) {
          throw error
        }

        sessionStorage.removeItem(storageKey)
        const sessionResponse = await createChatSessionOnce(prescriptionId)
        if (!isCurrentRequest()) return
        activeSessionId = sessionResponse.data.session_id
        sessionStorage.setItem(storageKey, activeSessionId)
        historyResponse = await getChatMessages(activeSessionId)
        if (!isCurrentRequest()) return
      }

      setSessionId(activeSessionId)
      setMessages(historyResponse.data.messages)
    } catch (error) {
      if (!isCurrentRequest()) return
      if (error instanceof ApiError && error.status === 401) {
        setRequiresLogin(true)
      }
      setSessionId(null)
      setErrorMessage(
        getErrorMessage(error, '복약 대화를 시작하는 중 오류가 발생했습니다.'),
      )
    } finally {
      if (isCurrentRequest()) {
        setIsLoading(false)
      }
    }
  }, [prescriptionId])

  useEffect(() => {
    sendRequestRef.current += 1
    void initializeChat()

    return () => {
      initializationRequestRef.current += 1
      sendRequestRef.current += 1
    }
  }, [initializeChat])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [isSending, messages])

  const isCurrentPrescriptionState = statePrescriptionId === prescriptionId
  const currentSessionId = isCurrentPrescriptionState ? sessionId : null
  const currentMessages = isCurrentPrescriptionState ? messages : []
  const currentDraft = isCurrentPrescriptionState ? draft : ''
  const currentErrorMessage = isCurrentPrescriptionState ? errorMessage : ''
  const currentIsLoading = isCurrentPrescriptionState ? isLoading : true
  const currentIsSending = isCurrentPrescriptionState ? isSending : false
  const currentRequiresLogin = isCurrentPrescriptionState && requiresLogin

  const handleSend = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const content = currentDraft.trim()
    if (!content || !currentSessionId || currentIsSending) return

    const requestedPrescriptionId = prescriptionId
    const requestedSessionId = currentSessionId
    const requestId = ++sendRequestRef.current
    const isCurrentRequest = () =>
      sendRequestRef.current === requestId &&
      activePrescriptionRef.current === requestedPrescriptionId

    try {
      setIsSending(true)
      setErrorMessage('')
      const response = await sendChatMessage(requestedSessionId, content)
      if (!isCurrentRequest()) return
      const completedAt = response.data.completed_at ?? response.data.created_at

      setMessages((current) => [
        ...current,
        {
          message_id: response.data.user_message_id,
          role: 'USER',
          content,
          generation_status: 'NOT_APPLICABLE',
          created_at: response.data.created_at,
        },
        {
          message_id: response.data.assistant_message_id,
          role: 'ASSISTANT',
          content: response.data.content,
          generation_status: response.data.generation_status,
          created_at: completedAt,
        },
      ])
      setDraft('')
    } catch (error) {
      if (!isCurrentRequest()) return
      if (error instanceof ApiError && error.status === 401) {
        setRequiresLogin(true)
      } else {
        try {
          const historyResponse = await getChatMessages(requestedSessionId)
          if (!isCurrentRequest()) return
          setMessages(historyResponse.data.messages)
        } catch {
          if (!isCurrentRequest()) return
        }
      }
      setDraft('')
      setErrorMessage(
        getErrorMessage(error, 'AI 답변을 받는 중 오류가 발생했습니다.'),
      )
    } finally {
      if (isCurrentRequest()) {
        setIsSending(false)
      }
    }
  }

  if (currentRequiresLogin) {
    return (
      <div className="chat-page">
        <MobileShell title="Dosey 도지" hideNavigation>
          <main className="app-scroll chat-page__gate">
            <Card>
              <StatusBadge tone="attention">로그인 필요</StatusBadge>
              <h1>로그인 후 복약 챗봇을 이용해 주세요</h1>
              <p>확정된 처방에 연결된 대화만 안전하게 불러옵니다.</p>
              <Button fullWidth onClick={() => navigate('/login')}>
                로그인
              </Button>
            </Card>
          </main>
        </MobileShell>
      </div>
    )
  }

  if (!prescriptionId || !uuidPattern.test(prescriptionId)) {
    return (
      <div className="chat-page">
        <MobileShell
          title="Dosey 도지"
          onBack={() => navigate('/')}
          hideNavigation
        >
          <main className="app-scroll chat-page__gate">
            <Card>
              <StatusBadge tone="attention">처방 확인 필요</StatusBadge>
              <h1>연결할 확정 처방이 없어요</h1>
              <p>복약 가이드에서 확인된 처방을 선택해 다시 들어와 주세요.</p>
              <Button
                fullWidth
                variant="secondary"
                onClick={() => navigate('/')}
              >
                홈으로 돌아가기
              </Button>
            </Card>
          </main>
        </MobileShell>
      </div>
    )
  }

  return (
    <div className="chat-page">
      <MobileShell
        title="복약 챗봇"
        onBack={() => navigate(-1)}
        hideNavigation
      >
        <main className="chat-layout">
          <div className="chat-page__context">
            <strong>확정된 처방 기준</strong>
            <span>AI가 답변을 만드는 동안 잠시 기다려 주세요.</span>
          </div>

          <div className="chat-messages" aria-live="polite">
            {currentIsLoading && (
              <div className="chat-page__state" role="status">
                대화를 불러오고 있어요.
              </div>
            )}

            {!currentIsLoading &&
              !currentErrorMessage &&
              currentMessages.length === 0 && (
              <div className="chat-page__state chat-page__empty">
                <strong>아직 대화가 없어요</strong>
                <span>확정된 처방에 대해 궁금한 내용을 입력해 주세요.</span>
              </div>
            )}

            {currentMessages.map((message) => (
              <div
                className={`chat-message ${message.role === 'USER' ? 'user' : ''}`}
                key={message.message_id}
              >
                {message.content ?? '답변을 생성하지 못했어요.'}
              </div>
            ))}

            {currentIsSending && (
              <div className="chat-message chat-page__typing" role="status">
                AI 답변을 만들고 있어요…
              </div>
            )}

            {currentErrorMessage && (
              <Card className="chat-page__error">
                <StatusBadge tone="attention">오류</StatusBadge>
                <p role="alert">{currentErrorMessage}</p>
                <Button
                  fullWidth
                  variant="secondary"
                  onClick={() => void initializeChat()}
                  disabled={currentIsSending}
                >
                  대화 다시 불러오기
                </Button>
              </Card>
            )}
            <div ref={messagesEndRef} />
          </div>

          <form className="chat-composer" onSubmit={handleSend}>
            <textarea
              className="chat-input"
              value={currentDraft}
              onChange={(event) => setDraft(event.target.value)}
              aria-label="복약 질문"
              placeholder="복약 질문을 입력해 주세요"
              rows={1}
              disabled={
                currentIsLoading || currentIsSending || !currentSessionId
              }
            />
            <button
              className="chat-send"
              type="submit"
              aria-label="질문 전송"
              disabled={
                currentIsLoading ||
                currentIsSending ||
                !currentSessionId ||
                !currentDraft.trim()
              }
            >
              ↑
            </button>
          </form>
        </main>
      </MobileShell>
    </div>
  )
}

export default ChatPage
