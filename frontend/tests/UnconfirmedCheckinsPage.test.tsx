import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type {
  UnconfirmedCheckinItem,
  UnconfirmedCheckinResponse,
} from '../src/api/medicationCheckinBacklog'
import type { MedicationCheckinResponse } from '../src/api/medicationCheckins'
import type { MedicationOccurrenceMedicationResponse } from '../src/api/medicationOccurrences'
import {
  UnconfirmedCheckinsPage,
  type UnconfirmedCheckinsPageServices,
} from '../src/pages/UnconfirmedCheckinsPage'

const occurrenceId = '11111111-1111-4111-8111-111111111111'
const checkinId = '22222222-2222-4222-8222-222222222222'
const prescriptionId = '33333333-3333-4333-8333-333333333333'
const historicalVersionId = '44444444-4444-4444-8444-444444444444'
const historicalMedicationId = '55555555-5555-4555-8555-555555555555'
const nextOccurrenceId = '66666666-6666-4666-8666-666666666666'
const nextCheckinId = '77777777-7777-4777-8777-777777777777'

function makeItem(
  overrides: Partial<UnconfirmedCheckinItem> = {},
): UnconfirmedCheckinItem {
  return {
    checkin_id: checkinId,
    occurrence_id: occurrenceId,
    prescription_id: prescriptionId,
    prescription_version_id: historicalVersionId,
    prescription_version_medication_id: historicalMedicationId,
    medication_name: '목록의 과거약',
    strength_text: '10mg',
    scheduled_local_date: '2026-09-04',
    scheduled_at: '2026-09-04T04:00:00Z',
    confirmation_deadline_at: '2026-09-04T09:00:00Z',
    status: 'UNCONFIRMED',
    revision: 1,
    ...overrides,
  }
}

function makePage(
  items: UnconfirmedCheckinItem[],
  nextCursor: string | null = null,
): UnconfirmedCheckinResponse {
  return { data: { items, next_cursor: nextCursor } }
}

function makeMedication(
  item = makeItem(),
  overrides: Partial<MedicationOccurrenceMedicationResponse['data']> = {},
): MedicationOccurrenceMedicationResponse {
  return {
    data: {
      occurrence_id: item.occurrence_id,
      prescription_version_id: item.prescription_version_id,
      prescription_version_medication_id:
        item.prescription_version_medication_id,
      medication_name: '원래 처방의 과거약',
      strength_text: '10mg',
      dose_value: 1,
      dose_unit: '정',
      ...overrides,
    },
  }
}

function makeCheckin(
  item: UnconfirmedCheckinItem,
  status: 'TAKEN' | 'NOT_TAKEN',
): MedicationCheckinResponse {
  return {
    data: {
      checkin_id: item.checkin_id,
      occurrence_id: item.occurrence_id,
      status,
      taken_at: null,
      revision: item.revision + 1,
      corrected: true,
    },
  }
}

