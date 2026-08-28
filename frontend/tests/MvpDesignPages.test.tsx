import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import HomePage from '../src/pages/HomePage'
import StartPage from '../src/pages/StartPage'

afterEach(() => {
  cleanup()
})

describe('Dosey MVP design pages', () => {
  it('AUTH-00 회원가입 CTA를 실제 signup route에 연결한다', () => {
    render(
      <MemoryRouter initialEntries={['/start']}>
        <Routes>
          <Route path="/start" element={<StartPage />} />
          <Route path="/signup" element={<div>회원가입 화면</div>} />
          <Route path="/login" element={<div>로그인 화면</div>} />
        </Routes>
      </MemoryRouter>,
    )

    expect(
      screen.getByRole('heading', { name: 'AI 복약 파트너' }),
    ).toBeTruthy()
    expect(screen.getByText('처방전 등록')).toBeTruthy()
    expect(screen.getByText('쉬운 가이드')).toBeTruthy()
    expect(screen.getByText('복약 챗봇 도지')).toBeTruthy()
    expect(screen.getByText('복약 지속 도움')).toBeTruthy()
    expect(screen.getByText('AI가 처방을 바꾸지 않아요.')).toBeTruthy()
    expect(
      screen
        .getByRole('link', { name: '이미 계정이 있어요 · 로그인' })
        .getAttribute('href'),
    ).toBe('/login')
    fireEvent.click(
      screen.getByRole('button', { name: '회원가입하고 시작하기' }),
    )
    expect(screen.getByText('회원가입 화면')).toBeTruthy()
  })

  it('HOME-01은 실제 업로드 CTA와 데이터 없는 neutral 상태를 표시한다', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route
            path="/prescriptions/upload"
            element={<div>처방전 업로드 화면</div>}
          />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText('표시할 복약 기록이 아직 없어요')).toBeTruthy()
    expect(screen.queryByText('홍길동님')).toBeNull()
    expect(
      screen.getByRole('button', { name: '건강정보 입력하기' }),
    ).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: '복약 챗봇' })).toHaveProperty(
      'disabled',
      true,
    )
    expect(screen.getByRole('button', { name: '복약 일정' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '일반 의약품' })).toBeTruthy()
    expect(screen.getAllByText('준비 중')).toHaveLength(2)

    fireEvent.click(screen.getByRole('button', { name: '처방전 등록하기' }))
    expect(screen.getByText('처방전 업로드 화면')).toBeTruthy()
  })

  it('최근 Guide 조회 API가 없어 Home Guide CTA를 준비 중 상태로 유지한다', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<HomePage />} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: '가이드' }))
    expect(screen.getByText('최근 가이드 목록은 준비 중이에요.')).toBeTruthy()
    expect(screen.getByText('오늘 약도 챙겨볼까요?')).toBeTruthy()
  })

  it('HOME-01 메뉴에서 인증 전용 내 정보 route로 진입한다', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/profile" element={<div>내 정보 화면</div>} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: '메뉴' }))
    expect(screen.getByText('내 정보 화면')).toBeTruthy()
  })
})
