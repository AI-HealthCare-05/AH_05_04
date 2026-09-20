import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type {
  MedicationCheckinSnapshot,
  MedicationDayResponse,
  MedicationOccurrenceData,
  MedicationOccurrenceMedicationResponse,
} from '../src/api/medicationSchedules'
import type { MedicationCheckinResponse } from '../src/api/medicationCheckins'
import {
  CheckinDetailPage,
  CheckinSummaryPage,
  type CheckinPageServices,
} from '../src/pages/CheckinPage'

const DATE = '2026-09-16'
// KST 14:00. fixture는 KST 08:00 / 13:00 / 20:00.
const FIXED_NOW = new Date('2026-09-16T05:00:00Z')

const OCC_A = '11111111-1111-4111-8111-111111111111'
const OCC_B = '22222222-2222-4222-8222-222222222222'
const OCC_MORNING = '33333333-3333-4333-8333-333333333333'
const OCC_EVENING = '44444444-4444-4444-8444-444444444444'

function makeOccurrence(
  occurrenceId: string,
  scheduledAt: string,
  checkin: MedicationCheckinSnapshot | null = null,
): MedicationOccurrenceData {
  return {
    occurrence_id: occurrenceId,
    prescription_version_id: 'pv-1',
    prescription_version_medication_id: `pvm-${occurrenceId}`,
    scheduled_local_date: DATE,
    scheduled_at: scheduledAt,
    confirmation_deadline_at: '2026-09-16T18:00:00Z',
    status: 'PENDING',
    checkin,
  } as MedicationOccurrenceData
}

// KST 13:00 그룹 2건 + 08:00 1건 + 20:00 1건
function defaultOccurrences(): MedicationOccurrenceData[] {
  return [
    makeOccurrence(OCC_MORNING, '2026-09-15T23:00:00Z'),
    makeOccurrence(OCC_A, '2026-09-16T04:00:00Z'),
    makeOccurrence(OCC_B, '2026-09-16T04:00:00Z'),
    makeOccurrence(OCC_EVENING, '2026-09-16T11:00:00Z'),
  ]
}

function makeDay(occurrences: MedicationOccurrenceData[]): MedicationDayResponse {
  return {
    data: {
      schedule_status: 'READY',
      schedule_items: [
        {
          prescription_version_medication_id: `pvm-${OCC_A}`,
          schedule_item_status: 'READY',
          schedule_id: 'schedule-a',
          revision: 1,
          setup_reason: null,
          schedule: {
            schedule_id: 'schedule-a',
            prescription_version_medication_id: `pvm-${OCC_A}`,
            revision: 1,
            status: 'ACTIVE',
            start_local_date: DATE,
            end_mode: 'OPEN_ENDED',
            end_local_date: null,
            local_times: ['08:00', '13:00'],
          },
        },
        {
          prescription_version_medication_id: `pvm-${OCC_B}`,
          schedule_item_status: 'READY',
          schedule_id: 'schedule-b',
          revision: 1,
          setup_reason: null,
          schedule: {
            schedule_id: 'schedule-b',
            prescription_version_medication_id: `pvm-${OCC_B}`,
            revision: 1,
            status: 'ACTIVE',
            start_local_date: DATE,
            end_mode: 'OPEN_ENDED',
            end_local_date: null,
            local_times: ['13:00'],
          },
        },
      ],
      occurrences,
    },
  } as unknown as MedicationDayResponse
}

function makeMedication(id: string): MedicationOccurrenceMedicationResponse {
  return {
    data: {
      occurrence_id: id,
      prescription_version_id: 'pv-1',
      prescription_version_medication_id: `pvm-${id}`,
      medication_name: id === OCC_A ? '메트포르민정' : '암로디핀정',
      strength_text: id === OCC_A ? '500mg' : '5mg',
      dose_value: 1,
      dose_unit: '정',
    },
  } as unknown as MedicationOccurrenceMedicationResponse
}

function makeCheckinResponse(
  occurrenceId: string,
  status: 'TAKEN' | 'NOT_TAKEN',
  revision = 1,
): MedicationCheckinResponse {
  return {
    data: {
      checkin_id: `chk-${occurrenceId}`,
      occurrence_id: occurrenceId,
      status,
      taken_at: null,
      revision,
      corrected: false,
    },
  } as unknown as MedicationCheckinResponse
}