function createServices(
  overrides: Partial<UnconfirmedCheckinsPageServices> = {},
): UnconfirmedCheckinsPageServices {
  return {
    getUnconfirmedCheckins: vi.fn().mockResolvedValue(makePage([])),
    getMedicationOccurrenceMedication: vi
      .fn()
      .mockImplementation(async (id: string) => makeMedication(makeItem({ occurrence_id: id }))),
    putMedicationCheckin: vi.fn(),
    createCheckinIdempotencyKey: vi
      .fn()
      .mockReturnValue('checkin:11111111-1111-4111-8111-111111111111'),
    ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderPage(services: UnconfirmedCheckinsPageServices) {
  return render(
    <MemoryRouter initialEntries={['/schedule/unconfirmed']}>
      <Routes>
        <Route
          path="*"
          element={<><UnconfirmedCheckinsPage services={services} /><LocationProbe /></>}
        />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => cleanup())

describe('UNCONFIRMED recovery list', () => {
  it('shows loading and then the empty state', async () => {
    let resolveRequest!: (value: UnconfirmedCheckinResponse) => void
    const services = createServices({
      getUnconfirmedCheckins: vi.fn().mockReturnValue(
        new Promise((resolve) => { resolveRequest = resolve }),
      ),
    })
    renderPage(services)

    expect(screen.getByText('미확인 기록을 불러오는 중이에요')).toBeTruthy()
    resolveRequest(makePage([]))
    expect(await screen.findByText('확인할 미확인 기록이 없어요')).toBeTruthy()
  })

  it('keeps backend order for one or multiple items and does not mutate on entry or card text click', async () => {
    const first = makeItem()
    const second = makeItem({
      checkin_id: nextCheckinId,
      occurrence_id: nextOccurrenceId,
      scheduled_at: '2026-09-05T04:00:00Z',
      scheduled_local_date: '2026-09-05',
    })
    const put = vi.fn()
    const services = createServices({
      getUnconfirmedCheckins: vi.fn().mockResolvedValue(makePage([first, second])),
      getMedicationOccurrenceMedication: vi
        .fn()
        .mockImplementation(async (id: string) =>
          makeMedication(id === first.occurrence_id ? first : second, {
            medication_name: id === first.occurrence_id ? '첫 번째 과거약' : '두 번째 과거약',
          }),
        ),
      putMedicationCheckin: put,
    })
    renderPage(services)

    await screen.findByText('첫 번째 과거약 · 10mg · 1정')
    const names = screen.getAllByText(/번째 과거약/).map((node) => node.textContent)
    expect(names).toEqual(['첫 번째 과거약 · 10mg · 1정', '두 번째 과거약 · 10mg · 1정'])
    fireEvent.click(screen.getByText('첫 번째 과거약 · 10mg · 1정'))
    expect(put).not.toHaveBeenCalled()
  })

  it('appends the next cursor page without reordering the first page', async () => {
    const first = makeItem()
    const second = makeItem({ checkin_id: nextCheckinId, occurrence_id: nextOccurrenceId })
    const get = vi.fn()
      .mockResolvedValueOnce(makePage([first], checkinId))
      .mockResolvedValueOnce(makePage([second]))
    const services = createServices({ getUnconfirmedCheckins: get })
    renderPage(services)

    await screen.findByText('원래 처방의 과거약 · 10mg · 1정')
    fireEvent.click(screen.getByRole('button', { name: '미확인 기록 더 보기' }))
    await waitFor(() => expect(get).toHaveBeenLastCalledWith({
      limit: 20,
      cursor: checkinId,
    }))
    expect(screen.getAllByText('원래 처방의 과거약 · 10mg · 1정')).toHaveLength(2)
  })

  it('disables correction choices while a cursor page is being appended', async () => {
    const first = makeItem()
    let resolveNextPage!: (value: UnconfirmedCheckinResponse) => void
    const get = vi.fn()
      .mockResolvedValueOnce(makePage([first], checkinId))
      .mockReturnValueOnce(new Promise((resolve) => { resolveNextPage = resolve }))
    const put = vi.fn()
    renderPage(createServices({ getUnconfirmedCheckins: get, putMedicationCheckin: put }))

    await screen.findByRole('button', { name: '미확인 기록 더 보기' })
    fireEvent.click(screen.getByRole('button', { name: '미확인 기록 더 보기' }))

    await waitFor(() => expect(
      screen.getByRole('button', { name: /복용했어요/ }).closest('fieldset'),
    ).toHaveProperty('disabled', true))
    fireEvent.click(screen.getByRole('button', { name: /복용했어요/ }))
    expect(put).not.toHaveBeenCalled()

    resolveNextPage(makePage([]))
    await waitFor(() => expect(
      screen.getByRole('button', { name: /복용했어요/ }).closest('fieldset'),
    ).toHaveProperty('disabled', false))
  })

  it('recovers a stale or hidden cursor by replacing the list from page one', async () => {
    const first = makeItem()
    const replacement = makeItem({
      checkin_id: nextCheckinId,
      occurrence_id: nextOccurrenceId,
    })
    const get = vi.fn()
      .mockResolvedValueOnce(makePage([first], checkinId))
      .mockRejectedValueOnce(new ApiError(404, 'hidden cursor', 'CHECKIN_CURSOR_NOT_FOUND'))
      .mockResolvedValueOnce(makePage([replacement]))
    renderPage(createServices({ getUnconfirmedCheckins: get }))

    await screen.findByRole('button', { name: '미확인 기록 더 보기' })
    fireEvent.click(screen.getByRole('button', { name: '미확인 기록 더 보기' }))

    expect(await screen.findByText('목록이 변경되어 처음부터 다시 불러왔어요.')).toBeTruthy()
    expect(get).toHaveBeenLastCalledWith({ limit: 20, signal: undefined })
  })

  it('uses the exact historical occurrence medication and never falls back on lookup failure', async () => {
    const item = makeItem()
    const services = createServices({
      getUnconfirmedCheckins: vi.fn().mockResolvedValue(makePage([item])),
      getMedicationOccurrenceMedication: vi
        .fn()
        .mockResolvedValueOnce(makeMedication(item, { medication_name: '과거 버전 약' })),
    })
    const { rerender } = renderPage(services)

    expect(await screen.findByText('과거 버전 약 · 10mg · 1정')).toBeTruthy()
    expect(services.getMedicationOccurrenceMedication).toHaveBeenCalledWith(item.occurrence_id, expect.any(AbortSignal))

    const failed = createServices({
      getUnconfirmedCheckins: vi.fn().mockResolvedValue(makePage([item])),
      getMedicationOccurrenceMedication: vi.fn().mockRejectedValue(new ApiError(404, 'hidden')),
    })
    rerender(
      <MemoryRouter initialEntries={['/schedule/unconfirmed']}>
        <UnconfirmedCheckinsPage services={failed} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('원래 약 정보를 확인하지 못했어요')).toBeTruthy()
    expect(screen.queryByText('목록의 과거약')).toBeNull()
    expect(
      screen.getByRole('button', { name: /복용했어요/ })
        .closest('fieldset')
        ?.hasAttribute('disabled'),
    ).toBe(true)
  })

  it.each([
    [new TypeError('network'), '인터넷 연결을 확인해 주세요'],
    [new ApiError(500, 'raw server detail'), '미확인 기록을 잠시 불러오지 못했어요'],
  ])('keeps list failures retryable with sanitized copy', async (error, title) => {
    const get = vi.fn()
      .mockRejectedValueOnce(error)
      .mockResolvedValueOnce(makePage([]))
    renderPage(createServices({ getUnconfirmedCheckins: get }))

    expect(await screen.findByText(title)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))
    expect(await screen.findByText('확인할 미확인 기록이 없어요')).toBeTruthy()
    expect(get).toHaveBeenCalledTimes(2)
  })
})

describe('UNCONFIRMED correction', () => {
  it.each(['TAKEN', 'NOT_TAKEN'] as const)(
    'submits %s with occurrence, current revision and refreshes from the server',
    async (status) => {
      const item = makeItem()
      const get = vi.fn()
        .mockResolvedValueOnce(makePage([item]))
        .mockResolvedValueOnce(makePage([]))
      const put = vi.fn().mockResolvedValue(makeCheckin(item, status))
      const services = createServices({ getUnconfirmedCheckins: get, putMedicationCheckin: put })
      renderPage(services)

      await screen.findByText('원래 처방의 과거약 · 10mg · 1정')
      fireEvent.click(screen.getByRole('button', {
        name: status === 'TAKEN' ? /복용했어요/ : /복용하지 않았어요/,
      }))

      await waitFor(() => expect(put).toHaveBeenCalledWith(
        item.occurrence_id,
        { status, expectedRevision: item.revision },
        'checkin:11111111-1111-4111-8111-111111111111',
      ))
      expect(await screen.findByText('확인할 미확인 기록이 없어요')).toBeTruthy()
      expect(screen.getByText('복약 기록을 저장하고 목록을 새로 불러왔어요.')).toBeTruthy()
    },
  )

  it('keeps the same key/body/revision for a lost-response retry', async () => {
    const item = makeItem()
    const get = vi.fn()
      .mockResolvedValueOnce(makePage([item]))
      .mockResolvedValueOnce(makePage([]))
    const put = vi.fn()
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce(makeCheckin(item, 'TAKEN'))
    const createKey = vi.fn().mockReturnValue('checkin:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    renderPage(createServices({ getUnconfirmedCheckins: get, putMedicationCheckin: put, createCheckinIdempotencyKey: createKey }))

    await screen.findByText('원래 처방의 과거약 · 10mg · 1정')
    fireEvent.click(screen.getByRole('button', { name: /복용했어요/ }))
    await screen.findByRole('button', { name: '다시 저장하기' })
    fireEvent.click(screen.getByRole('button', { name: '다시 저장하기' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[1]).toEqual(put.mock.calls[0])
    expect(createKey).toHaveBeenCalledTimes(1)
  })

  it('shows save pending and keeps other records after the server refresh', async () => {
    const first = makeItem()
    const second = makeItem({ checkin_id: nextCheckinId, occurrence_id: nextOccurrenceId })
    let resolvePut!: (value: MedicationCheckinResponse) => void
    const put = vi.fn().mockReturnValue(
      new Promise((resolve) => { resolvePut = resolve }),
    )
    const get = vi.fn()
      .mockResolvedValueOnce(makePage([first, second]))
      .mockResolvedValueOnce(makePage([second]))
    renderPage(createServices({ getUnconfirmedCheckins: get, putMedicationCheckin: put }))

    await screen.findAllByText('원래 처방의 과거약 · 10mg · 1정')
    fireEvent.click(screen.getAllByRole('button', { name: /복용했어요/ })[0]!)
    expect(await screen.findByText('선택한 기록을 저장하는 중이에요.')).toBeTruthy()
    resolvePut(makeCheckin(first, 'TAKEN'))

    await waitFor(() => expect(screen.getAllByText('원래 처방의 과거약 · 10mg · 1정')).toHaveLength(1))
    expect(screen.getByText('1건 표시')).toBeTruthy()
  })

  it('creates a new key when the user changes the selected answer', async () => {
    const item = makeItem()
    const put = vi.fn()
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce(makeCheckin(item, 'NOT_TAKEN'))
    const createKey = vi.fn()
      .mockReturnValueOnce('checkin:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
      .mockReturnValueOnce('checkin:bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
    const get = vi.fn()
      .mockResolvedValueOnce(makePage([item]))
      .mockResolvedValueOnce(makePage([]))
    renderPage(createServices({ getUnconfirmedCheckins: get, putMedicationCheckin: put, createCheckinIdempotencyKey: createKey }))

    await screen.findByText('원래 처방의 과거약 · 10mg · 1정')
    fireEvent.click(screen.getByRole('button', { name: /복용했어요/ }))
    await screen.findByRole('button', { name: '다시 저장하기' })
    fireEvent.click(screen.getByRole('button', { name: /복용하지 않았어요/ }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[0]?.[2]).not.toBe(put.mock.calls[1]?.[2])
    expect(createKey).toHaveBeenCalledTimes(2)
  })

  it('reloads on revision conflict and never resubmits the previous choice', async () => {
    const item = makeItem()
    const refreshed = makeItem({ revision: 2 })
    const get = vi.fn()
      .mockResolvedValueOnce(makePage([item]))
      .mockResolvedValueOnce(makePage([refreshed]))
    const put = vi.fn().mockRejectedValue(
      new ApiError(409, 'raw conflict', 'CHECKIN_REVISION_CONFLICT'),
    )
    renderPage(createServices({ getUnconfirmedCheckins: get, putMedicationCheckin: put }))

    await screen.findByText('원래 처방의 과거약 · 10mg · 1정')
    fireEvent.click(screen.getByRole('button', { name: /복용했어요/ }))

    expect(await screen.findByText('기록이 다른 곳에서 변경됐어요. 최신 상태에서 다시 선택해 주세요.')).toBeTruthy()
    expect(put).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: /복용했어요/ }).getAttribute('aria-pressed')).toBe('false')
  })

  it('removes an item already corrected elsewhere after revision conflict', async () => {
    const item = makeItem()
    const get = vi.fn()
      .mockResolvedValueOnce(makePage([item]))
      .mockResolvedValueOnce(makePage([]))
    const put = vi.fn().mockRejectedValue(
      new ApiError(409, 'raw conflict', 'CHECKIN_REVISION_CONFLICT'),
    )
    renderPage(createServices({ getUnconfirmedCheckins: get, putMedicationCheckin: put }))

    await screen.findByText('원래 처방의 과거약 · 10mg · 1정')
    fireEvent.click(screen.getByRole('button', { name: /복용했어요/ }))

    expect(await screen.findByText('이 기록은 다른 곳에서 이미 보완되어 목록에서 제외됐어요.')).toBeTruthy()
    expect(await screen.findByText('확인할 미확인 기록이 없어요')).toBeTruthy()
    expect(put).toHaveBeenCalledTimes(1)
  })

  it('traverses every cursor page before deciding a later-page conflict was removed', async () => {
    const first = makeItem()
    const later = makeItem({
      checkin_id: nextCheckinId,
      occurrence_id: nextOccurrenceId,
    })
    const refreshedLater = makeItem({
      checkin_id: nextCheckinId,
      occurrence_id: nextOccurrenceId,
      revision: 2,
    })
    const refreshCursor = '88888888-8888-4888-8888-888888888888'
    const get = vi.fn()
      .mockResolvedValueOnce(makePage([first], checkinId))
      .mockResolvedValueOnce(makePage([later]))
      .mockResolvedValueOnce(makePage([first], refreshCursor))
      .mockResolvedValueOnce(makePage([refreshedLater]))
    const put = vi.fn().mockRejectedValue(
      new ApiError(409, 'raw conflict', 'CHECKIN_REVISION_CONFLICT'),
    )
    renderPage(createServices({ getUnconfirmedCheckins: get, putMedicationCheckin: put }))

    await screen.findByRole('button', { name: '미확인 기록 더 보기' })
    fireEvent.click(screen.getByRole('button', { name: '미확인 기록 더 보기' }))
    await waitFor(() => expect(screen.getAllByRole('button', { name: /복용했어요/ })).toHaveLength(2))
    fireEvent.click(screen.getAllByRole('button', { name: /복용했어요/ })[1]!)

    expect(await screen.findByText('기록이 다른 곳에서 변경됐어요. 최신 상태에서 다시 선택해 주세요.')).toBeTruthy()
    expect(get).toHaveBeenNthCalledWith(3, { limit: 100, cursor: undefined })
    expect(get).toHaveBeenNthCalledWith(4, { limit: 100, cursor: refreshCursor })
    expect(put).toHaveBeenCalledTimes(1)
  })

  it('defers with navigation only and refetches when the page is entered again', async () => {
    const item = makeItem()
    const get = vi.fn().mockResolvedValue(makePage([item]))
    const put = vi.fn()
    const services = createServices({ getUnconfirmedCheckins: get, putMedicationCheckin: put })
    const first = renderPage(services)

    await screen.findByText('원래 처방의 과거약 · 10mg · 1정')
    fireEvent.click(screen.getByRole('button', { name: '지금은 확인하기 어려워요' }))
    expect(screen.getByTestId('location').textContent).toBe('/schedule')
    expect(put).not.toHaveBeenCalled()
    first.unmount()

    renderPage(services)
    await screen.findByText('원래 처방의 과거약 · 10mg · 1정')
    expect(get.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(put).not.toHaveBeenCalled()
  })

  it('sanitizes SELF 404 and server payloads without exposing raw details', async () => {
    const services = createServices({
      getUnconfirmedCheckins: vi.fn().mockRejectedValue(
        new ApiError(404, 'secret user payload', 'CHECKIN_CURSOR_NOT_FOUND'),
      ),
    })
    renderPage(services)

    expect(await screen.findByText('미확인 기록을 확인할 수 없어요')).toBeTruthy()
    expect(screen.queryByText(/secret user payload/)).toBeNull()
    expect(services.putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('clears the session and redirects on 401 from historical medication lookup', async () => {
    localStorage.setItem('access_token', 'expired-token')
    const item = makeItem()
    const services = createServices({
      getUnconfirmedCheckins: vi.fn().mockResolvedValue(makePage([item])),
      getMedicationOccurrenceMedication: vi.fn().mockRejectedValue(
        new ApiError(401, 'expired', 'EXPIRED_TOKEN'),
      ),
    })
    renderPage(services)

    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/login'))
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(services.putMedicationCheckin).not.toHaveBeenCalled()
  })
})
