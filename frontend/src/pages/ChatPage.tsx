import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import type { NavigateFunction } from 'react-router-dom'
import {
  createChatSession,
  getChatMessages,
  getChatSessionForPrescription,
  sendChatMessage,
  type ChatMessageData,
} from '../api/chat'
import { ApiError } from '../api/client'
import { getLatestPrescription } from '../api/prescriptions'
import { Button, Card, MobileShell, StatusBadge } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import {
  clearAuthenticatedSession,
  isStaleTokenError,
} from '../features/auth/authSession'
import { AssistantMessageContent } from './AssistantMessageContent'
import '../design-system/prototype.css'
import './ChatPage.css'
import { ResponseFeedback } from '../components/ResponseFeedback'
import {
  getChatGuideErrorPresentation,
  type ChatGuideErrorPresentation,
} from './chatGuideErrorPresentation'

export type ChatPageServices = {
  createChatSession: typeof createChatSession
  getChatSessionForPrescription: typeof getChatSessionForPrescription
  getChatMessages: typeof getChatMessages
  sendChatMessage: typeof sendChatMessage
  getLatestPrescription: typeof getLatestPrescription
}

export type ChatPreviewState = {
  prescriptionId: string
  draft?: string
  isSending?: boolean
  visibleMessages?: ChatMessageData[]
}

export type ChatPageProps = {
  services?: ChatPageServices
  previewState?: ChatPreviewState
  navigation?: NavigateFunction
}

type ChatErrorRecovery = 'INITIALIZE' | 'REFRESH' | 'RESEND' | null

type ChatErrorPresentation = ChatGuideErrorPresentation & {
  recovery: ChatErrorRecovery
}

const defaultChatPageServices: ChatPageServices = {
  createChatSession,
  getChatSessionForPrescription,
  getChatMessages,
  sendChatMessage,
  getLatestPrescription,
}

const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const optimisticUserMessageIdPrefix = 'optimistic-user-'
const questionPresets = [
  '아침 약은 언제 먹나요?',
  '복용을 잊었어요',
  '약을 함께 먹어도 되나요?',
] as const

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

  for (const currentMessage of currentMessages) {
    if (
      historyMessageIds.has(currentMessage.message_id) ||
      canonicalIdByOptimisticId.has(currentMessage.message_id)
    ) {
      continue
    }

    const currentIndex = currentMessages.findIndex(
      (message) => message.message_id === currentMessage.message_id,
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
      mergedMessages.push(currentMessage)
      historyMessageIds.add(currentMessage.message_id)
      continue
    }

    const insertionIndex = mergedMessages.findIndex(
      (message) => message.message_id === nextHistoryMessageId,
    )
    mergedMessages.splice(insertionIndex, 0, currentMessage)
    historyMessageIds.add(currentMessage.message_id)
  }

  return mergedMessages
}

const sessionCreationRequests = new WeakMap<
  typeof createChatSession,
  Map<string, ReturnType<typeof createChatSession>>
>()

const sessionRediscoveryRequests = new WeakMap<
  typeof getChatSessionForPrescription,
  Map<string, ReturnType<typeof getChatSessionForPrescription>>
>()

function getErrorPresentation(error: unknown, fallback: string) {
  return getChatGuideErrorPresentation(error, {
    unauthorized: '로그인 정보를 다시 확인한 뒤 시도해 주세요.',
    notFound: '대화 정보를 찾지 못했어요. 다시 불러와 주세요.',
    server: '도지와 연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요.',
    network: '네트워크 연결을 확인한 뒤 다시 시도해 주세요.',
    unknown: fallback,
  })
}

function createChatSessionOnce(
  prescriptionId: string,
  createSession: typeof createChatSession,
) {
  let requestsByPrescription = sessionCreationRequests.get(createSession)
  if (!requestsByPrescription) {
    requestsByPrescription = new Map()
    sessionCreationRequests.set(createSession, requestsByPrescription)
  }

  const pendingRequest = requestsByPrescription.get(prescriptionId)
  if (pendingRequest) return pendingRequest

  const request = createSession(prescriptionId).finally(() => {
    if (requestsByPrescription.get(prescriptionId) === request) {
      requestsByPrescription.delete(prescriptionId)
      if (requestsByPrescription.size === 0) {
        sessionCreationRequests.delete(createSession)
      }
    }
  })
  requestsByPrescription.set(prescriptionId, request)
  return request
}

