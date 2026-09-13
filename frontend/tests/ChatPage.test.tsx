import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React, { useLayoutEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom'
import {
  createChatSession,
  getChatMessages,
  getChatSessionForPrescription,
  sendChatMessage,
} from '../src/api/chat'
import { ApiError } from '../src/api/client'
import { getLatestPrescription } from '../src/api/prescriptions'
import ChatPage, { type ChatPageServices } from '../src/pages/ChatPage'

vi.mock('../src/api/chat', () => ({
  createChatSession: vi.fn(),
  getChatMessages: vi.fn(),
  getChatSessionForPrescription: vi.fn(),
  sendChatMessage: vi.fn(),
}))

vi.mock('../src/api/prescriptions', () => ({
  getLatestPrescription: vi.fn(),
}))

const prescriptionId = '11111111-1111-4111-8111-111111111111'
const sessionId = '22222222-2222-4222-8222-222222222222'
const secondPrescriptionId = '33333333-3333-4333-8333-333333333333'
const secondSessionId = '44444444-4444-4444-8444-444444444444'
const prescriptionVersionId = '55555555-5555-4555-8555-555555555555'

function latestPrescriptionResponse() {
  return {
    data: {
      prescription_id: prescriptionId,
      prescription_version_id: prescriptionVersionId,
      revision: 1,
      current: true,
      document_id: '66666666-6666-4666-8666-666666666666',
      prescribed_date: '2026-09-10',
      confirmed_at: '2026-09-10T00:00:00Z',
      medications: [],
    },
  }
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined
  let reject: (reason: unknown) => void = () => undefined
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })
  return { promise, reject, resolve }
}

function NavigationHarness() {
  const navigate = useNavigate()

  return (
    <button
      type="button"
      onClick={() =>
        navigate(`/chat?prescription_id=${secondPrescriptionId}`)
      }
    >
      두 번째 처방으로 이동
    </button>
  )
}

function LocationCommitProbe({
  onCommit,
}: {
  onCommit?: (search: string, bodyText: string) => void
}) {
  const location = useLocation()

  useLayoutEffect(() => {
    onCommit?.(location.search, document.body.textContent ?? '')
  }, [location, onCommit])

  return null
}

function UploadRoute() {
  const location = useLocation()

  return (
    <div>
      처방전 등록 화면
      <output data-testid="upload-intent">
        {(location.state as { intent?: string } | null)?.intent ?? ''}
      </output>
    </div>
  )
}

function renderPage(
  entry = `/chat?prescription_id=${prescriptionId}`,
  options: {
    strict?: boolean
    onLocationCommit?: (search: string, bodyText: string) => void
    services?: ChatPageServices
  } = {},
) {
  const content = (
    <MemoryRouter initialEntries={[entry]}>
      <NavigationHarness />
      <LocationCommitProbe onCommit={options.onLocationCommit} />
      <Routes>
        <Route path="/chat" element={<ChatPage services={options.services} />} />
        <Route path="/login" element={<div>로그인 화면</div>} />
        <Route path="/prescriptions/upload" element={<UploadRoute />} />
        <Route path="/guides" element={<div>복약 가이드 화면</div>} />
        <Route path="/menu" element={<div>메뉴 화면</div>} />
      </Routes>
    </MemoryRouter>
  )

  return render(
    options.strict ? <React.StrictMode>{content}</React.StrictMode> : content,
  )
}

function createServices(
  overrides: Partial<ChatPageServices>,
): ChatPageServices {
  return {
    createChatSession,
    getChatSessionForPrescription,
    getChatMessages,
    sendChatMessage,
    getLatestPrescription,
    ...overrides,
  }
}

