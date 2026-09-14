import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type {
  MedicationDayResponse,
  MedicationOccurrenceMedicationResponse,
} from '../src/api/medicationSchedules'
import {
  ScheduleOccurrencePage,
  SchedulePage,
  type SchedulePageServices,
} from '../src/pages/SchedulePage'

const occurrenceId = '11111111-1111-4111-8111-111111111111'
const medicationId = '22222222-2222-4222-8222-222222222222'
const prescriptionVersionId = '33333333-3333-4333-8333-333333333333'

function makeDay(
  overrides: Partial<MedicationDayResponse['data']> = {},
): MedicationDayResponse {
  return {
    data: {
      schedule_status: 'READY',
      schedule_items: [
        {
          prescription_version_medication_id: medicationId,
          schedule_item_status: 'READY',
          schedule_id: '44444444-4444-4444-8444-444444444444',
          revision: 3,
          setup_reason: null,
        },
      ],
      occurrences: [
        {
          occurrence_id: occurrenceId,
          prescription_version_id: prescriptionVersionId,
          prescription_version_medication_id: medicationId,
          scheduled_local_date: '2026-09-14',
          scheduled_at: '2026-09-14T00:00:00Z',
          confirmation_deadline_at: '2026-09-14T05:00:00Z',
          status: 'PENDING',
          checkin: null,
        },
      ],
      ...overrides,
    },
  }
}

function makeMedication(
  overrides: Partial<MedicationOccurrenceMedicationResponse['data']> = {},
): MedicationOccurrenceMedicationResponse {
  return {
    data: {
      occurrence_id: occurrenceId,
      prescription_version_id: prescriptionVersionId,
      prescription_version_medication_id: medicationId,
      medication_name: '당시 처방의 혈압약',
      strength_text: '5mg',
      dose_value: 1,
      dose_unit: '정',
      ...overrides,
    },
  }
}

function makeServices(
  overrides: Partial<SchedulePageServices> = {},
): SchedulePageServices {
  return {
    getMedicationDay: vi.fn().mockResolvedValue(makeDay()),
    getOccurrenceMedication: vi.fn().mockResolvedValue(makeMedication()),
    putMedicationSchedule: vi.fn().mockResolvedValue({ data: {} }),
    cancelMedicationSchedule: vi.fn().mockResolvedValue({ data: {} }),
    putMedicationCheckin: vi.fn().mockResolvedValue({
      data: {
        checkin_id: '55555555-5555-4555-8555-555555555555',
        occurrence_id: occurrenceId,
        status: 'TAKEN',
        taken_at: null,
        revision: 1,
        corrected: false,
      },
    }),
    createScheduleIdempotencyKey: vi.fn(() => 'schedule:test-key'),
    createCheckinIdempotencyKey: vi.fn(() => 'checkin:test-key'),
    ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function renderSchedule(services: SchedulePageServices, entry = '/schedule?date=2026-09-14') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/schedule" element={<SchedulePage services={services} />} />
        <Route
          path="/schedule/occurrences/:occurrenceId"
          element={<><div>복약 기록 route</div><LocationProbe /></>}
        />
      </Routes>
    </MemoryRouter>,
  )
}