function makeServices(
  overrides: Partial<CheckinPageServices> = {},
): CheckinPageServices {
  let keySeed = 0
  return {
    getMedicationDay: vi.fn(async () => makeDay(defaultOccurrences())),
    getOccurrenceMedication: vi.fn(async (id: string) => makeMedication(id)),
    putMedicationCheckin: vi.fn(async (id: string, input: { status: 'TAKEN' | 'NOT_TAKEN' }) =>
      makeCheckinResponse(id, input.status),
    ),
    createCheckinIdempotencyKey: vi.fn(() => `checkin:key-${(keySeed += 1)}`),
    ...overrides,
  } as CheckinPageServices
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
}

function renderDetail(
  services: CheckinPageServices,
  occurrenceId = OCC_A,
) {
  return render(
    <MemoryRouter initialEntries={[`/schedule/checkin/${occurrenceId}?date=${DATE}`]}>
      <Routes>
        <Route
          path="/schedule/checkin/:occurrenceId"
          element={<><CheckinDetailPage services={services} /><LocationProbe /></>}
        />
        <Route path="/schedule" element={<><div>일정 화면</div><LocationProbe /></>} />
        <Route path="/login" element={<div>로그인 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function renderSummary(services: CheckinPageServices) {
  return render(
    <MemoryRouter initialEntries={[`/schedule/checkin?date=${DATE}`]}>
      <Routes>
        <Route
          path="/schedule/checkin"
          element={<><CheckinSummaryPage services={services} /><LocationProbe /></>}
        />
        <Route
          path="/schedule/checkin/:occurrenceId"
          element={<><div>상세 화면</div><LocationProbe /></>}
        />
        <Route path="/schedule" element={<><div>일정 화면</div><LocationProbe /></>} />
        <Route path="/report" element={<div>리포트 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function selectBoth() {
  const taken = await screen.findAllByRole('button', { name: '복용했어요' })
  fireEvent.click(taken[0])
  const notTaken = screen.getAllByRole('button', { name: '복용하지 않았어요' })
  fireEvent.click(notTaken[1])
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(FIXED_NOW)
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('CHECKIN-01 PB-01 요약', () => {
  it('exact scheduled_at 기준으로 시간 그룹을 회차·시간순으로 표시한다', async () => {
    renderSummary(makeServices())

    expect(await screen.findByRole('heading', { name: '오늘의 복약 체크' })).toBeTruthy()
    const cards = document.querySelectorAll('.checkin-group-card')
    // 08:00 / 13:00 / 20:00 세 그룹
    expect(cards).toHaveLength(3)
    expect(cards[0].textContent).toContain('1회차')
    expect(cards[0].textContent).toContain('08:00')
    expect(cards[1].textContent).toContain('2회차')
    expect(cards[1].textContent).toContain('13:00')
    // 13:00 그룹은 2건, 미기록
    expect(cards[1].textContent).toContain('0/2')
  })

  it('완료 수와 상태를 public checkin 데이터로 계산한다', async () => {
    const occurrences = defaultOccurrences().map((occurrence) =>
      occurrence.occurrence_id === OCC_A
        ? {
            ...occurrence,
            checkin: {
              checkin_id: 'c1',
              occurrence_id: OCC_A,
              status: 'TAKEN',
              taken_at: null,
              revision: 1,
              corrected: false,
            } as MedicationCheckinSnapshot,
          }
        : occurrence,
    )
    renderSummary(makeServices({
      getMedicationDay: vi.fn(async () => makeDay(occurrences)),
    }))

    await screen.findByRole('heading', { name: '오늘의 복약 체크' })
    const cards = document.querySelectorAll('.checkin-group-card')
    expect(cards[1].textContent).toContain('1/2')
    expect(cards[1].textContent).toContain('확인 필요')
    // 20:00은 예정 시각 전
    expect(cards[2].textContent).toContain('예정')
  })

  it('시간 그룹을 누르면 해당 상세로 이동한다', async () => {
    renderSummary(makeServices())
    await screen.findByRole('heading', { name: '오늘의 복약 체크' })

    fireEvent.click(document.querySelectorAll('.checkin-group-card')[1])

    expect(await screen.findByText('상세 화면')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toContain(OCC_A)
  })

  it('PB-01 헤더는 뒤로가기와 마스코트를 유지하고 알림 버튼은 노출하지 않는다', async () => {
    renderSummary(makeServices())

    await screen.findByRole('heading', { name: '오늘의 복약 체크' })

    expect(
      screen.getByRole('button', { name: '이전 화면' }),
    ).toBeTruthy()
    expect(
      document.querySelector('.dosey-mascot--header'),
    ).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: '알림' }),
    ).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '이전 화면' }))

    expect(await screen.findByText('일정 화면')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe(
      `/schedule?date=${DATE}`,
    )
  })

  it('전체 일정 보기로 복귀한다', async () => {
    renderSummary(makeServices())
    await screen.findByRole('heading', { name: '오늘의 복약 체크' })

    fireEvent.click(screen.getByRole('button', { name: /전체 일정 보기/ }))

    expect(await screen.findByText('일정 화면')).toBeTruthy()
  })
})

describe('CHECKIN-01 PB-02/03 선택과 CTA', () => {
  it('같은 scheduled_at의 약만 표시하고 다른 시간은 제외한다', async () => {
    renderDetail(makeServices())

    expect(
      await screen.findByRole('heading', { name: '13:00 약을 확인해 주세요' }),
    ).toBeTruthy()
    expect(screen.getByText('복용 시간이 같은 약 2개')).toBeTruthy()
    expect(document.querySelectorAll('.checkin-medication-card')).toHaveLength(2)
    expect(screen.getByText('메트포르민정 500mg')).toBeTruthy()
    expect(screen.getByText('암로디핀정 5mg')).toBeTruthy()
    expect(screen.getByText('1정 · 하루 2회 · 13:00')).toBeTruthy()
    expect(screen.getByText('1정 · 하루 1회 · 13:00')).toBeTruthy()
  })


  it('occurrence와 약 상세 identity가 다르면 기록 UI를 노출하지 않는다', async () => {
    const services = makeServices({
      getOccurrenceMedication: vi.fn(async (id: string) => {
        const medication = makeMedication(id)

        if (id !== OCC_A) return medication

        return {
          data: {
            ...medication.data,
            prescription_version_medication_id: 'pvm-mismatch',
          },
        } as MedicationOccurrenceMedicationResponse
      }),
    })

    renderDetail(services)

    expect(
      await screen.findByRole('heading', {
        name: '복약 정보를 찾을 수 없어요',
      }),
    ).toBeTruthy()

    expect(screen.queryByText('메트포르민정')).toBeNull()
    expect(screen.queryByRole('button', { name: '복용했어요' })).toBeNull()
    expect(screen.queryByRole('button', { name: '기록하기' })).toBeNull()
  })

  it('전체 미선택이면 기록하기가 disabled다', async () => {
    renderDetail(makeServices())
    const cta = await screen.findByRole('button', { name: '기록하기' })
    expect((cta as HTMLButtonElement).disabled).toBe(true)
  })

  it('일부만 선택하면 여전히 disabled다', async () => {
    renderDetail(makeServices())
    const taken = await screen.findAllByRole('button', { name: '복용했어요' })
    fireEvent.click(taken[0])

    const cta = screen.getByRole('button', { name: '기록하기' })
    expect((cta as HTMLButtonElement).disabled).toBe(true)
  })

  it('전부 선택하면 enabled가 된다', async () => {
    renderDetail(makeServices())
    await selectBoth()

    const cta = screen.getByRole('button', { name: '기록하기' })
    expect((cta as HTMLButtonElement).disabled).toBe(false)
  })

  it('선택은 즉시 저장하지 않는다', async () => {
    const services = makeServices()
    renderDetail(services)
    await selectBoth()

    expect(services.putMedicationCheckin).not.toHaveBeenCalled()
  })

  it('TAKEN과 NOT_TAKEN 혼합을 occurrence별로 순차 저장한다', async () => {
    const services = makeServices()
    renderDetail(services)
    await selectBoth()

    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    await waitFor(() =>
      expect(services.putMedicationCheckin).toHaveBeenCalledTimes(2),
    )
    expect(services.putMedicationCheckin).toHaveBeenNthCalledWith(
      1,
      OCC_A,
      expect.objectContaining({ status: 'TAKEN', expectedRevision: 0 }),
      expect.any(String),
    )
    expect(services.putMedicationCheckin).toHaveBeenNthCalledWith(
      2,
      OCC_B,
      expect.objectContaining({ status: 'NOT_TAKEN', expectedRevision: 0 }),
      expect.any(String),
    )
  })

  it('기존 기록이 있으면 선택 상태로 복원하고 실제 revision을 사용한다', async () => {
    const occurrences = defaultOccurrences().map((occurrence) =>
      occurrence.occurrence_id === OCC_A
        ? {
            ...occurrence,
            checkin: {
              checkin_id: 'c1',
              occurrence_id: OCC_A,
              status: 'NOT_TAKEN',
              taken_at: null,
              revision: 3,
              corrected: false,
            } as MedicationCheckinSnapshot,
          }
        : occurrence,
    )
    const services = makeServices({
      getMedicationDay: vi.fn(async () => makeDay(occurrences)),
    })
    renderDetail(services)

    const notTaken = await screen.findAllByRole('button', { name: '복용하지 않았어요' })
    expect(notTaken[0].getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(screen.getAllByRole('button', { name: '복용했어요' })[1])
    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    await waitFor(() =>
      expect(services.putMedicationCheckin).toHaveBeenCalledWith(
        OCC_A,
        expect.objectContaining({ expectedRevision: 3 }),
        expect.any(String),
      ),
    )
  })

  it('저장 중에는 CTA를 비활성화해 중복 제출을 막는다', async () => {
    let resolve: (value: MedicationCheckinResponse) => void = () => {}
    const services = makeServices({
      putMedicationCheckin: vi.fn(
        () => new Promise<MedicationCheckinResponse>((r) => { resolve = r }),
      ),
    })
    renderDetail(services)
    await selectBoth()

    const cta = screen.getByRole('button', { name: '기록하기' })
    fireEvent.click(cta)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: '저장 중…' })).toBeTruthy(),
    )
    const saving = screen.getByRole('button', { name: '저장 중…' })
    expect((saving as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(saving)
    expect(services.putMedicationCheckin).toHaveBeenCalledTimes(1)

    resolve(makeCheckinResponse(OCC_A, 'TAKEN'))
  })
})

describe('CHECKIN-01 PB-04 저장 실패와 재시도', () => {
  it('5xx면 선택을 유지하고 다시 시도할 수 있다', async () => {
    const putMedicationCheckin = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(500, 'boom', 'INTERNAL'))
      .mockImplementation(async (id: string, input: { status: 'TAKEN' | 'NOT_TAKEN' }) =>
        makeCheckinResponse(id, input.status),
      )
    const services = makeServices({ putMedicationCheckin })
    renderDetail(services)
    await selectBoth()

    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    expect(await screen.findByText('기록을 저장하지 못했어요')).toBeTruthy()
    // 선택 유지
    expect(
      screen.getAllByRole('button', { name: '복용했어요' })[0].getAttribute('aria-pressed'),
    ).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: '저장 다시 시도' }))

    await waitFor(() =>
      expect(screen.getByText(/기록 완료/)).toBeTruthy(),
    )
  })

  it('부분 저장 실패를 전체 완료로 표시하지 않는다', async () => {
    const putMedicationCheckin = vi
      .fn()
      .mockImplementationOnce(async (id: string) => makeCheckinResponse(id, 'TAKEN'))
      .mockRejectedValue(new ApiError(500, 'boom', 'INTERNAL'))
    const services = makeServices({ putMedicationCheckin })
    renderDetail(services)
    await selectBoth()

    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    expect(await screen.findByText('기록을 저장하지 못했어요')).toBeTruthy()
    expect(screen.queryByText(/기록 완료/)).toBeNull()
    expect(putMedicationCheckin).toHaveBeenCalledTimes(2)
  })

  it('revision conflict면 재조회하고 자동 재제출하지 않는다', async () => {
    const services = makeServices({
      putMedicationCheckin: vi
        .fn()
        .mockRejectedValue(
          new ApiError(409, 'conflict', 'CHECKIN_REVISION_CONFLICT'),
        ),
    })
    renderDetail(services)
    await selectBoth()

    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    await waitFor(() =>
      expect(services.getMedicationDay).toHaveBeenCalledTimes(2),
    )
    // 자동 재제출 없음
    expect(services.putMedicationCheckin).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(/기록 완료/)).toBeNull()
  })

  it('부분 성공 뒤 conflict reload에서도 성공 occurrence를 다시 PUT하지 않는다', async () => {
    const refreshedDay = makeDay(
      defaultOccurrences().map((occurrence) => {
        if (occurrence.occurrence_id === OCC_A) {
          return {
            ...occurrence,
            checkin: makeCheckinResponse(OCC_A, 'TAKEN', 1).data as MedicationCheckinSnapshot,
          }
        }
        if (occurrence.occurrence_id === OCC_B) {
          return {
            ...occurrence,
            checkin: makeCheckinResponse(OCC_B, 'NOT_TAKEN', 7).data as MedicationCheckinSnapshot,
          }
        }
        return occurrence
      }),
    )

    const getMedicationDay = vi
      .fn()
      .mockResolvedValueOnce(makeDay(defaultOccurrences()))
      .mockResolvedValueOnce(refreshedDay)

    const putMedicationCheckin = vi
      .fn()
      .mockResolvedValueOnce(makeCheckinResponse(OCC_A, 'TAKEN', 1))
      .mockRejectedValueOnce(
        new ApiError(409, 'conflict', 'CHECKIN_REVISION_CONFLICT'),
      )
      .mockImplementationOnce(
        async (id: string, input: { status: 'TAKEN' | 'NOT_TAKEN' }) =>
          makeCheckinResponse(id, input.status, 8),
      )

    renderDetail(makeServices({ getMedicationDay, putMedicationCheckin }))
    await selectBoth()
    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    await waitFor(() => expect(getMedicationDay).toHaveBeenCalledTimes(2))
    expect(putMedicationCheckin).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getAllByRole('button', { name: '복용했어요' })[1])
    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    await waitFor(() => expect(putMedicationCheckin).toHaveBeenCalledTimes(3))
    expect(
      putMedicationCheckin.mock.calls.filter(([id]) => id === OCC_A),
    ).toHaveLength(1)
    expect(putMedicationCheckin).toHaveBeenNthCalledWith(
      3,
      OCC_B,
      expect.objectContaining({ status: 'TAKEN', expectedRevision: 7 }),
      expect.any(String),
    )
  })

  it('기술 오류 코드를 노출하지 않는다', async () => {
    const services = makeServices({
      putMedicationCheckin: vi
        .fn()
        .mockRejectedValue(new ApiError(500, 'INTERNAL_RAW_DETAIL', 'INTERNAL')),
    })
    renderDetail(services)
    await selectBoth()
    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    expect(await screen.findByText('기록을 저장하지 못했어요')).toBeTruthy()
    expect(screen.queryByText(/INTERNAL_RAW_DETAIL/)).toBeNull()
    expect(screen.queryByText(/INTERNAL/)).toBeNull()
  })
})

