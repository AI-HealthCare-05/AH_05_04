import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { getMedicationDay } from '../src/api/medicationSchedules'
import { getUserConsents } from '../src/api/userConsents'
import {
  enableWebPush,
  getWebPushLaunchContext,
  getWebPushState,
} from '../src/features/push/webPush'
import NotificationSettingsPage from '../src/pages/NotificationSettingsPage'

vi.mock('../src/api/medicationSchedules', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/medicationSchedules')>()),
  getMedicationDay: vi.fn(),
}))

vi.mock('../src/api/userConsents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/userConsents')>()),
  getUserConsents: vi.fn(),
}))

vi.mock('../src/features/push/webPush', () => ({
  getWebPushLaunchContext: vi.fn(() => 'browser'),
  getWebPushState: vi.fn(),
  enableWebPush: vi.fn(),
  disableWebPush: vi.fn(),
  hasStoredWebPushBinding: vi.fn(() => false),
}))

function notificationConsent(isGranted: boolean) {
  return {
    purpose: 'NOTIFICATION' as const,
    status: isGranted ? 'GRANTED' as const : 'WITHDRAWN' as const,
    policy_version: 'notification-v1',
    current_policy_version: 'notification-v1',
    is_granted: isGranted,
    granted_at: isGranted ? '2026-09-20T00:00:00Z' : null,
    withdrawn_at: isGranted ? null : '2026-09-20T01:00:00Z',
    updated_at: '2026-09-20T01:00:00Z',
  }
}

function scheduleResponse(times = ['08:00', '13:00', '19:00']) {
  return {
    data: {
      schedule_status: 'READY' as const,
      schedule_items: [
        {
          prescription_version_medication_id:
            '11111111-1111-4111-8111-111111111111',
          schedule_item_status: 'READY' as const,
          schedule_id: '22222222-2222-4222-8222-222222222222',
          revision: 1,
          setup_reason: null,
          schedule: {
            schedule_id: '22222222-2222-4222-8222-222222222222',
            prescription_version_medication_id:
              '11111111-1111-4111-8111-111111111111',
            revision: 1,
            status: 'ACTIVE' as const,
            start_local_date: '2026-09-21',
            end_mode: 'OPEN_ENDED' as const,
            end_local_date: null,
            local_times: times,
          },
        },
      ],
      occurrences: [],
    },
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/settings/notifications']}>
      <Routes>
        <Route
          path="/settings/notifications"
          element={<NotificationSettingsPage />}
        />
        <Route path="/schedule" element={<div>복약 일정 화면</div>} />
        <Route path="/menu" element={<div>메뉴 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(getMedicationDay).mockResolvedValue(scheduleResponse())
  vi.mocked(getUserConsents).mockResolvedValue({
    data: [notificationConsent(true)],
  })
  vi.mocked(getWebPushLaunchContext).mockReturnValue('browser')
  vi.mocked(getWebPushState).mockResolvedValue('unrequested')
  vi.mocked(enableWebPush).mockResolvedValue('denied')
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('NotificationSettingsPage', () => {
  it('NOTIFICATION 동의가 유효하면 앱 내 알림을 ON 상태로 표시한다', async () => {
    renderPage()

    expect(
      await screen.findByRole('img', {
        name: '앱 내 알림 동의 상태 켜짐',
      }),
    ).toBeTruthy()

    expect(
      screen.getByRole('button', { name: '메뉴' }).getAttribute('aria-current'),
    ).toBe('page')
  })

  it('NOTIFICATION 동의가 없으면 앱 내 알림을 OFF 상태로 표시한다', async () => {
    vi.mocked(getUserConsents).mockResolvedValue({
      data: [notificationConsent(false)],
    })

    renderPage()

    expect(
      await screen.findByRole('img', {
        name: '앱 내 알림 동의 상태 꺼짐',
      }),
    ).toBeTruthy()
  })

  it('확정된 실제 일정 시간을 표시하고 일정 수정 화면으로 이동한다', async () => {
    renderPage()

    expect(await screen.findByText('아침')).toBeTruthy()
    expect(screen.getByText('08:00')).toBeTruthy()
    expect(screen.getByText('점심')).toBeTruthy()
    expect(screen.getByText('13:00')).toBeTruthy()
    expect(screen.getByText('저녁')).toBeTruthy()
    expect(screen.getByText('19:00')).toBeTruthy()

    fireEvent.click(
      screen.getByRole('button', { name: /복약 일정 수정하기/ }),
    )

    expect(await screen.findByText('복약 일정 화면')).toBeTruthy()
  })

  it('iPhone Safari 일반 탭은 설치 안내와 앱 내 fallback만 표시한다', async () => {
    vi.mocked(getWebPushLaunchContext).mockReturnValue('ios-browser')

    renderPage()

    expect(await screen.findByText('홈 화면에 추가해 주세요')).toBeTruthy()
    expect(screen.getByText(/Safari의 공유 버튼/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: '알림 켜기' })).toBeNull()
    expect(
      screen.getByRole('button', { name: '앱 안의 알림 목록 보기' }),
    ).toBeTruthy()
    expect(getWebPushState).not.toHaveBeenCalled()
  })

  it('설치 웹앱은 사용자 클릭용 알림 켜기 CTA를 유지한다', async () => {
    vi.mocked(getWebPushLaunchContext).mockReturnValue('standalone')

    renderPage()

    expect(
      await screen.findByText('Push 알림을 사용하지 않고 있어요'),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: '알림 켜기' })).toBeTruthy()
  })

  it('사용자 클릭 전에는 Web Push 권한 흐름을 시작하지 않는다', async () => {
    renderPage()

    const enableButton = await screen.findByRole('button', {
      name: '알림 켜기',
    })

    expect(enableWebPush).not.toHaveBeenCalled()

    fireEvent.click(enableButton)

    expect(enableWebPush).toHaveBeenCalledTimes(1)
    expect(
      await screen.findByText('브라우저에서 알림이 차단되어 있어요'),
    ).toBeTruthy()
  })

  it('Backend Push 설정이 없으면 구독 실패와 구분한 안내를 표시한다', async () => {
    vi.mocked(getWebPushState).mockResolvedValue('config_unavailable')

    renderPage()

    expect(
      await screen.findByText('Push 알림 설정이 아직 준비되지 않았어요'),
    ).toBeTruthy()
    expect(screen.getByText(/관리자 설정이 완료되면/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '알림 켜기' })).toBeTruthy()
  })

  it('브라우저 권한 변경 후 foreground에서 Push 상태를 다시 확인한다', async () => {
    vi.mocked(getWebPushState)
      .mockResolvedValueOnce('denied')
      .mockResolvedValueOnce('unrequested')

    renderPage()

    expect(
      await screen.findByText('브라우저에서 알림이 차단되어 있어요'),
    ).toBeTruthy()

    fireEvent.focus(window)

    expect(
      await screen.findByText('Push 알림을 사용하지 않고 있어요'),
    ).toBeTruthy()
    expect(getWebPushState).toHaveBeenCalledTimes(2)
  })
})
