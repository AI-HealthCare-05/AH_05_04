import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import {
  type MedicationCheckinResponse,
  createCheckinIdempotencyKey,
  isCheckinConflictError,
  isCheckinRevisionConflictError,
  isCheckinValidationError,
  isOccurrenceNotFoundError,
  putMedicationCheckin,
} from '../src/api/medicationCheckins'
import { IDEMPOTENCY_KEY_PATTERN } from '../src/api/idempotency'

const occurrenceId = '44444444-4444-4444-8444-444444444444'
const checkinId = '55555555-5555-4555-8555-555555555555'
const idempotencyKey = 'checkin:11111111-1111-4111-8111-111111111111'

function makeCheckin(
  overrides: Partial<MedicationCheckinResponse['data']> = {},
): MedicationCheckinResponse {
  return {
    data: {
      checkin_id: checkinId,
      occurrence_id: occurrenceId,
      status: 'TAKEN',
      taken_at: '2026-09-11T09:00:00Z',
      revision: 1,
      corrected: false,
      ...overrides,
    },
  }
}

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

function makeErrorBody(code: string) {
  return {
    code,
    message: 'error',
    details: [],
    trace_id: 'trace-1',
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Check-in PUT adapter', () => {
  it('계약과 같은 경로·헤더·body 로 TAKEN 을 제출한다', async () => {
    const responseBody = makeCheckin()
    const fetchMock = stubFetch(responseBody)

    const response = await putMedicationCheckin(
      occurrenceId,
      {
        status: 'TAKEN',
        takenAt: '2026-09-11T09:00:00Z',
        expectedRevision: 0,
      },
      idempotencyKey,
    )

    expect(response).toEqual(responseBody)
    expect(response.data.revision).toBe(1)
    expect(response.data.corrected).toBe(false)

    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/medication-occurrences/${occurrenceId}/check-in`,
      expect.objectContaining({
        method: 'PUT',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        }),
        body: JSON.stringify({
          status: 'TAKEN',
          taken_at: '2026-09-11T09:00:00Z',
          expected_revision: 0,
        }),
      }),
    )
  })

  it('NOT_TAKEN 에는 taken_at 을 싣지 않는다', async () => {
    const fetchMock = stubFetch(
      makeCheckin({ status: 'NOT_TAKEN', taken_at: null }),
    )

    await putMedicationCheckin(
      occurrenceId,
      { status: 'NOT_TAKEN', expectedRevision: 0 },
      idempotencyKey,
    )

    const body = fetchMock.mock.calls[0]?.[1]?.body

    expect(body).toBe(
      JSON.stringify({ status: 'NOT_TAKEN', expected_revision: 0 }),
    )
    expect(JSON.parse(String(body))).not.toHaveProperty('taken_at')
  })

  it('NOT_TAKEN 에 takenAt 이 들어와도 body 에서 제외한다', async () => {
    const fetchMock = stubFetch(
      makeCheckin({ status: 'NOT_TAKEN', taken_at: null }),
    )

    await putMedicationCheckin(
      occurrenceId,
      {
        status: 'NOT_TAKEN',
        takenAt: '2026-09-11T09:00:00Z',
        expectedRevision: 0,
      } as never,
      idempotencyKey,
    )

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).not.toHaveProperty(
      'taken_at',
    )
  })

  it('정정은 마지막 revision 을 expected_revision 으로 보낸다', async () => {
    const fetchMock = stubFetch(
      makeCheckin({ revision: 2, corrected: true, status: 'NOT_TAKEN', taken_at: null }),
    )

    await putMedicationCheckin(
      occurrenceId,
      { status: 'NOT_TAKEN', expectedRevision: 1 },
      idempotencyKey,
    )

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))

    // DTO 의 `expected_revision` 은 strict int 이므로 숫자로 직렬화돼야 합니다.
    expect(body.expected_revision).toBe(1)
    expect(typeof body.expected_revision).toBe('number')
  })

  it('AbortSignal 을 그대로 전달한다', async () => {
    const fetchMock = stubFetch(makeCheckin())
    const controller = new AbortController()

    await putMedicationCheckin(
      occurrenceId,
      { status: 'TAKEN', takenAt: '2026-09-11T09:00:00Z', expectedRevision: 0 },
      idempotencyKey,
      controller.signal,
    )

    expect(fetchMock).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('형식에 맞지 않는 Idempotency-Key 는 요청 전에 거절한다', async () => {
    const fetchMock = stubFetch(makeCheckin())

    await expect(
      putMedicationCheckin(
        occurrenceId,
        { status: 'NOT_TAKEN', expectedRevision: 0 },
        'checkin key',
      ),
    ).rejects.toThrow(/Idempotency-Key/)

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('생성 helper 가 형식을 통과하는 키를 만든다', () => {
    expect(createCheckinIdempotencyKey()).toMatch(IDEMPOTENCY_KEY_PATTERN)
    expect(createCheckinIdempotencyKey()).toMatch(/^checkin:/)
  })
})

describe('Check-in 오류 판별 helper', () => {
  it('409 revision 충돌을 판별한다', async () => {
    stubFetch(makeErrorBody('CHECKIN_REVISION_CONFLICT'), 409)

    const error = await putMedicationCheckin(
      occurrenceId,
      { status: 'NOT_TAKEN', expectedRevision: 0 },
      idempotencyKey,
    ).catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(isCheckinConflictError(error)).toBe(true)
    expect(isCheckinRevisionConflictError(error)).toBe(true)
    expect(isCheckinValidationError(error)).toBe(false)
    expect(isOccurrenceNotFoundError(error)).toBe(false)
  })

  it('422 사용자 UNCONFIRMED 제출을 판별한다', async () => {
    stubFetch(makeErrorBody('CHECKIN_STATUS_NOT_USER_SETTABLE'), 422)

    const error = await putMedicationCheckin(
      occurrenceId,
      { status: 'NOT_TAKEN', expectedRevision: 0 },
      idempotencyKey,
    ).catch((caught: unknown) => caught)

    expect(isCheckinValidationError(error)).toBe(true)
    expect(isCheckinConflictError(error)).toBe(false)
    expect(isOccurrenceNotFoundError(error)).toBe(false)
  })

  it('404 occurrence 미존재를 판별한다', async () => {
    stubFetch(makeErrorBody('MEDICATION_OCCURRENCE_NOT_FOUND'), 404)

    const error = await putMedicationCheckin(
      occurrenceId,
      { status: 'NOT_TAKEN', expectedRevision: 0 },
      idempotencyKey,
    ).catch((caught: unknown) => caught)

    expect(isOccurrenceNotFoundError(error)).toBe(true)
    expect(isCheckinConflictError(error)).toBe(false)
    expect(isCheckinValidationError(error)).toBe(false)
  })

  it('409 취소된 occurrence 는 revision 충돌과 구분한다', async () => {
    stubFetch(makeErrorBody('OCCURRENCE_CANCELLED'), 409)

    const error = await putMedicationCheckin(
      occurrenceId,
      { status: 'NOT_TAKEN', expectedRevision: 0 },
      idempotencyKey,
    ).catch((caught: unknown) => caught)

    expect(isCheckinConflictError(error)).toBe(true)
    expect(isCheckinRevisionConflictError(error)).toBe(false)
  })

  it('ApiError 가 아닌 값에는 반응하지 않는다', () => {
    for (const value of [null, undefined, new Error('boom'), 409]) {
      expect(isCheckinConflictError(value)).toBe(false)
      expect(isCheckinRevisionConflictError(value)).toBe(false)
      expect(isCheckinValidationError(value)).toBe(false)
      expect(isOccurrenceNotFoundError(value)).toBe(false)
    }
  })
})
