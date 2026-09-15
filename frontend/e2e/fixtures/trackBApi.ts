import type { Page, Route } from '@playwright/test'

const occurrenceId = '11111111-1111-4111-8111-111111111111'
const checkinId = '22222222-2222-4222-8222-222222222222'
const prescriptionId = '33333333-3333-4333-8333-333333333333'
const prescriptionVersionId = '44444444-4444-4444-8444-444444444444'
const prescriptionVersionMedicationId = '55555555-5555-4555-8555-555555555555'

export const trackBIds = {
  occurrenceId,
  checkinId,
  prescriptionId,
  prescriptionVersionId,
  prescriptionVersionMedicationId,
}

export const longHistoricalMedicationName =
  '합성과거처방기준초장문무공백복합제제명ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'

type BacklogMode = 'populated' | 'empty' | 'error' | 'loading'
type OccurrenceDayMode = 'success' | 'error-once' | 'error'

type TrackBApiOptions = {
  backlogMode?: BacklogMode
  medicationError?: boolean
  occurrenceDayMode?: OccurrenceDayMode
}

export type TrackBApiState = {
  unconfirmedGetCount: number
  unconfirmedLimitOneGetCount: number
  occurrenceDayGetCount: number
  occurrenceMedicationGetCount: number
  checkinPutCount: number
  setBacklogMode: (mode: BacklogMode) => void
  setMedicationError: (value: boolean) => void
  setOccurrenceDayMode: (mode: OccurrenceDayMode) => void
  releaseBacklog: () => void
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'Cache-Control': 'no-store' },
    body: JSON.stringify(body),
  })
}

function error(route: Route, status: number, code: string) {
  return json(route, {
    code,
    message: 'synthetic Track B failure',
    details: [],
    trace_id: 'synthetic-track-b-trace',
  }, status)
}

const backlogItem = {
  checkin_id: checkinId,
  occurrence_id: occurrenceId,
  prescription_id: prescriptionId,
  prescription_version_id: prescriptionVersionId,
  prescription_version_medication_id: prescriptionVersionMedicationId,
  medication_name: '목록 합성 과거약',
  strength_text: '10mg',
  scheduled_local_date: '2026-09-04',
  scheduled_at: '2026-09-04T04:00:00Z',
  confirmation_deadline_at: '2026-09-04T09:00:00Z',
  status: 'UNCONFIRMED',
  revision: 1,
}

const occurrence = {
  occurrence_id: occurrenceId,
  prescription_version_id: prescriptionVersionId,
  prescription_version_medication_id: prescriptionVersionMedicationId,
  scheduled_local_date: '2026-09-04',
  scheduled_at: '2026-09-04T04:00:00Z',
  confirmation_deadline_at: '2026-09-04T09:00:00Z',
  status: 'CLOSED',
  checkin: {
    checkin_id: checkinId,
    occurrence_id: occurrenceId,
    status: 'UNCONFIRMED',
    taken_at: null,
    revision: 1,
    corrected: false,
  },
}

const medication = {
  occurrence_id: occurrenceId,
  prescription_version_id: prescriptionVersionId,
  prescription_version_medication_id: prescriptionVersionMedicationId,
  medication_name: longHistoricalMedicationName,
  strength_text: '10mg',
  dose_value: 1,
  dose_unit: '정',
}

export async function installTrackBApi(
  page: Page,
  options: TrackBApiOptions = {},
): Promise<TrackBApiState> {
  let backlogMode = options.backlogMode ?? 'populated'
  let occurrenceDayMode = options.occurrenceDayMode ?? 'success'
  let medicationError = options.medicationError ?? false
  const releaseLoadingBacklogs: Array<() => void> = []

  const state: TrackBApiState = {
    unconfirmedGetCount: 0,
    unconfirmedLimitOneGetCount: 0,
    occurrenceDayGetCount: 0,
    occurrenceMedicationGetCount: 0,
    checkinPutCount: 0,
    setBacklogMode: (mode) => { backlogMode = mode },
    setMedicationError: (value) => { medicationError = value },
    setOccurrenceDayMode: (mode) => { occurrenceDayMode = mode },
    releaseBacklog: () => releaseLoadingBacklogs.splice(0).forEach((release) => release()),
  }

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const method = request.method()
    const url = new URL(request.url())
    const path = url.pathname

    if (method === 'GET' && path === '/api/v1/medication-checkins/unconfirmed') {
      state.unconfirmedGetCount += 1
      if (url.searchParams.get('limit') === '1') {
        state.unconfirmedLimitOneGetCount += 1
      }
      if (backlogMode === 'loading') {
        await new Promise<void>((resolve) => { releaseLoadingBacklogs.push(resolve) })
      }
      if (backlogMode === 'error') {
        return error(route, 503, 'SERVICE_UNAVAILABLE')
      }
      return json(route, {
        data: {
          items: backlogMode === 'empty' ? [] : [backlogItem],
          next_cursor: null,
        },
      })
    }

    if (method === 'GET' && path === `/api/v1/medication-occurrences/${occurrenceId}/medication`) {
      state.occurrenceMedicationGetCount += 1
      if (medicationError) {
        return error(route, 404, 'MEDICATION_OCCURRENCE_NOT_FOUND')
      }
      return json(route, { data: medication })
    }

    if (method === 'GET' && path === '/api/v1/medication-occurrences') {
      state.occurrenceDayGetCount += 1
      if (occurrenceDayMode === 'error' || occurrenceDayMode === 'error-once') {
        if (occurrenceDayMode === 'error-once') occurrenceDayMode = 'success'
        return error(route, 503, 'SERVICE_UNAVAILABLE')
      }
      return json(route, {
        data: {
          schedule_status: 'READY',
          schedule_items: [],
          occurrences: [occurrence],
        },
      })
    }

    if (method === 'PUT' && path === `/api/v1/medication-occurrences/${occurrenceId}/check-in`) {
      state.checkinPutCount += 1
      return json(route, {
        data: {
          checkin_id: checkinId,
          occurrence_id: occurrenceId,
          status: 'TAKEN',
          taken_at: null,
          revision: 2,
          corrected: true,
        },
      })
    }

    return route.fallback()
  })

  return state
}
