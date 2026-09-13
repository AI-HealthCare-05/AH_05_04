import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import {
  MEDICATION_CANDIDATE_SEARCH_STATUSES,
  type MedicationCandidateSearchData,
  confirmMedicationCandidate,
  getMedicationCandidateSearch,
  isCandidateFeatureDisabledError,
  isCandidateSearchCreationNotConnectedError,
  mapCandidateSearchToScreenState,
  rejectMedicationCandidate,
} from '../src/api/medicationCandidates'

const prescriptionVersionMedicationId = '66666666-6666-4666-8666-666666666666'
const searchId = '77777777-7777-4777-8777-777777777777'
const candidateSearchResultId = '88888888-8888-4888-8888-888888888888'
const productId = '99999999-9999-4999-8999-999999999999'
const idempotencyKey = 'candidate:11111111-1111-4111-8111-111111111111'

function makeCandidateSnapshot() {
  return {
    product_name: '테스트정 10mg',
    strength_text: '10mg',
    dosage_form: '정제',
    manufacturer_name: null,
    product_status: 'ACTIVE',
  }
}

function makeSearchData(
  overrides: Partial<MedicationCandidateSearchData> = {},
): MedicationCandidateSearchData {
  return {
    search_id: searchId,
    prescription_version_medication_id: prescriptionVersionMedicationId,
    medication_index: 0,
    status: 'READY',
    candidate_search_result_id: candidateSearchResultId,
    candidate: makeCandidateSnapshot(),
    expires_at: '2026-09-12T00:00:00Z',
    ...overrides,
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

function makeErrorBody(code: string, reason?: string) {
  return {
    code,
    message: 'error',
    details: reason
      ? [{ field: 'medication_candidate', reason }]
      : [],
    trace_id: 'trace-1',
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Candidate 조회 adapter', () => {
  it('계약과 같은 경로로 GET 요청을 보내고 Idempotency-Key 를 싣지 않는다', async () => {
    const responseBody = { data: makeSearchData() }
    const fetchMock = stubFetch(responseBody)

    await expect(
      getMedicationCandidateSearch(prescriptionVersionMedicationId),
    ).resolves.toEqual(responseBody)

    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/medication-candidate-searches/${prescriptionVersionMedicationId}`,
      expect.objectContaining({ method: 'GET' }),
    )

    const headers = fetchMock.mock.calls[0]?.[1]?.headers as
      | Record<string, string>
      | undefined

    expect(headers?.['Idempotency-Key']).toBeUndefined()
  })

  it('AbortSignal 을 그대로 전달한다', async () => {
    const fetchMock = stubFetch({ data: makeSearchData() })
    const controller = new AbortController()

    await getMedicationCandidateSearch(
      prescriptionVersionMedicationId,
      controller.signal,
    )

    expect(fetchMock).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ signal: controller.signal }),
    )
  })
})

describe('Candidate 확인 adapter', () => {
  it('계약과 같은 경로·헤더·body 로 확인 요청을 보낸다', async () => {
    const responseBody = {
      data: {
        identification_id: '10101010-1010-4101-8101-101010101010',
        prescription_version_medication_id: prescriptionVersionMedicationId,
        status: 'MATCHED',
        source: 'USER_SELECTED',
        product_id: productId,
        confirmed_at: '2026-09-11T09:00:00Z',
      },
    }
    const fetchMock = stubFetch(responseBody)

    await expect(
      confirmMedicationCandidate(
        {
          prescriptionVersionMedicationId,
          candidateSearchResultId,
        },
        idempotencyKey,
      ),
    ).resolves.toEqual(responseBody)

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/medication-candidates/confirm',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        }),
        body: JSON.stringify({
          prescription_version_medication_id: prescriptionVersionMedicationId,
          candidate_search_result_id: candidateSearchResultId,
        }),
      }),
    )
  })

  it('형식에 맞지 않는 Idempotency-Key 는 요청 전에 거절한다', async () => {
    const fetchMock = stubFetch({})

    await expect(
      confirmMedicationCandidate(
        { prescriptionVersionMedicationId, candidateSearchResultId },
        'too short',
      ),
    ).rejects.toThrow(/Idempotency-Key/)

    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('Candidate 거절 adapter', () => {
  it('계약과 같은 경로·헤더·body 로 거절 요청을 보낸다', async () => {
    const responseBody = {
      data: {
        identification_event_id: '20202020-2020-4202-8202-202020202020',
        prescription_version_medication_id: prescriptionVersionMedicationId,
        status: 'UNRESOLVED',
        search_status: 'INVALIDATED_USER_REJECTED',
        rejected_at: '2026-09-11T09:00:00Z',
      },
    }
    const fetchMock = stubFetch(responseBody)

    await expect(
      rejectMedicationCandidate(
        { searchId, candidateSearchResultId },
        idempotencyKey,
      ),
    ).resolves.toEqual(responseBody)

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/medication-candidates/reject',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        }),
        body: JSON.stringify({
          search_id: searchId,
          candidate_search_result_id: candidateSearchResultId,
        }),
      }),
    )
  })

  it('형식에 맞지 않는 Idempotency-Key 는 요청 전에 거절한다', async () => {
    const fetchMock = stubFetch({})

    await expect(
      rejectMedicationCandidate(
        { searchId, candidateSearchResultId },
        'too short',
      ),
    ).rejects.toThrow(/Idempotency-Key/)

    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('503 판별 helper', () => {
  it('PUBLIC_TRACK_F_DISABLED 는 공개 게이트로 판별한다', async () => {
    stubFetch(makeErrorBody('SERVICE_UNAVAILABLE', 'PUBLIC_TRACK_F_DISABLED'), 503)

    const error = await getMedicationCandidateSearch(
      prescriptionVersionMedicationId,
    ).catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(isCandidateFeatureDisabledError(error)).toBe(true)
    expect(isCandidateSearchCreationNotConnectedError(error)).toBe(false)
  })

  it('PRESCRIPTION_VERSION_MEDICATION_OWNERSHIP_NOT_CONNECTED 는 미연결로 판별한다', async () => {
    stubFetch(
      makeErrorBody(
        'SERVICE_UNAVAILABLE',
        'PRESCRIPTION_VERSION_MEDICATION_OWNERSHIP_NOT_CONNECTED',
      ),
      503,
    )

    const error = await getMedicationCandidateSearch(
      prescriptionVersionMedicationId,
    ).catch((caught: unknown) => caught)

    expect(isCandidateSearchCreationNotConnectedError(error)).toBe(true)
    expect(isCandidateFeatureDisabledError(error)).toBe(false)
  })

  it('서로 다른 reason 을 혼동하지 않는다', async () => {
    stubFetch(makeErrorBody('SERVICE_UNAVAILABLE'), 503)

    const error = await getMedicationCandidateSearch(
      prescriptionVersionMedicationId,
    ).catch((caught: unknown) => caught)

    expect(isCandidateFeatureDisabledError(error)).toBe(false)
    expect(isCandidateSearchCreationNotConnectedError(error)).toBe(false)
  })

  it('ApiError 가 아닌 값·다른 status 에는 반응하지 않는다', () => {
    const wrongStatus = new ApiError(
      404,
      'not found',
      'SERVICE_UNAVAILABLE',
      [{ field: 'medication_candidate', reason: 'PUBLIC_TRACK_F_DISABLED' }],
    )

    for (const value of [null, undefined, new Error('boom'), 503, wrongStatus]) {
      expect(isCandidateFeatureDisabledError(value)).toBe(false)
      expect(isCandidateSearchCreationNotConnectedError(value)).toBe(false)
    }
  })
})

describe('Candidate 화면 상태 매핑 (fail-closed)', () => {
  // DTO의 11개 status 전수 + 미지원 값 1개를 덮습니다. 새 status 추가 시
  // MEDICATION_CANDIDATE_SEARCH_STATUSES 와 여기 모두 갱신해야 fail-closed 회귀를 잡습니다.
  it('DTO 의 11개 status 를 모두 포함한다', () => {
    expect(MEDICATION_CANDIDATE_SEARCH_STATUSES).toHaveLength(11)
    expect(MEDICATION_CANDIDATE_SEARCH_STATUSES).toEqual([
      'RUNNING',
      'READY',
      'AMBIGUOUS',
      'NO_CANDIDATE',
      'INGREDIENT_ONLY',
      'INVALID_INPUT',
      'INVALIDATED_INPUT_CHANGED',
      'INVALIDATED_USER_REJECTED',
      'EXPIRED',
      'FAILED',
      'CONSUMED',
    ])
  })

  it.each([
    ['RUNNING', { kind: 'PENDING' }],
    [
      'READY',
      {
        kind: 'AWAITING_USER_DECISION',
        candidate: makeCandidateSnapshot(),
        candidateSearchResultId,
      },
    ],
    ['AMBIGUOUS', { kind: 'AMBIGUOUS' }],
    ['NO_CANDIDATE', { kind: 'NO_CANDIDATE' }],
    ['INGREDIENT_ONLY', { kind: 'INGREDIENT_ONLY' }],
    ['INVALID_INPUT', { kind: 'INVALID_INPUT' }],
    [
      'INVALIDATED_INPUT_CHANGED',
      { kind: 'INVALIDATED', reason: 'INPUT_CHANGED' },
    ],
    [
      'INVALIDATED_USER_REJECTED',
      { kind: 'INVALIDATED', reason: 'USER_REJECTED' },
    ],
    ['EXPIRED', { kind: 'EXPIRED' }],
    ['FAILED', { kind: 'FAILED' }],
    ['CONSUMED', { kind: 'CONSUMED' }],
    // 미지원 값: 백엔드가 향후 12번째 status 를 추가한 상황을 흉내냅니다.
    ['SOME_FUTURE_STATUS', { kind: 'INCONSISTENT' }],
  ] as const)('status=%s 를 화면 상태로 매핑한다', (status, expected) => {
    expect(mapCandidateSearchToScreenState(makeSearchData({ status }))).toEqual(
      expected,
    )
  })

  it('READY 이지만 candidate 가 없으면 사용자 결정을 요청하지 않는다', () => {
    expect(
      mapCandidateSearchToScreenState(
        makeSearchData({ status: 'READY', candidate: null }),
      ),
    ).toEqual({ kind: 'INCONSISTENT' })
  })

  it('READY 이지만 candidate_search_result_id 가 없으면 사용자 결정을 요청하지 않는다', () => {
    expect(
      mapCandidateSearchToScreenState(
        makeSearchData({ status: 'READY', candidate_search_result_id: null }),
      ),
    ).toEqual({ kind: 'INCONSISTENT' })
  })

  it('빈 문자열·대소문자가 다른 값도 허용하지 않는다', () => {
    expect(mapCandidateSearchToScreenState(makeSearchData({ status: '' }))).toEqual(
      { kind: 'INCONSISTENT' },
    )
    expect(
      mapCandidateSearchToScreenState(makeSearchData({ status: 'ready' })),
    ).toEqual({ kind: 'INCONSISTENT' })
  })
})
