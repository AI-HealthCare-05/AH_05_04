import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { getGuide, getGuideForPrescription } from '../src/api/guides'
import { getLatestPrescription } from '../src/api/prescriptions'
import { getCurrentUser } from '../src/api/users'
import AppRouter from '../src/routes/AppRouter'

vi.mock('../src/api/users', () => ({
  getCurrentUser: vi.fn(),
}))

vi.mock('../src/api/guides', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/guides')>()),
  getGuide: vi.fn(),
  getGuideForPrescription: vi.fn(),
}))

vi.mock('../src/api/prescriptions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/prescriptions')>()),
  getLatestPrescription: vi.fn(),
}))

const CURRENT_USER = {
  id: '00000000-0000-4000-8000-000000000105',
  name: '라우터 사용자',
  email: 'router@example.com',
  phone_number: null,
  birthday: null,
  gender: null,
  created_at: '2026-08-28T00:00:00Z',
}

function renderRoute(path: string, strict = false) {
  window.history.pushState({}, '', path)
  return render(
    strict ? (
      <React.StrictMode>
        <AppRouter />
      </React.StrictMode>
    ) : (
      <AppRouter />
    ),
  )
}

afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
})

describe('인증 상태별 AppRouter 이동', () => {
  it('개발 환경의 /dev/preview는 인증 API 없이 열린다', async () => {
    renderRoute('/dev/preview')

    expect(await screen.findByText('DEV PREVIEW')).toBeTruthy()
    expect(screen.getByText('Mock data only')).toBeTruthy()
    expect(getCurrentUser).not.toHaveBeenCalled()
  })

  it('Preview CTA는 인증된 실제 제품 route로 빠져나가지 않는다', async () => {
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/dev/preview?screen=guide&scenario=completed')

    fireEvent.click(
      await screen.findByRole('button', {
        name: '복약 챗봇 도지와 이야기하기',
      }),
    )

    expect(window.location.pathname).toBe('/dev/preview')
    expect(getCurrentUser).not.toHaveBeenCalled()
  })

  it('비로그인 사용자가 첫 화면에 접속하면 /start로 이동해 시작 화면을 표시한다', async () => {
    renderRoute('/')

    expect(
      await screen.findByRole('heading', {
        name: '처방과 일정을 쉽게 살펴봐요.',
      }),
    ).toBeTruthy()
    expect(window.location.pathname).toBe('/start')
  })

  it('비로그인 사용자가 /start에 접속하면 시작 화면을 표시한다', () => {
    renderRoute('/start')

    expect(
      screen.getByRole('heading', {
        name: '처방과 일정을 쉽게 살펴봐요.',
      }),
    ).toBeTruthy()
    expect(window.location.pathname).toBe('/start')
  })

  it('로그인 사용자가 첫 화면에 접속하면 홈 화면을 표시한다', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/')

    expect(await screen.findByRole('heading', { name: '오늘도 건강한 하루 되세요' })).toBeTruthy()
    expect(screen.getByText('라우터 사용자님!')).toBeTruthy()
  })

  it('인증된 / 진입은 users/me를 한 번만 호출하고 조회한 이름으로 HOME을 표시한다', async () => {
    vi.mocked(getCurrentUser)
      .mockResolvedValueOnce(CURRENT_USER)
      .mockRejectedValueOnce(new Error('두 번째 users/me 호출은 발생하면 안 됩니다.'))
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/', true)

    expect(await screen.findByRole('heading', { name: '오늘도 건강한 하루 되세요' })).toBeTruthy()
    expect(screen.getByText('라우터 사용자님!')).toBeTruthy()
    expect(getCurrentUser).toHaveBeenCalledTimes(1)
  })

  it.each([
    '/profile',
    '/guides/00000000-0000-4000-8000-000000000105',
    '/prescriptions/upload',
    '/prescriptions/review',
    '/chat',
  ])('비로그인 사용자가 회원 전용 화면 %s에 직접 접속하면 로그인 화면으로 이동한다', (path) => {
    renderRoute(path)

    expect(screen.getByRole('heading', { name: '다시 만나서 반가워요' })).toBeTruthy()
    expect(window.location.pathname).toBe('/login')
  })

  it('로그인 사용자가 회원 전용 화면에 직접 접속하면 화면 접근을 허용한다', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/profile')

    expect(await screen.findByText(CURRENT_USER.email)).toBeTruthy()
  })

  it('로그아웃으로 임시 상태가 비워진 뒤 재로그인한 /guides 진입은 서버에서 처방과 Guide를 복원한다', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
    vi.mocked(getLatestPrescription).mockResolvedValue({
      data: {
        prescription_id: '44444444-4444-4444-8444-444444444444',
        document_id: '11111111-1111-4111-8111-111111111111',
        prescribed_date: '2026-09-07',
        confirmed_at: '2026-09-07T08:00:00Z',
        medications: [],
      },
    })
    const guideResponse = {
      data: {
        guide_id: '55555555-5555-4555-8555-555555555555',
        prescription_id: '44444444-4444-4444-8444-444444444444',
        generation_status: 'COMPLETED',
        content: '재로그인 뒤 복원한 합성 Guide 내용',
        model_name: 'guide-model',
        prompt_version: 'guide-prompt-v1',
        requested_at: '2026-09-07T08:00:00Z',
        completed_at: '2026-09-07T08:00:03Z',
      },
    }
    vi.mocked(getGuideForPrescription).mockResolvedValue(guideResponse)
    vi.mocked(getGuide).mockResolvedValue(guideResponse)
    localStorage.setItem('access_token', 'relogin-access-token')
    expect(sessionStorage.length).toBe(0)

    renderRoute('/guides')

    expect(await screen.findByText('재로그인 뒤 복원한 합성 Guide 내용')).toBeTruthy()
    expect(getCurrentUser).toHaveBeenCalledTimes(1)
    expect(getLatestPrescription).toHaveBeenCalledTimes(1)
    expect(getGuideForPrescription).toHaveBeenCalledWith(
      '44444444-4444-4444-8444-444444444444',
    )
    expect(getGuide).toHaveBeenCalledWith(
      '55555555-5555-4555-8555-555555555555',
    )
    expect(window.location.pathname).toBe(
      '/guides/55555555-5555-4555-8555-555555555555',
    )
    expect(localStorage.length).toBe(1)
    expect(sessionStorage.length).toBe(0)
  })

  it('로그인 사용자가 메뉴 화면에 직접 접속하면 최신 메뉴를 표시한다', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/menu')

    expect(await screen.findByRole('heading', { name: '메뉴' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '사용자 정보' })).toBeTruthy()
  })

  it('로그인 사용자가 로그인 화면에 접속하면 홈 화면으로 이동한다', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/login')

    expect(await screen.findByRole('heading', { name: '오늘도 건강한 하루 되세요' })).toBeTruthy()
  })

  it('로그인 사용자가 회원가입 화면에 접속하면 홈 화면으로 이동한다', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/signup')

    expect(await screen.findByRole('heading', { name: '오늘도 건강한 하루 되세요' })).toBeTruthy()
  })

  it('로그인 사용자가 /start에 접속하면 홈 화면으로 이동한다', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/start')

    expect(await screen.findByRole('heading', { name: '오늘도 건강한 하루 되세요' })).toBeTruthy()
    expect(window.location.pathname).toBe('/')
  })

  it('남아 있는 토큰이 만료된 경우 로그인 화면 진입을 허용한다', async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(
      new ApiError(401, '만료된 토큰입니다.', 'EXPIRED_TOKEN'),
    )
    localStorage.setItem('access_token', 'stale-access-token')
    sessionStorage.setItem('dosey_ocr_job_recovery:v1', '{"job":"active"}')
    sessionStorage.setItem('dosey_chat_session:fixture-prescription', 'fixture-session')
    renderRoute('/login')

    expect(await screen.findByRole('heading', { name: '다시 만나서 반가워요' })).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).toBeNull()
    expect(sessionStorage.getItem('dosey_chat_session:fixture-prescription')).toBeNull()
    expect(getCurrentUser).toHaveBeenCalledTimes(1)
  })
})
