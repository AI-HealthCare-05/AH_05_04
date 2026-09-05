import { cleanup, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { getCurrentUser } from '../src/api/users'
import AppRouter from '../src/routes/AppRouter'

vi.mock('../src/api/users', () => ({
  getCurrentUser: vi.fn(),
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
  vi.clearAllMocks()
})

describe('인증 상태별 AppRouter 이동', () => {
  it('비로그인 사용자가 첫 화면에 접속하면 시작 화면을 표시한다', () => {
    renderRoute('/')

    expect(screen.getByRole('heading', { name: 'AI 복약 파트너' })).toBeTruthy()
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

  it('비로그인 사용자가 회원 전용 화면에 직접 접속하면 로그인 화면으로 이동한다', () => {
    renderRoute('/profile')

    expect(screen.getByRole('heading', { name: '다시 만나서 반가워요' })).toBeTruthy()
  })

  it('로그인 사용자가 회원 전용 화면에 직접 접속하면 화면 접근을 허용한다', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/profile')

    expect(await screen.findByText(CURRENT_USER.email)).toBeTruthy()
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

  it('남아 있는 토큰이 만료된 경우 로그인 화면 진입을 허용한다', async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(
      new ApiError(401, '만료된 토큰입니다.', 'EXPIRED_TOKEN'),
    )
    localStorage.setItem('access_token', 'stale-access-token')
    renderRoute('/login')

    expect(await screen.findByRole('heading', { name: '다시 만나서 반가워요' })).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(getCurrentUser).toHaveBeenCalledTimes(1)
  })
})
