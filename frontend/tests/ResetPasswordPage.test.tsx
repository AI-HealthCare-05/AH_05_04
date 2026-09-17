import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { confirmPasswordReset } from '../src/api/auth'
import { ApiError } from '../src/api/client'
import ResetPasswordPage from '../src/pages/ResetPasswordPage'

vi.mock('../src/api/auth', () => ({
  confirmPasswordReset: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(confirmPasswordReset).mockResolvedValue({
    detail: '비밀번호가 변경되었습니다. 다시 로그인해 주세요.',
  })
})

afterEach(() => {
  cleanup()
})

function renderPage(search = '?token=valid-token') {
  return render(
    <MemoryRouter initialEntries={[`/reset-password${search}`]}>
      <Routes>
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/login" element={<div>로그인 화면</div>} />
        <Route path="/forgot-password" element={<div>비밀번호 찾기 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function fillMatchingPasswords(value = 'NewPassword1!') {
  fireEvent.change(screen.getByLabelText('새 비밀번호'), { target: { value } })
  fireEvent.change(screen.getByLabelText('새 비밀번호 확인'), { target: { value } })
}

describe('ResetPasswordPage', () => {
  it('URL에 token이 없으면 폼 대신 재요청 안내를 보여준다', () => {
    renderPage('')

    expect(screen.getByRole('heading', { name: '재설정 링크를 확인할 수 없어요' })).toBeTruthy()
    expect(screen.queryByLabelText('새 비밀번호')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '비밀번호 재설정 다시 요청하기' }))
    expect(screen.getByText('비밀번호 찾기 화면')).toBeTruthy()
  })

  it('비밀번호 정책을 만족하지 않으면 API를 호출하지 않는다', () => {
    renderPage()

    fireEvent.change(screen.getByLabelText('새 비밀번호'), { target: { value: 'short' } })
    fireEvent.change(screen.getByLabelText('새 비밀번호 확인'), { target: { value: 'short' } })
    fireEvent.click(screen.getByRole('button', { name: '비밀번호 변경하기' }))

    expect(confirmPasswordReset).not.toHaveBeenCalled()
    expect(
      screen.getByText('8자 이상이며 대문자·소문자·숫자·특수문자를 포함해 주세요.'),
    ).toBeTruthy()
  })

  it('두 비밀번호가 일치하지 않으면 API를 호출하지 않는다', () => {
    renderPage()

    fireEvent.change(screen.getByLabelText('새 비밀번호'), { target: { value: 'NewPassword1!' } })
    fireEvent.change(screen.getByLabelText('새 비밀번호 확인'), { target: { value: 'Different1!' } })
    fireEvent.click(screen.getByRole('button', { name: '비밀번호 변경하기' }))

    expect(confirmPasswordReset).not.toHaveBeenCalled()
    expect(screen.getByText('비밀번호가 일치하지 않아요.')).toBeTruthy()
  })

  it('유효한 입력이면 token과 새 비밀번호로 API를 호출하고 완료 화면을 보여준다', async () => {
    renderPage('?token=valid-token')
    fillMatchingPasswords()

    fireEvent.click(screen.getByRole('button', { name: '비밀번호 변경하기' }))

    expect(await screen.findByRole('heading', { name: '비밀번호가 변경되었어요' })).toBeTruthy()
    expect(confirmPasswordReset).toHaveBeenCalledWith('valid-token', 'NewPassword1!')

    fireEvent.click(screen.getByRole('button', { name: '로그인하러 가기' }))
    expect(screen.getByText('로그인 화면')).toBeTruthy()
  })

  it('RESET_TOKEN_INVALID 오류는 재요청 안내로 전환한다', async () => {
    vi.mocked(confirmPasswordReset).mockRejectedValue(
      new ApiError(422, '요청을 처리할 수 없습니다.', 'VALIDATION_FAILED', [
        { field: 'token', reason: 'RESET_TOKEN_INVALID' },
      ]),
    )
    renderPage()
    fillMatchingPasswords()

    fireEvent.click(screen.getByRole('button', { name: '비밀번호 변경하기' }))

    expect(
      await screen.findByRole('heading', { name: '재설정 링크를 확인할 수 없어요' }),
    ).toBeTruthy()
  })

  it('PASSWORD_POLICY_VIOLATION 오류는 새 비밀번호 필드 오류로 보여준다', async () => {
    vi.mocked(confirmPasswordReset).mockRejectedValue(
      new ApiError(422, '요청을 처리할 수 없습니다.', 'VALIDATION_FAILED', [
        { field: 'new_password', reason: 'PASSWORD_POLICY_VIOLATION' },
      ]),
    )
    renderPage()
    fillMatchingPasswords()

    fireEvent.click(screen.getByRole('button', { name: '비밀번호 변경하기' }))

    expect(
      await screen.findByText('8자 이상이며 대문자·소문자·숫자·특수문자를 포함해 주세요.'),
    ).toBeTruthy()
    expect(screen.getByLabelText('새 비밀번호')).toBeTruthy()
  })

  it('네트워크 오류는 완료 화면으로 넘어가지 않고 안내한다', async () => {
    vi.mocked(confirmPasswordReset).mockRejectedValue(new TypeError('Failed to fetch'))
    renderPage()
    fillMatchingPasswords()

    fireEvent.click(screen.getByRole('button', { name: '비밀번호 변경하기' }))

    expect(
      await screen.findByText('네트워크 연결을 확인하고 다시 시도해 주세요.'),
    ).toBeTruthy()
  })
})
