import type { Page, Route } from '@playwright/test'

const unreadNotificationId = '71111111-1111-4111-8111-111111111111'
const readNotificationId = '73333333-3333-4333-8333-333333333333'
const unreadOccurrenceId = '72222222-2222-4222-8222-222222222222'
const readOccurrenceId = '74444444-4444-4444-8444-444444444444'
const prescriptionVersionId = '75555555-5555-4555-8555-555555555555'
const prescriptionVersionMedicationId = '76666666-6666-4666-8666-666666666666'

export const notificationIds = {
  unreadNotificationId,
  readNotificationId,
  unreadOccurrenceId,
  readOccurrenceId,
  prescriptionVersionId,
  prescriptionVersionMedicationId,
}

type NotificationApiOptions = {
  deferNotificationList?: boolean
  deferOccurrenceDay?: boolean
  identityFailure?: boolean
}

export type NotificationApiState = {
  readPatchCount: number
  checkinMutationCount: number
  currentPrescriptionGetCount: number
  occurrenceDayDates: string[]
  occurrenceMedicationIds: string[]
  lastReadBody: unknown
  lastReadIdempotencyKey: string | null
  releaseNotificationList: () => void
  releaseOccurrenceDay: () => void
  waitForOccurrenceDayRequest: () => Promise<void>
  waitForOccurrenceDayRelease: () => Promise<void>
}

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'Cache-Control': 'no-store' },
    body: JSON.stringify(body),
  })
}

function occurrence(occurrenceId: string, localDate: string) {
  return {
    occurrence_id: occurrenceId,
    prescription_version_id: prescriptionVersionId,
    prescription_version_medication_id: prescriptionVersionMedicationId,
    scheduled_local_date: localDate,
    scheduled_at: `${localDate}T13:00:00+09:00`,
    confirmation_deadline_at: `${localDate}T18:00:00+09:00`,
    status: 'PENDING',
    checkin: null,
  }
}

export async function installNotificationApi(
  page: Page,
  options: NotificationApiOptions = {},
): Promise<NotificationApiState> {
  const notificationListGate = deferred()
  const occurrenceDayGate = deferred()
  const occurrenceDayRequested = deferred()
  const occurrenceDayReleased = deferred()
  let notificationListDeferred = options.deferNotificationList ?? false
  let occurrenceDayDeferred = options.deferOccurrenceDay ?? false

  const notifications = [
    {
      id: unreadNotificationId,
      occurrence_id: unreadOccurrenceId,
      occurrence_local_date: '2026-09-10',
      kind: 'REMINDER',
      scheduled_at: '2026-09-11T00:30:00Z',
      status: 'DELIVERED',
      delivered_at: '2026-09-11T00:30:01Z',
      read_at: null as string | null,
    },
    {
      id: readNotificationId,
      occurrence_id: readOccurrenceId,
      occurrence_local_date: '2026-09-09',
      kind: 'SCHEDULED',
      scheduled_at: '2026-09-09T04:00:00Z',
      status: 'DELIVERED',
      delivered_at: '2026-09-09T04:00:01Z',
      read_at: '2026-09-09T04:05:00Z',
    },
  ]

  const state: NotificationApiState = {
    readPatchCount: 0,
    checkinMutationCount: 0,
    currentPrescriptionGetCount: 0,
    occurrenceDayDates: [],
    occurrenceMedicationIds: [],
    lastReadBody: null,
    lastReadIdempotencyKey: null,
    releaseNotificationList: () => notificationListGate.resolve(),
    releaseOccurrenceDay: () => occurrenceDayGate.resolve(),
    waitForOccurrenceDayRequest: () => occurrenceDayRequested.promise,
    waitForOccurrenceDayRelease: () => occurrenceDayReleased.promise,
  }

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const method = request.method()
    const url = new URL(request.url())
    const path = url.pathname

    if (method === 'GET' && path === '/api/v1/notifications') {
      if (notificationListDeferred) {
        notificationListDeferred = false
        await notificationListGate.promise
      }
      return json(route, { data: { items: notifications, next_offset: null } })
    }

    const readMatch = path.match(/^\/api\/v1\/notifications\/([^/]+)\/read$/)
    if (method === 'PATCH' && readMatch) {
      state.readPatchCount += 1
      state.lastReadBody = request.postDataJSON()
      state.lastReadIdempotencyKey = request.headers()['idempotency-key'] ?? null
      const selected = notifications.find((item) => item.id === readMatch[1])
      return json(route, {
        data: {
          ...selected,
          read_at: '2026-09-11T01:00:00Z',
        },
      })
    }

    if (method === 'GET' && path === '/api/v1/medication-occurrences') {
      const localDate = url.searchParams.get('date') ?? ''
      state.occurrenceDayDates.push(localDate)
      occurrenceDayRequested.resolve()
      if (occurrenceDayDeferred) {
        occurrenceDayDeferred = false
        await occurrenceDayGate.promise
        occurrenceDayReleased.resolve()
      }
      const occurrenceId = localDate === '2026-09-10'
        ? unreadOccurrenceId
        : readOccurrenceId
      try {
        return await json(route, {
          data: {
            schedule_status: 'READY',
            schedule_items: [],
            occurrences: [occurrence(occurrenceId, localDate)],
          },
        })
      } catch (error) {
        if (!request.failure()) throw error
      }
      return undefined
    }

    const medicationMatch = path.match(
      /^\/api\/v1\/medication-occurrences\/([^/]+)\/medication$/,
    )
    if (method === 'GET' && medicationMatch) {
      const occurrenceId = medicationMatch[1] ?? ''
      state.occurrenceMedicationIds.push(occurrenceId)
      return json(route, {
        data: {
          occurrence_id: occurrenceId,
          prescription_version_id: prescriptionVersionId,
          prescription_version_medication_id: options.identityFailure
            ? '77777777-7777-4777-8777-777777777777'
            : prescriptionVersionMedicationId,
          medication_name: '합성 과거 처방약',
          strength_text: '10mg',
          dose_value: 1,
          dose_unit: '정',
        },
      })
    }

    if (method === 'GET' && path === '/api/v1/prescriptions/latest') {
      state.currentPrescriptionGetCount += 1
      return route.fallback()
    }

    if (
      ['PUT', 'PATCH'].includes(method) &&
      /^\/api\/v1\/medication-occurrences\/[^/]+\/check-in$/.test(path)
    ) {
      state.checkinMutationCount += 1
      return json(route, {
        code: 'UNEXPECTED_CHECKIN_MUTATION',
        message: 'Notification E2E must not mutate Check-in.',
        details: [],
        trace_id: 'synthetic-notification-e2e',
      }, 500)
    }

    return route.fallback()
  })

  return state
}
