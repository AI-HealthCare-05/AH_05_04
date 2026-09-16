import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../src/api/client'
import {
  OccurrenceMedicationUnavailableError,
  resolveNotificationOccurrenceMedication,
} from '../src/api/medicationOccurrences'
import {
  listNotifications,
  markNotificationRead,
  type NotificationData,
  type NotificationOccurrenceHandoff,
} from '../src/api/notifications'
import { putMedicationCheckin } from '../src/api/medicationCheckins'
import NotificationsPage from '../src/pages/NotificationsPage'

vi.mock('../src/api/notifications', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/notifications')>()),
  createNotificationReadIdempotencyKey: vi.fn(() => 'notification-read:test-key'),
  listNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
}))

vi.mock('../src/api/medicationOccurrences', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/medicationOccurrences')>()),
  resolveNotificationOccurrenceMedication: vi.fn(),
}))

vi.mock('../src/api/medicationCheckins', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/medicationCheckins')>()),
  putMedicationCheckin: vi.fn(),
}))

const UNREAD_NOTIFICATION: NotificationData = {
  id: '11111111-1111-4111-8111-111111111111',
  occurrence_id: '22222222-2222-4222-8222-222222222222',
  occurrence_local_date: '2026-09-10',
  kind: 'REMINDER',
  scheduled_at: '2026-09-11T00:30:00Z',
  status: 'DELIVERED',
  delivered_at: '2026-09-11T00:30:01Z',
  read_at: null,
}

const READ_NOTIFICATION: NotificationData = {
  ...UNREAD_NOTIFICATION,
  id: '33333333-3333-4333-8333-333333333333',
  occurrence_id: '44444444-4444-4444-8444-444444444444',
  occurrence_local_date: '2026-09-09',
  kind: 'SCHEDULED',
  read_at: '2026-09-11T01:00:00Z',
}

