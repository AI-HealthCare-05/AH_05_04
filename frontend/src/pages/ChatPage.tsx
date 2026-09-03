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
const optimisticUserMessageIdPrefix = 'optimistic-user-'

function isOptimisticUserMessage(message: ChatMessageData) {
  return (
    message.role === 'USER' &&
    message.message_id.startsWith(optimisticUserMessageIdPrefix)
  )
}

function reconcileHistoryMessages(
  currentMessages: ChatMessageData[],
  historyMessages: ChatMessageData[],
  knownMessageIds: Set<string>,
) {
  const optimisticUserMessages = currentMessages.filter(
    isOptimisticUserMessage,
  )
  const unmatchedCanonicalUsers = historyMessages.filter(
    (message) =>
      message.role === 'USER' && !knownMessageIds.has(message.message_id),
  )
  const canonicalIdByOptimisticId = new Map<string, string>()

  for (let index = optimisticUserMessages.length - 1; index >= 0; index -= 1) {
    const optimisticMessage = optimisticUserMessages[index]
    let canonicalIndex = -1

    for (
      let candidateIndex = unmatchedCanonicalUsers.length - 1;
      candidateIndex >= 0;
      candidateIndex -= 1
    ) {
      if (
        unmatchedCanonicalUsers[candidateIndex].content ===
        optimisticMessage.content
      ) {
        canonicalIndex = candidateIndex
        break
      }
    }

    if (canonicalIndex === -1) continue

    const [canonicalMessage] = unmatchedCanonicalUsers.splice(
      canonicalIndex,
      1,
    )
    canonicalIdByOptimisticId.set(
      optimisticMessage.message_id,
      canonicalMessage.message_id,
    )
  }

  const mergedMessages = [...historyMessages]
  const historyMessageIds = new Set(
    historyMessages.map((message) => message.message_id),
  )

  for (const optimisticMessage of optimisticUserMessages) {
    if (canonicalIdByOptimisticId.has(optimisticMessage.message_id)) continue

    const currentIndex = currentMessages.findIndex(
      (message) => message.message_id === optimisticMessage.message_id,
    )
    let nextHistoryMessageId: string | undefined

    for (
      let index = currentIndex + 1;
      index < currentMessages.length;
      index += 1
    ) {
      const nextMessage = currentMessages[index]
      const candidateId = isOptimisticUserMessage(nextMessage)
        ? canonicalIdByOptimisticId.get(nextMessage.message_id)
        : nextMessage.message_id

      if (candidateId && historyMessageIds.has(candidateId)) {
        nextHistoryMessageId = candidateId
        break
      }
    }

    if (!nextHistoryMessageId) {
      mergedMessages.push(optimisticMessage)
      continue
    }

    const insertionIndex = mergedMessages.findIndex(
      (message) => message.message_id === nextHistoryMessageId,
    )
    mergedMessages.splice(insertionIndex, 0, optimisticMessage)
  }

  return mergedMessages
}

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
    const knownMessageIds = new Set(
      currentMessages.map((message) => message.message_id),
    )
    const optimisticUserMessage: ChatMessageData = {
      message_id: `${optimisticUserMessageIdPrefix}${requestId}`,
      role: 'USER',
      content,
      generation_status: 'NOT_APPLICABLE',
      created_at: new Date().toISOString(),
    }
    const isCurrentRequest = () =>
      sendRequestRef.current === requestId &&
      activePrescriptionRef.current === requestedPrescriptionId

    try {
      setIsSending(true)
      setErrorMessage('')
      setDraft('')
      setMessages((current) => [...current, optimisticUserMessage])
      const response = await sendChatMessage(requestedSessionId, content)
      if (!isCurrentRequest()) return
      const completedAt = response.data.completed_at ?? response.data.created_at
      const canonicalUserMessage: ChatMessageData = {
        ...optimisticUserMessage,
        message_id: response.data.user_message_id,
        created_at: response.data.created_at,
      }

      setMessages((current) => {
        const reconciledMessages = current.map((message) =>
          message.message_id === optimisticUserMessage.message_id
            ? canonicalUserMessage
            : message,
        )

        return [
          ...reconciledMessages,
          {
            message_id: response.data.assistant_message_id,
            role: 'ASSISTANT',
            content: response.data.content,
            generation_status: response.data.generation_status,
            created_at: completedAt,
          },
        ]
      })
    } catch (error) {
      if (!isCurrentRequest()) return
      if (error instanceof ApiError && error.status === 401) {
        setRequiresLogin(true)
      } else {
        try {
          const historyResponse = await getChatMessages(requestedSessionId)
          if (!isCurrentRequest()) return
          const historyMessages = historyResponse.data.messages
          setMessages((current) =>
            reconcileHistoryMessages(
              current,
              historyMessages,
              knownMessageIds,
            ),
          )
        } catch {
          if (!isCurrentRequest()) return
        }
      }
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
        <MobileShell
          title="Dosey 도지"
          onBack={() => navigate('/start')}
          backPlacement="content"
          hideNavigation
        >
          <main className="app-scroll chat-page__gate">
            <Card>
              <StatusBadge tone="attention">로그인 필요</StatusBadge>
              <h1>로그인 후 복약 챗봇을 이용해 주세요</h1>
              <p>로그인 후 처방에 연결된 대화를 불러올 수 있어요.</p>
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
          backPlacement="content"
          hideNavigation
        >
          <main className="app-scroll chat-page__gate chat-page__gate--guide-required">
            <h1>먼저 확정된 처방이 필요해요</h1>
            <p>
              처방전을 등록하고 인식 결과를 확인한 뒤 이용할 수 있어요.
            </p>
            <div className="notice attention chat-page__gate-notice">
              현재 확인된 처방이 없어요. 처방전을 등록하고 인식 결과를 직접
              확인해 주세요.
            </div>
            <Button fullWidth onClick={() => navigate('/prescriptions/upload')}>
              처방전 등록하기
            </Button>
          </main>
        </MobileShell>
      </div>
    )
  }

  return (
    <div className="chat-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate(-1)}
        backPlacement="content"
        hideNavigation
      >
        <main className="chat-layout">
          <header className="chat-page__intro">
            <h1>복약 챗봇</h1>
            <p>확정된 처방 정보와 현재 질문으로 답변을 생성해요.</p>
          </header>

          <div className="chat-page__suggestions" aria-label="추천 질문">
            <button type="button" disabled>복용 방법 확인</button>
            <button type="button" disabled>놓친 복용 안내</button>
            <button type="button" disabled>불편·안전 확인</button>
          </div>
          <p className="chat-page__suggestions-notice" role="status">
            추천 질문 기능은 준비 중이에요.
          </p>

          <div className="chat-page__conversation">
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
                  <div className="chat-page__empty-card">
                    <strong>무엇을 확인하고 싶으신가요?</strong>
                    <span>
                      확정된 처방에 대해 궁금한 내용을 입력해 주세요.
                    </span>
                  </div>
                  <div className="chat-page__empty-callout">
                    복약 질문을 입력해 주세요
                  </div>
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
              <label className="chat-page__composer-label" htmlFor="dosey-chat-input">
                복약 질문
              </label>
              <textarea
                id="dosey-chat-input"
                className="chat-input"
                value={currentDraft}
                onChange={(event) => setDraft(event.target.value)}
                aria-label="복약 질문"
                placeholder="궁금한 내용을 입력하세요"
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
                <span className="chat-send__icon" aria-hidden="true" />
              </button>
            </form>
          </div>
        </main>
      </MobileShell>
    </div>
  )
}

export default ChatPage