function mockSessionCreation() {
  vi.mocked(getChatSessionForPrescription).mockRejectedValue(
    new ApiError(
      404,
      '대화 세션을 찾을 수 없습니다.',
      'CHAT_SESSION_NOT_FOUND',
    ),
  )
  vi.mocked(createChatSession).mockResolvedValue({
    data: {
      session_id: sessionId,
      prescription_id: prescriptionId,
      prescription_version_id: prescriptionVersionId,
      session_status: 'ACTIVE',
      created_at: '2026-08-24T00:00:00Z',
    },
  })
  vi.mocked(getChatMessages).mockResolvedValue({
    data: { session_id: sessionId, messages: [] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  localStorage.setItem('access_token', 'test-token')
  vi.mocked(getLatestPrescription).mockResolvedValue(
    latestPrescriptionResponse(),
  )
  mockSessionCreation()
})

afterEach(() => {
  cleanup()
})

describe('ChatPage', () => {
  it('확정된 prescription_id로 Chat session을 생성하고 브라우저에 저장하지 않는다', async () => {
    renderPage()

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(getChatSessionForPrescription).toHaveBeenCalledWith(prescriptionId)
    expect(createChatSession).toHaveBeenCalledWith(prescriptionId)
    expect(getChatMessages).toHaveBeenCalledWith(sessionId)
    expect(sessionStorage.length).toBe(0)
  })

  it('StrictMode에서도 동일 처방의 session 생성 POST를 한 번만 호출한다', async () => {
    let resolveSession: (
      value: Awaited<ReturnType<typeof createChatSession>>,
    ) => void = () => undefined
    vi.mocked(createChatSession).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSession = resolve
        }),
    )

    renderPage(`/chat?prescription_id=${prescriptionId}`, { strict: true })

    await waitFor(() =>
      expect(getChatSessionForPrescription).toHaveBeenCalledTimes(1),
    )
    await waitFor(() => expect(createChatSession).toHaveBeenCalledTimes(1))
    await act(async () =>
      resolveSession({
        data: {
          session_id: sessionId,
          prescription_id: prescriptionId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-08-24T00:00:00Z',
        },
      }),
    )

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(getChatSessionForPrescription).toHaveBeenCalledTimes(1)
    expect(createChatSession).toHaveBeenCalledTimes(1)
    expect(getChatMessages).toHaveBeenCalledTimes(1)
  })

  it('StrictMode rediscovery 200에서도 GET을 한 번만 호출하고 session을 생성하지 않는다', async () => {
    const rediscoveryRequest = deferred<
      Awaited<ReturnType<typeof getChatSessionForPrescription>>
    >()
    vi.mocked(getChatSessionForPrescription).mockReturnValue(
      rediscoveryRequest.promise,
    )

    renderPage(`/chat?prescription_id=${prescriptionId}`, { strict: true })

    await waitFor(() =>
      expect(getChatSessionForPrescription).toHaveBeenCalledTimes(1),
    )
    await act(async () =>
      rediscoveryRequest.resolve({
        data: {
          session_id: sessionId,
          prescription_id: prescriptionId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-09-08T00:00:00Z',
        },
      }),
    )

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(getChatSessionForPrescription).toHaveBeenCalledTimes(1)
    expect(createChatSession).not.toHaveBeenCalled()
    expect(getChatMessages).toHaveBeenCalledTimes(1)
  })

  it('StrictMode injected service에서 ACTIVE session GET을 한 번만 호출한다', async () => {
    const rediscoveryRequest = deferred<
      Awaited<ReturnType<typeof getChatSessionForPrescription>>
    >()
    const injectedGetSession = vi
      .fn<typeof getChatSessionForPrescription>()
      .mockReturnValue(rediscoveryRequest.promise)
    const injectedCreateSession = vi.fn<typeof createChatSession>()

    renderPage(`/chat?prescription_id=${prescriptionId}`, {
      strict: true,
      services: createServices({
        createChatSession: injectedCreateSession,
        getChatSessionForPrescription: injectedGetSession,
      }),
    })

    await waitFor(() => expect(injectedGetSession).toHaveBeenCalledTimes(1))
    await act(async () =>
      rediscoveryRequest.resolve({
        data: {
          session_id: sessionId,
          prescription_id: prescriptionId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-09-08T00:00:00Z',
        },
      }),
    )

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(injectedGetSession).toHaveBeenCalledTimes(1)
    expect(injectedCreateSession).not.toHaveBeenCalled()
    expect(getChatMessages).toHaveBeenCalledTimes(1)
  })

  it('StrictMode injected service에서 CHAT_SESSION_NOT_FOUND면 GET·POST를 각각 한 번만 호출한다', async () => {
    const rediscoveryRequest = deferred<
      Awaited<ReturnType<typeof getChatSessionForPrescription>>
    >()
    const creationRequest = deferred<
      Awaited<ReturnType<typeof createChatSession>>
    >()
    const injectedGetSession = vi
      .fn<typeof getChatSessionForPrescription>()
      .mockReturnValue(rediscoveryRequest.promise)
    const injectedCreateSession = vi
      .fn<typeof createChatSession>()
      .mockReturnValue(creationRequest.promise)

    renderPage(`/chat?prescription_id=${prescriptionId}`, {
      strict: true,
      services: createServices({
        createChatSession: injectedCreateSession,
        getChatSessionForPrescription: injectedGetSession,
      }),
    })

    await waitFor(() => expect(injectedGetSession).toHaveBeenCalledTimes(1))
    await act(async () =>
      rediscoveryRequest.reject(
        new ApiError(
          404,
          '대화 세션을 찾을 수 없습니다.',
          'CHAT_SESSION_NOT_FOUND',
        ),
      ),
    )
    await waitFor(() => expect(injectedCreateSession).toHaveBeenCalledTimes(1))
    await act(async () =>
      creationRequest.resolve({
        data: {
          session_id: sessionId,
          prescription_id: prescriptionId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-09-08T00:00:00Z',
        },
      }),
    )

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(injectedGetSession).toHaveBeenCalledTimes(1)
    expect(injectedCreateSession).toHaveBeenCalledTimes(1)
    expect(getChatMessages).toHaveBeenCalledTimes(1)
  })

  it('사용자 메시지를 전송하고 실제 AI 응답 content를 표시한다', async () => {
    vi.mocked(sendChatMessage).mockResolvedValue({
      data: {
        user_message_id: 'user-message-1',
        assistant_message_id: 'assistant-message-1',
        session_id: sessionId,
        generation_status: 'COMPLETED',
        content: '확정된 처방을 기준으로 생성한 실제 답변입니다.',
        model_name: 'chat-model',
        prompt_version: 'chat-v1',
        created_at: '2026-08-24T00:00:01Z',
        completed_at: '2026-08-24T00:00:02Z',
      },
    })
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '이 약은 언제 먹나요?' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    expect(
      await screen.findByText('확정된 처방을 기준으로 생성한 실제 답변입니다.'),
    ).toBeTruthy()
    expect(screen.getByText('이 약은 언제 먹나요?')).toBeTruthy()
    expect(sendChatMessage).toHaveBeenCalledWith(
      sessionId,
      '이 약은 언제 먹나요?',
    )
    expect(screen.getAllByText('이 약은 언제 먹나요?')).toHaveLength(1)
    expect(
      screen.getAllByText('확정된 처방을 기준으로 생성한 실제 답변입니다.'),
    ).toHaveLength(1)
  })

  it('ASSISTANT content만 Markdown으로 표시하고 USER content는 원문을 유지한다', async () => {
    vi.mocked(sendChatMessage).mockResolvedValue({
      data: {
        session_id: sessionId,
        user_message_id: 'markdown-user',
        assistant_message_id: 'markdown-assistant',
        generation_status: 'COMPLETED',
        content: '**도지 답변**',
        model_name: 'chat-model',
        prompt_version: 'chat-v1',
        created_at: '2026-09-10T00:00:00Z',
        completed_at: '2026-09-10T00:00:01Z',
      },
    })
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '**사용자 원문**' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    const userMessage = screen.getByText('**사용자 원문**')
    const assistantMessage = await screen.findByText('도지 답변')

    expect(userMessage.closest('.chat-message')?.classList).toContain('user')
    expect(userMessage.querySelector('strong')).toBeNull()
    expect(assistantMessage.tagName).toBe('STRONG')
    expect(assistantMessage.closest('.chat-message')?.classList).toContain(
      'assistant',
    )
  })

  it('세션 준비 후 입력창에 focus할 수 있고 accessible name을 제공한다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    expect(input).toHaveProperty('disabled', false)

    input.focus()

    expect(document.activeElement).toBe(input)
    expect(input.getAttribute('aria-label')).toBe('복약 질문')
    expect(screen.getByText('복약 질문')).toBeTruthy()
  })

  it('Enter는 공통 submit 경로로 메시지를 정확히 한 번 전송한다', async () => {
    const messageRequest = deferred<Awaited<ReturnType<typeof sendChatMessage>>>()
    vi.mocked(sendChatMessage).mockReturnValue(messageRequest.promise)
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '  Enter 합성 질문  ' } })

    expect(fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledTimes(1))
    expect(sendChatMessage).toHaveBeenCalledWith(sessionId, 'Enter 합성 질문')
    expect(screen.getAllByText('Enter 합성 질문')).toHaveLength(1)

    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))
    expect(sendChatMessage).toHaveBeenCalledTimes(1)

    await act(async () =>
      messageRequest.resolve({
        data: {
          user_message_id: 'enter-user-message',
          assistant_message_id: 'enter-assistant-message',
          session_id: sessionId,
          generation_status: 'COMPLETED',
          content: 'Enter 응답',
          model_name: 'chat-model',
          prompt_version: 'chat-v1',
          created_at: '2026-08-24T00:00:01Z',
          completed_at: '2026-08-24T00:00:02Z',
        },
      }),
    )

    expect(await screen.findByText('Enter 응답')).toBeTruthy()
  })

  it('Shift+Enter는 submit하지 않고 줄바꿈을 유지한다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '첫 줄' } })

    expect(
      fireEvent.keyDown(input, {
        key: 'Enter',
        code: 'Enter',
        shiftKey: true,
      }),
    ).toBe(true)
    fireEvent.change(input, { target: { value: '첫 줄\n둘째 줄' } })

    expect(input).toHaveProperty('value', '첫 줄\n둘째 줄')
    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('한글 IME 조합 중 Enter는 submit하지 않는다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '조합 중 질문' } })

    expect(
      fireEvent.keyDown(input, {
        key: 'Enter',
        code: 'Enter',
        isComposing: true,
      }),
    ).toBe(true)
    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('한글 IME 조합 중 isComposing=false·keyCode=13 Enter도 submit하지 않는다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.compositionStart(input)
    fireEvent.change(input, { target: { value: '조합 중 비표준 Enter' } })

    expect(
      fireEvent.keyDown(input, {
        key: 'Enter',
        code: 'Enter',
        isComposing: false,
        keyCode: 13,
      }),
    ).toBe(true)
    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('composition 종료 후에도 isComposing=true Enter는 submit하지 않는다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.compositionStart(input)
    fireEvent.change(input, { target: { value: '조합을 끝낸 질문' } })

    fireEvent.keyDown(input, {
      key: 'Enter',
      code: 'Enter',
      isComposing: true,
      keyCode: 229,
    })
    fireEvent.compositionEnd(input)

    expect(
      fireEvent.keyDown(input, {
        key: 'Enter',
        code: 'Enter',
        isComposing: true,
      }),
    ).toBe(true)
    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('composition 종료 후 이어지는 isComposing=false Enter는 정확히 한 번 전송한다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.compositionStart(input)
    fireEvent.change(input, { target: { value: '조합을 끝낸 질문' } })
    fireEvent.compositionEnd(input)

    fireEvent.keyDown(input, {
      key: 'Enter',
      code: 'Enter',
      isComposing: true,
    })
    expect(sendChatMessage).not.toHaveBeenCalled()

    expect(
      fireEvent.keyDown(input, {
        key: 'Enter',
        code: 'Enter',
        isComposing: false,
      }),
    ).toBe(false)

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledTimes(1))
    expect(sendChatMessage).toHaveBeenCalledWith(
      sessionId,
      '조합을 끝낸 질문',
    )
  })

  it('WebKit IME 조합 경계의 Enter는 submit하지 않는다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: 'WebKit 조합 중 질문' } })

    expect(
      fireEvent.keyDown(input, {
        key: 'Enter',
        code: 'Enter',
        keyCode: 229,
      }),
    ).toBe(true)
    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('composition 종료 후 229로 보고된 의도한 Enter는 한 번 전송한다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.compositionStart(input)
    fireEvent.change(input, { target: { value: 'WebKit 조합 완료 질문' } })
    fireEvent.keyDown(input, {
      key: 'Enter',
      code: 'Enter',
      isComposing: true,
      keyCode: 229,
    })
    fireEvent.compositionEnd(input)

    expect(
      fireEvent.keyDown(input, {
        key: 'Enter',
        code: 'Enter',
        keyCode: 229,
      }),
    ).toBe(false)

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledTimes(1))
    expect(sendChatMessage).toHaveBeenCalledWith(
      sessionId,
      'WebKit 조합 완료 질문',
    )
  })

  it('composition 종료 후 다른 keydown이 ended를 소비하면 229 방어를 복원한다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '상태 reset 질문' } })
    fireEvent.compositionStart(input)
    fireEvent.compositionEnd(input)

    fireEvent.keyDown(input, { key: 'ArrowLeft', code: 'ArrowLeft' })

    expect(
      fireEvent.keyDown(input, {
        key: 'Enter',
        code: 'Enter',
        keyCode: 229,
      }),
    ).toBe(true)
    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('새 compositionstart는 이전 ended를 덮어써 조합 Enter를 차단한다', async () => {
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '새 조합 질문' } })
    fireEvent.compositionStart(input)
    fireEvent.compositionEnd(input)
    fireEvent.compositionStart(input)

    expect(
      fireEvent.keyDown(input, {
        key: 'Enter',
        code: 'Enter',
        keyCode: 229,
      }),
    ).toBe(true)
    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('사용자 질문을 즉시 표시하고 응답 완료 후 canonical 메시지로 reconcile한다', async () => {
    const messageRequest = deferred<Awaited<ReturnType<typeof sendChatMessage>>>()
    vi.mocked(sendChatMessage).mockReturnValue(messageRequest.promise)
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    const sendButton = screen.getByRole('button', { name: '질문 전송' })
    fireEvent.change(input, { target: { value: '중복 전송 확인' } })
    fireEvent.click(sendButton)

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledTimes(1))
    expect(screen.getAllByText('중복 전송 확인')).toHaveLength(1)
    expect(screen.getByText('답변을 확인하고 있어요')).toBeTruthy()
    expect(screen.queryByText('중복 없이 생성된 답변')).toBeNull()
    expect(input).toHaveProperty('value', '')
    expect(input).toHaveProperty('disabled', true)
    expect(sendButton).toHaveProperty('disabled', true)
    fireEvent.click(sendButton)
    expect(sendChatMessage).toHaveBeenCalledTimes(1)

    await act(async () =>
      messageRequest.resolve({
        data: {
          user_message_id: 'user-message-2',
          assistant_message_id: 'assistant-message-2',
          session_id: sessionId,
          generation_status: 'COMPLETED',
          content: '중복 없이 생성된 답변',
          model_name: 'chat-model',
          prompt_version: 'chat-v1',
          created_at: '2026-08-24T00:00:01Z',
          completed_at: '2026-08-24T00:00:02Z',
        },
      }),
    )

    expect(await screen.findByText('중복 없이 생성된 답변')).toBeTruthy()
    expect(screen.getAllByText('중복 전송 확인')).toHaveLength(1)
    expect(screen.queryByText('답변을 확인하고 있어요')).toBeNull()
    expect(input).toHaveProperty('value', '')
    expect(input).toHaveProperty('disabled', false)
  })

  it('API 오류를 안내하고 대화를 다시 불러올 수 있다', async () => {
    vi.mocked(getChatSessionForPrescription).mockRejectedValueOnce(
      new ApiError(503, '현재 서비스를 사용할 수 없습니다.'),
    )
    renderPage()

    expect(
      await screen.findByText('도지와 연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요.'),
    ).toBeTruthy()
    expect(screen.queryByText('현재 서비스를 사용할 수 없습니다.')).toBeNull()
    mockSessionCreation()
    fireEvent.click(screen.getByRole('button', { name: '대화 다시 불러오기' }))

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(createChatSession).toHaveBeenCalledTimes(1)
  })

  it('메시지 생성 실패 후 저장된 이력을 다시 불러오고 중복 전송을 막는다', async () => {
    vi.mocked(sendChatMessage).mockRejectedValue(
      new ApiError(503, 'AI 서비스에 잠시 연결할 수 없습니다.'),
    )
    vi.mocked(getChatMessages)
      .mockResolvedValueOnce({
        data: {
          session_id: sessionId,
          messages: [
            {
              message_id: 'old-user',
              role: 'USER',
              content: '이전에 저장된 질문',
              generation_status: 'NOT_APPLICABLE',
              created_at: '2026-08-23T00:00:01Z',
            },
            {
              message_id: 'old-assistant',
              role: 'ASSISTANT',
              content: '이전에 저장된 답변',
              generation_status: 'COMPLETED',
              created_at: '2026-08-23T00:00:02Z',
            },
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          session_id: sessionId,
          messages: [
            {
              message_id: 'old-user',
              role: 'USER',
              content: '이전에 저장된 질문',
              generation_status: 'NOT_APPLICABLE',
              created_at: '2026-08-23T00:00:01Z',
            },
            {
              message_id: 'old-assistant',
              role: 'ASSISTANT',
              content: '이전에 저장된 답변',
              generation_status: 'COMPLETED',
              created_at: '2026-08-23T00:00:02Z',
            },
            {
              message_id: 'failed-user',
              role: 'USER',
              content: '오류 후 다시 보낼 질문',
              generation_status: 'NOT_APPLICABLE',
              created_at: '2026-08-24T00:00:01Z',
            },
            {
              message_id: 'failed-assistant',
              role: 'ASSISTANT',
              content: null,
              generation_status: 'FAILED',
              created_at: '2026-08-24T00:00:02Z',
            },
          ],
        },
      })
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '오류 후 다시 보낼 질문' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    expect(
      await screen.findByText('도지와 연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요.'),
    ).toBeTruthy()
    expect(screen.queryByText('AI 서비스에 잠시 연결할 수 없습니다.')).toBeNull()
    expect(screen.queryByText('이전에 저장된 질문')).toBeNull()
    expect(screen.queryByText('이전에 저장된 답변')).toBeNull()
    expect(screen.getByText('오류 후 다시 보낼 질문')).toBeTruthy()
    expect(screen.getByText('답변을 생성하지 못했어요.')).toBeTruthy()
    expect(getChatMessages).toHaveBeenCalledTimes(2)
    expect(input).toHaveProperty('disabled', false)
    expect(input).toHaveProperty('value', '')
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))
    expect(sendChatMessage).toHaveBeenCalledTimes(1)
    expect(
      screen.getByRole('button', { name: '대화 다시 불러오기' }),
    ).toBeTruthy()
  })

  it('대화 재시도 후에도 최초 history는 숨기고 현재 방문 USER와 ASSISTANT를 유지한다', async () => {
    vi.mocked(getChatSessionForPrescription).mockResolvedValue({
      data: {
        session_id: sessionId,
        prescription_id: prescriptionId,
        prescription_version_id: prescriptionVersionId,
        session_status: 'ACTIVE',
        created_at: '2026-09-08T00:00:00Z',
      },
    })
    vi.mocked(sendChatMessage).mockRejectedValue(new TypeError('Failed to fetch'))

    const hiddenHistory = [
      {
        message_id: 'initial-user',
        role: 'USER' as const,
        content: '최초 진입 전에 저장된 질문',
        generation_status: 'NOT_APPLICABLE' as const,
        created_at: '2026-09-09T00:00:01Z',
      },
      {
        message_id: 'initial-assistant',
        role: 'ASSISTANT' as const,
        content: '최초 진입 전에 저장된 답변',
        generation_status: 'COMPLETED' as const,
        created_at: '2026-09-09T00:00:02Z',
      },
    ]
    const currentVisitHistory = [
      ...hiddenHistory,
      {
        message_id: 'current-user',
        role: 'USER' as const,
        content: '현재 방문에서 보낸 질문',
        generation_status: 'NOT_APPLICABLE' as const,
        created_at: '2026-09-10T00:00:01Z',
      },
      {
        message_id: 'current-assistant',
        role: 'ASSISTANT' as const,
        content: '현재 방문에서 받은 답변',
        generation_status: 'COMPLETED' as const,
        created_at: '2026-09-10T00:00:02Z',
      },
    ]

    vi.mocked(getChatMessages)
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: hiddenHistory },
      })
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: currentVisitHistory },
      })
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: hiddenHistory },
      })

    renderPage()

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('최초 진입 전에 저장된 질문')).toBeNull()
    expect(screen.queryByText('최초 진입 전에 저장된 답변')).toBeNull()

    fireEvent.change(screen.getByLabelText('복약 질문'), {
      target: { value: '현재 방문에서 보낸 질문' },
    })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    expect(await screen.findByText('현재 방문에서 받은 답변')).toBeTruthy()
    expect(screen.getAllByText('현재 방문에서 보낸 질문')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: '대화 다시 불러오기' }))

    await waitFor(() => expect(getChatMessages).toHaveBeenCalledTimes(3))
    expect(screen.queryByText('최초 진입 전에 저장된 질문')).toBeNull()
    expect(screen.queryByText('최초 진입 전에 저장된 답변')).toBeNull()
    expect(screen.getAllByText('현재 방문에서 보낸 질문')).toHaveLength(1)
    expect(screen.getAllByText('현재 방문에서 받은 답변')).toHaveLength(1)
    expect(getChatSessionForPrescription).toHaveBeenCalledTimes(2)
    expect(createChatSession).not.toHaveBeenCalled()
  })

  it('재시도에서 ACTIVE session이 교체되면 이전 session 메시지를 섞지 않는다', async () => {
    const replacementSessionId = '77777777-7777-4777-8777-777777777777'
    vi.mocked(getChatSessionForPrescription)
      .mockResolvedValueOnce({
        data: {
          session_id: sessionId,
          prescription_id: prescriptionId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-09-08T00:00:00Z',
        },
      })
      .mockResolvedValueOnce({
        data: {
          session_id: replacementSessionId,
          prescription_id: prescriptionId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-09-10T00:00:00Z',
        },
      })
    vi.mocked(sendChatMessage).mockRejectedValue(new TypeError('Failed to fetch'))
    vi.mocked(getChatMessages)
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: [] },
      })
      .mockResolvedValueOnce({
        data: {
          session_id: sessionId,
          messages: [
            {
              message_id: 'old-session-user',
              role: 'USER',
              content: '이전 session의 현재 방문 질문',
              generation_status: 'NOT_APPLICABLE',
              created_at: '2026-09-10T00:00:01Z',
            },
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          session_id: replacementSessionId,
          messages: [
            {
              message_id: 'replacement-history',
              role: 'ASSISTANT',
              content: '교체된 session의 기존 답변',
              generation_status: 'COMPLETED',
              created_at: '2026-09-10T00:01:00Z',
            },
          ],
        },
      })

    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, {
      target: { value: '이전 session의 현재 방문 질문' },
    })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    expect(
      await screen.findByText('이전 session의 현재 방문 질문'),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '대화 다시 불러오기' }))

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('이전 session의 현재 방문 질문')).toBeNull()
    expect(screen.queryByText('교체된 session의 기존 답변')).toBeNull()
    expect(getChatMessages).toHaveBeenLastCalledWith(replacementSessionId)
    expect(createChatSession).not.toHaveBeenCalled()
  })

  it('메시지 실패 후 history 재조회도 실패하면 안전한 오류 상태를 유지한다', async () => {
    const messageRequest = deferred<Awaited<ReturnType<typeof sendChatMessage>>>()
    vi.mocked(sendChatMessage).mockReturnValue(messageRequest.promise)
    vi.mocked(getChatMessages)
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: [] },
      })
      .mockRejectedValueOnce(
        new ApiError(503, '대화 이력을 다시 불러올 수 없습니다.'),
      )
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '실패 이력 확인 질문' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledTimes(1))
    expect(screen.getAllByText('실패 이력 확인 질문')).toHaveLength(1)
    expect(screen.getByText('답변을 확인하고 있어요')).toBeTruthy()

    await act(async () =>
      messageRequest.reject(
        new ApiError(503, 'AI 서비스에 잠시 연결할 수 없습니다.'),
      ),
    )

    expect(
      await screen.findByText('도지와 연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요.'),
    ).toBeTruthy()
    expect(screen.getAllByText('실패 이력 확인 질문')).toHaveLength(1)
    expect(screen.queryByText('답변을 확인하고 있어요')).toBeNull()
    expect(input).toHaveProperty('value', '')
    expect(input).toHaveProperty('disabled', false)
    expect(sendChatMessage).toHaveBeenCalledTimes(1)
    expect(getChatMessages).toHaveBeenCalledTimes(2)
    expect(
      screen.getByRole('button', { name: '대화 다시 불러오기' }),
    ).toBeTruthy()
  })

  it('연속 실패 복구에서도 이전 optimistic 사용자 질문을 유실하지 않는다', async () => {
    vi.mocked(sendChatMessage).mockRejectedValue(
      new ApiError(503, 'AI 서비스에 잠시 연결할 수 없습니다.'),
    )
    vi.mocked(getChatMessages)
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: [] },
      })
      .mockRejectedValueOnce(
        new ApiError(503, '첫 번째 대화 이력을 다시 불러올 수 없습니다.'),
      )
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: [] },
      })
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    const sendButton = screen.getByRole('button', { name: '질문 전송' })
    fireEvent.change(input, { target: { value: '보존할 첫 번째 질문' } })
    fireEvent.click(sendButton)

    await waitFor(() => expect(getChatMessages).toHaveBeenCalledTimes(2))
    expect(screen.getAllByText('보존할 첫 번째 질문')).toHaveLength(1)

    fireEvent.change(input, { target: { value: '보존할 두 번째 질문' } })
    fireEvent.click(sendButton)

    await waitFor(() => expect(getChatMessages).toHaveBeenCalledTimes(3))
    expect(screen.getAllByText('보존할 첫 번째 질문')).toHaveLength(1)
    expect(screen.getAllByText('보존할 두 번째 질문')).toHaveLength(1)
    expect(input).toHaveProperty('disabled', false)
    expect(sendChatMessage).toHaveBeenCalledTimes(2)
  })

  it('history 복구 후에도 기존 optimistic 사용자 질문의 순서를 유지한다', async () => {
    vi.mocked(sendChatMessage).mockRejectedValue(
      new ApiError(503, 'AI 서비스에 잠시 연결할 수 없습니다.'),
    )
    vi.mocked(getChatMessages)
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: [] },
      })
      .mockRejectedValueOnce(
        new ApiError(503, '첫 번째 대화 이력을 다시 불러올 수 없습니다.'),
      )
      .mockResolvedValueOnce({
        data: {
          session_id: sessionId,
          messages: [
            {
              message_id: 'canonical-user-b',
              role: 'USER',
              content: '질문 B',
              generation_status: 'NOT_APPLICABLE',
              created_at: '2026-09-03T00:00:02Z',
            },
            {
              message_id: 'canonical-assistant-b',
              role: 'ASSISTANT',
              content: '질문 B의 실패 결과',
              generation_status: 'FAILED',
              created_at: '2026-09-03T00:00:03Z',
            },
          ],
        },
      })
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    const sendButton = screen.getByRole('button', { name: '질문 전송' })
    fireEvent.change(input, { target: { value: '질문 A' } })
    fireEvent.click(sendButton)

    await waitFor(() => expect(getChatMessages).toHaveBeenCalledTimes(2))
    fireEvent.change(input, { target: { value: '질문 B' } })
    fireEvent.click(sendButton)

    await waitFor(() =>
      expect(
        Array.from(document.querySelectorAll('.chat-message')).map(
          (message) => message.textContent,
        ),
      ).toEqual(['질문 A', '질문 B', '질문 B의 실패 결과']),
    )
    expect(screen.getAllByText('질문 A')).toHaveLength(1)
    expect(screen.getAllByText('질문 B')).toHaveLength(1)
  })

  it('동일한 질문을 반복해도 최신 canonical USER와 안정적으로 reconcile한다', async () => {
    vi.mocked(sendChatMessage).mockRejectedValue(
      new ApiError(503, 'AI 서비스에 잠시 연결할 수 없습니다.'),
    )
    vi.mocked(getChatMessages)
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: [] },
      })
      .mockRejectedValueOnce(
        new ApiError(503, '첫 번째 대화 이력을 다시 불러올 수 없습니다.'),
      )
      .mockResolvedValueOnce({
        data: {
          session_id: sessionId,
          messages: [
            {
              message_id: 'canonical-repeated-user',
              role: 'USER',
              content: '같은 질문',
              generation_status: 'NOT_APPLICABLE',
              created_at: '2026-09-03T00:00:02Z',
            },
            {
              message_id: 'canonical-repeated-assistant',
              role: 'ASSISTANT',
              content: '두 번째 질문의 실패 결과',
              generation_status: 'FAILED',
              created_at: '2026-09-03T00:00:03Z',
            },
          ],
        },
      })
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    const sendButton = screen.getByRole('button', { name: '질문 전송' })
    fireEvent.change(input, { target: { value: '같은 질문' } })
    fireEvent.click(sendButton)

    await waitFor(() => expect(getChatMessages).toHaveBeenCalledTimes(2))
    fireEvent.change(input, { target: { value: '같은 질문' } })
    fireEvent.click(sendButton)

    await waitFor(() =>
      expect(
        Array.from(document.querySelectorAll('.chat-message')).map(
          (message) => message.textContent,
        ),
      ).toEqual(['같은 질문', '같은 질문', '두 번째 질문의 실패 결과']),
    )
    expect(screen.getAllByText('같은 질문')).toHaveLength(2)
    expect(sendChatMessage).toHaveBeenCalledTimes(2)
  })

  it('Guide 진입은 기존 session을 유지하되 과거 이력을 숨기고 새 대화만 표시한다', async () => {
    vi.mocked(getChatSessionForPrescription).mockResolvedValue({
      data: {
        session_id: sessionId,
        prescription_id: prescriptionId,
        prescription_version_id: prescriptionVersionId,
        session_status: 'ACTIVE',
        created_at: '2026-09-08T00:00:00Z',
      },
    })
    vi.mocked(getChatMessages).mockResolvedValue({
      data: {
        session_id: sessionId,
        messages: [
          {
            message_id: 'history-user',
            role: 'USER',
            content: '기존 질문입니다.',
            generation_status: 'NOT_APPLICABLE',
            created_at: '2026-08-24T00:00:01Z',
          },
          {
            message_id: 'history-assistant',
            role: 'ASSISTANT',
            content: '기존 AI 답변입니다.',
            generation_status: 'COMPLETED',
            created_at: '2026-08-24T00:00:02Z',
          },
        ],
      },
    })
    vi.mocked(sendChatMessage).mockResolvedValue({
      data: {
        user_message_id: 'new-user-message',
        assistant_message_id: 'new-assistant-message',
        session_id: sessionId,
        generation_status: 'COMPLETED',
        content: '현재 화면에서 새로 받은 답변입니다.',
        model_name: 'chat-model',
        prompt_version: 'chat-v1',
        created_at: '2026-09-10T00:00:01Z',
        completed_at: '2026-09-10T00:00:02Z',
      },
    })

    renderPage()

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('기존 질문입니다.')).toBeNull()
    expect(screen.queryByText('기존 AI 답변입니다.')).toBeNull()
    for (const preset of [
      '아침 약은 언제 먹나요?',
      '복용을 잊었어요',
      '약을 함께 먹어도 되나요?',
    ]) {
      expect(screen.getByRole('button', { name: preset })).toBeTruthy()
    }
    expect(getChatSessionForPrescription).toHaveBeenCalledWith(prescriptionId)
    expect(createChatSession).not.toHaveBeenCalled()
    expect(getChatMessages).toHaveBeenCalledWith(sessionId)
    expect(getLatestPrescription).not.toHaveBeenCalled()

    const input = screen.getByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '현재 화면의 새 질문입니다.' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    expect(await screen.findByText('현재 화면에서 새로 받은 답변입니다.')).toBeTruthy()
    expect(screen.getByText('현재 화면의 새 질문입니다.')).toBeTruthy()
    expect(sendChatMessage).toHaveBeenCalledWith(
      sessionId,
      '현재 화면의 새 질문입니다.',
    )
  })

  it('Home과 Bottom Nav의 /chat 진입은 최신 처방의 기존 session으로 초기 화면을 표시한다', async () => {
    vi.mocked(getChatSessionForPrescription).mockResolvedValue({
      data: {
        session_id: sessionId,
        prescription_id: prescriptionId,
        prescription_version_id: prescriptionVersionId,
        session_status: 'ACTIVE',
        created_at: '2026-09-08T00:00:00Z',
      },
    })
    vi.mocked(getChatMessages).mockResolvedValue({
      data: {
        session_id: sessionId,
        messages: [
          {
            message_id: 'hidden-latest-history',
            role: 'ASSISTANT',
            content: 'latest 경로에서 숨겨야 할 과거 답변',
            generation_status: 'COMPLETED',
            created_at: '2026-09-09T00:00:00Z',
          },
        ],
      },
    })
    vi.mocked(sendChatMessage).mockResolvedValue({
      data: {
        user_message_id: 'latest-user-message',
        assistant_message_id: 'latest-assistant-message',
        session_id: sessionId,
        generation_status: 'COMPLETED',
        content: 'latest 처방의 기존 session 답변입니다.',
        model_name: 'chat-model',
        prompt_version: 'chat-v1',
        created_at: '2026-09-10T00:00:01Z',
        completed_at: '2026-09-10T00:00:02Z',
      },
    })

    renderPage('/chat')

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('latest 경로에서 숨겨야 할 과거 답변')).toBeNull()
    expect(getLatestPrescription).toHaveBeenCalledTimes(1)
    expect(getChatSessionForPrescription).toHaveBeenCalledWith(prescriptionId)
    expect(createChatSession).not.toHaveBeenCalled()
    expect(getChatMessages).toHaveBeenCalledWith(sessionId)

    const input = screen.getByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: 'latest 진입 후 새 질문' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    expect(await screen.findByText('latest 처방의 기존 session 답변입니다.')).toBeTruthy()
    expect(screen.getByText('latest 진입 후 새 질문')).toBeTruthy()
    expect(sendChatMessage).toHaveBeenCalledWith(
      sessionId,
      'latest 진입 후 새 질문',
    )
  })

  it('잘못된 prescription_id는 latest 조회 없이 등록 gate를 표시한다', async () => {
    renderPage('/chat?prescription_id=invalid-id')

    expect(
      await screen.findByRole('heading', { name: /먼저 처방전을 등록해 주세요/ }),
    ).toBeTruthy()
    expect(getLatestPrescription).not.toHaveBeenCalled()
    expect(getChatSessionForPrescription).not.toHaveBeenCalled()
    expect(createChatSession).not.toHaveBeenCalled()
    expect(getChatMessages).not.toHaveBeenCalled()
  })

  it('질문 예시는 API 호출 없이 입력창에만 채우고 일정 CTA는 비활성화한다', async () => {
    renderPage()

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '아침 약은 언제 먹나요?' }))

    expect(sendChatMessage).not.toHaveBeenCalled()
    expect(screen.getByLabelText('복약 질문')).toHaveProperty(
      'value',
      '아침 약은 언제 먹나요?',
    )
    expect(
      screen.getByRole('button', { name: '복약 일정 설정하기 (준비 중)' }),
    ).toHaveProperty('disabled', true)
  })

  it('활성 처방이 없을 때 등록 CTA가 새 처방 등록 intent로 이동한다', async () => {
    vi.mocked(getLatestPrescription).mockRejectedValue(
      new ApiError(404, '처방을 찾을 수 없습니다.', 'PRESCRIPTION_NOT_FOUND'),
    )
    renderPage('/chat')

    expect(
      await screen.findByRole('heading', { name: /먼저 처방전을 등록해 주세요/ }),
    ).toBeTruthy()
    expect(getLatestPrescription).toHaveBeenCalledTimes(1)
    expect(getChatSessionForPrescription).not.toHaveBeenCalled()
    expect(createChatSession).not.toHaveBeenCalled()
    expect(getChatMessages).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '도지' }).getAttribute('aria-current')).toBe(
      'page',
    )
    expect(screen.getByRole('button', { name: '일정 (준비 중)' })).toHaveProperty(
      'disabled',
      true,
    )
    fireEvent.click(screen.getByRole('button', { name: '처방전 등록하기' }))
    expect(screen.getByText('처방전 등록 화면')).toBeTruthy()
    expect(screen.getByTestId('upload-intent').textContent).toBe('new-prescription')
  })

  it.each([
    ['network', new TypeError('Failed to fetch')],
    ['5xx', new ApiError(503, '현재 서비스를 사용할 수 없습니다.')],
    [
      '다른 404 code',
      new ApiError(404, '다른 리소스를 찾을 수 없습니다.', 'OTHER_NOT_FOUND'),
    ],
  ])('latest %s 오류를 처방 없음으로 오인하지 않는다', async (_label, error) => {
    vi.mocked(getLatestPrescription).mockRejectedValue(error)

    renderPage('/chat')

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(
      screen.queryByRole('heading', { name: /먼저 처방전을 등록해 주세요/ }),
    ).toBeNull()
    expect(getChatSessionForPrescription).not.toHaveBeenCalled()
    expect(createChatSession).not.toHaveBeenCalled()
    expect(getChatMessages).not.toHaveBeenCalled()
  })

  it('인증 API가 401을 반환하면 로그인 안내로 전환한다', async () => {
    sessionStorage.setItem(`dosey_chat_session:${prescriptionId}`, 'previous-session')
    vi.mocked(getChatSessionForPrescription).mockRejectedValue(
      new ApiError(401, '로그인이 필요합니다.'),
    )
    renderPage()

    expect(
      await screen.findByText('로그인 후 복약 챗봇을 이용해 주세요'),
    ).toBeTruthy()
    expect(getChatSessionForPrescription).toHaveBeenCalledWith(prescriptionId)
    expect(createChatSession).not.toHaveBeenCalled()
    expect(getChatMessages).not.toHaveBeenCalled()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem(`dosey_chat_session:${prescriptionId}`)).toBeNull()
  })

  it.each([
    [
      'PRESCRIPTION_NOT_FOUND 404',
      new ApiError(
        404,
        '처방을 찾을 수 없습니다.',
        'PRESCRIPTION_NOT_FOUND',
      ),
    ],
    ['network', new TypeError('Failed to fetch')],
    ['5xx', new ApiError(503, '현재 서비스를 사용할 수 없습니다.')],
  ])('%s rediscovery 실패 시 session을 생성하지 않는다', async (_label, error) => {
    vi.mocked(getChatSessionForPrescription).mockRejectedValue(error)

    renderPage()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(getChatSessionForPrescription).toHaveBeenCalledWith(prescriptionId)
    expect(createChatSession).not.toHaveBeenCalled()
    expect(getChatMessages).not.toHaveBeenCalled()
  })

  it.each([
    [
      'PRESCRIPTION_NOT_FOUND 404',
      new ApiError(
        404,
        '처방을 찾을 수 없습니다.',
        'PRESCRIPTION_NOT_FOUND',
      ),
    ],
    ['401', new ApiError(401, '로그인이 필요합니다.')],
    ['network', new TypeError('Failed to fetch')],
    ['5xx', new ApiError(503, '현재 서비스를 사용할 수 없습니다.')],
  ])('StrictMode injected service의 %s rediscovery 실패에서 POST하지 않는다', async (
    label,
    error,
  ) => {
    const rediscoveryRequest = deferred<
      Awaited<ReturnType<typeof getChatSessionForPrescription>>
    >()
    const injectedGetSession = vi
      .fn<typeof getChatSessionForPrescription>()
      .mockReturnValue(rediscoveryRequest.promise)
    const injectedCreateSession = vi.fn<typeof createChatSession>()

    renderPage(`/chat?prescription_id=${prescriptionId}`, {
      strict: true,
      services: createServices({
        createChatSession: injectedCreateSession,
        getChatSessionForPrescription: injectedGetSession,
      }),
    })

    await waitFor(() => expect(injectedGetSession).toHaveBeenCalledTimes(1))
    await act(async () => rediscoveryRequest.reject(error))

    if (label === '401') {
      expect(
        await screen.findByText('로그인 후 복약 챗봇을 이용해 주세요'),
      ).toBeTruthy()
    } else {
      expect(await screen.findByRole('alert')).toBeTruthy()
    }
    expect(injectedGetSession).toHaveBeenCalledTimes(1)
    expect(injectedCreateSession).not.toHaveBeenCalled()
    expect(getChatMessages).not.toHaveBeenCalled()
  })

  it('메시지 전송이 401이면 로그인 안내로 전환한다', async () => {
    vi.mocked(sendChatMessage).mockRejectedValue(
      new ApiError(401, '로그인이 필요합니다.'),
    )
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '인증 확인 질문' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    expect(
      await screen.findByText('로그인 후 복약 챗봇을 이용해 주세요'),
    ).toBeTruthy()
    expect(sendChatMessage).toHaveBeenCalledTimes(1)
    expect(getChatMessages).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem(`dosey_chat_session:${prescriptionId}`)).toBeNull()
  })

  it('메시지 실패 후 이력 복구가 401이면 중앙 세션을 정리한다', async () => {
    vi.mocked(sendChatMessage).mockRejectedValue(new TypeError('Failed to fetch'))
    vi.mocked(getChatMessages)
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: [] },
      })
      .mockRejectedValueOnce(
        new ApiError(401, '로그인이 필요합니다.', 'EXPIRED_TOKEN'),
      )
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '인증 복구 확인 질문' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))

    expect(
      await screen.findByText('로그인 후 복약 챗봇을 이용해 주세요'),
    ).toBeTruthy()
    expect(sendChatMessage).toHaveBeenCalledTimes(1)
    expect(getChatMessages).toHaveBeenCalledTimes(2)
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem(`dosey_chat_session:${prescriptionId}`)).toBeNull()
  })

  it('CHAT_SESSION_NOT_FOUND 404일 때만 새 session을 생성한다', async () => {
    vi.mocked(getChatSessionForPrescription).mockRejectedValue(
      new ApiError(
        404,
        '대화 세션을 찾을 수 없습니다.',
        'CHAT_SESSION_NOT_FOUND',
      ),
    )

    renderPage()

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(getChatSessionForPrescription).toHaveBeenCalledWith(prescriptionId)
    expect(createChatSession).toHaveBeenCalledWith(prescriptionId)
    expect(createChatSession).toHaveBeenCalledTimes(1)
    expect(getChatMessages).toHaveBeenCalledWith(sessionId)
    expect(sessionStorage.length).toBe(0)
  })

  it('처방을 전환해도 Chat session id를 브라우저에 저장하지 않는다', async () => {
    vi.mocked(getChatSessionForPrescription).mockImplementation((requestedId) =>
      Promise.resolve({
        data: {
          session_id:
            requestedId === prescriptionId ? sessionId : secondSessionId,
          prescription_id: requestedId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-08-24T00:00:00Z',
        },
      }),
    )
    vi.mocked(getChatMessages).mockResolvedValue({
      data: { session_id: sessionId, messages: [] },
    })
    renderPage()

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    fireEvent.click(screen.getByText('두 번째 처방으로 이동'))

    await waitFor(() =>
      expect(getChatSessionForPrescription).toHaveBeenCalledWith(
        secondPrescriptionId,
      ),
    )
    expect(createChatSession).not.toHaveBeenCalled()
    expect(sessionStorage.length).toBe(0)
  })

  it('처방 A 초기화 응답이 늦게 와도 처방 B 상태를 덮어쓰지 않는다', async () => {
    let resolveFirstSession: (
      value: Awaited<ReturnType<typeof createChatSession>>,
    ) => void = () => undefined
    vi.mocked(createChatSession).mockImplementation((requestedId) => {
      if (requestedId === prescriptionId) {
        return new Promise((resolve) => {
          resolveFirstSession = resolve
        })
      }
      return Promise.resolve({
        data: {
          session_id: secondSessionId,
          prescription_id: secondPrescriptionId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-08-24T00:00:00Z',
        },
      })
    })
    vi.mocked(getChatMessages).mockImplementation((requestedSessionId) =>
      Promise.resolve({
        data: {
          session_id: requestedSessionId,
          messages: [
            {
              message_id: `message-${requestedSessionId}`,
              role: 'ASSISTANT',
              content:
                requestedSessionId === secondSessionId
                  ? '처방 B의 대화'
                  : '처방 A의 대화',
              generation_status: 'COMPLETED',
              created_at: '2026-08-24T00:00:01Z',
            },
          ],
        },
      }),
    )
    renderPage()

    fireEvent.click(screen.getByText('두 번째 처방으로 이동'))
    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('처방 B의 대화')).toBeNull()
    expect(getChatMessages).toHaveBeenCalledWith(secondSessionId)

    await act(async () =>
      resolveFirstSession({
        data: {
          session_id: sessionId,
          prescription_id: prescriptionId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-08-24T00:00:00Z',
        },
      }),
    )

    expect(screen.getByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('처방 B의 대화')).toBeNull()
    expect(screen.queryByText('처방 A의 대화')).toBeNull()
    expect(getChatMessages).not.toHaveBeenCalledWith(sessionId)
  })

  it('처방 B route commit 순간에도 처방 A 데이터를 렌더링하지 않는다', async () => {
    const committedBodies: string[] = []
    let resolveSecondHistory: (
      value: Awaited<ReturnType<typeof getChatMessages>>,
    ) => void = () => undefined
    vi.mocked(getChatSessionForPrescription).mockImplementation((requestedId) =>
      Promise.resolve({
        data: {
          session_id:
            requestedId === prescriptionId ? sessionId : secondSessionId,
          prescription_id: requestedId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-08-24T00:00:00Z',
        },
      }),
    )
    vi.mocked(getChatMessages).mockImplementation((requestedSessionId) => {
      if (requestedSessionId === secondSessionId) {
        return new Promise((resolve) => {
          resolveSecondHistory = resolve
        })
      }
      return Promise.resolve({
        data: {
          session_id: sessionId,
          messages: [
            {
              message_id: 'first-history',
              role: 'ASSISTANT',
              content: '절대 B 화면에 보이면 안 되는 처방 A 메시지',
              generation_status: 'COMPLETED',
              created_at: '2026-08-24T00:00:01Z',
            },
          ],
        },
      })
    })
    renderPage(`/chat?prescription_id=${prescriptionId}`, {
      onLocationCommit: (search, bodyText) => {
        if (search.includes(secondPrescriptionId)) committedBodies.push(bodyText)
      },
    })

    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(getChatMessages).toHaveBeenCalledWith(sessionId)
    expect(
      screen.queryByText('절대 B 화면에 보이면 안 되는 처방 A 메시지'),
    ).toBeNull()
    fireEvent.click(screen.getByText('두 번째 처방으로 이동'))

    expect(committedBodies).toHaveLength(1)
    expect(committedBodies[0]).not.toContain(
      '절대 B 화면에 보이면 안 되는 처방 A 메시지',
    )
    expect(
      screen.queryByText('절대 B 화면에 보이면 안 되는 처방 A 메시지'),
    ).toBeNull()
    expect(screen.getByText('대화를 불러오고 있어요.')).toBeTruthy()
    await waitFor(() =>
      expect(getChatMessages).toHaveBeenCalledWith(secondSessionId),
    )

    await act(async () =>
      resolveSecondHistory({
        data: { session_id: secondSessionId, messages: [] },
      }),
    )
    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
  })

  it('처방 A 초기화 오류와 finally가 늦게 와도 처방 B 상태를 변경하지 않는다', async () => {
    let rejectFirstSession: (reason: unknown) => void = () => undefined
    vi.mocked(createChatSession).mockImplementation((requestedId) => {
      if (requestedId === prescriptionId) {
        return new Promise((_, reject) => {
          rejectFirstSession = reject
        })
      }
      return Promise.resolve({
        data: {
          session_id: secondSessionId,
          prescription_id: secondPrescriptionId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-08-24T00:00:00Z',
        },
      })
    })
    vi.mocked(getChatMessages).mockResolvedValue({
      data: {
        session_id: secondSessionId,
        messages: [
          {
            message_id: 'second-message',
            role: 'ASSISTANT',
            content: '오류 없이 유지되는 처방 B 대화',
            generation_status: 'COMPLETED',
            created_at: '2026-08-24T00:00:01Z',
          },
        ],
      },
    })
    renderPage()

    fireEvent.click(screen.getByText('두 번째 처방으로 이동'))
    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('오류 없이 유지되는 처방 B 대화')).toBeNull()
    expect(getChatMessages).toHaveBeenCalledWith(secondSessionId)

    await act(async () =>
      rejectFirstSession(new ApiError(503, '처방 A의 늦은 오류')),
    )

    expect(screen.getByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('오류 없이 유지되는 처방 B 대화')).toBeNull()
    expect(screen.queryByText('처방 A의 늦은 오류')).toBeNull()
    expect(screen.queryByText('대화를 불러오고 있어요.')).toBeNull()
    expect(screen.getByLabelText('복약 질문')).toHaveProperty(
      'disabled',
      false,
    )
  })

  it('전송 중 처방이 변경되면 이전 처방의 응답을 무시한다', async () => {
    let resolveFirstMessage: (
      value: Awaited<ReturnType<typeof sendChatMessage>>,
    ) => void = () => undefined
    vi.mocked(sendChatMessage).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFirstMessage = resolve
        }),
    )
    vi.mocked(createChatSession).mockImplementation((requestedId) =>
      Promise.resolve({
        data: {
          session_id:
            requestedId === prescriptionId ? sessionId : secondSessionId,
          prescription_id: requestedId,
          prescription_version_id: prescriptionVersionId,
          session_status: 'ACTIVE',
          created_at: '2026-08-24T00:00:00Z',
        },
      }),
    )
    vi.mocked(getChatMessages).mockImplementation((requestedSessionId) =>
      Promise.resolve({
        data: {
          session_id: requestedSessionId,
          messages:
            requestedSessionId === secondSessionId
              ? [
                  {
                    message_id: 'second-history',
                    role: 'ASSISTANT',
                    content: '처방 B의 기존 대화',
                    generation_status: 'COMPLETED',
                    created_at: '2026-08-24T00:00:01Z',
                  },
                ]
              : [],
        },
      }),
    )
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    fireEvent.change(input, { target: { value: '처방 A 질문' } })
    fireEvent.click(screen.getByRole('button', { name: '질문 전송' }))
    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByText('두 번째 처방으로 이동'))
    expect(await screen.findByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('처방 B의 기존 대화')).toBeNull()
    expect(getChatMessages).toHaveBeenCalledWith(secondSessionId)

    await act(async () =>
      resolveFirstMessage({
        data: {
          user_message_id: 'stale-user',
          assistant_message_id: 'stale-assistant',
          session_id: sessionId,
          generation_status: 'COMPLETED',
          content: '처방 A의 늦은 답변',
          model_name: 'chat-model',
          prompt_version: 'chat-v1',
          created_at: '2026-08-24T00:00:01Z',
          completed_at: '2026-08-24T00:00:02Z',
        },
      }),
    )

    expect(screen.getByText('무엇을 도와드릴까요?')).toBeTruthy()
    expect(screen.queryByText('처방 B의 기존 대화')).toBeNull()
    expect(screen.queryByText('처방 A 질문')).toBeNull()
    expect(screen.queryByText('처방 A의 늦은 답변')).toBeNull()
    expect(screen.getByLabelText('복약 질문')).toHaveProperty('value', '')
  })
})
