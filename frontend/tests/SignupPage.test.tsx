import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { signup } from '../src/api/auth'
import SignupPage from '../src/pages/SignupPage'

vi.mock('../src/api/auth', () => ({
  signup: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(signup).mockResolvedValue({ detail: '회원가입 완료' })
})

afterEach(() => {
  cleanup()
})

describe('SignupPage', () => {
  it('#65 계약의 name, email, password만 회원가입 요청에 포함한다', async () => {
    render(
      <MemoryRouter initialEntries={['/signup']}>
        <Routes>
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/login" element={<div>로그인 화면</div>} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.change(screen.getByLabelText('이름'), {
      target: { value: '홍길동' },
    })
    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'dosey@example.com' },
    })
    fireEvent.change(screen.getByLabelText('비밀번호'), {
      target: { value: 'Password1!' },
    })

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
  })
})
