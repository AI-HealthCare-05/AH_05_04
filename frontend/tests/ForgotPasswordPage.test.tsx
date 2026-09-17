import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { requestPasswordReset } from '../src/api/auth'
import { ApiError } from '../src/api/client'
import ForgotPasswordPage from '../src/pages/ForgotPasswordPage'

vi.mock('../src/api/auth', () => ({
  requestPasswordReset: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(requestPasswordReset).mockResolvedValue({
    detail: '비밀번호 재설정 안내를 확인해 주세요.',
    reset_token: null,
  })
})

afterEach(() => {
  cleanup()
})

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/forgot-password']}>
      <Routes>
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/login" element={<div>로그인 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ForgotPasswordPage', () => {
  it('이메일 입력 없이 제출하면 API를 호출하지 않고 안내한다', () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '재설정 안내 받기' }))

    expect(requestPasswordReset).not.toHaveBeenCalled()
    expect(screen.getByText('이메일을 입력해 주세요.')).toBeTruthy()
  })

  it('유효한 이메일로 제출하면 계정 존재 여부와 무관하게 같은 완료 화면을 보여준다', async () => {
    renderPage()

    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'dosey@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: '재설정 안내 받기' }))

    expect(await screen.findByRole('heading', { name: '이메일을 확인해 주세요' })).toBeTruthy()
    expect(requestPasswordReset).toHaveBeenCalledWith('dosey@example.com')
    expect(screen.queryByLabelText('이메일')).toBeNull()
  })

  it('네트워크 오류는 완료 화면으로 넘어가지 않고 안내한다', async () => {
    vi.mocked(requestPasswordReset).mockRejectedValue(new TypeError('Failed to fetch'))
    renderPage()

    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'dosey@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: '재설정 안내 받기' }))

    expect(
      await screen.findByText('네트워크 연결을 확인하고 다시 시도해 주세요.'),
    ).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '이메일을 확인해 주세요' })).toBeNull()
  })

  it('Backend 검증 오류(예: 형식 위반)를 그대로 안내한다', async () => {
    vi.mocked(requestPasswordReset).mockRejectedValue(
      new ApiError(422, '올바른 이메일 주소를 입력해 주세요.', 'VALIDATION_FAILED'),
    )
    renderPage()

    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'dosey@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: '재설정 안내 받기' }))

    expect(await screen.findByText('올바른 이메일 주소를 입력해 주세요.')).toBeTruthy()
  })

  it('로그인으로 돌아가기 링크를 제공한다', () => {
    renderPage()

    fireEvent.click(screen.getByRole('link', { name: '로그인으로 돌아가기' }))

    expect(screen.getByText('로그인 화면')).toBeTruthy()
  })
})
