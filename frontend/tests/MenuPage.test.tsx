import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { logout } from '../src/api/auth'
import MenuPage from '../src/pages/MenuPage'

vi.mock('../src/api/auth', () => ({ logout: vi.fn() }))

function deferred<T>() {
  return new Promise<T>(() => undefined)
}

function renderMenu() {
  return render(
    <MemoryRouter initialEntries={['/menu']}>
      <Routes>
        <Route path="/menu" element={<MenuPage />} />
        <Route path="/profile" element={<div>사용자 정보 화면</div>} />
        <Route path="/login" element={<div>로그인 화면</div>} />
        <Route path="/" element={<div>홈 화면</div>} />
        <Route path="/guides" element={<div>가이드 화면</div>} />
        <Route path="/chat" element={<div>도지 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.setItem('access_token', 'fixture-token')
  vi.mocked(logout).mockResolvedValue({ detail: '로그아웃되었습니다.' })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Dosey 메뉴', () => {
  it('사용자 정보는 실제 route로 이동하고 미연결 항목은 준비 중으로 비활성화한다', () => {
    renderMenu()

    for (const label of ['복약 기록', '복약 리포트', '알림 설정']) {
      expect(screen.getByRole('button', { name: `${label} (준비 중)` })).toHaveProperty('disabled', true)
    }

    fireEvent.click(screen.getByRole('button', { name: '사용자 정보' }))
    expect(screen.getByText('사용자 정보 화면')).toBeTruthy()
  })

  it('현재 가능한 하단 navigation만 기존 route로 이동한다', () => {
    const first = renderMenu()
    expect(screen.getByRole('button', { name: '메뉴' }).getAttribute('aria-current')).toBe('page')
    expect(screen.getByRole('button', { name: '일정 (준비 중)' })).toHaveProperty('disabled', true)
    fireEvent.click(screen.getByRole('button', { name: '가이드' }))
    expect(screen.getByText('가이드 화면')).toBeTruthy()

    first.unmount()
    renderMenu()
    fireEvent.click(screen.getByRole('button', { name: '도지' }))
    expect(screen.getByText('도지 화면')).toBeTruthy()

    cleanup()
    renderMenu()
    fireEvent.click(screen.getByRole('button', { name: '홈' }))
    expect(screen.getByText('홈 화면')).toBeTruthy()
  })
})

describe('메뉴 로그아웃', () => {
  it('서버 응답을 기다리지 않고 토큰을 제거한 뒤 로그인으로 이동한다', async () => {
    vi.mocked(logout).mockReturnValue(deferred())
    renderMenu()

    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(logout).toHaveBeenCalledTimes(1)
  })

  it('서버 요청이 실패해도 로컬 로그아웃을 완료한다', async () => {
    vi.mocked(logout).mockRejectedValue(new Error('network unavailable'))
    renderMenu()

    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
  })
})
