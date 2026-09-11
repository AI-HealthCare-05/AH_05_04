import { ApiError, apiRequest } from './client'
import { isValidIdempotencyKey } from './idempotency'

// 계약 정본:
// - backend/app/apis/v1/medication_candidate_routers.py
// - backend/app/dtos/medication_candidates.py
//
// Single Candidate Gate 원칙(#131): 후보 목록·Top-K·score·confidence 는 계약에도
// 없고 이 adapter 도 만들지 않습니다. Frontend 는 후보를 생성·정렬·자동선택하지 않고
// 서버가 이미 골라 보여주기로 결정한 단일 candidate 만 그대로 전달합니다.
//
// `POST /api/v1/medication-candidate-searches` 는 develop 기준 항상 503
// (PRESCRIPTION_VERSION_MEDICATION_OWNERSHIP_NOT_CONNECTED) 을 던지는 스텁입니다.
// 이 adapter 는 그 함수를 만들지 않고, 조회·확인·거절 3개 endpoint 만 다룹니다.

/** DTO 의 `MedicationCandidateSearchStatus` 11개 값을 그대로 옮깁니다. 하나라도
 * 빠뜨리면 화면 상태 매핑에서 fail-closed 되어야 할 값이 통과할 수 있습니다. */
export const MEDICATION_CANDIDATE_SEARCH_STATUSES = [
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
] as const

export type MedicationCandidateSearchStatus =
  (typeof MEDICATION_CANDIDATE_SEARCH_STATUSES)[number]

export type MedicationIdentificationStatus = 'MATCHED' | 'UNRESOLVED'

export type MedicationIdentificationSource = 'USER_SELECTED' | 'USER_REJECTED'

export type MedicationCandidateSnapshot = {
  product_name: string
  strength_text: string | null
  dosage_form: string | null
  manufacturer_name: string | null
  product_status: string
}

export type MedicationCandidateSearchData = {
  search_id: string
  prescription_version_medication_id: string
  medication_index: number
  // 서버 응답은 JSON 이라 런타임에 위 11개 밖의 값이 실려 올 수 있습니다. 이 필드를
  // 좁은 union 으로 단정하지 않고 문자열로 받아, 상태 매핑 함수가 직접 fail-closed 로
  // 판정하게 합니다.
  status: string
  candidate_search_result_id: string | null
  candidate: MedicationCandidateSnapshot | null
  expires_at: string | null
}

export type MedicationCandidateSearchResponse = {
  data: MedicationCandidateSearchData
}

export type ConfirmMedicationCandidateData = {
  identification_id: string
  prescription_version_medication_id: string
  status: MedicationIdentificationStatus
  source: MedicationIdentificationSource
  product_id: string
  confirmed_at: string
}

export type ConfirmMedicationCandidateResponse = {
  data: ConfirmMedicationCandidateData
}

export type RejectMedicationCandidateData = {
  identification_event_id: string
  prescription_version_medication_id: string
  status: MedicationIdentificationStatus
  search_status: MedicationCandidateSearchStatus
  rejected_at: string
}

export type RejectMedicationCandidateResponse = {
  data: RejectMedicationCandidateData
}

function requireIdempotencyKey(idempotencyKey: string): void {
  if (!isValidIdempotencyKey(idempotencyKey)) {
    throw new Error('Idempotency-Key does not satisfy the contract format')
  }
}

/**
 * `GET /api/v1/medication-candidate-searches/{prescription_version_medication_id}`
 *
 * 조회 전용이라 `Idempotency-Key` 가 필요 없습니다.
 */
export async function getMedicationCandidateSearch(
  prescriptionVersionMedicationId: string,
  signal?: AbortSignal,
): Promise<MedicationCandidateSearchResponse> {
  return apiRequest<MedicationCandidateSearchResponse>(
    `/api/v1/medication-candidate-searches/${prescriptionVersionMedicationId}`,
    { method: 'GET', signal },
  )
}

/**
 * `POST /api/v1/medication-candidates/confirm`
 *
 * `idempotencyKey` 는 호출자가 논리적 시도 단위로 만들어 전달합니다
 * (`frontend/src/api/idempotency.ts` 의 `createIdempotencyKey`).
 */