function renderOccurrence(
  services: SchedulePageServices,
  entry = `/schedule/occurrences/${occurrenceId}?date=2026-09-14`,
) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/schedule" element={<div>일정 route</div>} />
        <Route
          path="/schedule/occurrences/:occurrenceId"
          element={<ScheduleOccurrencePage services={services} />}
        />
        <Route path="/login" element={<div>로그인 route</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('production 복약 일정', () => {
  it('선택한 KST local date를 조회하고 occurrence 원본 약 정보를 표시한다', async () => {
    const services = makeServices()
    renderSchedule(services)

    expect(await screen.findByText('당시 처방의 혈압약 · 5mg · 1정')).toBeTruthy()
    expect(services.getMedicationDay).toHaveBeenCalledWith('2026-09-14', expect.any(AbortSignal))
    expect(services.getOccurrenceMedication).toHaveBeenCalledWith(occurrenceId, expect.any(AbortSignal))

    fireEvent.click(screen.getByRole('button', { name: /09:00 당시 처방의 혈압약/ }))
    expect(screen.getByTestId('location').textContent).toBe(
      `/schedule/occurrences/${occurrenceId}?date=2026-09-14`,
    )
  })

  it.each([
    ['SETUP_REQUIRED', '일정 설정이 필요해요'],
    ['PARTIAL', '일부 약의 시간이 비어 있어요'],
    ['INACTIVE', '현재 사용 중인 일정이 없어요'],
    ['NO_ACTIVE_PRESCRIPTION', '활성 처방이 필요해요'],
  ] as const)('Backend schedule_status %s를 해당 상태 UI로 표시한다', async (status, title) => {
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: status,
        schedule_items: status === 'NO_ACTIVE_PRESCRIPTION' ? [] : makeDay().data.schedule_items,
        occurrences: [],
      })),
    })
    renderSchedule(services)
    expect(await screen.findByRole('heading', { name: title })).toBeTruthy()
  })

  it('최초 일정은 revision 0과 직접 입력한 DATE 값으로만 저장한다', async () => {
    const setupItem = {
      ...makeDay().data.schedule_items[0],
      schedule_item_status: 'SETUP_REQUIRED' as const,
      schedule_id: null,
      revision: null,
      setup_reason: 'MISSING_START_DATE' as const,
    }
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem],
        occurrences: [],
      })),
    })
    renderSchedule(services)

    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    const dates = screen.getAllByDisplayValue('')
      .filter((element) => element.getAttribute('type') === 'date')
    fireEvent.change(dates[0], { target: { value: '2026-09-14' } })
    fireEvent.change(dates[1], { target: { value: '2026-09-20' } })
    fireEvent.change(screen.getByLabelText('1번째 복용 시간'), { target: { value: '08:30' } })
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(services.putMedicationSchedule).toHaveBeenCalledTimes(1))
    expect(services.putMedicationSchedule).toHaveBeenCalledWith(
      medicationId,
      {
        startLocalDate: '2026-09-14',
        endMode: 'DATE',
        endLocalDate: '2026-09-20',
        localTimes: ['08:30'],
        expectedRevision: 0,
      },
      'schedule:test-key',
    )
  })

  it('활성 일정 중지는 2단계 확인 후 현재 revision으로 PATCH한다', async () => {
    const services = makeServices()
    renderSchedule(services)
    await screen.findByText('당시 처방의 혈압약 · 5mg · 1정')

    fireEvent.click(screen.getByRole('button', { name: /당시 처방의 혈압약\s*설정됨/ }))
    fireEvent.click(screen.getByRole('button', { name: '이 일정 사용 중지' }))
    expect(services.cancelMedicationSchedule).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '사용 중지 확인' }))

    await waitFor(() => expect(services.cancelMedicationSchedule).toHaveBeenCalledTimes(1))
    expect(services.cancelMedicationSchedule).toHaveBeenCalledWith(
      medicationId,
      3,
      'schedule:test-key',
    )
  })

  it.each([
    [new TypeError('Failed to fetch'), '인터넷 연결을 확인해 주세요'],
    [new ApiError(503, 'raw backend detail', 'SERVICE_UNAVAILABLE'), '일정을 잠시 불러오지 못했어요'],
  ])('조회 오류를 raw detail 없이 복구 UI로 표시한다', async (error, title) => {
    const services = makeServices({
      getMedicationDay: vi.fn().mockRejectedValue(error),
    })
    renderSchedule(services)
    expect(await screen.findByRole('heading', { name: title })).toBeTruthy()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeTruthy()
    expect(screen.queryByText('raw backend detail')).toBeNull()
  })
})

