import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { signup } from '../src/api/auth'
import { ApiError } from '../src/api/client'
import SignupPage from '../src/pages/SignupPage'

vi.mock('../src/api/auth', () => ({
  signup: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(signup).mockResolvedValue({ detail: '회원가입 완료' })
})

afterEach(() => {
  cleanup()
})

describe('SignupPage', () => {
  function renderPage() {
    return render(
      <MemoryRouter initialEntries={['/signup']}>
        <Routes>
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/login" element={<div>로그인 화면</div>} />
        </Routes>
      </MemoryRouter>,
    )
  }

  function fillValidForm() {
    fireEvent.change(screen.getByLabelText('이름'), {
      target: { value: '홍길동' },
    })
    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'dosey@example.com' },
    })
    fireEvent.change(screen.getByLabelText('비밀번호'), {
      target: { value: 'Password1!' },
    })
  }

  it('AUTH-01 Figma 문구와 필수 입력을 렌더링한다', () => {
    renderPage()

    expect(
      screen.getByRole('heading', {
        name: 'Dosey 도지와 복약 관리를 시작해 주세요',
      }),
    ).toBeTruthy()
    expect(
      screen.getByText('의료정보는 본인 확인과 동의 후 안전하게 관리합니다.'),
    ).toBeTruthy()
    expect(
      screen.getByText(
        '서비스 이용약관, 개인정보 수집·이용, 민감정보 처리에 필수 동의합니다.',
      ),
    ).toBeTruthy()
    expect(screen.getByLabelText('이름')).toHaveProperty('required', true)
    expect(screen.getByLabelText('이메일')).toHaveProperty('required', true)
    expect(screen.getByLabelText('비밀번호')).toHaveProperty('required', true)
  })

  it('#224 정상 입력 중 현재 필드의 focus와 입력값을 유지한다', () => {
    renderPage()

    const nameInput = screen.getByLabelText('이름')
    const emailInput = screen.getByLabelText('이메일')

    fireEvent.change(nameInput, {
      target: { value: '홍길동' },
    })
    emailInput.focus()
    fireEvent.change(emailInput, {
      target: { value: 'd' },
    })

    expect(document.activeElement).toBe(emailInput)
    expect(nameInput).toHaveProperty('value', '홍길동')
    expect(emailInput).toHaveProperty('value', 'd')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('빈 값과 Backend 비밀번호 정책 불일치 시 API를 호출하지 않고 오류를 연결한다', () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(signup).not.toHaveBeenCalled()
    expect(screen.getByLabelText('이름').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByLabelText('비밀번호').getAttribute('aria-invalid')).toBe('true')
    expect(document.activeElement).toBe(screen.getByLabelText('이름'))

    fireEvent.change(screen.getByLabelText('이름'), {
      target: { value: '홍길동' },
    })
    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'dosey@example.com' },
    })
    fireEvent.change(screen.getByLabelText('비밀번호'), {
      target: { value: 'password' },
    })
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(
      screen.getByText('8자 이상이며 대문자·소문자·숫자·특수문자를 포함해 주세요.'),
    ).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByLabelText('비밀번호'))
    expect(signup).not.toHaveBeenCalled()
  })

  it('#65 계약의 name, email, password만 회원가입 요청에 포함한다', async () => {
    localStorage.setItem('existing_key', 'preserved')
    renderPage()
    fillValidForm()

    expect(screen.queryByLabelText('성별')).toBeNull()
    expect(screen.queryByLabelText('생년월일')).toBeNull()
    expect(screen.queryByLabelText('휴대전화')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    await waitFor(() => {
      expect(signup).toHaveBeenCalledWith({
        name: '홍길동',
        email: 'dosey@example.com',
        password: 'Password1!',
      })
    })
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('existing_key')).toBe('preserved')
  })

  it('중복 이메일 Backend 오류를 이메일 입력에 연결한다', async () => {
    vi.mocked(signup).mockRejectedValue(
      new ApiError(409, '이미 사용중인 이메일입니다.', 'CONFLICT', [
        { field: 'email', reason: 'ALREADY_EXISTS' },
      ]),
    )
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(await screen.findByText('이미 사용중인 이메일입니다.')).toBeTruthy()
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('true')
    expect(document.activeElement).toBe(screen.getByLabelText('이메일'))
  })

  it('네트워크 실패를 Backend 입력 오류와 구분해 안내한다', async () => {
    vi.mocked(signup).mockRejectedValue(new TypeError('Failed to fetch'))
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(
      await screen.findByText('네트워크 연결을 확인하고 다시 시도해 주세요.'),
    ).toBeTruthy()
  })

  it('가입 요청 중 버튼을 비활성화해 중복 제출을 막는다', async () => {
    let resolveSignup: ((value: { detail: string }) => void) | undefined
    vi.mocked(signup).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSignup = resolve
        }),
    )
    renderPage()
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    const loadingButton = await screen.findByRole('button', { name: '가입 중...' })
    fireEvent.click(loadingButton)

    expect(loadingButton).toHaveProperty('disabled', true)
    expect(signup).toHaveBeenCalledTimes(1)

    resolveSignup?.({ detail: '회원가입 완료' })
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
  })
})
