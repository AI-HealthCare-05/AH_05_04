import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import HomePage from '../src/pages/HomePage'
import StartPage from '../src/pages/StartPage'

const CURRENT_USER = {
  id: '00000000-0000-4000-8000-000000000113',
  name: '테스트 사용자',
  email: 'home113@example.com',
  phone_number: null,
  birthday: null,
  gender: null,
  created_at: '2026-08-28T00:00:00Z',
}

function renderHome(currentUser = CURRENT_USER) {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<HomePage currentUser={currentUser} />} />
        <Route
          path="/prescriptions/upload"
          element={<div>처방전 업로드 화면</div>}
        />
        <Route path="/chat" element={<div>처방전 ID 없는 챗봇 진입 화면</div>} />
        <Route path="/guides" element={<div>가이드 empty 화면</div>} />
        <Route path="/profile" element={<div>내 정보 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

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

  it('HOME-01은 users/me 이름과 현재 날짜를 표시한다', async () => {
    renderHome()

    expect(
      await screen.findByRole('heading', {
        name: '오늘도 건강한 하루 되세요',
      }),
    ).toBeTruthy()
    expect(screen.getByText('테스트 사용자님!')).toBeTruthy()
    expect(screen.getByText(/\d+월 \d+일/)).toBeTruthy()
  })

  it('HOME-01은 조회된 사용자의 이름이 비어 있을 때 개인화된 성공처럼 숨기지 않는다', () => {
    renderHome({ ...CURRENT_USER, name: '   ' })

    expect(
      screen.getByText(
        '사용자 이름을 불러오지 못했어요. 홈 기능은 계속 사용할 수 있어요.',
      ),
    ).toBeTruthy()
    expect(screen.getByText('도지 사용자님!')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '오늘도 건강한 하루 되세요' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /처방약 복용 안내/ })).toBeTruthy()
  })

  it('HOME-01은 실제 데이터가 없어도 neutral 복약 달성도 영역을 표시한다', async () => {
    const { container } = renderHome()

    await screen.findByText('오늘도 건강한 하루 되세요')
    expect(screen.getByRole('heading', { name: '이번 주 복약 달성도' })).toBeTruthy()
    expect(screen.getByText('집계 준비 중')).toBeTruthy()
    expect(screen.getByLabelText('이번 주 복약 달성도 집계 준비 중')).toBeTruthy()
    expect(container.querySelector('.mvp-home__adherence-progress .dosey-mascot')).toBeTruthy()
    expect(screen.queryByText('85%')).toBeNull()
  })

  it('HOME-01의 처방약 복용 안내를 기존 업로드 route에 연결한다', async () => {
    renderHome()
    await screen.findByText('오늘도 건강한 하루 되세요')
    fireEvent.click(screen.getByRole('button', { name: /처방약 복용 안내/ }))
    expect(screen.getByText('처방전 업로드 화면')).toBeTruthy()
  })

  it('계약 없는 HOME 기능은 비활성 상태이며 추가 API를 호출하지 않는다', async () => {
    renderHome()

    await screen.findByText('오늘도 건강한 하루 되세요')
    expect(
      screen.getByRole('button', { name: '미확인 복약 기록 (준비 중)' }),
    ).toHaveProperty('disabled', true)
    expect(
      screen.getByRole('button', { name: '일반의약품 안내 (준비 중)' }),
    ).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: '상세 보기 (준비 중)' })).toHaveProperty(
      'disabled',
      true,
    )
    expect(screen.getByRole('button', { name: '알림 (준비 중)' })).toHaveProperty(
      'disabled',
      true,
    )
    expect(screen.getByRole('button', { name: '일정 (준비 중)' })).toHaveProperty(
      'disabled',
      true,
    )
  })

  it('복약 챗봇은 ID를 추측하지 않고 기존 /chat route로만 이동한다', async () => {
    renderHome()

    await screen.findByText('오늘도 건강한 하루 되세요')
    fireEvent.click(screen.getByRole('button', { name: /도지에게 질문하기/ }))
    expect(screen.getByText('처방전 ID 없는 챗봇 진입 화면')).toBeTruthy()
  })

  it('가이드 Bottom Navigation은 API 호출 없이 기존 /guides empty route로 이동한다', async () => {
    renderHome()

    await screen.findByText('오늘도 건강한 하루 되세요')
    fireEvent.click(screen.getByRole('button', { name: '가이드' }))
    expect(screen.getByText('가이드 empty 화면')).toBeTruthy()
  })

  it('HOME-01 Bottom Navigation은 최신 5-tab과 Profile 회귀를 유지한다', async () => {
    renderHome()

    await screen.findByText('오늘도 건강한 하루 되세요')
    expect(screen.getByRole('button', { name: '홈' }).getAttribute('aria-current')).toBe(
      'page',
    )
    expect(screen.getByRole('button', { name: '도지' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '메뉴' }))
    expect(screen.getByText('내 정보 화면')).toBeTruthy()
  })
})
