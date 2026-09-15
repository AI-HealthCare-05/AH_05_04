import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ApiError } from '../src/api/client'
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

const PRESCRIPTION_RESPONSE = {
  data: {
    prescription_id: '11111111-1111-4111-8111-111111111111',
    prescription_version_id: '22222222-2222-4222-8222-222222222222',
    revision: 1,
    current: true,
    document_id: '33333333-3333-4333-8333-333333333333',
    prescribed_date: '2026-09-15',
    confirmed_at: '2026-09-15T00:00:00Z',
    medications: [],
  },
}

const REPORT_RESPONSE = {
  data: {
    period_days: 7 as const,
    start_date: '2026-09-09',
    end_date: '2026-09-15',
    timezone: 'Asia/Seoul' as const,
    as_of: '2026-09-15T00:00:00Z',
    counts: {
      taken_count: 6,
      not_taken_count: 1,
      unconfirmed_count: 0,
      pending_count: 0,
      cancelled_count: 0,
    },
    overdue_pending_count: 0,
    adherence_rate: { numerator: 6, denominator: 7, percentage: 86 },
    confirmation_rate: { numerator: 7, denominator: 7, percentage: 100 },
    records: [],
  },
}

const EMPTY_HOME_SERVICES = {
  getLatestPrescription: async () => {
    throw new ApiError(404, '처방 없음', 'PRESCRIPTION_NOT_FOUND')
  },
  getMedicationReport: async () => REPORT_RESPONSE,
}

const ACTIVE_HOME_SERVICES = {
  getLatestPrescription: async () => PRESCRIPTION_RESPONSE,
  getMedicationReport: async () => REPORT_RESPONSE,
}

const ERROR_HOME_SERVICES = {
  getLatestPrescription: async () => {
    throw new ApiError(500, '조회 실패', 'INTERNAL_SERVER_ERROR')
  },
  getMedicationReport: async () => REPORT_RESPONSE,
}

const NON_ABSENCE_404_HOME_SERVICES = {
  getLatestPrescription: async () => {
    throw new ApiError(404, '다른 리소스를 찾을 수 없음', 'RESOURCE_NOT_FOUND')
  },
  getMedicationReport: async () => REPORT_RESPONSE,
}

