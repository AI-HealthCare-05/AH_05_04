import { ApiError, apiRequest } from './client'
import { createIdempotencyKey, isValidIdempotencyKey } from './idempotency'

// Contract source of truth:
// - backend/app/apis/v1/medication_schedule_routers.py
// - backend/app/dtos/medication_schedules.py

export type MedicationScheduleEndMode = 'DATE' | 'OPEN_ENDED'
export type MedicationScheduleStatus = 'ACTIVE' | 'CANCELLED' | 'ENDED'
export type MedicationScheduleItemStatus =
  | 'READY'
  | 'SETUP_REQUIRED'
  | 'INACTIVE'
export type MedicationDayStatus =
  | 'READY'
  | 'PARTIAL'
  | 'SETUP_REQUIRED'
  | 'INACTIVE'
  | 'NO_ACTIVE_PRESCRIPTION'
export type MedicationOccurrenceStatus = 'PENDING' | 'CANCELLED' | 'CLOSED'
export type MedicationScheduleSetupReason =
  | 'UNSUPPORTED_SCHEDULE_PATTERN'
  | 'MISSING_START_DATE'
  | 'MISSING_EXACT_TIME'
  | 'MISSING_DURATION_DECISION'
  | 'USER_CONFIRMATION_REQUIRED'

export type MedicationCheckinSnapshot = {
  checkin_id: string
  occurrence_id: string
  status: 'TAKEN' | 'NOT_TAKEN' | 'UNCONFIRMED'
  taken_at: string | null
  revision: number
  corrected: boolean
}

export type MedicationScheduleData = {
  schedule_id: string
  prescription_version_medication_id: string
  revision: number
  status: MedicationScheduleStatus
  start_local_date: string
  end_mode: MedicationScheduleEndMode
  end_local_date: string | null
  local_times: string[]
}

export type MedicationScheduleResponse = {
  data: MedicationScheduleData
}

export type MedicationScheduleItem = {
  prescription_version_medication_id: string
  schedule_item_status: MedicationScheduleItemStatus
  schedule_id: string | null
  revision: number | null
  setup_reason: MedicationScheduleSetupReason | null
  schedule: MedicationScheduleData | null
}

export type MedicationOccurrenceData = {
  occurrence_id: string
  prescription_version_id: string
  prescription_version_medication_id: string
  scheduled_local_date: string
  scheduled_at: string
  confirmation_deadline_at: string
  status: MedicationOccurrenceStatus
  checkin: MedicationCheckinSnapshot | null
}

export type MedicationDayResponse = {
  data: {
    schedule_status: MedicationDayStatus
    schedule_items: MedicationScheduleItem[]
    occurrences: MedicationOccurrenceData[]
  }
}

export type MedicationOccurrenceMedicationResponse = {
  data: {
    occurrence_id: string
    prescription_version_id: string
    prescription_version_medication_id: string
    medication_name: string
    strength_text: string | null
    dose_value: number | null
    dose_unit: string | null
  }
}

type PutMedicationScheduleBaseInput = {
  startLocalDate: string
  localTimes: string[]
  expectedRevision: number
  recommendationContext?: RecommendationContext
}

export type PutMedicationScheduleInput = PutMedicationScheduleBaseInput &
  (
    | {
        endMode: 'DATE'
        endLocalDate: string
      }
    | {
        endMode: 'OPEN_ENDED'
        endLocalDate?: never
      }
  )

function requireIdempotencyKey(idempotencyKey: string): void {
  if (!isValidIdempotencyKey(idempotencyKey)) {
    throw new Error('Idempotency-Key does not satisfy the contract format')
  }
}

/** Schedule 변경의 한 논리적 시도에 사용할 멱등성 키를 만듭니다. */
export function createScheduleIdempotencyKey(): string {
  return createIdempotencyKey('schedule')
}

