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
  sendChatMessage,
} from '../src/api/chat'
import { ApiError } from '../src/api/client'
import ChatPage from '../src/pages/ChatPage'

vi.mock('../src/api/chat', () => ({
  createChatSession: vi.fn(),
  getChatMessages: vi.fn(),
  sendChatMessage: vi.fn(),
}))

const prescriptionId = '11111111-1111-4111-8111-111111111111'
const sessionId = '22222222-2222-4222-8222-222222222222'
const secondPrescriptionId = '33333333-3333-4333-8333-333333333333'
const secondSessionId = '44444444-4444-4444-8444-444444444444'

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

function renderPage(
  entry = `/chat?prescription_id=${prescriptionId}`,
  options: {
    strict?: boolean
    onLocationCommit?: (search: string, bodyText: string) => void
  } = {},
) {
  const content = (
    <MemoryRouter initialEntries={[entry]}>
      <NavigationHarness />
      <LocationCommitProbe onCommit={options.onLocationCommit} />
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/login" element={<div>로그인 화면</div>} />
      </Routes>
    </MemoryRouter>
  )

  return render(
    options.strict ? <React.StrictMode>{content}</React.StrictMode> : content,
  )
}

function mockSessionCreation() {
  vi.mocked(createChatSession).mockResolvedValue({
    data: {
      session_id: sessionId,
      prescription_id: prescriptionId,
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
  mockSessionCreation()
})

afterEach(() => {
  cleanup()
})

describe('ChatPage', () => {
  it('확정된 prescription_id로 Chat session을 생성한다', async () => {
    renderPage()

    expect(await screen.findByText('무엇을 확인하고 싶으신가요?')).toBeTruthy()
    expect(createChatSession).toHaveBeenCalledWith(prescriptionId)
    expect(getChatMessages).toHaveBeenCalledWith(sessionId)
    expect(
      sessionStorage.getItem(`dosey_chat_session:${prescriptionId}`),
    ).toBe(sessionId)
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

    await waitFor(() => expect(createChatSession).toHaveBeenCalledTimes(1))
    await act(async () =>
      resolveSession({
        data: {
          session_id: sessionId,
          prescription_id: prescriptionId,
          session_status: 'ACTIVE',
          created_at: '2026-08-24T00:00:00Z',
        },
      }),
    )

    expect(await screen.findByText('무엇을 확인하고 싶으신가요?')).toBeTruthy()
    expect(createChatSession).toHaveBeenCalledTimes(1)
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

  it('메시지 전송 중 입력과 버튼을 막아 중복 제출하지 않는다', async () => {
    let resolveMessage: (
      value: Awaited<ReturnType<typeof sendChatMessage>>,
    ) => void = () => undefined
    vi.mocked(sendChatMessage).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveMessage = resolve
        }),
    )
    renderPage()

    const input = await screen.findByLabelText('복약 질문')
    const sendButton = screen.getByRole('button', { name: '질문 전송' })
    fireEvent.change(input, { target: { value: '중복 전송 확인' } })
    fireEvent.click(sendButton)

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledTimes(1))
    expect(input).toHaveProperty('disabled', true)
    expect(sendButton).toHaveProperty('disabled', true)
    fireEvent.click(sendButton)
    expect(sendChatMessage).toHaveBeenCalledTimes(1)

    await act(async () =>
      resolveMessage({
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
  })

  it('API 오류를 안내하고 대화를 다시 불러올 수 있다', async () => {
    vi.mocked(createChatSession).mockRejectedValueOnce(
      new ApiError(503, '현재 서비스를 사용할 수 없습니다.'),
    )
    renderPage()

    expect(
      await screen.findByText('현재 서비스를 사용할 수 없습니다.'),
    ).toBeTruthy()
    mockSessionCreation()
    fireEvent.click(screen.getByRole('button', { name: '대화 다시 불러오기' }))

    expect(await screen.findByText('무엇을 확인하고 싶으신가요?')).toBeTruthy()
    expect(createChatSession).toHaveBeenCalledTimes(2)
  })

  it('메시지 생성 실패 후 저장된 이력을 다시 불러오고 중복 전송을 막는다', async () => {
    vi.mocked(sendChatMessage).mockRejectedValue(
      new ApiError(503, 'AI 서비스에 잠시 연결할 수 없습니다.'),
    )
    vi.mocked(getChatMessages)
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: [] },
      })
      .mockResolvedValueOnce({
        data: {
          session_id: sessionId,
          messages: [
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
      await screen.findByText('AI 서비스에 잠시 연결할 수 없습니다.'),
    ).toBeTruthy()
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

  it('메시지 실패 후 history 재조회도 실패하면 안전한 오류 상태를 유지한다', async () => {
    vi.mocked(sendChatMessage).mockRejectedValue(
      new ApiError(503, 'AI 서비스에 잠시 연결할 수 없습니다.'),
    )
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

    expect(
      await screen.findByText('AI 서비스에 잠시 연결할 수 없습니다.'),
    ).toBeTruthy()
    expect(input).toHaveProperty('value', '')
    expect(input).toHaveProperty('disabled', false)
    expect(sendChatMessage).toHaveBeenCalledTimes(1)
    expect(getChatMessages).toHaveBeenCalledTimes(2)
    expect(
      screen.getByRole('button', { name: '대화 다시 불러오기' }),
    ).toBeTruthy()
  })

  it('저장된 session_id의 기존 대화 이력을 표시한다', async () => {
    sessionStorage.setItem(`dosey_chat_session:${prescriptionId}`, sessionId)
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

    renderPage()

    expect(await screen.findByText('기존 질문입니다.')).toBeTruthy()
    expect(screen.getByText('기존 AI 답변입니다.')).toBeTruthy()
    expect(createChatSession).not.toHaveBeenCalled()
    expect(getChatMessages).toHaveBeenCalledWith(sessionId)
  })

  it.each(['/chat', '/chat?prescription_id=invalid-id'])(
    '누락되거나 잘못된 prescription_id를 차단한다: %s',
    async (entry) => {
      renderPage(entry)

      expect(await screen.findByText('먼저 복약 가이드가 필요해요')).toBeTruthy()
      expect(createChatSession).not.toHaveBeenCalled()
      expect(getChatMessages).not.toHaveBeenCalled()
    },
  )

  it('추천 질문 chip은 표시하지만 합의되지 않은 전송 동작을 실행하지 않는다', async () => {
    renderPage()

    expect(await screen.findByText('무엇을 확인하고 싶으신가요?')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '복용 방법 확인' }))

    expect(sendChatMessage).not.toHaveBeenCalled()
    expect(screen.getByLabelText('복약 질문')).toHaveProperty('value', '')
  })

  it('인증 API가 401을 반환하면 로그인 안내로 전환한다', async () => {
    vi.mocked(createChatSession).mockRejectedValue(
      new ApiError(401, '로그인이 필요합니다.'),
    )
    renderPage()

    expect(
      await screen.findByText('로그인 후 복약 챗봇을 이용해 주세요'),
    ).toBeTruthy()
    expect(createChatSession).toHaveBeenCalledWith(prescriptionId)
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
  })

  it('저장된 session이 404이면 새 session을 생성해 복구한다', async () => {
    const expiredSessionId = '55555555-5555-4555-8555-555555555555'
    sessionStorage.setItem(
      `dosey_chat_session:${prescriptionId}`,
      expiredSessionId,
    )
    vi.mocked(getChatMessages)
      .mockRejectedValueOnce(new ApiError(404, '세션을 찾을 수 없습니다.'))
      .mockResolvedValueOnce({
        data: { session_id: sessionId, messages: [] },
      })

    renderPage()

    expect(await screen.findByText('무엇을 확인하고 싶으신가요?')).toBeTruthy()
    expect(getChatMessages).toHaveBeenNthCalledWith(1, expiredSessionId)
    expect(createChatSession).toHaveBeenCalledWith(prescriptionId)
    expect(getChatMessages).toHaveBeenNthCalledWith(2, sessionId)
    expect(
      sessionStorage.getItem(`dosey_chat_session:${prescriptionId}`),
    ).toBe(sessionId)
  })

  it('prescription별 sessionStorage key를 분리한다', async () => {
    sessionStorage.setItem(`dosey_chat_session:${prescriptionId}`, sessionId)
    vi.mocked(createChatSession).mockResolvedValue({
      data: {
        session_id: secondSessionId,
        prescription_id: secondPrescriptionId,
        session_status: 'ACTIVE',
        created_at: '2026-08-24T00:00:00Z',
      },
    })
    vi.mocked(getChatMessages).mockResolvedValue({
      data: { session_id: sessionId, messages: [] },
    })
    renderPage()

    expect(await screen.findByText('무엇을 확인하고 싶으신가요?')).toBeTruthy()
    fireEvent.click(screen.getByText('두 번째 처방으로 이동'))

    await waitFor(() =>
      expect(createChatSession).toHaveBeenCalledWith(secondPrescriptionId),
    )
    expect(
      sessionStorage.getItem(`dosey_chat_session:${prescriptionId}`),
    ).toBe(sessionId)
    expect(
      sessionStorage.getItem(`dosey_chat_session:${secondPrescriptionId}`),
    ).toBe(secondSessionId)
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
    expect(await screen.findByText('처방 B의 대화')).toBeTruthy()

    await act(async () =>
      resolveFirstSession({
        data: {
          session_id: sessionId,
          prescription_id: prescriptionId,
          session_status: 'ACTIVE',
          created_at: '2026-08-24T00:00:00Z',
        },
      }),
    )

    expect(screen.getByText('처방 B의 대화')).toBeTruthy()
    expect(screen.queryByText('처방 A의 대화')).toBeNull()
    expect(getChatMessages).not.toHaveBeenCalledWith(sessionId)
  })

  it('처방 B route commit 순간에도 처방 A 데이터를 렌더링하지 않는다', async () => {
    const committedBodies: string[] = []
    let resolveSecondHistory: (
      value: Awaited<ReturnType<typeof getChatMessages>>,
    ) => void = () => undefined
    sessionStorage.setItem(`dosey_chat_session:${prescriptionId}`, sessionId)
    sessionStorage.setItem(
      `dosey_chat_session:${secondPrescriptionId}`,
      secondSessionId,
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

    expect(
      await screen.findByText('절대 B 화면에 보이면 안 되는 처방 A 메시지'),
    ).toBeTruthy()
    fireEvent.click(screen.getByText('두 번째 처방으로 이동'))

    expect(committedBodies).toHaveLength(1)
    expect(committedBodies[0]).not.toContain(
      '절대 B 화면에 보이면 안 되는 처방 A 메시지',
    )
    expect(
      screen.queryByText('절대 B 화면에 보이면 안 되는 처방 A 메시지'),
    ).toBeNull()
    expect(screen.getByText('대화를 불러오고 있어요.')).toBeTruthy()

    await act(async () =>
      resolveSecondHistory({
        data: { session_id: secondSessionId, messages: [] },
      }),
    )
    expect(await screen.findByText('무엇을 확인하고 싶으신가요?')).toBeTruthy()
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
    expect(
      await screen.findByText('오류 없이 유지되는 처방 B 대화'),
    ).toBeTruthy()

    await act(async () =>
      rejectFirstSession(new ApiError(503, '처방 A의 늦은 오류')),
    )

    expect(screen.getByText('오류 없이 유지되는 처방 B 대화')).toBeTruthy()
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
    expect(await screen.findByText('처방 B의 기존 대화')).toBeTruthy()

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

    expect(screen.getByText('처방 B의 기존 대화')).toBeTruthy()
    expect(screen.queryByText('처방 A 질문')).toBeNull()
    expect(screen.queryByText('처방 A의 늦은 답변')).toBeNull()
    expect(screen.getByLabelText('복약 질문')).toHaveProperty('value', '')
  })
})
