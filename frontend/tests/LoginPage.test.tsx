import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { login } from '../src/api/auth'
import { ApiError } from '../src/api/client'
import { getUnconfirmedCheckins } from '../src/api/medicationCheckinBacklog'
import { putMedicationCheckin } from '../src/api/medicationCheckins'
import LoginPage from '../src/pages/LoginPage'

vi.mock('../src/api/auth', () => ({
  login: vi.fn(),
}))

vi.mock('../src/api/medicationCheckinBacklog', () => ({
  getUnconfirmedCheckins: vi.fn(),
}))

vi.mock('../src/api/medicationCheckins', () => ({
  putMedicationCheckin: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  vi.mocked(login).mockResolvedValue({ access_token: 'synthetic-token' })
  vi.mocked(getUnconfirmedCheckins).mockResolvedValue({
    data: { items: [], next_cursor: null },
  })
})

afterEach(() => {
  cleanup()
})

describe('LoginPage', () => {
  function HomeStateProbe() {
    const location = useLocation()

    return (
      <div>
        홈 화면
        <span>
          {String(
            (
              location.state as {
                showPrescriptionOnboarding?: boolean
              } | null
            )?.showPrescriptionOnboarding === true,
          )}
        </span>
      </div>
    )
  }

  function fillValidForm() {
    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'dosey@example.com' },
    })
    fireEvent.change(screen.getByLabelText('비밀번호'), {
      target: { value: 'Password1!' },
    })
  }

  function renderPage(fromSignup = false) {
    return render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/login',
            state: fromSignup ? { fromSignup: true } : null,
          },
        ]}
      >
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<div>회원가입 화면</div>} />
          <Route path="/" element={<HomeStateProbe />} />
          <Route path="/schedule/unconfirmed" element={<div>미확인 기록 보완 화면</div>} />
        </Routes>
      </MemoryRouter>,
    )
  }

  function renderPushRecoveryPage(returnTo: string) {
    return render(
      <MemoryRouter initialEntries={[{ pathname: '/login', state: { returnTo } }]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/notifications" element={<LocationProbe />} />
          <Route path="/" element={<HomeStateProbe />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  function LocationProbe() {
    const location = useLocation()
    return <div>복구 화면 {location.pathname}{location.search}</div>
  }

  it('#395 가입 직후 로그인 성공 시 Home onboarding 상태를 전달한다', async () => {
    renderPage(true)
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(await screen.findByText('홈 화면')).toBeTruthy()
    expect(screen.getByText('true')).toBeTruthy()
  })

  it('#395 일반 로그인 성공 시 Home onboarding 상태를 전달하지 않는다', async () => {
    renderPage(false)
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(await screen.findByText('홈 화면')).toBeTruthy()
    expect(screen.getByText('false')).toBeTruthy()
  })

  it('Push 클릭 뒤 로그인하면 검증된 알림 target을 한 번 복구한다', async () => {
    renderPushRecoveryPage('/notifications?push_notification_id=synthetic-notification')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(await screen.findByText('복구 화면 /notifications?push_notification_id=synthetic-notification')).toBeTruthy()
    expect(getUnconfirmedCheckins).not.toHaveBeenCalled()
  })

  it('외부 origin 또는 허용되지 않은 로그인 target은 폐기한다', async () => {
    renderPushRecoveryPage('https://example.test/schedule')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(await screen.findByText('홈 화면')).toBeTruthy()
  })


  it('AUTH-02 Figma 문구와 회원가입 경로를 렌더링한다', () => {
    renderPage()

    expect(screen.getByRole('heading', { name: '다시 만나서 반가워요' })).toBeTruthy()
    expect(
      screen.getByText('로그인하고 Dosey 도지에서 복약 관리를 이어가세요.'),
    ).toBeTruthy()
    expect(screen.getByText('의료정보는 로그인한 본인만 볼 수 있어요.')).toBeTruthy()
    expect(
      screen.getByRole('link', { name: '계정이 없다면 회원가입' }).getAttribute('href'),
    ).toBe('/signup')
  })

  it('입력 검증 실패 시 API를 호출하지 않고 aria-invalid를 설정한다', () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(login).not.toHaveBeenCalled()
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByLabelText('비밀번호').getAttribute('aria-invalid')).toBe('true')
    expect(document.activeElement).toBe(screen.getByLabelText('이메일'))
    expect(screen.getByText('이메일을 입력해 주세요.')).toBeTruthy()
    expect(screen.getByText('비밀번호를 8자 이상 입력해 주세요.')).toBeTruthy()
  })

  it('이메일이 40자를 넘으면 API를 호출하지 않고 안내한다', () => {
    renderPage()

    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: `${'a'.repeat(35)}@example.com` },
    })
    fireEvent.change(screen.getByLabelText('비밀번호'), {
      target: { value: 'Password1!' },
    })
    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(login).not.toHaveBeenCalled()
    expect(screen.getByText('올바른 이메일 주소를 40자 이하로 입력해 주세요.')).toBeTruthy()
  })

  it('로그인 성공 시 토큰을 저장하고 홈으로 이동한다', async () => {
    sessionStorage.setItem('dosey_ocr_job_recovery:v1', '{"stale":true}')
    sessionStorage.setItem('dosey_chat_session:previous-prescription', 'previous-session')
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    await waitFor(() => {
      expect(login).toHaveBeenCalledWith({
        email: 'dosey@example.com',
        password: 'Password1!',
      })
    })
    expect(await screen.findByText('홈 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBe('synthetic-token')
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).toBeNull()
    expect(sessionStorage.getItem('dosey_chat_session:previous-prescription')).toBeNull()
  })

  it('로그인 성공 후 미확인 기록이 있으면 보완 화면으로 이동한다', async () => {
    vi.mocked(getUnconfirmedCheckins).mockResolvedValue({
      data: {
        items: [{
          checkin_id: '11111111-1111-4111-8111-111111111111',
          occurrence_id: '22222222-2222-4222-8222-222222222222',
          prescription_id: '33333333-3333-4333-8333-333333333333',
          prescription_version_id: '44444444-4444-4444-8444-444444444444',
          prescription_version_medication_id: '55555555-5555-4555-8555-555555555555',
          medication_name: '합성 혈압약',
          strength_text: '5mg',
          scheduled_local_date: '2026-09-14',
          scheduled_at: '2026-09-14T00:00:00Z',
          confirmation_deadline_at: '2026-09-14T01:00:00Z',
          status: 'UNCONFIRMED',
          revision: 1,
        }],
        next_cursor: null,
      },
    })
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(await screen.findByText('미확인 기록 보완 화면')).toBeTruthy()
    expect(getUnconfirmedCheckins).toHaveBeenCalledWith({ limit: 1 })
    expect(putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('로그인 성공 후 미확인 기록이 없으면 기존 홈 흐름을 유지한다', async () => {
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(await screen.findByText('홈 화면')).toBeTruthy()
    expect(getUnconfirmedCheckins).toHaveBeenCalledWith({ limit: 1 })
    expect(putMedicationCheckin).not.toHaveBeenCalled()
  })

  it.each([
    new TypeError('Failed to fetch'),
    new ApiError(503, 'raw backend detail', 'SERVICE_UNAVAILABLE'),
  ])('미확인 기록 조회 실패가 %s이면 로그인 성공을 유지하고 홈으로 이동한다', async (error) => {
    vi.mocked(getUnconfirmedCheckins).mockRejectedValue(error)
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(await screen.findByText('홈 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBe('synthetic-token')
    expect(screen.queryByText('raw backend detail')).toBeNull()
    expect(putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('미확인 기록 200 응답 구조가 잘못되면 성공으로 추정해 Home으로 이동하지 않는다', async () => {
    vi.mocked(getUnconfirmedCheckins).mockResolvedValue(
      {} as Awaited<ReturnType<typeof getUnconfirmedCheckins>>,
    )
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(await screen.findByText('네트워크 연결을 확인하고 다시 시도해 주세요.')).toBeTruthy()
    expect(screen.queryByText('홈 화면')).toBeNull()
    expect(screen.queryByText('미확인 기록 보완 화면')).toBeNull()
    expect(putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('미확인 기록 조회가 401이면 기존 인증 오류 흐름으로 처리한다', async () => {
    vi.mocked(getUnconfirmedCheckins).mockRejectedValue(
      new ApiError(401, '로그인이 필요합니다.', 'UNAUTHORIZED'),
    )
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(await screen.findByText('로그인이 필요합니다.')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(screen.queryByText('홈 화면')).toBeNull()
    expect(putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('Backend 401 자격 증명 오류를 그대로 안내한다', async () => {
    vi.mocked(login).mockRejectedValue(
      new ApiError(401, '이메일 또는 비밀번호가 올바르지 않습니다.', 'UNAUTHORIZED'),
    )
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(
      await screen.findByText('이메일 또는 비밀번호가 올바르지 않습니다.'),
    ).toBeTruthy()
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByLabelText('비밀번호').getAttribute('aria-invalid')).toBe('true')
  })

  it('네트워크 실패를 자격 증명 오류와 구분해 안내한다', async () => {
    vi.mocked(login).mockRejectedValue(new TypeError('Failed to fetch'))
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))

    expect(
      await screen.findByText('네트워크 연결을 확인하고 다시 시도해 주세요.'),
    ).toBeTruthy()
    expect(screen.queryByText('이메일 또는 비밀번호가 올바르지 않습니다.')).toBeNull()
  })

  it('로그인 요청 중 버튼을 비활성화해 중복 제출을 막는다', async () => {
    let resolveLogin: ((value: { access_token: string }) => void) | undefined
    vi.mocked(login).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveLogin = resolve
        }),
    )
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '로그인' }))
    const loadingButton = await screen.findByRole('button', { name: '로그인 중...' })
    fireEvent.click(loadingButton)

    expect(loadingButton).toHaveProperty('disabled', true)
    expect(login).toHaveBeenCalledTimes(1)

    resolveLogin?.({ access_token: 'synthetic-token' })
    expect(await screen.findByText('홈 화면')).toBeTruthy()
  })
})