export async function confirmMedicationCandidate(
  input: {
    prescriptionVersionMedicationId: string
    candidateSearchResultId: string
  },
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<ConfirmMedicationCandidateResponse> {
  requireIdempotencyKey(idempotencyKey)

  return apiRequest<ConfirmMedicationCandidateResponse>(
    '/api/v1/medication-candidates/confirm',
    {
      method: 'POST',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({
        prescription_version_medication_id: input.prescriptionVersionMedicationId,
        candidate_search_result_id: input.candidateSearchResultId,
      }),
    },
  )
}

/**
 * `POST /api/v1/medication-candidates/reject`
 *
 * `idempotencyKey` 는 호출자가 논리적 시도 단위로 만들어 전달합니다
 * (`frontend/src/api/idempotency.ts` 의 `createIdempotencyKey`).
 */
export async function rejectMedicationCandidate(
  input: {
    searchId: string
    candidateSearchResultId: string
  },
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<RejectMedicationCandidateResponse> {
  requireIdempotencyKey(idempotencyKey)

  return apiRequest<RejectMedicationCandidateResponse>(
    '/api/v1/medication-candidates/reject',
    {
      method: 'POST',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({
        search_id: input.searchId,
        candidate_search_result_id: input.candidateSearchResultId,
      }),
    },
  )
}

// --- 503 판별: "상호작용 없음"과 "미구현·미공개"는 서로 다른 대응이 필요합니다 ---

function getErrorDetailReason(error: ApiError): string | undefined {
  return error.details.find((detail) => detail.field === 'medication_candidate')
    ?.reason
}

/**
 * 공개 게이트가 닫혀 있어 기능 자체를 아직 사용할 수 없는 상태입니다
 * (`GET`·`confirm`·`reject` 공통, `PUBLIC_TRACK_F_ENABLED=false`).
 *
 * 장애가 아니라 아직 공개하지 않은 기능이므로, 에러 배너보다는 "준비 중" 안내로
 * 처리해야 합니다.
 */
export function isCandidateFeatureDisabledError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 503 &&
    getErrorDetailReason(error) === 'PUBLIC_TRACK_F_DISABLED'
  )
}

/**
 * Candidate Search 생성 경로가 아직 연결되지 않은 상태입니다
 * (`POST /medication-candidate-searches` 스텁 전용).
 *
 * 이 adapter 는 그 endpoint 를 호출하는 함수를 만들지 않지만, 호출자가 같은 503
 * 이라도 "공개 게이트" 와 "미구현 스텁"을 서로 다른 원인으로 구분할 수 있어야
 * 하므로 판별 helper 를 함께 제공합니다.
 */
export function isCandidateSearchCreationNotConnectedError(
  error: unknown,
): boolean {
  return (
    error instanceof ApiError &&
    error.status === 503 &&
    getErrorDetailReason(error) ===
      'PRESCRIPTION_VERSION_MEDICATION_OWNERSHIP_NOT_CONNECTED'
  )
}

// --- 화면 상태 매핑: 순수 함수, fail-closed ---

export type MedicationCandidateScreenState =
  | { kind: 'PENDING' }
  | {
      kind: 'AWAITING_USER_DECISION'
      candidate: MedicationCandidateSnapshot
      candidateSearchResultId: string
    }
  | { kind: 'NO_CANDIDATE' }
  | { kind: 'AMBIGUOUS' }
  | { kind: 'INGREDIENT_ONLY' }
  | { kind: 'INVALID_INPUT' }
  | { kind: 'INVALIDATED'; reason: 'INPUT_CHANGED' | 'USER_REJECTED' }
  | { kind: 'EXPIRED' }
  | { kind: 'FAILED' }
  | { kind: 'CONSUMED' }
  /** 계약 위반·미지원 status 등 신뢰할 수 없는 조합. 허용이 아니라 실패 쪽입니다. */
  | { kind: 'INCONSISTENT' }

/**
 * Candidate Search 응답을 화면 상태로 매핑하는 순수 함수입니다.
 *
 * fail-open 이 되면 안 되므로:
 * - `switch` 에 반드시 `default` 를 두고, 백엔드가 향후 12번째 status 를 추가하거나
 *   응답이 손상돼도 알 수 없는 값은 `INCONSISTENT` 로 보냅니다.
 * - `AWAITING_USER_DECISION` (사용자 확인·거절 UI 를 열 수 있는 유일한 상태)은
 *   `status === 'READY'` 이면서 `candidate`·`candidate_search_result_id` 가 모두
 *   존재할 때만 반환합니다. 계약상 이 둘은 READY 에서 함께 채워지지만, 응답이
 *   이를 어기면 사용자 결정을 요청하지 않고 `INCONSISTENT` 로 fail-closed 합니다.
 */
export function mapCandidateSearchToScreenState(
  search: MedicationCandidateSearchData,
): MedicationCandidateScreenState {
  switch (search.status) {
    case 'RUNNING':
      return { kind: 'PENDING' }
    case 'READY':
      if (search.candidate !== null && search.candidate_search_result_id !== null) {
        return {
          kind: 'AWAITING_USER_DECISION',
          candidate: search.candidate,
          candidateSearchResultId: search.candidate_search_result_id,
        }
      }
      return { kind: 'INCONSISTENT' }
    case 'NO_CANDIDATE':
      return { kind: 'NO_CANDIDATE' }
    case 'AMBIGUOUS':
      return { kind: 'AMBIGUOUS' }
    case 'INGREDIENT_ONLY':
      return { kind: 'INGREDIENT_ONLY' }
    case 'INVALID_INPUT':
      return { kind: 'INVALID_INPUT' }
    case 'INVALIDATED_INPUT_CHANGED':
      return { kind: 'INVALIDATED', reason: 'INPUT_CHANGED' }
    case 'INVALIDATED_USER_REJECTED':
      return { kind: 'INVALIDATED', reason: 'USER_REJECTED' }
    case 'EXPIRED':
      return { kind: 'EXPIRED' }
    case 'FAILED':
      return { kind: 'FAILED' }
    case 'CONSUMED':
      return { kind: 'CONSUMED' }
    default:
      return { kind: 'INCONSISTENT' }
  }
}
