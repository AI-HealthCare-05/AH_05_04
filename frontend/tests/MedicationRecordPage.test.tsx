import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type {
  MedicationDayResponse,
  MedicationOccurrenceData,
  MedicationOccurrenceMedicationResponse,
} from '../src/api/medicationSchedules'
import {
  MedicationRecordPage,
  type MedicationRecordPageServices,
} from '../src/pages/MedicationRecordPage'

const prescriptionVersionId = '33333333-3333-4333-8333-333333333333'
const medicationId = '22222222-2222-4222-8222-222222222222'

// 고정 시계: KST 2026-09-16(수) 14:00
const FIXED_NOW = new Date('2026-09-16T05:00:00Z')
const TODAY = '2026-09-16'

function makeOccurrence(
  occurrenceId: string,
  scheduledAt: string,
  overrides: Partial<MedicationOccurrenceData> = {},
): MedicationOccurrenceData {
  return {
    occurrence_id: occurrenceId,
    prescription_version_id: prescriptionVersionId,
    prescription_version_medication_id: medicationId,
    scheduled_local_date: TODAY,
    scheduled_at: scheduledAt,
    confirmation_deadline_at: '2026-09-16T18:00:00Z',
    status: 'PENDING',
    checkin: null,
    ...overrides,
  } as MedicationOccurrenceData
}

function makeDay(occurrences: MedicationOccurrenceData[]): MedicationDayResponse {
  return {
    data: {
      schedule_status: 'READY',
      schedule_items: [],
      occurrences,
    },
  } as unknown as MedicationDayResponse
}

function makeMedication(
  occurrenceId: string,
): MedicationOccurrenceMedicationResponse {
  return {
    data: {
      occurrence_id: occurrenceId,
      prescription_version_id: prescriptionVersionId,
      prescription_version_medication_id: medicationId,
      medication_name: '합성 혈압약',
      strength_text: '5mg',
      dose_value: 1,
      dose_unit: '정',
    },
  } as unknown as MedicationOccurrenceMedicationResponse
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
}

function makeServices(
  overrides: Partial<MedicationRecordPageServices> = {},
): MedicationRecordPageServices {
  return {
    getMedicationDay: vi.fn(async () =>
      makeDay([
        // KST 08:00 / 13:00 / 20:00
        makeOccurrence('occ-1', '2026-09-15T23:00:00Z'),
        makeOccurrence('occ-2', '2026-09-16T04:00:00Z'),
        makeOccurrence('occ-3', '2026-09-16T11:00:00Z'),
      ]),
    ),
    getOccurrenceMedication: vi.fn(async (id: string) => makeMedication(id)),
    ...overrides,
  } as MedicationRecordPageServices
}

