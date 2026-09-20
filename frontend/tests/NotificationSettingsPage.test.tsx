import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { getMedicationDay } from '../src/api/medicationSchedules'
import { getUserConsents } from '../src/api/userConsents'
import NotificationSettingsPage from '../src/pages/NotificationSettingsPage'

vi.mock('../src/api/medicationSchedules', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/medicationSchedules')>()),
  getMedicationDay: vi.fn(),
}))

vi.mock('../src/api/userConsents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/userConsents')>()),
  getUserConsents: vi.fn(),
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
})
