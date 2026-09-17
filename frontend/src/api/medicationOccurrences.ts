import { ApiError, apiRequest } from './client'
import type { MedicationCheckinResponse } from './medicationCheckins'
import type { MedicationScheduleData } from './medicationSchedules'
import type { NotificationOccurrenceHandoff } from './notifications'

// Contract sources:
// - backend/app/apis/v1/medication_schedule_routers.py
// - backend/app/dtos/medication_schedules.py

export type MedicationScheduleItemStatus =
  | 'READY'
  | 'SETUP_REQUIRED'
  | 'INACTIVE'

export type MedicationScheduleSetupReason =
  | 'UNSUPPORTED_SCHEDULE_PATTERN'
  | 'MISSING_START_DATE'
  | 'MISSING_EXACT_TIME'
  | 'MISSING_DURATION_DECISION'
  | 'USER_CONFIRMATION_REQUIRED'

export type MedicationOccurrenceStatus = 'PENDING' | 'CANCELLED' | 'CLOSED'

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
  checkin: MedicationCheckinResponse['data'] | null
}

export type MedicationDayScheduleStatus =
  | 'READY'
  | 'PARTIAL'
  | 'SETUP_REQUIRED'
  | 'INACTIVE'
  | 'NO_ACTIVE_PRESCRIPTION'

export type MedicationDayResponse = {
  data: {
    schedule_status: MedicationDayScheduleStatus
    schedule_items: MedicationScheduleItem[]
    occurrences: MedicationOccurrenceData[]
  }
}

export type MedicationOccurrenceMedicationData = {
  occurrence_id: string
  prescription_version_id: string
  prescription_version_medication_id: string
  medication_name: string
  strength_text: string | null
  dose_value: number | null
  dose_unit: string | null
}

export type MedicationOccurrenceMedicationResponse = {
  data: MedicationOccurrenceMedicationData
}

export type ResolvedOccurrenceMedication = {
  occurrenceLocalDate: string
  occurrence: MedicationOccurrenceData
  medication: MedicationOccurrenceMedicationData
}

export class OccurrenceMedicationUnavailableError extends Error {
  constructor() {
    super('The original occurrence medication could not be loaded.')
    this.name = 'OccurrenceMedicationUnavailableError'
  }
}

export async function getMedicationOccurrencesByDate(
  localDate: string,
  signal?: AbortSignal,
): Promise<MedicationDayResponse> {
  return apiRequest<MedicationDayResponse>(
    `/api/v1/medication-occurrences?date=${encodeURIComponent(localDate)}`,
    { signal },
  )
}

export async function getMedicationOccurrenceMedication(
  occurrenceId: string,
  signal?: AbortSignal,
): Promise<MedicationOccurrenceMedicationResponse> {
  return apiRequest<MedicationOccurrenceMedicationResponse>(
    `/api/v1/medication-occurrences/${occurrenceId}/medication`,
    { signal },
  )
}

export function isMedicationOccurrenceNotFoundError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

export function isOccurrenceMedicationUnavailableError(
  error: unknown,
): error is OccurrenceMedicationUnavailableError {
  return error instanceof OccurrenceMedicationUnavailableError
}

/**
 * Resolve only the immutable occurrence snapshot referenced by a notification.
 * The notification date, occurrence, and medication detail must agree on all IDs.
 * Missing or mismatched data never falls back to the current prescription.
 */
export async function resolveNotificationOccurrenceMedication(
  handoff: NotificationOccurrenceHandoff,
  signal?: AbortSignal,
): Promise<ResolvedOccurrenceMedication> {
  const day = await getMedicationOccurrencesByDate(
    handoff.occurrenceLocalDate,
    signal,
  )
  const occurrence = day.data.occurrences.find(
    (item) => item.occurrence_id === handoff.occurrenceId,
  )

  if (!occurrence) {
    throw new OccurrenceMedicationUnavailableError()
  }

  if (occurrence.scheduled_local_date !== handoff.occurrenceLocalDate) {
    throw new OccurrenceMedicationUnavailableError()
  }

  let medicationResponse: MedicationOccurrenceMedicationResponse
  try {
    medicationResponse = await getMedicationOccurrenceMedication(
      handoff.occurrenceId,
      signal,
    )
  } catch (error) {
    if (isMedicationOccurrenceNotFoundError(error)) {
      throw new OccurrenceMedicationUnavailableError()
    }
    throw error
  }

  const medication = medicationResponse.data
  if (
    medication.occurrence_id !== occurrence.occurrence_id ||
    medication.prescription_version_id !== occurrence.prescription_version_id ||
    medication.prescription_version_medication_id !==
      occurrence.prescription_version_medication_id
  ) {
    throw new OccurrenceMedicationUnavailableError()
  }

  return {
    occurrenceLocalDate: handoff.occurrenceLocalDate,
    occurrence,
    medication,
  }
}