function resolvedFixture(handoff: NotificationOccurrenceHandoff) {
  return {
    occurrenceLocalDate: handoff.occurrenceLocalDate,
    occurrence: {
      occurrence_id: handoff.occurrenceId,
      prescription_version_id: '55555555-5555-4555-8555-555555555555',
      prescription_version_medication_id: '66666666-6666-4666-8666-666666666666',
      scheduled_local_date: handoff.occurrenceLocalDate,
      scheduled_at: '2026-09-11T00:30:00Z',
      confirmation_deadline_at: '2026-09-11T05:30:00Z',
      status: 'PENDING' as const,
      checkin: null,
    },
    medication: {
      occurrence_id: handoff.occurrenceId,
      prescription_version_id: '55555555-5555-4555-8555-555555555555',
      prescription_version_medication_id: '66666666-6666-4666-8666-666666666666',
      medication_name: '원래 처방약',
      strength_text: null,
      dose_value: null,
      dose_unit: null,
    },
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function LoginStateProbe() {
  const location = useLocation()
  const returnTo = (location.state as { returnTo?: string } | null)?.returnTo
  return <output data-testid="login-return">{returnTo ?? 'none'}</output>
}

function RouteLeaveControl() {
  const navigate = useNavigate()
  return <button type="button" onClick={() => navigate('/away')}>테스트 경로 이동</button>
}

function createDeferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function renderPage(
  onHandoffReady?: (handoff: NotificationOccurrenceHandoff) => void,
  path = '/notifications',
) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <RouteLeaveControl />
      <Routes>
        <Route path="/notifications" element={<NotificationsPage onHandoffReady={onHandoffReady} />} />
        <Route path="/schedule/occurrences/:occurrenceId" element={<LocationProbe />} />
        <Route path="/schedule" element={<div>일정 화면</div>} />
        <Route path="/login" element={<LoginStateProbe />} />
        <Route path="/away" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(listNotifications).mockResolvedValue({
    data: { items: [UNREAD_NOTIFICATION, READ_NOTIFICATION], next_offset: null },
  })
  vi.mocked(markNotificationRead).mockResolvedValue({
    data: { ...UNREAD_NOTIFICATION, read_at: '2026-09-11T02:00:00Z' },
  })
  vi.mocked(resolveNotificationOccurrenceMedication).mockImplementation(
    async (handoff) => resolvedFixture(handoff),
  )
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('NotificationsPage', () => {
  it('loading 뒤 빈 상태를 표시한다', async () => {
    vi.mocked(listNotifications).mockImplementation(() => new Promise(() => undefined))
    const first = renderPage()
    expect(screen.getByRole('status').textContent).toContain('알림을 불러오는 중이에요')
    first.unmount()

    vi.mocked(listNotifications).mockResolvedValue({ data: { items: [], next_offset: null } })
    renderPage()
    expect(await screen.findByText('새로운 알림이 없어요')).toBeTruthy()
  })

  it('읽음과 읽지 않음을 구분하고 occurrence_local_date를 그대로 표시한다', async () => {
    renderPage()
    expect(await screen.findByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
    })).toBeTruthy()
    expect(screen.getByRole('button', {
      name: '복약 알림, 복약일 2026-09-09, 읽음',
    })).toBeTruthy()
    expect(screen.queryByText('2026-09-11', { exact: true })).toBeNull()
  })

  it('next_offset이 있으면 중복 없이 다음 페이지를 이어서 표시한다', async () => {
    vi.mocked(listNotifications)
      .mockResolvedValueOnce({ data: { items: [UNREAD_NOTIFICATION], next_offset: 20 } })
      .mockResolvedValueOnce({ data: { items: [UNREAD_NOTIFICATION, READ_NOTIFICATION], next_offset: null } })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '알림 더 보기' }))
    expect(await screen.findByRole('button', {
      name: '복약 알림, 복약일 2026-09-09, 읽음',
    })).toBeTruthy()
    expect(listNotifications).toHaveBeenNthCalledWith(2, { offset: 20 })
  })

  it('unread 알림은 read PATCH 후 occurrence를 검증하고 production route로 이동한다', async () => {
    const onHandoffReady = vi.fn()
    renderPage(onHandoffReady)
    expect(putMedicationCheckin).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
    }))

    expect((await screen.findByTestId('location')).textContent).toBe(
      `/schedule/occurrences/${UNREAD_NOTIFICATION.occurrence_id}?date=2026-09-10`,
    )
    expect(markNotificationRead).toHaveBeenCalledWith(
      UNREAD_NOTIFICATION.id,
      'notification-read:test-key',
      expect.anything(),
    )
    expect(resolveNotificationOccurrenceMedication).toHaveBeenCalledWith(
      {
        occurrenceId: UNREAD_NOTIFICATION.occurrence_id,
        occurrenceLocalDate: UNREAD_NOTIFICATION.occurrence_local_date,
      },
      expect.anything(),
    )
    expect(markNotificationRead.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(resolveNotificationOccurrenceMedication).mock.invocationCallOrder[0]!,
    )
    expect(onHandoffReady).toHaveBeenCalledWith({
      occurrenceId: UNREAD_NOTIFICATION.occurrence_id,
      occurrenceLocalDate: '2026-09-10',
    })
    expect(onHandoffReady).toHaveBeenCalledTimes(1)
    expect(putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('Push click target은 read PATCH나 Check-in 없이 최신 occurrence를 검증해 이동한다', async () => {
    renderPage(undefined, `/notifications?push_notification_id=${UNREAD_NOTIFICATION.id}`)

    expect((await screen.findByTestId('location')).textContent).toBe(
      `/schedule/occurrences/${UNREAD_NOTIFICATION.occurrence_id}?date=2026-09-10`,
    )
    expect(markNotificationRead).not.toHaveBeenCalled()
    expect(putMedicationCheckin).not.toHaveBeenCalled()
    expect(resolveNotificationOccurrenceMedication).toHaveBeenCalledWith(
      {
        occurrenceId: UNREAD_NOTIFICATION.occurrence_id,
        occurrenceLocalDate: UNREAD_NOTIFICATION.occurrence_local_date,
      },
      expect.any(AbortSignal),
    )
  })

  it('Push target이 다음 페이지에 있으면 bounded pagination으로 찾아 mutation 없이 이동한다', async () => {
    vi.mocked(listNotifications)
      .mockResolvedValueOnce({ data: { items: [READ_NOTIFICATION], next_offset: 20 } })
      .mockResolvedValueOnce({ data: { items: [UNREAD_NOTIFICATION], next_offset: null } })
    renderPage(undefined, `/notifications?push_notification_id=${UNREAD_NOTIFICATION.id}`)

    expect((await screen.findByTestId('location')).textContent).toBe(
      `/schedule/occurrences/${UNREAD_NOTIFICATION.occurrence_id}?date=2026-09-10`,
    )
    expect(listNotifications).toHaveBeenNthCalledWith(2, {
      offset: 20,
      signal: expect.any(AbortSignal),
    })
    expect(markNotificationRead).not.toHaveBeenCalled()
    expect(putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('Push handoff 중 401이면 로그인 후 복구할 원래 notification target을 보존한다', async () => {
    vi.mocked(resolveNotificationOccurrenceMedication).mockRejectedValue(
      new ApiError(401, '로그인이 필요합니다.', 'INVALID_TOKEN'),
    )
    renderPage(undefined, `/notifications?push_notification_id=${UNREAD_NOTIFICATION.id}`)

    fireEvent.click(await screen.findByRole('button', { name: '다시 로그인' }))

    expect((await screen.findByTestId('login-return')).textContent).toBe(
      `/notifications?push_notification_id=${UNREAD_NOTIFICATION.id}`,
    )
    expect(markNotificationRead).not.toHaveBeenCalled()
  })

  it('이미 read 알림은 PATCH 없이 occurrence를 검증하고 같은 route로 이동한다', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', {
      name: '복약 알림, 복약일 2026-09-09, 읽음',
    }))
    expect((await screen.findByTestId('location')).textContent).toBe(
      `/schedule/occurrences/${READ_NOTIFICATION.occurrence_id}?date=2026-09-09`,
    )
    expect(markNotificationRead).not.toHaveBeenCalled()
    expect(resolveNotificationOccurrenceMedication).toHaveBeenCalledWith(
      {
        occurrenceId: READ_NOTIFICATION.occurrence_id,
        occurrenceLocalDate: READ_NOTIFICATION.occurrence_local_date,
      },
      expect.anything(),
    )
    expect(putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('scheduled_at의 날짜와 다른 occurrence_local_date를 URL에 사용한다', async () => {
    expect(UNREAD_NOTIFICATION.scheduled_at.startsWith('2026-09-11')).toBe(true)
    expect(UNREAD_NOTIFICATION.occurrence_local_date).toBe('2026-09-10')
    renderPage()
    fireEvent.click(await screen.findByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
    }))
    expect((await screen.findByTestId('location')).textContent).toContain('date=2026-09-10')
  })

  it('read PATCH pending 중 route를 떠나면 late resolve가 navigation하지 않는다', async () => {
    const read = createDeferred<Awaited<ReturnType<typeof markNotificationRead>>>()
    const onHandoffReady = vi.fn()
    vi.mocked(markNotificationRead).mockReturnValueOnce(read.promise)
    renderPage(onHandoffReady)

    fireEvent.click(await screen.findByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
    }))
    const readSignal = vi.mocked(markNotificationRead).mock.calls[0]?.[2]
    expect(readSignal?.aborted).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '테스트 경로 이동' }))
    expect(screen.getByTestId('location').textContent).toBe('/away')
    expect(readSignal?.aborted).toBe(true)

    await act(async () => {
      read.resolve({
        data: { ...UNREAD_NOTIFICATION, read_at: '2026-09-11T02:00:00Z' },
      })
      await read.promise
    })

    expect(screen.getByTestId('location').textContent).toBe('/away')
    expect(resolveNotificationOccurrenceMedication).not.toHaveBeenCalled()
    expect(onHandoffReady).not.toHaveBeenCalled()
  })

  it('occurrence GET pending 중 route를 떠나면 late resolve가 navigation하지 않는다', async () => {
    const occurrence = createDeferred<void>()
    const medication = createDeferred<ReturnType<typeof resolvedFixture>>()
    const onHandoffReady = vi.fn()
    vi.mocked(resolveNotificationOccurrenceMedication).mockImplementationOnce(
      async (handoff) => {
        await occurrence.promise
        return medication.promise.then(() => resolvedFixture(handoff))
      },
    )
    renderPage(onHandoffReady)

    fireEvent.click(await screen.findByRole('button', {
      name: '복약 알림, 복약일 2026-09-09, 읽음',
    }))
    const lookupSignal = vi.mocked(resolveNotificationOccurrenceMedication).mock.calls[0]?.[1]
    expect(lookupSignal?.aborted).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '테스트 경로 이동' }))
    expect(screen.getByTestId('location').textContent).toBe('/away')
    expect(lookupSignal?.aborted).toBe(true)

    await act(async () => {
      occurrence.resolve()
      medication.resolve(resolvedFixture({
        occurrenceId: READ_NOTIFICATION.occurrence_id,
        occurrenceLocalDate: READ_NOTIFICATION.occurrence_local_date,
      }))
      await medication.promise
    })

    expect(screen.getByTestId('location').textContent).toBe('/away')
    expect(onHandoffReady).not.toHaveBeenCalled()
  })

  it('medication validation pending 중 route를 떠나면 late resolve가 navigation하지 않는다', async () => {
    const occurrence = createDeferred<void>()
    const medication = createDeferred<ReturnType<typeof resolvedFixture>>()
    const onHandoffReady = vi.fn()
    vi.mocked(resolveNotificationOccurrenceMedication).mockImplementationOnce(
      async (handoff) => {
        await occurrence.promise
        return medication.promise.then(() => resolvedFixture(handoff))
      },
    )
    renderPage(onHandoffReady)

    fireEvent.click(await screen.findByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
    }))
    await act(async () => occurrence.resolve())
    const lookupSignal = vi.mocked(resolveNotificationOccurrenceMedication).mock.calls[0]?.[1]
    expect(lookupSignal?.aborted).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '테스트 경로 이동' }))
    expect(lookupSignal?.aborted).toBe(true)

    await act(async () => {
      medication.resolve(resolvedFixture({
        occurrenceId: UNREAD_NOTIFICATION.occurrence_id,
        occurrenceLocalDate: UNREAD_NOTIFICATION.occurrence_local_date,
      }))
      await medication.promise
    })

    expect(screen.getByTestId('location').textContent).toBe('/away')
    expect(onHandoffReady).not.toHaveBeenCalled()
  })

  it('이미 read된 notification의 pending 조회도 route 이탈 뒤 navigation하지 않는다', async () => {
    const lookup = createDeferred<ReturnType<typeof resolvedFixture>>()
    const onHandoffReady = vi.fn()
    vi.mocked(resolveNotificationOccurrenceMedication).mockReturnValueOnce(lookup.promise)
    renderPage(onHandoffReady)

    fireEvent.click(await screen.findByRole('button', {
      name: '복약 알림, 복약일 2026-09-09, 읽음',
    }))
    const lookupSignal = vi.mocked(resolveNotificationOccurrenceMedication).mock.calls[0]?.[1]
    fireEvent.click(screen.getByRole('button', { name: '테스트 경로 이동' }))
    expect(lookupSignal?.aborted).toBe(true)
    await act(async () => {
      lookup.resolve(resolvedFixture({
        occurrenceId: READ_NOTIFICATION.occurrence_id,
        occurrenceLocalDate: READ_NOTIFICATION.occurrence_local_date,
      }))
      await lookup.promise
    })

    expect(markNotificationRead).not.toHaveBeenCalled()
    expect(screen.getByTestId('location').textContent).toBe('/away')
    expect(onHandoffReady).not.toHaveBeenCalled()
  })

  it('notification A의 late resolve가 이후 선택한 B의 handoff를 덮어쓰지 않는다', async () => {
    const firstLookup = createDeferred<ReturnType<typeof resolvedFixture>>()
    const onHandoffReady = vi.fn()
    vi.mocked(resolveNotificationOccurrenceMedication)
      .mockReturnValueOnce(firstLookup.promise)
      .mockImplementationOnce(async (handoff) => resolvedFixture(handoff))
    renderPage(onHandoffReady)

    fireEvent.click(await screen.findByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
    }))
    fireEvent.click(await screen.findByRole('button', {
      name: '복약 알림, 복약일 2026-09-09, 읽음',
    }))
    expect(
      vi.mocked(resolveNotificationOccurrenceMedication).mock.calls[0]?.[1]?.aborted,
    ).toBe(true)

    expect((await screen.findByTestId('location')).textContent).toBe(
      `/schedule/occurrences/${READ_NOTIFICATION.occurrence_id}?date=2026-09-09`,
    )

    await act(async () => {
      firstLookup.resolve(resolvedFixture({
        occurrenceId: UNREAD_NOTIFICATION.occurrence_id,
        occurrenceLocalDate: UNREAD_NOTIFICATION.occurrence_local_date,
      }))
      await firstLookup.promise
    })

    expect(screen.getByTestId('location').textContent).toBe(
      `/schedule/occurrences/${READ_NOTIFICATION.occurrence_id}?date=2026-09-09`,
    )
    expect(onHandoffReady).toHaveBeenCalledTimes(1)
    expect(onHandoffReady).toHaveBeenCalledWith({
      occurrenceId: READ_NOTIFICATION.occurrence_id,
      occurrenceLocalDate: READ_NOTIFICATION.occurrence_local_date,
    })
    expect(putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('선택 중 상태를 accessible name과 aria-busy로 알린다', async () => {
    vi.mocked(markNotificationRead).mockImplementation(() => new Promise(() => undefined))
    renderPage()
    fireEvent.click(await screen.findByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
    }))
    const selectingButton = await screen.findByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 복약 기록 확인 중',
    })
    expect(selectingButton.getAttribute('aria-busy')).toBe('true')
  })

  it.each([
    ['404', new ApiError(404, 'not owned'), '알림 정보를 확인할 수 없어요.'],
    ['5xx', new ApiError(503, 'backend detail'), '알림 서비스에 잠시 연결할 수 없어요. 잠시 후 다시 시도해 주세요.'],
    ['network', new TypeError('Failed to fetch'), '네트워크 연결을 확인한 뒤 다시 시도해 주세요.'],
  ])('%s 목록 오류는 내부 정보 없이 retry를 제공한다', async (_label, error, message) => {
    vi.mocked(listNotifications).mockRejectedValueOnce(error)
    renderPage()
    expect((await screen.findByRole('alert')).textContent).toContain(message)
    vi.mocked(listNotifications).mockResolvedValueOnce({ data: { items: [], next_offset: null } })
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))
    expect(await screen.findByText('새로운 알림이 없어요')).toBeTruthy()
  })

  it('read PATCH 실패는 기록 진입 검증을 시작하지 않고 같은 키로 retry한다', async () => {
    vi.mocked(markNotificationRead)
      .mockRejectedValueOnce(new ApiError(503, 'provider detail'))
      .mockResolvedValueOnce({ data: { ...UNREAD_NOTIFICATION, read_at: '2026-09-11T02:00:00Z' } })
    renderPage()
    fireEvent.click(await screen.findByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
    }))
    expect((await screen.findByRole('alert')).textContent).toContain('알림을 읽음 처리하지 못했어요')
    expect(resolveNotificationOccurrenceMedication).not.toHaveBeenCalled()
    expect(screen.queryByTestId('location')).toBeNull()
    expect(putMedicationCheckin).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '읽음 처리 다시 시도' }))
    await screen.findByTestId('location')
    expect(markNotificationRead).toHaveBeenNthCalledWith(
      2,
      UNREAD_NOTIFICATION.id,
      'notification-read:test-key',
      expect.anything(),
    )
  })

  it.each(['occurrence ID mismatch', 'medication detail ID mismatch'])(
    '%s는 중립적 실패로 표시하고 navigation하지 않는다',
    async () => {
      vi.mocked(resolveNotificationOccurrenceMedication).mockRejectedValueOnce(
        new OccurrenceMedicationUnavailableError(),
      )
      renderPage()
      fireEvent.click(await screen.findByRole('button', {
        name: '복약 알림, 복약일 2026-09-09, 읽음',
      }))
      expect((await screen.findByRole('alert')).textContent).toContain('복약 기록을 확인할 수 없어요.')
      expect(screen.queryByTestId('location')).toBeNull()
      expect(markNotificationRead).not.toHaveBeenCalled()
      expect(putMedicationCheckin).not.toHaveBeenCalled()
    },
  )

  it('기록 진입 실패는 read 실패와 다른 retry 상태를 표시한다', async () => {
    vi.mocked(resolveNotificationOccurrenceMedication).mockRejectedValueOnce(
      new ApiError(503, 'raw backend detail'),
    )
    renderPage()
    fireEvent.click(await screen.findByRole('button', {
      name: '복약 알림, 복약일 2026-09-09, 읽음',
    }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('복약 기록을 열지 못했어요')
    expect(alert.textContent).not.toContain('raw backend detail')
    expect(screen.getByRole('button', { name: '복약 기록 다시 확인' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '읽음 처리 다시 시도' })).toBeNull()
  })

  it('401 목록 오류는 세션을 정리하는 재로그인 복구를 제공한다', async () => {
    localStorage.setItem('access_token', 'expired-token')
    vi.mocked(listNotifications).mockRejectedValueOnce(new ApiError(401, 'backend detail'))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '다시 로그인' }))
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('알림 선택 전후에 Check-in PUT을 한 번도 호출하지 않는다', async () => {
    renderPage()
    expect(putMedicationCheckin).toHaveBeenCalledTimes(0)
    fireEvent.click(await screen.findByRole('button', {
      name: '복약 알림, 복약일 2026-09-09, 읽음',
    }))
    await screen.findByTestId('location')
    expect(putMedicationCheckin).toHaveBeenCalledTimes(0)
  })
})
