import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { login } from '../src/api/auth'
import { ApiError } from '../src/api/client'
import LoginPage from '../src/pages/LoginPage'

vi.mock('../src/api/auth', () => ({
  login: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(login).mockResolvedValue({ access_token: 'synthetic-token' })
})

afterEach(() => {
  cleanup()
})

describe('LoginPage', () => {
  function renderPage() {
    return render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<div>회원가입 화면</div>} />
          <Route path="/" element={<div>홈 화면</div>} />
        </Routes>
      </MemoryRouter>,
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
