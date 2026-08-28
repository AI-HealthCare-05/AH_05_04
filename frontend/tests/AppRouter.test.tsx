import { cleanup, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import AppRouter from '../src/routes/AppRouter'

function renderRoute(path: string) {
  window.history.pushState({}, '', path)
  return render(<AppRouter />)
}

afterEach(() => {
  cleanup()
})

describe('인증 상태별 AppRouter 이동', () => {
  it('비로그인 사용자가 첫 화면에 접속하면 시작 화면을 표시한다', () => {
    renderRoute('/')

    expect(screen.getByRole('heading', { name: 'AI 복약 파트너' })).toBeTruthy()
  })

  it('로그인 사용자가 첫 화면에 접속하면 홈 화면을 표시한다', () => {
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/')

    expect(screen.getByRole('heading', { name: '오늘 약도 챙겨볼까요?' })).toBeTruthy()
  })

  it('비로그인 사용자가 회원 전용 화면에 직접 접속하면 로그인 화면으로 이동한다', () => {
    renderRoute('/profile')

    expect(screen.getByRole('heading', { name: '다시 만나서 반가워요' })).toBeTruthy()
  })

  it('로그인 사용자가 로그인 화면에 접속하면 홈 화면으로 이동한다', () => {
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/login')

    expect(screen.getByRole('heading', { name: '오늘 약도 챙겨볼까요?' })).toBeTruthy()
  })

  it('로그인 사용자가 회원가입 화면에 접속하면 홈 화면으로 이동한다', () => {
    localStorage.setItem('access_token', 'fixture-access-token')
    renderRoute('/signup')

    expect(screen.getByRole('heading', { name: '오늘 약도 챙겨볼까요?' })).toBeTruthy()
  })
})