describe('CHECKIN-01 PB-05 완료와 시각 게이트', () => {
  it('전부 저장되면 완료 수와 전체 일정 보기를 표시한다', async () => {
    renderDetail(makeServices())
    await selectBoth()
    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    expect(await screen.findByText('2/2 기록 완료')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /전체 일정 보기/ }))
    expect(await screen.findByText('일정 화면')).toBeTruthy()
  })

  it('예정 시각 전 그룹은 선택과 저장을 막는다', async () => {
    renderDetail(makeServices(), OCC_EVENING)

    expect(
      await screen.findByRole('heading', { name: '20:00 약을 확인해 주세요' }),
    ).toBeTruthy()
    expect(
      screen.getByText('20:00부터 복약 기록을 남길 수 있어요.'),
    ).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: '복용했어요' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    expect(
      (screen.getByRole('button', { name: '기록하기' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it('서버 CHECKIN_BEFORE_SCHEDULED_AT도 안전 문구로 처리한다', async () => {
    const services = makeServices({
      putMedicationCheckin: vi
        .fn()
        .mockRejectedValue(
          new ApiError(422, 'too early', 'VALIDATION_FAILED', [
            { field: 'occurrence_id', reason: 'CHECKIN_BEFORE_SCHEDULED_AT' },
          ]),
        ),
    })
    renderDetail(services)
    await selectBoth()
    fireEvent.click(screen.getByRole('button', { name: '기록하기' }))

    expect(
      await screen.findByText('13:00부터 복약 기록을 남길 수 있어요.'),
    ).toBeTruthy()
    expect(screen.queryByText(/기록 완료/)).toBeNull()
  })
})
