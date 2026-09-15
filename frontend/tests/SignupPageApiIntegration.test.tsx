import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import SignupPage from '../src/pages/SignupPage'

beforeEach(() => {
  vi.stubEnv('VITE_EMAIL_VERIFICATION_ENABLED', 'false')
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('SignupPage signup API integration', () => {
  it('선택 동의 0개를 실제 request body의 consents 빈 배열로 전송한다', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ detail: '회원가입 완료' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter initialEntries={['/signup']}>
        <Routes>
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/login" element={<div>로그인 화면</div>} />
        </Routes>
      </MemoryRouter>,
    )
    fireEvent.change(screen.getByLabelText('이름'), { target: { value: '홍길동' } })
    fireEvent.change(screen.getByLabelText('이메일'), { target: { value: 'dosey@example.com' } })
    fireEvent.change(screen.getByLabelText('비밀번호'), { target: { value: 'Password1!' } })
    fireEvent.click(screen.getByRole('checkbox', { name: '필수 약관에 동의합니다' }))
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:8000/api/v1/auth/signup')
    expect(JSON.parse(String(options?.body))).toEqual({
      name: '홍길동',
      email: 'dosey@example.com',
      password: 'Password1!',
      consents: [],
    })
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
  })

  it('기능 이용 선택 동의를 실제 request body의 4개 purpose로 전송한다', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ detail: '회원가입 완료' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter initialEntries={['/signup']}>
        <Routes>
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/login" element={<div>로그인 화면</div>} />
        </Routes>
      </MemoryRouter>,
    )
    fireEvent.change(screen.getByLabelText('이름'), { target: { value: '홍길동' } })
    fireEvent.change(screen.getByLabelText('이메일'), { target: { value: 'dosey@example.com' } })
    fireEvent.change(screen.getByLabelText('비밀번호'), { target: { value: 'Password1!' } })
    fireEvent.click(screen.getByRole('checkbox', { name: '필수 약관에 동의합니다' }))
    fireEvent.click(screen.getByRole('checkbox', { name: /기능 이용 선택 동의/ }))
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const [, options] = fetchMock.mock.calls[0]
    expect(JSON.parse(String(options?.body))).toEqual({
      name: '홍길동',
      email: 'dosey@example.com',
      password: 'Password1!',
      consents: [
        { purpose: 'OCR' },
        { purpose: 'GUIDE' },
        { purpose: 'CHAT' },
        { purpose: 'NOTIFICATION' },
      ],
    })
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
  })

})
