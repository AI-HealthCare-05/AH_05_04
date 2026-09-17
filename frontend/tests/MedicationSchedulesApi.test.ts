import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { IDEMPOTENCY_KEY_PATTERN } from '../src/api/idempotency'
import scheduleFixture from '../../docs/validation/track-b/issue-202-schedule-fixtures.json'
import {
  cancelMedicationSchedule,
  createScheduleIdempotencyKey,
  getMedicationDay,
  getScheduleRecommendation,
  getOccurrenceMedication,
  isOccurrenceMedicationNotFoundError,
  isPrescriptionMedicationNotFoundError,
  isPrescriptionVersionConflictError,
  isScheduleRevisionConflictError,
  putMedicationSchedule,
  type MedicationDayResponse,
  type MedicationOccurrenceMedicationResponse,
  type MedicationScheduleResponse,
} from '../src/api/medicationSchedules'

const medicationId = '22222222-2222-4222-8222-222222222222'
const occurrenceId = '44444444-4444-4444-8444-444444444444'
const idempotencyKey = 'schedule:11111111-1111-4111-8111-111111111111'

function stubFetch(
  body: unknown,
  status = 200,
): ReturnType<typeof vi.fn<typeof fetch>> {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function errorBody(code: string) {
  return { code, message: 'error', details: [], trace_id: 'trace-1' }
}

const scheduleResponse: MedicationScheduleResponse = {
  data: {
    schedule_id: '33333333-3333-4333-8333-333333333333',
    prescription_version_medication_id: medicationId,
    revision: 2,
    status: 'ACTIVE',
    start_local_date: '2026-09-14',
    end_mode: 'DATE',
    end_local_date: '2026-09-21',
    local_times: ['09:00', '21:00'],
  },
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Schedule and occurrence GET adapters', () => {
  it('날짜별 일정과 occurrence를 계약 query로 조회한다', async () => {
    const response = scheduleFixture.partial_day as MedicationDayResponse
    const fetchMock = stubFetch(response)

    await expect(getMedicationDay('2026-09-14')).resolves.toEqual(response)
    expect(response.data.schedule_status).toBe('PARTIAL')
    expect(response.data.schedule_items[1]).toMatchObject({
      schedule_item_status: 'SETUP_REQUIRED',
      schedule_id: null,
      revision: null,
      setup_reason: 'MISSING_START_DATE',
      schedule: null,
    })
    expect(response.data.schedule_items[0]?.schedule).toMatchObject({
      start_local_date: '2026-09-11',
      end_mode: 'OPEN_ENDED',
      end_local_date: null,
      local_times: ['23:00'],
    })
    expect(response.data.occurrences[0]).toMatchObject({
      prescription_version_medication_id:
        '11111111-1111-4111-8111-111111111111',
      confirmation_deadline_at: '2026-09-11T18:00:00Z',
      checkin: null,
    })
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/medication-occurrences?date=2026-09-14',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('날짜별 응답의 현재 Check-in snapshot을 그대로 보존한다', async () => {
    const response: MedicationDayResponse = {
      data: {
        ...scheduleFixture.partial_day.data,
        occurrences: [
          {
            ...scheduleFixture.partial_day.data.occurrences[0],
            status: 'CLOSED',
            checkin: {
              checkin_id: '55555555-5555-4555-8555-555555555555',
              occurrence_id: occurrenceId,
              status: 'NOT_TAKEN',
              taken_at: null,
              revision: 2,
              corrected: true,
            },
          },
        ],
      },
    }
    stubFetch(response)

    const result = await getMedicationDay('2026-09-11')

    expect(result.data.occurrences[0]?.checkin).toEqual(
      response.data.occurrences[0]?.checkin,
    )
  })

  it('과거 occurrence의 원래 약 정보를 occurrence id로 조회한다', async () => {
    const response: MedicationOccurrenceMedicationResponse = {
      data: {
        occurrence_id: occurrenceId,
        prescription_version_id: '11111111-1111-4111-8111-111111111111',
        prescription_version_medication_id: medicationId,
        medication_name: '합성 테스트약',
        strength_text: null,
        dose_value: 0.5,
        dose_unit: '정',
      },
    }
    const fetchMock = stubFetch(response)

    await expect(getOccurrenceMedication(occurrenceId)).resolves.toEqual(response)
    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/medication-occurrences/${occurrenceId}/medication`,
      expect.objectContaining({ method: 'GET' }),
    )
  })
})

describe('Schedule mutation adapters', () => {
  it('DATE 일정은 종료일과 revision을 포함해 PUT한다', async () => {
    const fetchMock = stubFetch(scheduleResponse)

    await putMedicationSchedule(
      medicationId,
      {
        startLocalDate: '2026-09-14',
        endMode: 'DATE',
        endLocalDate: '2026-09-21',
        localTimes: ['09:00', '21:00'],
        expectedRevision: 1,
      },
      idempotencyKey,
    )

    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/prescription-version-medications/${medicationId}/schedule`,
      expect.objectContaining({
        method: 'PUT',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        }),
        body: JSON.stringify({
          start_local_date: '2026-09-14',
          end_mode: 'DATE',
          end_local_date: '2026-09-21',
          local_times: ['09:00', '21:00'],
          expected_revision: 1,
        }),
      }),
    )
  })

  it.each([
    {
      startLocalDate: '2026-09-14',
      endMode: 'DATE',
      localTimes: ['09:00'],
      expectedRevision: 0,
    },
    {
      startLocalDate: '2026-09-14',
      endMode: 'OPEN_ENDED',
      endLocalDate: '2026-09-21',
      localTimes: ['09:00'],
      expectedRevision: 0,
    },
  ])('종료 방식과 모순된 입력은 요청 전에 거절한다', async (input) => {
    const fetchMock = stubFetch(scheduleResponse)

    await expect(
      putMedicationSchedule(medicationId, input as never, idempotencyKey),
    ).rejects.toThrow(/endLocalDate/)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('취소는 고정 status와 최신 revision을 PATCH한다', async () => {
    const fetchMock = stubFetch({
      data: { ...scheduleResponse.data, revision: 3, status: 'CANCELLED' },
    })

    await cancelMedicationSchedule(medicationId, 2, idempotencyKey)

    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/prescription-version-medications/${medicationId}/schedule`,
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ status: 'CANCELLED', expected_revision: 2 }),
      }),
    )
  })

  it('잘못된 멱등성 키는 요청 전에 거절한다', async () => {
    const fetchMock = stubFetch(scheduleResponse)

    await expect(
      cancelMedicationSchedule(medicationId, 1, 'schedule key'),
    ).rejects.toThrow(/Idempotency-Key/)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('생성 helper가 Schedule prefix와 공통 형식을 지킨다', () => {
    expect(createScheduleIdempotencyKey()).toMatch(IDEMPOTENCY_KEY_PATTERN)
    expect(createScheduleIdempotencyKey()).toMatch(/^schedule:/)
  })
})

describe('Schedule 오류 판별 helper', () => {
  it.each([
    ['SCHEDULE_REVISION_CONFLICT', 409, true, false, false, false],
    ['PRESCRIPTION_VERSION_CONFLICT', 409, false, true, false, false],
    ['PRESCRIPTION_MEDICATION_NOT_FOUND', 404, false, false, true, false],
    ['MEDICATION_OCCURRENCE_NOT_FOUND', 404, false, false, false, true],
  ] as const)(
    '%s를 다른 복구 분기와 구분한다',
    async (code, status, revision, version, medication, occurrence) => {
      stubFetch(errorBody(code), status)

      const error = await getOccurrenceMedication(occurrenceId).catch(
        (caught: unknown) => caught,
      )

      expect(error).toBeInstanceOf(ApiError)
      expect(isScheduleRevisionConflictError(error)).toBe(revision)
      expect(isPrescriptionVersionConflictError(error)).toBe(version)
      expect(isPrescriptionMedicationNotFoundError(error)).toBe(medication)
      expect(isOccurrenceMedicationNotFoundError(error)).toBe(occurrence)
    },
  )
})


it('previews without persistence and serializes optional recomputation context only when supplied', async () => {
  const fetchMock = stubFetch(scheduleResponse)
  const input = { meal_end_times: { DINNER: '19:30' }, same_times_every_day: true as const }
  await getScheduleRecommendation(medicationId, input)
  expect(fetchMock.mock.calls[0]?.[0]).toContain('/schedule-recommendation')
  expect(JSON.parse(fetchMock.mock.calls[0]?.[1]?.body as string)).toEqual(input)
  fetchMock.mockResolvedValue(new Response(JSON.stringify(scheduleResponse), { headers: { 'Content-Type': 'application/json' } }))
  await putMedicationSchedule(medicationId, {
    startLocalDate: '2026-09-17', endMode: 'OPEN_ENDED', localTimes: ['20:00'], expectedRevision: 0,
    recommendationContext: { ...input, rule_version: 'explicit-after-meal-v1' },
  }, idempotencyKey)
  expect(JSON.parse(fetchMock.mock.calls[1]?.[1]?.body as string).recommendation_context).toEqual({ ...input, rule_version: 'explicit-after-meal-v1' })
})
