import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import {
  type NotificationData,
  createNotificationOccurrenceHandoff,
  createNotificationOccurrenceRoute,
  createNotificationReadIdempotencyKey,
  isNotificationNotFoundError,
  listNotifications,
  markNotificationRead,
} from '../src/api/notifications'
import { IDEMPOTENCY_KEY_PATTERN } from '../src/api/idempotency'

const notificationId = '11111111-1111-4111-8111-111111111111'
const occurrenceId = '22222222-2222-4222-8222-222222222222'
const idempotencyKey =
  'notification-read:33333333-3333-4333-8333-333333333333'

function makeNotification(
  overrides: Partial<NotificationData> = {},
): NotificationData {
  return {
    id: notificationId,
    occurrence_id: occurrenceId,
    occurrence_local_date: '2026-09-13',
    kind: 'SCHEDULED',
    scheduled_at: '2026-09-14T00:30:00Z',
    status: 'DELIVERED',
    delivered_at: '2026-09-14T00:30:01Z',
    read_at: null,
    ...overrides,
  }
}

function stubJson(body: unknown, status = 200) {
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
  return { code, message: 'error', details: [], trace_id: 'trace-1' }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('notification API adapter', () => {
  it('lists the merged DTO without changing unread state', async () => {
    const responseBody = {
      data: { items: [makeNotification()], next_offset: null },
    }
    const fetchMock = stubJson(responseBody)

    await expect(listNotifications()).resolves.toEqual(responseBody)
    expect(responseBody.data.items[0]?.read_at).toBeNull()
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/notifications',
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('passes limit and offset as the list contract query', async () => {
    const fetchMock = stubJson({
      data: { items: [], next_offset: null },
    })

    await listNotifications({ limit: 50, offset: 100 })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/notifications?limit=50&offset=100',
      expect.any(Object),
    )
  })

  it('marks read with an exact empty object body and caller-owned key', async () => {
    const responseBody = {
      data: makeNotification({ read_at: '2026-09-14T00:31:00Z' }),
    }
    const fetchMock = stubJson(responseBody)

    await expect(
      markNotificationRead(notificationId, idempotencyKey),
    ).resolves.toEqual(responseBody)

    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/notifications/${notificationId}/read`,
      expect.objectContaining({
        method: 'PATCH',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        }),
        body: '{}',
      }),
    )
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({})
  })

  it('rejects an invalid idempotency key before sending a request', async () => {
    const fetchMock = stubJson({ data: makeNotification() })

    await expect(
      markNotificationRead(notificationId, 'short'),
    ).rejects.toThrow(/Idempotency-Key/)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('creates a contract-valid read idempotency key', () => {
    const key = createNotificationReadIdempotencyKey()
    expect(key).toMatch(IDEMPOTENCY_KEY_PATTERN)
    expect(key).toMatch(/^notification-read:/)
  })

  it('preserves occurrence_local_date instead of deriving from scheduled_at', () => {
    const notification = makeNotification({
      occurrence_local_date: '2026-09-13',
      scheduled_at: '2026-09-14T00:30:00Z',
    })

    const handoff = createNotificationOccurrenceHandoff(notification)
    expect(handoff).toEqual({
      occurrenceId,
      occurrenceLocalDate: '2026-09-13',
    })
    expect(createNotificationOccurrenceRoute(handoff)).toBe(
      `/schedule/occurrences/${occurrenceId}?date=2026-09-13`,
    )
  })
})

describe('notification API errors', () => {
  it('surfaces 401 without converting it to a not-found state', async () => {
    stubJson(makeErrorBody('UNAUTHORIZED'), 401)

    const error = await listNotifications().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(401)
    expect(isNotificationNotFoundError(error)).toBe(false)
  })

  it.each(['NOTIFICATION_NOT_FOUND', 'ANY_HIDDEN_RESOURCE_CODE'])(
    'uses one neutral 404 branch for %s',
    async (code) => {
      stubJson(makeErrorBody(code), 404)

      const error = await markNotificationRead(
        notificationId,
        idempotencyKey,
      ).catch((caught: unknown) => caught)

      expect(isNotificationNotFoundError(error)).toBe(true)
    },
  )

  it('preserves server errors for retry handling', async () => {
    stubJson(makeErrorBody('SERVICE_UNAVAILABLE'), 503)

    await expect(listNotifications()).rejects.toMatchObject({
      status: 503,
      code: 'SERVICE_UNAVAILABLE',
    })
  })

  it('preserves network failures for retry handling', async () => {
    const networkError = new TypeError('Failed to fetch')
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockRejectedValue(networkError))

    await expect(listNotifications()).rejects.toBe(networkError)
  })
})