function renderHome(
  currentUser = CURRENT_USER,
  showPrescriptionOnboarding = false,
  services = EMPTY_HOME_SERVICES,
) {
  function UploadRoute() {
    const location = useLocation()

    return (
      <div>
        처방전 업로드 화면
        <output data-testid="upload-intent">
          {(location.state as { intent?: string } | null)?.intent ?? ''}
        </output>
      </div>
    )
  }

  return render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: '/',
          state: showPrescriptionOnboarding
            ? { showPrescriptionOnboarding: true }
            : null,
        },
      ]}
    >
      <Routes>
        <Route path="/" element={<HomePage currentUser={currentUser} services={services} />} />
        <Route
          path="/prescriptions/upload"
          element={<UploadRoute />}
        />
        <Route path="/chat" element={<div>처방전 ID 없는 챗봇 진입 화면</div>} />
        <Route path="/guides" element={<div>가이드 empty 화면</div>} />
        <Route path="/schedule" element={<div>복약 일정 화면</div>} />
        <Route path="/menu" element={<div>메뉴 화면</div>} />
        <Route path="/notifications" element={<div>알림 화면</div>} />
        <Route path="/report" element={<div>복약 리포트 화면</div>} />
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
      screen.getByRole('heading', { name: '처방과 일정을 쉽게 살펴봐요.' }),
    ).toBeTruthy()
    expect(screen.getByText('Dose + Easy')).toBeTruthy()
    expect(screen.getByText('처방전 등록')).toBeTruthy()
    expect(screen.getByText('쉬운 가이드')).toBeTruthy()
    expect(screen.getByText('도지에게 질문')).toBeTruthy()
    expect(screen.getByText('복약 지속 도움')).toBeTruthy()
    expect(screen.queryByText('AI가 처방을 바꾸지 않아요.')).toBeNull()
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

  it('#395 온보딩은 키보드 포커스를 모달 내부에 가두고 닫힌 뒤 Home CTA로 복귀한다', async () => {
    const { container } = renderHome(CURRENT_USER, true)

    const primaryButton = await screen.findByRole('button', {
      name: '지금 처방전 촬영하기',
    })
    const secondaryButton = screen.getByRole('button', {
      name: '나중에 촬영할게요',
    })
    const homePrescriptionButton = container.querySelector<HTMLButtonElement>(
    '.mvp-home__hub-card--prescription',
    )

    expect(homePrescriptionButton).not.toBeNull()

    // 열리면 첫 CTA에 포커스
    expect(document.activeElement).toBe(primaryButton)

    // 배경 Home 접근 차단
    const mobileApp = homePrescriptionButton!.closest('.mobile-app')
    expect(mobileApp?.getAttribute('aria-hidden')).toBe('true')

    // 마지막 버튼에서 Tab → 첫 버튼
    secondaryButton.focus()
    fireEvent.keyDown(secondaryButton, {
      key: 'Tab',
    })
    expect(document.activeElement).toBe(primaryButton!)

    // 첫 버튼에서 Shift+Tab → 마지막 버튼
    primaryButton.focus()
    fireEvent.keyDown(primaryButton, {
      key: 'Tab',
      shiftKey: true,
    })
    expect(document.activeElement).toBe(secondaryButton)

    // Escape → 닫힘 + Home 처방전 CTA로 포커스 복귀
    fireEvent.keyDown(secondaryButton, {
      key: 'Escape',
    })

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(document.activeElement).toBe(homePrescriptionButton)
    })

    expect(mobileApp?.getAttribute('aria-hidden')).toBeNull()
  })

  it('#395 가입 직후 Home에서 처방전 온보딩을 표시하고 촬영 CTA를 새 처방 등록으로 연결한다', async () => {
    renderHome(CURRENT_USER, true)

    expect(
      await screen.findByRole('heading', {
        name: '처방전을 등록해 볼까요?',
      }),
    ).toBeTruthy()

    fireEvent.click(
      screen.getByRole('button', {
        name: '지금 처방전 촬영하기',
      }),
    )

    expect(screen.getByText('처방전 업로드 화면')).toBeTruthy()
    expect(screen.getByTestId('upload-intent').textContent).toBe(
      'new-prescription',
    )
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

  it('HOME-01은 조회된 사용자의 이름이 비어 있을 때 개인화된 성공처럼 숨기지 않는다', async () => {
    renderHome({ ...CURRENT_USER, name: '   ' })

    expect(
      screen.getByText(
        '사용자 이름을 불러오지 못했어요. 홈 기능은 계속 사용할 수 있어요.',
      ),
    ).toBeTruthy()
    expect(screen.getByText('도지 사용자님!')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '오늘도 건강한 하루 되세요' })).toBeTruthy()
    expect(await screen.findByRole('button', { name: /내 처방전 등록하기/ })).toBeTruthy()
  })

  it('HOME 처방 완료 상태는 실제 7일 리포트의 복약 달성도를 표시한다', async () => {
    const { container } = renderHome(CURRENT_USER, false, ACTIVE_HOME_SERVICES)

    await screen.findByText('오늘도 건강한 하루 되세요')
    expect(screen.getByRole('heading', { name: '이번 주 복약 달성도' })).toBeTruthy()
    expect(await screen.findByText('86%')).toBeTruthy()
    expect(screen.getByRole('progressbar', { name: '이번 주 복약 달성도 86%' })).toBeTruthy()
    expect(container.querySelector('.mvp-home__adherence-progress .dosey-mascot')).toBeTruthy()
    expect(screen.queryByText('집계 준비 중')).toBeNull()
    expect(screen.queryByRole('button', { name: /내 처방전 등록하기/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /도지에게 질문하기/ })).toBeNull()
  })

  it('미처방 HOME의 등록 카드를 새 처방 등록 intent와 함께 업로드 route에 연결한다', async () => {
    renderHome()
    await screen.findByText('오늘도 건강한 하루 되세요')
    fireEvent.click(screen.getByRole('button', { name: /내 처방전 등록하기/ }))
    expect(screen.getByText('처방전 업로드 화면')).toBeTruthy()
    expect(screen.getByTestId('upload-intent').textContent).toBe('new-prescription')
  })

  it('HOME 처방 조회의 404 외 오류는 빈 처방으로 위장하지 않는다', async () => {
    renderHome(CURRENT_USER, false, ERROR_HOME_SERVICES)

    expect((await screen.findByRole('alert')).textContent).toContain('홈 정보를 불러오지 못했어요')
    expect(screen.queryByRole('button', { name: /내 처방전 등록하기/ })).toBeNull()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeTruthy()
  })

  it('HOME은 PRESCRIPTION_NOT_FOUND가 아닌 404를 빈 처방으로 위장하지 않는다', async () => {
    renderHome(CURRENT_USER, false, NON_ABSENCE_404_HOME_SERVICES)

    expect((await screen.findByRole('alert')).textContent).toContain('홈 정보를 불러오지 못했어요')
    expect(screen.queryByRole('button', { name: /내 처방전 등록하기/ })).toBeNull()
  })

  it('처방 완료 HOME은 제거된 OTC와 기존 구 hub를 다시 표시하지 않는다', async () => {
    renderHome(CURRENT_USER, false, ACTIVE_HOME_SERVICES)

    await screen.findByText('오늘도 건강한 하루 되세요')
    expect(screen.queryByRole('heading', { name: '미확인 기록' })).toBeNull()
    expect(screen.queryByRole('button', { name: /일반의약품 안내/ })).toBeNull()
    expect(await screen.findByRole('button', { name: '상세 보기 >' })).toHaveProperty('disabled', false)
    expect(screen.getByRole('button', { name: /복약 리포트 보기/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /도지에게 질문하기/ })).toBeNull()
    expect(screen.getByRole('button', { name: '알림' })).toHaveProperty('disabled', false)
    expect(screen.getByRole('button', { name: '일정' })).toHaveProperty('disabled', false)
  })

  it('모든 HOME 마스코트 이미지는 브라우저 기본 드래그를 비활성화한다', async () => {
    const { container } = renderHome(CURRENT_USER, false, ACTIVE_HOME_SERVICES)

    await screen.findByText('오늘도 건강한 하루 되세요')
    const mascotImages = container.querySelectorAll<HTMLImageElement>('.dosey-mascot img')
    expect(mascotImages.length).toBeGreaterThan(0)
    mascotImages.forEach((image) => {
      expect(image.draggable).toBe(false)
    })
  })

  it('Home 알림 버튼은 production /notifications route로 이동한다', async () => {
    renderHome()

    fireEvent.click(await screen.findByRole('button', { name: '알림' }))
    expect(screen.getByText('알림 화면')).toBeTruthy()
  })

  it('처방 완료 HOME의 리포트 카드는 기존 /report route로 이동한다', async () => {
    renderHome(CURRENT_USER, false, ACTIVE_HOME_SERVICES)

    fireEvent.click(await screen.findByRole('button', { name: /복약 리포트 보기/ }))
    expect(screen.getByText('복약 리포트 화면')).toBeTruthy()
  })

  it('가이드 Bottom Navigation은 API 호출 없이 기존 /guides empty route로 이동한다', async () => {
    renderHome()

    await screen.findByText('오늘도 건강한 하루 되세요')
    fireEvent.click(screen.getByRole('button', { name: '가이드' }))
    expect(screen.getByText('가이드 empty 화면')).toBeTruthy()
  })

  it('도지 Bottom Navigation은 prescription_id를 추측하지 않고 /chat으로 이동한다', async () => {
    renderHome()

    await screen.findByText('오늘도 건강한 하루 되세요')
    fireEvent.click(screen.getByRole('button', { name: '도지' }))
    expect(screen.getByText('처방전 ID 없는 챗봇 진입 화면')).toBeTruthy()
  })

  it('HOME-01 Bottom Navigation은 최신 5-tab과 Menu 경로를 유지한다', async () => {
    renderHome()

    await screen.findByText('오늘도 건강한 하루 되세요')
    expect(screen.getByRole('button', { name: '홈' }).getAttribute('aria-current')).toBe(
      'page',
    )
    expect(screen.getByRole('button', { name: '도지' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '메뉴' }))
    expect(screen.getByText('메뉴 화면')).toBeTruthy()
  })
})
