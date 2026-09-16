import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type {
  MedicationDayResponse,
  MedicationOccurrenceMedicationResponse,
} from '../src/api/medicationSchedules'
import type { PrescriptionResponse } from '../src/api/prescriptions'
import {
  ScheduleOccurrencePage,
  SchedulePage,
  type SchedulePageServices,
} from '../src/pages/SchedulePage'

const occurrenceId = '11111111-1111-4111-8111-111111111111'
const medicationId = '22222222-2222-4222-8222-222222222222'
const prescriptionVersionId = '33333333-3333-4333-8333-333333333333'
const secondMedicationId = '66666666-6666-4666-8666-666666666666'

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

function makePrescription(
  overrides: Partial<PrescriptionResponse['data']> = {},
): PrescriptionResponse {
  return {
    data: {
      prescription_id: '77777777-7777-4777-8777-777777777777',
      prescription_version_id: prescriptionVersionId,
      revision: 1,
      current: true,
      document_id: '88888888-8888-4888-8888-888888888888',
      prescribed_date: '2026-09-14',
      confirmed_at: '2026-09-14T00:00:00Z',
      medications: [
        {
          prescription_version_medication_id: medicationId,
          medication_name: '현재 처방의 혈압약',
          strength_text: '5mg',
          dose_value: 1,
          dose_unit: '정',
          frequency_per_day: 1,
          timing_text: null,
          duration_days: 7,
          display_order: 0,
        },
      ],
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
    getLatestPrescription: vi.fn().mockResolvedValue(makePrescription()),
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
        <Route path="/schedule" element={<><SchedulePage services={services} /><LocationProbe /></>} />
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

function setupItem(
  id = medicationId,
  overrides: Partial<MedicationDayResponse['data']['schedule_items'][number]> = {},
) {
  return {
    prescription_version_medication_id: id,
    schedule_item_status: 'SETUP_REQUIRED' as const,
    schedule_id: null,
    revision: null,
    setup_reason: 'MISSING_START_DATE' as const,
    ...overrides,
  }
}

function fillScheduleEditor(
  medicationName = '현재 처방의 혈압약',
  startDate = '2026-09-14',
  endDate = '2026-09-20',
  times = ['08:30'],
) {
  fireEvent.change(screen.getByLabelText(`${medicationName} 복용 시작일`), {
    target: { value: startDate },
  })
  fireEvent.change(screen.getByLabelText(`${medicationName} 복용 종료일`), {
    target: { value: endDate },
  })
  times.forEach((time, index) => {
    fireEvent.change(screen.getByLabelText(`${medicationName} ${index + 1}번째 복용 시간`), {
      target: { value: time },
    })
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
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

    fireEvent.click(screen.getByRole('button', { name: '복용 여부 기록하기' }))
    expect(screen.getByTestId('location').textContent).toBe(
      `/schedule/occurrences/${occurrenceId}?date=2026-09-14`,
    )
  })

  it('READY 기본 화면을 오늘의 복약 Source 위계로 표시한다', async () => {
    renderSchedule(makeServices())

    expect(await screen.findByRole('heading', { name: '복약 일정' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '오늘의 복약' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '복용 여부 기록하기' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '복약 일정 설정·수정' })).toBeTruthy()
    expect(screen.queryByText('선택한 날짜의 복약')).toBeNull()
    expect(screen.queryByText('확인하지 못한 복약 기록')).toBeNull()
  })

  it('READY인 선택 날짜에 occurrence가 없으면 오늘의 복약 빈 상태를 유지한다', async () => {
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({ occurrences: [] })),
    })
    renderSchedule(services)

    expect(await screen.findByRole('heading', { name: '오늘의 복약' })).toBeTruthy()
    expect(screen.getByText('이 날짜에 표시할 복약 일정이 없어요.')).toBeTruthy()
  })

  it('취소 이력은 접힌 영역으로 분리하고 현재 복약만 카드로 표시한다', async () => {
    const original = makeDay().data.occurrences[0]
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({ occurrences: [
        { ...original, occurrence_id: 'cancelled-1', status: 'CANCELLED', scheduled_at: '2026-09-14T09:15:00Z' },
        { ...original, occurrence_id: 'cancelled-2', status: 'CANCELLED', scheduled_at: '2026-09-14T09:17:00Z' },
        { ...original, scheduled_at: '2026-09-14T10:00:00Z' },
      ] })),
    })
    renderSchedule(services)
    const summary = await screen.findByText('취소된 일정 2건 보기')
    const details = summary.closest('details')!
    expect(details.open).toBe(false)
    expect(document.querySelectorAll('.schedule-occurrence-card')).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: '복용 여부 기록하기' })).toHaveLength(1)
    fireEvent.click(summary)
    expect(details.open).toBe(true)
    expect(details.querySelectorAll('li')).toHaveLength(2)
    expect(details.textContent).toContain('18:15')
    expect(details.textContent).toContain('18:17')
    expect(details.querySelector('button')).toBeNull()
    expect(services.putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('취소 항목만 있는 날에는 빈 복약 상태와 취소 이력을 표시한다', async () => {
    renderSchedule(makeServices({ getMedicationDay: vi.fn().mockResolvedValue(makeDay({
      schedule_status: 'INACTIVE',
      occurrences: [{ ...makeDay().data.occurrences[0], status: 'CANCELLED' }],
    })) }))
    expect(await screen.findByText('이 날짜에 표시할 복약 일정이 없어요.')).toBeTruthy()
    expect(screen.getByText('취소된 일정 1건 보기').closest('details')!.open).toBe(false)
    expect(document.querySelectorAll('.schedule-occurrence-card')).toHaveLength(0)
  })

  it('같은 약의 가까운 비취소 일정과 완료 기록은 합치거나 숨기지 않는다', async () => {
    const original = makeDay().data.occurrences[0]
    renderSchedule(makeServices({ getMedicationDay: vi.fn().mockResolvedValue(makeDay({ occurrences: [
      original,
      { ...original, occurrence_id: 'second', scheduled_at: '2026-09-14T00:02:00Z' },
      { ...original, occurrence_id: 'completed', status: 'CLOSED', checkin: {
        checkin_id: 'checkin', occurrence_id: 'completed', status: 'TAKEN', taken_at: original.scheduled_at, revision: 1, corrected: false,
      } },
    ] })) }))
    await screen.findByRole('button', { name: '복약 기록 수정하기' })
    expect(document.querySelectorAll('.schedule-occurrence-card')).toHaveLength(3)
    expect(screen.getAllByRole('button', { name: '복용 여부 기록하기' })).toHaveLength(2)
    expect(document.querySelector('details')).toBeNull()
  })

  it('일정 설정 진입 시 고정 시각 설정 Source만 표시한다', async () => {
    renderSchedule(makeServices())
    fireEvent.click(await screen.findByRole('button', { name: '복약 일정 설정·수정' }))

    expect(screen.getByText('복약 일정 설정')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '복용할 날짜와 시간을 확인해 주세요' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '오늘의 복약' })).toBeNull()
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

  it.each(['PARTIAL', 'INACTIVE'] as const)(
    '%s 상태에서도 Backend가 보존한 occurrence와 Check-in CTA를 숨기지 않는다',
    async (status) => {
      const services = makeServices({
        getMedicationDay: vi.fn().mockResolvedValue(makeDay({ schedule_status: status })),
      })
      renderSchedule(services)

      expect(await screen.findByRole('heading', { name: '오늘의 복약' })).toBeTruthy()
      expect(screen.getByRole('button', { name: '복용 여부 기록하기' })).toBeTruthy()
    },
  )

  it('현재 처방의 모든 약 카드를 동시에 표시하고 단일 CTA로 약별 PUT을 순차 호출한다', async () => {
    const items = [setupItem(), setupItem(secondMedicationId)]
    const putOrder: string[] = []
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: items,
        occurrences: [],
      })),
      getLatestPrescription: vi.fn().mockResolvedValue(makePrescription({
        medications: [
          makePrescription().data.medications[0],
          {
            ...makePrescription().data.medications[0],
            prescription_version_medication_id: secondMedicationId,
            medication_name: '두 번째 혈당약',
            frequency_per_day: 2,
            display_order: 1,
          },
        ],
      })),
      putMedicationSchedule: vi.fn(async (id) => {
        putOrder.push(id)
        return { data: {} } as never
      }),
      createScheduleIdempotencyKey: vi.fn()
        .mockReturnValueOnce('schedule:first-key')
        .mockReturnValueOnce('schedule:second-key'),
    })
    renderSchedule(services)

    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    expect(screen.getByRole('heading', { name: '현재 처방의 혈압약' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '두 번째 혈당약' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '복약 일정 저장하기' })).toBeTruthy()
    expect(services.getOccurrenceMedication).not.toHaveBeenCalled()

    fillScheduleEditor()
    fillScheduleEditor('두 번째 혈당약', '2026-09-15', '2026-09-21', ['09:00', '21:00'])
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(services.putMedicationSchedule).toHaveBeenCalledTimes(2))
    expect(putOrder).toEqual([medicationId, secondMedicationId])
    expect(services.putMedicationSchedule).toHaveBeenNthCalledWith(
      1,
      medicationId,
      expect.objectContaining({ localTimes: ['08:30'], expectedRevision: 0 }),
      'schedule:first-key',
    )
    expect(services.putMedicationSchedule).toHaveBeenNthCalledWith(
      2,
      secondMedicationId,
      expect.objectContaining({ localTimes: ['09:00', '21:00'], expectedRevision: 0 }),
      'schedule:second-key',
    )
  })

  it('순차 저장 중에는 후속 약 입력을 잠가 제출 snapshot과 화면값을 일치시킨다', async () => {
    const firstPut = deferred<unknown>()
    const put = vi.fn()
      .mockImplementationOnce(() => firstPut.promise)
      .mockResolvedValueOnce({ data: {} })
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem(), setupItem(secondMedicationId)],
        occurrences: [],
      })),
      getLatestPrescription: vi.fn().mockResolvedValue(makePrescription({
        medications: [
          makePrescription().data.medications[0],
          {
            ...makePrescription().data.medications[0],
            prescription_version_medication_id: secondMedicationId,
            medication_name: '대기 중인 위장약',
            display_order: 1,
          },
        ],
      })),
      putMedicationSchedule: put,
    })
    renderSchedule(services)
    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fillScheduleEditor()
    fillScheduleEditor('대기 중인 위장약', '2026-09-15', '2026-09-21', ['20:30'])
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(1))
    expect(screen.getByLabelText('대기 중인 위장약 복용 시작일').hasAttribute('disabled')).toBe(true)
    expect(screen.getByLabelText('대기 중인 위장약 1번째 복용 시간').hasAttribute('disabled')).toBe(true)
    firstPut.resolve({})

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[1]?.[1]).toEqual(expect.objectContaining({
      startLocalDate: '2026-09-15',
      localTimes: ['20:30'],
    }))
  })

  it('1·2·3회 처방은 약별 고정 개수의 시간 입력만 표시한다', async () => {
    const thirdMedicationId = '99999999-9999-4999-8999-999999999999'
    const medications = [
      { ...makePrescription().data.medications[0], frequency_per_day: 1 },
      {
        ...makePrescription().data.medications[0],
        prescription_version_medication_id: secondMedicationId,
        medication_name: '하루 두 번 약',
        frequency_per_day: 2,
        display_order: 1,
      },
      {
        ...makePrescription().data.medications[0],
        prescription_version_medication_id: thirdMedicationId,
        medication_name: '하루 세 번 약',
        frequency_per_day: 3,
        display_order: 2,
      },
    ]
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem(), setupItem(secondMedicationId), setupItem(thirdMedicationId)],
        occurrences: [],
      })),
      getLatestPrescription: vi.fn().mockResolvedValue(makePrescription({ medications })),
    })
    renderSchedule(services)
    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))

    expect(screen.getAllByLabelText(/번째 복용 시간$/)).toHaveLength(6)
    expect(screen.queryByRole('button', { name: /복용 시간 (추가|삭제)/ })).toBeNull()
  })

  it('PARTIAL의 미설정 약도 현재 처방 identity로 표시한다', async () => {
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'PARTIAL',
        schedule_items: [
          makeDay().data.schedule_items[0],
          setupItem(secondMedicationId),
        ],
        occurrences: [],
      })),
      getLatestPrescription: vi.fn().mockResolvedValue(makePrescription({
        medications: [
          makePrescription().data.medications[0],
          {
            ...makePrescription().data.medications[0],
            prescription_version_medication_id: secondMedicationId,
            medication_name: '미설정 위장약',
            strength_text: '20mg',
            display_order: 1,
          },
        ],
      })),
    })
    renderSchedule(services)

    fireEvent.click(await screen.findByRole('button', { name: '이어서 설정하기' }))
    expect(screen.getByRole('heading', { name: '미설정 위장약' })).toBeTruthy()
  })

  it.each([
    ['현재가 아닌 처방', makePrescription({ current: false })],
    ['이전 version의 schedule item', makePrescription({ medications: [] })],
  ])('%s으로 identity를 확정할 수 없으면 임의 label과 저장 CTA를 막는다', async (_case, prescription) => {
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem()],
        occurrences: [],
      })),
      getLatestPrescription: vi.fn().mockResolvedValue(prescription),
    })
    renderSchedule(services)

    expect((await screen.findByRole('alert')).textContent).toContain(
      '약 정보를 확인할 수 없어 일정을 설정할 수 없습니다.',
    )
    expect(screen.queryByRole('button', { name: '일정 설정하기' })).toBeNull()
    expect(screen.queryByText(/처방약 1/)).toBeNull()
    expect(services.putMedicationSchedule).not.toHaveBeenCalled()
  })

  it('현재 처방 identity 조회 실패 시 안전 안내를 표시하고 저장을 막는다', async () => {
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem()],
        occurrences: [],
      })),
      getLatestPrescription: vi.fn().mockRejectedValue(new TypeError('Failed to fetch')),
    })
    renderSchedule(services)

    expect((await screen.findByRole('alert')).textContent).toContain('처방 정보를 다시 확인해 주세요.')
    expect(screen.queryByRole('button', { name: '일정 설정하기' })).toBeNull()
    expect(services.putMedicationSchedule).not.toHaveBeenCalled()
  })

  it('일부 카드가 미입력이면 어떤 약도 저장하지 않고 해당 카드 오류를 유지한다', async () => {
    const items = [setupItem(), setupItem(secondMedicationId)]
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: items,
        occurrences: [],
      })),
      getLatestPrescription: vi.fn().mockResolvedValue(makePrescription({
        medications: [
          makePrescription().data.medications[0],
          {
            ...makePrescription().data.medications[0],
            prescription_version_medication_id: secondMedicationId,
            medication_name: '미입력 위장약',
            display_order: 1,
          },
        ],
      })),
    })
    renderSchedule(services)

    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fillScheduleEditor()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    expect(screen.getByText('입력하지 않았거나 확인이 필요한 항목이 있어요.')).toBeTruthy()
    expect(screen.getAllByRole('alert').some((alert) => alert.textContent?.includes('복용 시작일'))).toBe(true)
    const invalidStartDate = screen.getByLabelText('미입력 위장약 복용 시작일')
    expect(invalidStartDate.getAttribute('aria-invalid')).toBe('true')
    expect(invalidStartDate.getAttribute('aria-describedby')).toBe(`schedule-error-${secondMedicationId}`)
    expect(invalidStartDate).toBe(document.activeElement)
    expect(services.putMedicationSchedule).not.toHaveBeenCalled()
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
    fillScheduleEditor()
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

  it('일정 PUT 응답 유실 후 동일 입력을 재시도하면 key·body·revision을 재사용한다', async () => {
    const put = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({ data: {} })
    const createKey = vi.fn()
      .mockReturnValueOnce('schedule:first-key')
      .mockReturnValueOnce('schedule:unexpected-key')
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem()],
        occurrences: [],
      })),
      putMedicationSchedule: put,
      createScheduleIdempotencyKey: createKey,
    })
    renderSchedule(services)

    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fillScheduleEditor()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))
    expect(await screen.findByText(/연결을 확인한 뒤 다시 시도/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[1]).toEqual(put.mock.calls[0])
    expect(createKey).toHaveBeenCalledTimes(1)
  })

  it('일정 PUT 실패 후 사용자가 시간을 바꾸면 새 논리적 시도 key를 발급한다', async () => {
    const put = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({ data: {} })
    const createKey = vi.fn()
      .mockReturnValueOnce('schedule:first-key')
      .mockReturnValueOnce('schedule:changed-key')
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem()],
        occurrences: [],
      })),
      putMedicationSchedule: put,
      createScheduleIdempotencyKey: createKey,
    })
    renderSchedule(services)

    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fillScheduleEditor()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))
    await screen.findByText(/연결을 확인한 뒤 다시 시도/)
    fireEvent.change(screen.getByLabelText('현재 처방의 혈압약 1번째 복용 시간'), {
      target: { value: '09:30' },
    })
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[0]?.[2]).toBe('schedule:first-key')
    expect(put.mock.calls[1]?.[2]).toBe('schedule:changed-key')
    expect(put.mock.calls[0]?.[1]).toMatchObject({ localTimes: ['08:30'], expectedRevision: 0 })
    expect(put.mock.calls[1]?.[1]).toMatchObject({ localTimes: ['09:30'], expectedRevision: 0 })
  })

  it('일정 PUT revision conflict 재조회 후에는 최신 revision과 새 key로 시작한다', async () => {
    const latestItem = setupItem(medicationId, { revision: 4 })
    const reloadResponse = deferred<MedicationDayResponse>()
    const getDay = vi.fn()
      .mockResolvedValueOnce(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem()],
        occurrences: [],
      }))
      .mockReturnValueOnce(reloadResponse.promise)
      .mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [latestItem],
        occurrences: [],
      }))
    const put = vi.fn()
      .mockRejectedValueOnce(new ApiError(409, 'raw', 'SCHEDULE_REVISION_CONFLICT'))
      .mockResolvedValue({ data: {} })
    const createKey = vi.fn()
      .mockReturnValueOnce('schedule:conflict-key')
      .mockReturnValueOnce('schedule:reloaded-key')
    const services = makeServices({
      getMedicationDay: getDay,
      putMedicationSchedule: put,
      createScheduleIdempotencyKey: createKey,
    })
    renderSchedule(services)

    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fillScheduleEditor()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))
    await waitFor(() => expect(getDay).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('button', { name: '저장 중…' }).hasAttribute('disabled')).toBe(true)
    reloadResponse.resolve(makeDay({
      schedule_status: 'SETUP_REQUIRED',
      schedule_items: [latestItem],
      occurrences: [],
    }))
    await waitFor(() => expect(
      screen.getByRole('button', { name: '복약 일정 저장하기' }).hasAttribute('disabled'),
    ).toBe(false))
    fillScheduleEditor()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[0]?.[1]).toMatchObject({ expectedRevision: 0 })
    expect(put.mock.calls[0]?.[2]).toBe('schedule:conflict-key')
    expect(put.mock.calls[1]?.[1]).toMatchObject({ expectedRevision: 4 })
    expect(put.mock.calls[1]?.[2]).toBe('schedule:reloaded-key')
  })

  it('부분 실패 뒤 재시도하면 성공한 약은 건너뛰고 실패 약만 같은 key로 다시 저장한다', async () => {
    const items = [setupItem(), setupItem(secondMedicationId)]
    const put = vi.fn()
      .mockResolvedValueOnce({ data: {} })
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ data: {} })
    const createKey = vi.fn()
      .mockReturnValueOnce('schedule:first-key')
      .mockReturnValueOnce('schedule:second-key')
      .mockReturnValueOnce('schedule:unexpected-key')
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: items,
        occurrences: [],
      })),
      getLatestPrescription: vi.fn().mockResolvedValue(makePrescription({
        medications: [
          makePrescription().data.medications[0],
          {
            ...makePrescription().data.medications[0],
            prescription_version_medication_id: secondMedicationId,
            medication_name: '실패한 위장약',
            display_order: 1,
          },
        ],
      })),
      putMedicationSchedule: put,
      createScheduleIdempotencyKey: createKey,
    })
    renderSchedule(services)
    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fillScheduleEditor()
    fillScheduleEditor('실패한 위장약')
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    expect(await screen.findByText('일부 약만 저장됐어요. 실패한 약의 입력값을 확인하고 다시 시도해 주세요.')).toBeTruthy()
    expect(screen.getByText('저장 완료')).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(3))
    expect(put.mock.calls[2]?.[0]).toBe(secondMedicationId)
    expect(put.mock.calls[2]?.[2]).toBe('schedule:second-key')
    expect(createKey).toHaveBeenCalledTimes(2)
  })

  it('계속 복용은 end_local_date 없이 기존 약별 계약으로 저장한다', async () => {
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem()],
        occurrences: [],
      })),
    })
    renderSchedule(services)
    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fireEvent.click(screen.getByRole('radio', { name: '계속 복용' }))
    fireEvent.change(screen.getByLabelText('현재 처방의 혈압약 복용 시작일'), {
      target: { value: '2026-09-14' },
    })
    fireEvent.change(screen.getByLabelText('현재 처방의 혈압약 1번째 복용 시간'), {
      target: { value: '08:30' },
    })
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(services.putMedicationSchedule).toHaveBeenCalledTimes(1))
    expect(services.putMedicationSchedule).toHaveBeenCalledWith(
      medicationId,
      {
        startLocalDate: '2026-09-14',
        endMode: 'OPEN_ENDED',
        localTimes: ['08:30'],
        expectedRevision: 0,
      },
      'schedule:test-key',
    )
  })

  it('약별 저장 401은 후속 호출 없이 중단하고 로그인 확인 상태를 보존한다', async () => {
    const put = vi.fn().mockRejectedValue(
      new ApiError(401, 'raw', 'UNAUTHORIZED'),
    )
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem(), setupItem(secondMedicationId)],
        occurrences: [],
      })),
      getLatestPrescription: vi.fn().mockResolvedValue(makePrescription({
        medications: [
          makePrescription().data.medications[0],
          {
            ...makePrescription().data.medications[0],
            prescription_version_medication_id: secondMedicationId,
            medication_name: '후속 호출 금지 약',
            display_order: 1,
          },
        ],
      })),
      putMedicationSchedule: put,
    })
    renderSchedule(services)
    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fillScheduleEditor()
    fillScheduleEditor('후속 호출 금지 약')
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    expect(await screen.findByText('로그인 정보를 다시 확인해 주세요.')).toBeTruthy()
    expect(screen.getByText('저장을 중단했어요. 로그인 정보를 확인한 뒤 다시 시도해 주세요.')).toBeTruthy()
    expect(put).toHaveBeenCalledTimes(1)
    expect(services.getMedicationDay).toHaveBeenCalledTimes(1)
  })

  it('약별 저장 404는 최신 처방을 재조회하고 자동 재시도하지 않는다', async () => {
    const getDay = vi.fn().mockResolvedValue(makeDay({
      schedule_status: 'SETUP_REQUIRED',
      schedule_items: [setupItem()],
      occurrences: [],
    }))
    const put = vi.fn().mockRejectedValue(
      new ApiError(404, 'raw', 'PRESCRIPTION_MEDICATION_NOT_FOUND'),
    )
    const services = makeServices({
      getMedicationDay: getDay,
      putMedicationSchedule: put,
    })
    renderSchedule(services)
    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fillScheduleEditor()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(getDay).toHaveBeenCalledTimes(2))
    expect(put).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/현재 처방 내용이 변경됐어요/)).toBeTruthy()
  })

  it('약별 저장 5xx는 입력값과 동일 logical attempt를 유지한다', async () => {
    const put = vi.fn()
      .mockRejectedValueOnce(new ApiError(503, 'raw', 'SERVICE_UNAVAILABLE'))
      .mockResolvedValueOnce({ data: {} })
    const createKey = vi.fn()
      .mockReturnValueOnce('schedule:server-key')
      .mockReturnValueOnce('schedule:unexpected-key')
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_status: 'SETUP_REQUIRED',
        schedule_items: [setupItem()],
        occurrences: [],
      })),
      putMedicationSchedule: put,
      createScheduleIdempotencyKey: createKey,
    })
    renderSchedule(services)
    fireEvent.click(await screen.findByRole('button', { name: '일정 설정하기' }))
    fillScheduleEditor()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))
    expect(await screen.findByText('일정을 저장하지 못했어요. 입력값을 유지한 채 다시 시도해 주세요.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[1]).toEqual(put.mock.calls[0])
    expect(createKey).toHaveBeenCalledTimes(1)
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
  it('최초 조회 실패 후 다시 시도하면 같은 occurrence 상세를 복구하며 Check-in을 저장하지 않는다', async () => {
    const getDay = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue(makeDay())
    const services = makeServices({ getMedicationDay: getDay })
    renderOccurrence(services)

    expect(await screen.findByRole('heading', { name: '인터넷 연결을 확인해 주세요' })).toBeTruthy()
    expect(screen.getByRole('alert').getAttribute('aria-live')).toBe('assertive')
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))

    expect(await screen.findByRole('heading', { name: '당시 처방의 혈압약' })).toBeTruthy()
    expect(getDay).toHaveBeenCalledTimes(2)
    expect(services.putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('상세 조회 재시도도 실패하면 오류 상태와 다시 시도를 유지하며 Check-in을 저장하지 않는다', async () => {
    const getDay = vi.fn().mockRejectedValue(new ApiError(
      503,
      'raw backend detail',
      'SERVICE_UNAVAILABLE',
    ))
    const services = makeServices({ getMedicationDay: getDay })
    renderOccurrence(services)

    expect(await screen.findByRole('heading', { name: '일정을 잠시 불러오지 못했어요' })).toBeTruthy()
    expect(screen.getByRole('alert').getAttribute('aria-live')).toBe('assertive')
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))

    await waitFor(() => expect(getDay).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('heading', { name: '일정을 잠시 불러오지 못했어요' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeTruthy()
    expect(screen.queryByText('raw backend detail')).toBeNull()
    expect(services.putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('path occurrenceId와 query date를 day/detail ID와 검증한 뒤에만 표시한다', async () => {
    const services = makeServices()
    renderOccurrence(services)
    expect(await screen.findByRole('heading', { name: '당시 처방의 혈압약' })).toBeTruthy()
    expect(screen.getByText('9월 14일 월요일')).toBeTruthy()
    expect(services.getLatestPrescription).not.toHaveBeenCalled()
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

  it('Check-in 응답 유실 후 동일 선택을 재시도하면 key·body·revision을 재사용한다', async () => {
    const put = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({
        data: {
          checkin_id: '55555555-5555-4555-8555-555555555555',
          occurrence_id: occurrenceId,
          status: 'TAKEN',
          taken_at: null,
          revision: 1,
          corrected: false,
        },
      })
    const createKey = vi.fn()
      .mockReturnValueOnce('checkin:first-key')
      .mockReturnValueOnce('checkin:unexpected-key')
    const services = makeServices({
      putMedicationCheckin: put,
      createCheckinIdempotencyKey: createKey,
    })
    renderOccurrence(services)
    await screen.findByRole('heading', { name: '당시 처방의 혈압약' })

    fireEvent.click(screen.getByRole('button', { name: '복용했어요' }))
    expect(await screen.findByText(/연결을 확인한 뒤 다시 시도/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '복용했어요' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[1]).toEqual(put.mock.calls[0])
    expect(createKey).toHaveBeenCalledTimes(1)
  })

  it('Check-in 실패 후 사용자가 상태 선택을 바꾸면 새 key를 발급한다', async () => {
    const put = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({
        data: {
          checkin_id: '55555555-5555-4555-8555-555555555555',
          occurrence_id: occurrenceId,
          status: 'NOT_TAKEN',
          taken_at: null,
          revision: 1,
          corrected: false,
        },
      })
    const createKey = vi.fn()
      .mockReturnValueOnce('checkin:taken-key')
      .mockReturnValueOnce('checkin:not-taken-key')
    const services = makeServices({
      putMedicationCheckin: put,
      createCheckinIdempotencyKey: createKey,
    })
    renderOccurrence(services)
    await screen.findByRole('heading', { name: '당시 처방의 혈압약' })

    fireEvent.click(screen.getByRole('button', { name: '복용했어요' }))
    await screen.findByText(/연결을 확인한 뒤 다시 시도/)
    fireEvent.click(screen.getByRole('button', { name: '복용하지 않았어요' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[0]).toEqual([
      occurrenceId,
      { status: 'TAKEN', expectedRevision: 0 },
      'checkin:taken-key',
    ])
    expect(put.mock.calls[1]).toEqual([
      occurrenceId,
      { status: 'NOT_TAKEN', expectedRevision: 0 },
      'checkin:not-taken-key',
    ])
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

  it('Check-in revision conflict 재조회 후에는 최신 revision과 새 key로 시작한다', async () => {
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
    const reloadResponse = deferred<MedicationDayResponse>()
    const getDay = vi.fn()
      .mockResolvedValueOnce(makeDay())
      .mockReturnValueOnce(reloadResponse.promise)
      .mockResolvedValue(latest)
    const put = vi.fn()
      .mockRejectedValueOnce(new ApiError(409, 'raw backend detail', 'CHECKIN_REVISION_CONFLICT'))
      .mockResolvedValue({
        data: {
          ...latest.data.occurrences[0].checkin!,
          status: 'TAKEN',
          revision: 3,
        },
      })
    const createKey = vi.fn()
      .mockReturnValueOnce('checkin:conflict-key')
      .mockReturnValueOnce('checkin:reloaded-key')
    const services = makeServices({
      getMedicationDay: getDay,
      putMedicationCheckin: put,
      createCheckinIdempotencyKey: createKey,
    })
    renderOccurrence(services)
    await screen.findByRole('heading', { name: '당시 처방의 혈압약' })

    fireEvent.click(screen.getByRole('button', { name: '복용했어요' }))
    await waitFor(() => expect(getDay).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('status').textContent).toContain('복약 기록을 불러오는 중')
    expect(screen.queryByRole('button', { name: '복용했어요' })).toBeNull()
    reloadResponse.resolve(latest)
    expect(await screen.findByText(/최신 상태를 확인한 뒤 다시 선택/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '복용했어요' }))

    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[0]).toEqual([
      occurrenceId,
      { status: 'TAKEN', expectedRevision: 0 },
      'checkin:conflict-key',
    ])
    expect(put.mock.calls[1]).toEqual([
      occurrenceId,
      { status: 'TAKEN', expectedRevision: 2 },
      'checkin:reloaded-key',
    ])
  })
})


describe('Track C reminder target handoff', () => {
  it('preserves the target while normalizing date and opens only that medication editor', async () => {
    const services = makeServices({
      getMedicationDay: vi.fn().mockResolvedValue(makeDay({
        schedule_items: [setupItem(), setupItem(secondMedicationId)],
      })),
      getLatestPrescription: vi.fn().mockResolvedValue(makePrescription({
        medications: [makePrescription().data.medications[0], {
          ...makePrescription().data.medications[0],
          prescription_version_medication_id: secondMedicationId,
          medication_name: '두 번째 혈당약',
          display_order: 1,
        }],
      })),
    })
    renderSchedule(services, `/schedule?support_medication=${medicationId}`)
    fireEvent.click(await screen.findByRole('button', { name: '이 약의 일정 확인·설정' }))
    expect(await screen.findByRole('heading', { name: '현재 처방의 혈압약' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '두 번째 혈당약' })).toBeNull()
    expect(screen.getByLabelText('현재 처방의 혈압약 복용 시작일')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toContain(`support_medication=${medicationId}`)
    expect(services.putMedicationSchedule).not.toHaveBeenCalled()
    fillScheduleEditor()
    fireEvent.click(screen.getByRole('button', { name: '복약 일정 저장하기' }))
    await waitFor(() => expect(services.putMedicationSchedule).toHaveBeenCalledTimes(1))
    expect(services.putMedicationSchedule).toHaveBeenCalledWith(
      medicationId,
      expect.objectContaining({ localTimes: ['08:30'], expectedRevision: 0 }),
      'schedule:test-key',
    )
    fireEvent.click(screen.getByRole('button', { name: '이전 화면' }))
    fireEvent.click(await screen.findByRole('button', { name: '복약 일정 설정·수정' }))
    expect(screen.getByRole('heading', { name: '두 번째 혈당약' })).toBeTruthy()
  })
  it('does not substitute another medication if the saved target is absent', async () => {
    const services = makeServices()
    renderSchedule(services, `/schedule?support_medication=${secondMedicationId}`)
    await screen.findByText(/이 계획에 연결된 약의 일정을 확인할 수 없어요/)
    expect(screen.queryByRole('button', { name: '이 약의 일정 확인·설정' })).toBeNull()
    expect(services.putMedicationSchedule).not.toHaveBeenCalled()
  })
})