describe('production 복약 기록 handoff', () => {
  it('path occurrenceId와 query date를 day/detail ID와 검증한 뒤에만 표시한다', async () => {
    const services = makeServices()
    renderOccurrence(services)
    expect(await screen.findByRole('heading', { name: '당시 처방의 혈압약' })).toBeTruthy()
    expect(screen.getByText('9월 14일 월요일')).toBeTruthy()
  })

  it('occurrence/date가 일치하지 않으면 detail을 요청하지 않고 404를 소유권 구분 없이 표시한다', async () => {
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({ occurrences: [] })),
    })
    renderOccurrence(services)
    expect(await screen.findByRole('heading', { name: '복약 기록을 찾을 수 없어요' })).toBeTruthy()
    expect(services.getOccurrenceMedication).not.toHaveBeenCalled()
  })

  it('checkin이 없으면 TAKEN을 expected revision 0으로 저장하며 열람만으로는 저장하지 않는다', async () => {
    const services = makeServices()
    renderOccurrence(services)
    await screen.findByRole('heading', { name: '당시 처방의 혈압약' })
    expect(services.putMedicationCheckin).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '복용했어요' }))
    await waitFor(() => expect(services.putMedicationCheckin).toHaveBeenCalledTimes(1))
    expect(services.putMedicationCheckin).toHaveBeenCalledWith(
      occurrenceId,
      { status: 'TAKEN', expectedRevision: 0 },
      'checkin:test-key',
    )
  })

  it('기존 Check-in을 NOT_TAKEN으로 정정할 때 실제 revision을 사용한다', async () => {
    const day = makeDay()
    day.data.occurrences[0].status = 'CLOSED'
    day.data.occurrences[0].checkin = {
      checkin_id: '55555555-5555-4555-8555-555555555555',
      occurrence_id: occurrenceId,
      status: 'TAKEN',
      taken_at: null,
      revision: 4,
      corrected: true,
    }
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(day),
      putMedicationCheckin: vi.fn().mockResolvedValue({
        data: {
          ...day.data.occurrences[0].checkin,
          status: 'NOT_TAKEN',
          revision: 5,
        },
      }),
    })
    renderOccurrence(services)
    await screen.findByRole('heading', { name: '당시 처방의 혈압약' })
    fireEvent.click(screen.getByRole('button', { name: '복용하지 않았어요' }))

    await waitFor(() => expect(services.putMedicationCheckin).toHaveBeenCalledTimes(1))
    expect(services.putMedicationCheckin).toHaveBeenCalledWith(
      occurrenceId,
      { status: 'NOT_TAKEN', expectedRevision: 4 },
      'checkin:test-key',
    )
  })

  it('revision conflict에서 최신 occurrence를 재조회하고 이전 선택을 자동 재제출하지 않는다', async () => {
    const latest = makeDay()
    latest.data.occurrences[0].status = 'CLOSED'
    latest.data.occurrences[0].checkin = {
      checkin_id: '55555555-5555-4555-8555-555555555555',
      occurrence_id: occurrenceId,
      status: 'NOT_TAKEN',
      taken_at: null,
      revision: 2,
      corrected: true,
    }
    const services = makeServices({
      getMedicationDay: vi.fn()
        .mockResolvedValueOnce(makeDay())
        .mockResolvedValue(latest),
      putMedicationCheckin: vi.fn().mockRejectedValueOnce(
        new ApiError(409, 'raw backend detail', 'CHECKIN_REVISION_CONFLICT'),
      ),
    })
    renderOccurrence(services)
    await screen.findByRole('heading', { name: '당시 처방의 혈압약' })

    fireEvent.click(screen.getByRole('button', { name: '복용했어요' }))
    expect(await screen.findByText(/latest 상태를 확인한 뒤|\uCD5C신 상태를 확인한 뒤/)).toBeTruthy()
    await waitFor(() => expect(services.getMedicationDay).toHaveBeenCalledTimes(2))
    expect(services.putMedicationCheckin).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('raw backend detail')).toBeNull()
    expect(await screen.findByText('복용하지 않았어요.')).toBeTruthy()
  })
})