function renderPage(
  services: MedicationRecordPageServices = makeServices(),
  initialEntry = `/records?date=${TODAY}`,
) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/records"
          element={<><MedicationRecordPage services={services} /><LocationProbe /></>}
        />
        <Route
          path="/schedule/occurrences/:occurrenceId"
          element={<><div>복용 여부 기록 화면</div><LocationProbe /></>}
        />
        <Route path="/menu" element={<div>메뉴 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(FIXED_NOW)
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('RECORD-01 복약 기록', () => {
  it('제목과 선택 날짜, occurrence 카드를 표시한다', async () => {
    renderPage()

    expect(
      await screen.findByRole('heading', { name: '복약 기록', level: 1 }),
    ).toBeTruthy()
    expect(
      await screen.findByRole('heading', { name: /복약 기록$/, level: 2 }),
    ).toBeTruthy()
    await waitFor(() =>
      expect(screen.getAllByText('합성 혈압약 · 5mg · 1정').length).toBe(3),
    )
  })

  it('고정 7일 selector에서 미래 날짜만 비활성화한다', async () => {
    renderPage()
    await screen.findByRole('heading', { name: '복약 기록', level: 1 })

    const dayGroup = screen.getByRole('group', { name: '날짜 선택' })
    const days = dayGroup.querySelectorAll('button')

    // 월~일 7일 고정
    expect(days).toHaveLength(7)
    // 2026-09-16은 수요일 -> 월/화/수는 선택 가능, 목~일은 비활성
    expect((days[0] as HTMLButtonElement).disabled).toBe(false)
    expect((days[2] as HTMLButtonElement).disabled).toBe(false)
    expect((days[3] as HTMLButtonElement).disabled).toBe(true)
    expect((days[6] as HTMLButtonElement).disabled).toBe(true)
    // 오늘 표시
    expect(days[2].getAttribute('aria-current')).toBe('date')
    expect(days[2].getAttribute('aria-pressed')).toBe('true')
  })

  it('과거 날짜를 선택하면 해당 date로 다시 조회한다', async () => {
    const services = makeServices()
    renderPage(services)
    await screen.findByRole('heading', { name: '복약 기록', level: 1 })

    const dayGroup = screen.getByRole('group', { name: '날짜 선택' })
    fireEvent.click(dayGroup.querySelectorAll('button')[0])

    await waitFor(() =>
      expect(services.getMedicationDay).toHaveBeenCalledWith(
        '2026-09-14',
        expect.anything(),
      ),
    )
    expect(screen.getByTestId('location').textContent).toBe(
      '/records?date=2026-09-14',
    )
  })

  it('예정 시각 전 PENDING은 read-only로 표시한다', async () => {
    renderPage()
    await screen.findByRole('heading', { name: '복약 기록', level: 1 })

    // 08:00, 13:00은 기록 가능 / 20:00은 read-only
    await waitFor(() =>
      expect(
        screen.getAllByRole('button', { name: '복용 여부 기록하기' }),
      ).toHaveLength(2),
    )
    expect(
      document.querySelectorAll('.medication-record-card--readonly'),
    ).toHaveLength(1)
    expect(
      screen.getByText('20:00부터 복약 기록을 남길 수 있어요.'),
    ).toBeTruthy()
  })

  // 기존 Check-in 화면(CHECKIN-01)으로 진입한다. RECORD-02는 이번 범위가 아니다.
  it('기록 가능한 PENDING은 기존 Check-in 화면으로 이동한다', async () => {
    renderPage()
    const actions = await screen.findAllByRole('button', {
      name: '복용 여부 기록하기',
    })

    fireEvent.click(actions[0])

    expect(await screen.findByText('복용 여부 기록 화면')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toContain(
      '/schedule/occurrences/occ-1',
    )
  })

  it('occurrence가 없으면 빈 상태를 표시한다', async () => {
    const services = makeServices({
      getMedicationDay: vi.fn(async () => makeDay([])),
    })
    renderPage(services)

    expect(
      await screen.findByText('이 날짜에 표시할 복약 기록이 없어요.'),
    ).toBeTruthy()
  })

  it('조회 실패 시 재시도할 수 있다', async () => {
    const getMedicationDay = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(500, 'boom', 'INTERNAL'))
      .mockResolvedValue(makeDay([makeOccurrence('occ-1', '2026-09-15T23:00:00Z')]))
    const services = makeServices({ getMedicationDay })
    renderPage(services)

    expect(
      await screen.findByText('복약 기록을 불러오지 못했어요'),
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: '복용 여부 기록하기' }),
      ).toBeTruthy(),
    )
  })

  it('약 정보 조회가 실패해도 기록 목록은 유지한다', async () => {
    const services = makeServices({
      getOccurrenceMedication: vi.fn(async () => {
        throw new ApiError(404, 'not found', 'MEDICATION_NOT_FOUND')
      }),
    })
    renderPage(services)

    expect(
      (await screen.findAllByText('약 정보를 확인할 수 없어요')).length,
    ).toBe(3)
  })

  it('Back은 MENU-01로 이동하고 Bottom Navigation은 일정이 active다', async () => {
    renderPage()
    await screen.findByRole('heading', { name: '복약 기록', level: 1 })

    const activeNav = document.querySelector(
      '.bottom-nav button[aria-current="page"]',
    )
    // 복약 일정과 같은 복약 관리 영역이므로 '일정'을 active로 둔다.
    expect(activeNav?.textContent).toContain('일정')

    fireEvent.click(screen.getByRole('button', { name: '이전 화면' }))
    expect(await screen.findByText('메뉴 화면')).toBeTruthy()
  })
})
