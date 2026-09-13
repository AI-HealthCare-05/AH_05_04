import { ApiError, apiRequest } from './client'
import { createIdempotencyKey, isValidIdempotencyKey } from './idempotency'

// 계약 정본:
// - backend/app/apis/v1/medication_checkin_routers.py
// - backend/app/dtos/medication_checkins.py

/** 사용자가 직접 제출할 수 있는 상태. `UNCONFIRMED` 는 기한 경과 시 서버가 생성합니다. */
export type MedicationCheckinUserStatus = 'TAKEN' | 'NOT_TAKEN'

/** 응답에 실릴 수 있는 상태. DTO 는 전체 enum 을 반환 타입으로 선언합니다. */
export type MedicationCheckinStatus =
  | MedicationCheckinUserStatus
  | 'UNCONFIRMED'

export type MedicationCheckinResponse = {
  data: {
    checkin_id: string
    occurrence_id: string
    status: MedicationCheckinStatus
    taken_at: string | null
    revision: number
    corrected: boolean
  }
}

export type PutMedicationCheckinInput = {
  status: MedicationCheckinUserStatus
  /**
   * `TAKEN` 일 때만 허용합니다. DTO 가 `AwareDatetime` 이므로 timezone offset 이 있는
   * ISO 8601 문자열이어야 합니다.
   */
  takenAt?: string
  /** 최초 생성은 `0`, 정정은 직전 응답의 `revision`. */
  expectedRevision: number
}

type PutMedicationCheckinBody = {
  status: MedicationCheckinUserStatus
  taken_at?: string
  expected_revision: number
}

/** Check-in 제출·정정용 `Idempotency-Key` 를 만듭니다. */
export function createCheckinIdempotencyKey(): string {
  return createIdempotencyKey('checkin')
}

function buildRequestBody(
  input: PutMedicationCheckinInput,
): PutMedicationCheckinBody {
  // DTO 가 `extra="forbid"` 이고 `taken_at` 은 `TAKEN` 에서만 허용되므로,
  // NOT_TAKEN 에서는 `undefined` 도 아니라 키 자체를 넣지 않습니다.
  if (input.status === 'TAKEN' && input.takenAt !== undefined) {
    return {
      status: input.status,
      taken_at: input.takenAt,
      expected_revision: input.expectedRevision,
    }
  }

  return {
    status: input.status,
    expected_revision: input.expectedRevision,
  }
}

/**
 * `PUT /api/v1/medication-occurrences/{occurrence_id}/check-in`
 *
 * `idempotencyKey` 는 호출자가 논리적 시도 단위로 만들어 전달합니다. 네트워크 실패
 * 재시도에서 같은 키를 다시 보내야 서버가 중복 mutation 대신 기존 응답을 재생하므로,
 * adapter 가 호출마다 새 키를 만들지 않습니다.
 */
export async function putMedicationCheckin(
  occurrenceId: string,
  input: PutMedicationCheckinInput,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<MedicationCheckinResponse> {
  if (!isValidIdempotencyKey(idempotencyKey)) {
    throw new Error('Idempotency-Key does not satisfy the contract format')
  }

  return apiRequest<MedicationCheckinResponse>(
    `/api/v1/medication-occurrences/${occurrenceId}/check-in`,
    {
      method: 'PUT',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(buildRequestBody(input)),
    },
  )
}

/**
 * 존재하지 않거나 SELF 소유가 아닌 occurrence (`MEDICATION_OCCURRENCE_NOT_FOUND`).
 * 서버는 다른 사용자의 리소스도 404 로 숨깁니다.
 */
export function isOccurrenceNotFoundError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

/**
 * 입력 검증 실패 (`VALIDATION_FAILED`) 또는 사용자 `UNCONFIRMED` 제출
 * (`CHECKIN_STATUS_NOT_USER_SETTABLE`).
 */
export function isCheckinValidationError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 422
}

/**
 * revision 충돌·취소된 occurrence·`Idempotency-Key` 충돌을 모두 포함하는 409.
 * 각각 후속 처리가 다르므로 revision 충돌은 `isCheckinRevisionConflictError` 로 구분합니다.
 */
export function isCheckinConflictError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409
}

/**
 * `CHECKIN_REVISION_CONFLICT`: 다른 경로에서 기록이 이미 바뀐 경우입니다.
 *
 * 이때 사용자가 입력한 값을 그대로 다시 보내 조용히 덮어쓰지 않습니다. 최신 Check-in 을
 * 다시 조회해 현재 `revision` 과 값을 사용자에게 보여주고, 사용자가 확인한 뒤에만
 * 그 `revision` 을 `expectedRevision` 으로 삼아 다시 제출합니다.
 */
export function isCheckinRevisionConflictError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    error.code === 'CHECKIN_REVISION_CONFLICT'
  )
}