/** `GET /api/v1/medication-occurrences?date=YYYY-MM-DD` */
export async function getMedicationDay(
  localDate: string,
  signal?: AbortSignal,
): Promise<MedicationDayResponse> {
  const query = new URLSearchParams({ date: localDate })

  return apiRequest<MedicationDayResponse>(
    `/api/v1/medication-occurrences?${query.toString()}`,
    { method: 'GET', signal },
  )
}

/** `GET /api/v1/medication-occurrences/{occurrence_id}/medication` */
export async function getOccurrenceMedication(
  occurrenceId: string,
  signal?: AbortSignal,
): Promise<MedicationOccurrenceMedicationResponse> {
  return apiRequest<MedicationOccurrenceMedicationResponse>(
    `/api/v1/medication-occurrences/${occurrenceId}/medication`,
    { method: 'GET', signal },
  )
}

/** `PUT /api/v1/prescription-version-medications/{id}/schedule` */
export async function putMedicationSchedule(
  prescriptionVersionMedicationId: string,
  input: PutMedicationScheduleInput,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<MedicationScheduleResponse> {
  requireIdempotencyKey(idempotencyKey)

  if (input.endMode === 'DATE' && input.endLocalDate === undefined) {
    throw new Error('DATE schedule requires endLocalDate')
  }
  if (input.endMode === 'OPEN_ENDED' && 'endLocalDate' in input) {
    throw new Error('OPEN_ENDED schedule must not include endLocalDate')
  }

  const body = {
    start_local_date: input.startLocalDate,
    end_mode: input.endMode,
    ...(input.endMode === 'DATE' ? { end_local_date: input.endLocalDate } : {}),
    local_times: input.localTimes,
    expected_revision: input.expectedRevision,
    ...(input.recommendationContext ? { recommendation_context: input.recommendationContext } : {}),
  }

  return apiRequest<MedicationScheduleResponse>(
    `/api/v1/prescription-version-medications/${prescriptionVersionMedicationId}/schedule`,
    {
      method: 'PUT',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(body),
    },
  )
}

/** `PATCH /api/v1/prescription-version-medications/{id}/schedule` */
export async function cancelMedicationSchedule(
  prescriptionVersionMedicationId: string,
  expectedRevision: number,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<MedicationScheduleResponse> {
  requireIdempotencyKey(idempotencyKey)

  return apiRequest<MedicationScheduleResponse>(
    `/api/v1/prescription-version-medications/${prescriptionVersionMedicationId}/schedule`,
    {
      method: 'PATCH',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({
        status: 'CANCELLED',
        expected_revision: expectedRevision,
      }),
    },
  )
}

export function isScheduleRevisionConflictError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    error.code === 'SCHEDULE_REVISION_CONFLICT'
  )
}

export function isPrescriptionVersionConflictError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    error.code === 'PRESCRIPTION_VERSION_CONFLICT'
  )
}

export function isPrescriptionMedicationNotFoundError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 404 &&
    error.code === 'PRESCRIPTION_MEDICATION_NOT_FOUND'
  )
}

export function isOccurrenceMedicationNotFoundError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 404 &&
    error.code === 'MEDICATION_OCCURRENCE_NOT_FOUND'
  )
}

export type RecommendationInput = {
  meal_end_times: Partial<Record<'BREAKFAST' | 'LUNCH' | 'DINNER', string>>
  same_times_every_day: true
}
export type RecommendationContext = RecommendationInput & { rule_version: string }
export type RecommendationResponse = {
  data: {
    prescription_version_medication_id: string
    prescription_version_id: string
    rule_version: string
    timing_text: string | null
    local_times: string[]
    reason:
      | 'EXPLICIT_AFTER_MEAL'
      | 'UNSUPPORTED_INSTRUCTION'
      | 'FREQUENCY_MISMATCH'
      | 'MISSING_MEAL_END'
      | 'DAY_BOUNDARY'
      | 'DUPLICATE_TIME'
  }
}

export async function getScheduleRecommendation(
  medicationId: string,
  input: RecommendationInput,
  signal?: AbortSignal,
): Promise<RecommendationResponse> {
  return apiRequest(`/api/v1/prescription-version-medications/${medicationId}/schedule-recommendation`, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}