function getChatSessionForPrescriptionOnce(
  prescriptionId: string,
  getSession: typeof getChatSessionForPrescription,
) {
  let requestsByPrescription = sessionRediscoveryRequests.get(getSession)
  if (!requestsByPrescription) {
    requestsByPrescription = new Map()
    sessionRediscoveryRequests.set(getSession, requestsByPrescription)
  }

  const pendingRequest = requestsByPrescription.get(prescriptionId)
  if (pendingRequest) return pendingRequest

  const request = getSession(prescriptionId).finally(() => {
    if (requestsByPrescription.get(prescriptionId) === request) {
      requestsByPrescription.delete(prescriptionId)
      if (requestsByPrescription.size === 0) {
        sessionRediscoveryRequests.delete(getSession)
      }
    }
  })
  requestsByPrescription.set(prescriptionId, request)
  return request
}

function ChatPage({
  services = defaultChatPageServices,
  previewState,
  navigation,
}: ChatPageProps = {}) {
  const routerNavigate = useNavigate()
  const navigate = navigation ?? routerNavigate
  const [searchParams] = useSearchParams()
  const prescriptionId = previewState?.prescriptionId ??
    searchParams.get('prescription_id')?.trim() ?? ''
  const activePrescriptionRef = useRef(prescriptionId)
  const initializationRequestRef = useRef(0)
  const sendRequestRef = useRef(0)
  const retryableSendRef = useRef<{
    content: string
    optimisticUserMessage: ChatMessageData
  } | null>(null)
  const initialHistoryMessageIdsRef = useRef<Set<string>>(new Set())
  const hasInitialHistorySnapshotRef = useRef(false)
  const initialHistorySessionIdRef = useRef<string | null>(null)
  const compositionStateRef = useRef<'idle' | 'composing' | 'ended'>('idle')
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const [stateRoutePrescriptionId, setStateRoutePrescriptionId] = useState(
    prescriptionId,
  )
  const [statePrescriptionId, setStatePrescriptionId] = useState(prescriptionId)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessageData[]>([])
  const [draft, setDraft] = useState(previewState?.draft ?? '')
  const [errorPresentation, setErrorPresentation] =
    useState<ChatErrorPresentation | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSending, setIsSending] = useState(previewState?.isSending ?? false)
  const [requiresLogin, setRequiresLogin] = useState(false)

  const initializeChat = useCallback(async (
    preserveCurrentVisit = false,
    allowSessionCreation = true,
  ) => {
    const requestedRoutePrescriptionId = prescriptionId
    let requestedPrescriptionId = requestedRoutePrescriptionId
    const requestId = ++initializationRequestRef.current

    activePrescriptionRef.current = requestedPrescriptionId

    const isCurrentRequest = () =>
      initializationRequestRef.current === requestId &&
      activePrescriptionRef.current === requestedPrescriptionId

    setStateRoutePrescriptionId(requestedRoutePrescriptionId)
    setStatePrescriptionId(requestedPrescriptionId)
    setSessionId(null)
    if (!preserveCurrentVisit) {
      setMessages([])
      initialHistoryMessageIdsRef.current = new Set()
      hasInitialHistorySnapshotRef.current = false
      initialHistorySessionIdRef.current = null
    }
    setDraft(previewState?.draft ?? '')
    setErrorPresentation(null)
    retryableSendRef.current = null
    setIsSending(previewState?.isSending ?? false)
    setRequiresLogin(false)

    try {
      setIsLoading(true)

      if (!requestedPrescriptionId) {
        try {
          const latestResponse = await services.getLatestPrescription()
          if (initializationRequestRef.current !== requestId) return

          requestedPrescriptionId = latestResponse.data.prescription_id

          activePrescriptionRef.current = requestedPrescriptionId
          setStatePrescriptionId(requestedPrescriptionId)

          if (!uuidPattern.test(requestedPrescriptionId)) {
            setIsLoading(false)
            return
          }

          navigate(
            `/chat?prescription_id=${encodeURIComponent(requestedPrescriptionId)}`,
            { replace: true },
          )
          return
        } catch (error) {
          if (initializationRequestRef.current !== requestId) return

          if (
            error instanceof ApiError &&
            error.status === 404 &&
            error.code === 'PRESCRIPTION_NOT_FOUND'
          ) {
            setStatePrescriptionId('')
            setSessionId(null)
            setMessages([])
            setErrorPresentation(null)
            setIsLoading(false)
            return
          }

          throw error
        }
      }

      if (!uuidPattern.test(requestedPrescriptionId)) {
        setIsLoading(false)
        return
      }

      let sessionResponse

      try {
        sessionResponse = await getChatSessionForPrescriptionOnce(
          requestedPrescriptionId,
          services.getChatSessionForPrescription,
        )
      } catch (error) {
        if (!isCurrentRequest()) return

        if (
          !(
            error instanceof ApiError &&
            error.status === 404 &&
            error.code === 'CHAT_SESSION_NOT_FOUND'
          )
        ) {
          throw error
        }

        if (!allowSessionCreation) throw error

        sessionResponse = await createChatSessionOnce(
          requestedPrescriptionId,
          services.createChatSession,
        )
      }

      if (!isCurrentRequest()) return

      const activeSessionId = sessionResponse.data.session_id

      const historyResponse = await services.getChatMessages(activeSessionId)

      if (!isCurrentRequest()) return

      setSessionId(activeSessionId)
      if (
        preserveCurrentVisit &&
        hasInitialHistorySnapshotRef.current &&
        initialHistorySessionIdRef.current === activeSessionId
      ) {
        const currentVisitHistory = historyResponse.data.messages.filter(
          (message) =>
            !initialHistoryMessageIdsRef.current.has(message.message_id),
        )
        setMessages((current) =>
          reconcileHistoryMessages(
            current,
            currentVisitHistory,
            new Set(current.map((message) => message.message_id)),
          ),
        )
      } else {
        initialHistoryMessageIdsRef.current = new Set(
          historyResponse.data.messages.map((message) => message.message_id),
        )
        hasInitialHistorySnapshotRef.current = true
        initialHistorySessionIdRef.current = activeSessionId
        setMessages(previewState?.visibleMessages ?? [])
      }
    } catch (error) {
      if (!isCurrentRequest()) return

      if (isStaleTokenError(error)) {
        clearAuthenticatedSession()
        setRequiresLogin(true)
        return
      }

      setSessionId(null)
      const presentation = getErrorPresentation(
        error,
        '복약 대화를 시작하는 중 오류가 발생했습니다.',
      )
      const recovery: ChatErrorRecovery =
        presentation.action === 'CONSENT_SETTINGS'
          ? null
          : error instanceof ApiError &&
              (error.code === 'PRESCRIPTION_VERSION_STALE' ||
                error.code === 'CONSENT_POLICY_UNAVAILABLE')
            ? 'REFRESH'
            : 'INITIALIZE'
      setErrorPresentation({ ...presentation, recovery })
    } finally {
      if (isCurrentRequest()) {
        setIsLoading(false)
      }
    }
  }, [navigate, prescriptionId, previewState, services])

  useEffect(() => {
    sendRequestRef.current += 1
    void initializeChat()

    return () => {
      initializationRequestRef.current += 1
      sendRequestRef.current += 1
    }
  }, [initializeChat])

  useEffect(() => {
    if (previewState) return
    messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [isSending, messages, previewState])

  const isCurrentPrescriptionState =
    stateRoutePrescriptionId === prescriptionId
  const currentPrescriptionId = isCurrentPrescriptionState
    ? statePrescriptionId
    : ''
  const currentSessionId = isCurrentPrescriptionState ? sessionId : null
  const currentMessages = isCurrentPrescriptionState ? messages : []
  const currentDraft = isCurrentPrescriptionState ? draft : ''
  const currentErrorPresentation = isCurrentPrescriptionState
    ? errorPresentation
    : null
  const currentIsLoading = isCurrentPrescriptionState ? isLoading : true
  const currentIsSending = isCurrentPrescriptionState ? isSending : false
  const currentRequiresLogin = isCurrentPrescriptionState && requiresLogin
  const lastCurrentMessage = currentMessages.at(-1)
  const feedbackMessageId =
    !currentIsSending &&
    lastCurrentMessage?.role === 'ASSISTANT' &&
    lastCurrentMessage.generation_status === 'COMPLETED'
      ? lastCurrentMessage.message_id
      : null

  const sendChatContent = async (
    content: string,
    retryOptimisticMessage?: ChatMessageData,
  ) => {
    if (!content || !currentSessionId || currentIsSending) return
    const requestedPrescriptionId = currentPrescriptionId
    const requestedSessionId = currentSessionId
    const requestId = ++sendRequestRef.current
    const knownMessageIds = new Set(
      currentMessages.map((message) => message.message_id),
    )
    const optimisticUserMessage: ChatMessageData =
      retryOptimisticMessage ?? {
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
      setErrorPresentation(null)
      retryableSendRef.current = null
      if (!retryOptimisticMessage) {
        setDraft('')
        setMessages((current) => [...current, optimisticUserMessage])
      }
      const response = await services.sendChatMessage(requestedSessionId, content)
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
      if (isStaleTokenError(error)) {
        clearAuthenticatedSession()
        setRequiresLogin(true)
      } else {
        try {
          const historyResponse = await services.getChatMessages(requestedSessionId)
          if (!isCurrentRequest()) return
          const historyMessages = historyResponse.data.messages.filter(
            (message) =>
              !initialHistoryMessageIdsRef.current.has(message.message_id),
          )
          setMessages((current) =>
            reconcileHistoryMessages(
              current,
              historyMessages,
              knownMessageIds,
            ),
          )
        } catch (historyError) {
          if (!isCurrentRequest()) return
          if (isStaleTokenError(historyError)) {
            clearAuthenticatedSession()
            setRequiresLogin(true)
          }
        }
      }
      const presentation = getErrorPresentation(
        error,
        'AI 답변을 받는 중 오류가 발생했습니다.',
      )
      const isPolicyUnavailable =
        error instanceof ApiError &&
        error.code === 'CONSENT_POLICY_UNAVAILABLE'
      const recovery: ChatErrorRecovery =
        presentation.action === 'CONSENT_SETTINGS'
          ? null
          : isPolicyUnavailable
            ? 'RESEND'
            : error instanceof ApiError &&
                error.code === 'PRESCRIPTION_VERSION_STALE'
              ? 'REFRESH'
              : 'INITIALIZE'
      if (isPolicyUnavailable) {
        retryableSendRef.current = { content, optimisticUserMessage }
      }
      setErrorPresentation({ ...presentation, recovery })
    } finally {
      if (isCurrentRequest()) {
        setIsSending(false)
      }
    }
  }

  const handleSend = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    await sendChatContent(currentDraft.trim())
  }

  const handleComposerKeyDown = (
    event: React.KeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if (event.key !== 'Enter' || event.shiftKey) {
      if (compositionStateRef.current === 'ended') {
        compositionStateRef.current = 'idle'
      }
      return
    }

    const compositionState = compositionStateRef.current
    if (
      compositionState === 'composing' ||
      event.nativeEvent.isComposing ||
      (event.keyCode === 229 && compositionState !== 'ended')
    ) {
      return
    }

    compositionStateRef.current = 'idle'
    event.preventDefault()
    event.currentTarget.form?.requestSubmit()
  }

  const handleNavigation = (item: '홈' | '일정' | '도지' | '가이드' | '메뉴') => {
    if (item === '홈') navigate('/')
    if (item === '일정') navigate('/schedule')
    if (item === '도지' && !currentPrescriptionId) navigate('/chat')
    if (item === '가이드') navigate('/guides')
    if (item === '메뉴') navigate('/menu')
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

  if (
    !currentIsLoading &&
    !currentErrorPresentation &&
    (!currentPrescriptionId || !uuidPattern.test(currentPrescriptionId))
  ) {
    return (
      <div className="chat-page">
        <MobileShell
          title="Dosey 도지"
          onBack={() => navigate('/')}
          brandMark={<DoseyMascot variant="header" />}
          backPlacement="content"
          activeNavigation="도지"
          onNavigate={handleNavigation}
        >
          <main className="app-scroll chat-page__gate chat-page__gate--no-prescription">
            <span className="chat-page__prescription-icon" aria-hidden="true">▣</span>
            <h1>도지와 처방에 대해 이야기하려면<br />먼저 처방전을 등록해 주세요</h1>
            <p>처방전을 등록하고 내용을 확인하면<br />도지가 현재 처방을 참고해 답변할 수 있어요.</p>
            <Card className="chat-page__gate-actions">
              <Button
                fullWidth
                onClick={() => navigate('/prescriptions/upload', {
                  state: { intent: 'new-prescription' },
                })}
              >
                처방전 등록하기
              </Button>
              <Button fullWidth variant="ghost" onClick={() => navigate('/')}>
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
        title="Dosey 도지"
        onBack={() => navigate(-1)}
        brandMark={<DoseyMascot variant="header" />}
        backPlacement="content"
        activeNavigation="도지"
        onNavigate={handleNavigation}
      >
        <main className="chat-layout">
          <header className="chat-page__intro">
            <h1>도지와 대화하기</h1>
            <p>현재 확인된 처방과 제공된 근거 범위에서만 답해요.</p>
          </header>

          <div className="chat-page__conversation">
            <div className="app-scroll chat-messages" aria-live="polite">
              {currentIsLoading && (
                <div className="chat-page__state" role="status">
                  대화를 불러오고 있어요.
                </div>
              )}

              {!currentIsLoading &&
                !currentErrorPresentation &&
                currentMessages.length === 0 && (
                <div className="chat-page__state chat-page__empty">
                  <div className="chat-page__greeting">
                    <DoseyMascot variant="chat" />
                    <div className="chat-page__empty-card">
                      <strong>도지</strong>
                      <span>안녕하세요, 도지입니다.</span>
                      <span>무엇을 도와드릴까요?</span>
                    </div>
                  </div>
                  <div className="chat-page__presets" aria-label="이런 질문을 해보세요">
                    <strong>이런 질문을 해보세요</strong>
                    {questionPresets.map((question) => (
                      <button
                        key={question}
                        type="button"
                        onClick={() => setDraft(question)}
                        disabled={!currentSessionId}
                      >
                        {question}
                      </button>
                    ))}
                    <button
                      type="button"
                      className="chat-page__schedule-cta"
                      onClick={() => navigate('/schedule')}
                    >
                      복약 일정 설정하기
                    </button>
                  </div>
                </div>
              )}

              {currentMessages.map((message) => (
                <div
                  className={`chat-message-row ${message.role === 'USER' ? 'user' : 'assistant'}`}
                  key={message.message_id}
                >
                  {message.role === 'ASSISTANT' && (
                    <DoseyMascot variant="chat" />
                  )}
                  <div className={`chat-message ${message.role === 'USER' ? 'user' : 'assistant'}`}>
                    {message.role === 'ASSISTANT' && message.content ? (
                      <AssistantMessageContent content={message.content} />
                    ) : (
                      message.content ?? '답변을 생성하지 못했어요.'
                    )}
                    {import.meta.env.DEV &&
                      currentSessionId &&
                      message.role === 'ASSISTANT' &&
                      message.generation_status === 'COMPLETED' && (
                      <ResponseFeedback
                        key={message.message_id}
                        target={{ sessionId: currentSessionId, messageId: message.message_id }}
                        active={message.message_id === feedbackMessageId}
                      />
                    )}
                  </div>
                </div>
              ))}

              {currentIsSending && (
                <div className="chat-page__processing" role="status">
                  <strong>CHAT · 처리 중</strong>
                  <h2>답변을 확인하고 있어요</h2>
                  <p>질문은 이미 보냈어요. 확인이 끝나면 이 자리에서 답변을 보여드릴게요.</p>
                  <span>● ● ●&nbsp;&nbsp;도지가 확인하고 있어요…</span>
                </div>
              )}

              {currentErrorPresentation && (
                <Card className="chat-page__error">
                  <StatusBadge tone="attention">오류</StatusBadge>
                  <div role="alert">
                    <h2>{currentErrorPresentation.title}</h2>
                    {currentErrorPresentation.helper && (
                      <p>{currentErrorPresentation.helper}</p>
                    )}
                  </div>
                  <Button
                    fullWidth
                    variant="secondary"
                    onClick={() => {
                      if (currentErrorPresentation.action === 'CONSENT_SETTINGS') {
                        navigate('/profile')
                        return
                      }
                      if (currentErrorPresentation.recovery === 'RESEND') {
                        const failedSend = retryableSendRef.current
                        if (failedSend) {
                          void sendChatContent(
                            failedSend.content,
                            failedSend.optimisticUserMessage,
                          )
                        }
                        return
                      }
                      void initializeChat(
                        true,
                        currentErrorPresentation.recovery !== 'REFRESH',
                      )
                    }}
                    disabled={currentIsSending}
                  >
                    {currentErrorPresentation.action === 'CONSENT_SETTINGS'
                      ? '동의 설정 확인하기'
                      : currentErrorPresentation.action === 'RETRY'
                        ? '다시 시도'
                        : '대화 다시 불러오기'}
                  </Button>
                </Card>
              )}
              <div ref={messagesEndRef} />
            </div>

            <form className="chat-composer" onSubmit={handleSend}>
              <label className="chat-page__composer-label" htmlFor="dosey-chat-input">
                <span className="chat-page__composer-label-legacy">복약 질문</span>
                <span aria-hidden="true">복약 챗봇 도지에게 질문</span>
              </label>
              <textarea
                id="dosey-chat-input"
                className="chat-input"
                value={currentDraft}
                onChange={(event) => setDraft(event.target.value)}
                onCompositionStart={() => {
                  compositionStateRef.current = 'composing'
                }}
                onCompositionEnd={() => {
                  compositionStateRef.current = 'ended'
                }}
                onKeyDown={handleComposerKeyDown}
                aria-label="복약 질문"
                placeholder="궁금한 내용을 입력하세요"
                rows={1}
                enterKeyHint="send"
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
