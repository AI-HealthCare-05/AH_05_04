import { ApiError, apiRequest } from './client'
import { createIdempotencyKey, isValidIdempotencyKey } from './idempotency'

// Contract sources:
// - backend/app/apis/v1/notification_routers.py
// - backend/app/dtos/notifications.py

export type NotificationKind = 'SCHEDULED' | 'REMINDER'
export type NotificationStatus = 'DELIVERED'

export type NotificationData = {
  id: string
  occurrence_id: string
  occurrence_local_date: string
  kind: NotificationKind
  scheduled_at: string
  status: NotificationStatus
  delivered_at: string
  read_at: string | null
}

export type NotificationListResponse = {
  data: {
    items: NotificationData[]
    next_offset: number | null
  }
}

export type NotificationResponse = {
  data: NotificationData
}

export type ListNotificationsParams = {
  limit?: number
  offset?: number
  signal?: AbortSignal
}

/** Stable #138 route context retained independently of transient navigation state. */
export type NotificationOccurrenceHandoff = {
  occurrenceId: string
  occurrenceLocalDate: string
}

export async function listNotifications(
  params: ListNotificationsParams = {},
): Promise<NotificationListResponse> {
  const searchParams = new URLSearchParams()

  if (params.limit !== undefined) {
    searchParams.set('limit', String(params.limit))
  }
  if (params.offset !== undefined) {
    searchParams.set('offset', String(params.offset))
  }

  const query = searchParams.toString()

  return apiRequest<NotificationListResponse>(
    `/api/v1/notifications${query ? `?${query}` : ''}`,
    { signal: params.signal },
  )
}

export function createNotificationReadIdempotencyKey(): string {
  return createIdempotencyKey('notification-read')
}

export async function markNotificationRead(
  notificationId: string,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<NotificationResponse> {
  if (!isValidIdempotencyKey(idempotencyKey)) {
    throw new Error('Idempotency-Key does not satisfy the contract format')
  }

  return apiRequest<NotificationResponse>(
    `/api/v1/notifications/${notificationId}/read`,
    {
      method: 'PATCH',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({}),
    },
  )
}

/** Missing and non-SELF notifications intentionally share one 404 branch. */
export function isNotificationNotFoundError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

/** Copy the server-provided local date; never derive it from `scheduled_at`. */
export function createNotificationOccurrenceHandoff(
  notification: NotificationData,
): NotificationOccurrenceHandoff {
  return {
    occurrenceId: notification.occurrence_id,
    occurrenceLocalDate: notification.occurrence_local_date,
  }
}

/** Build the refresh-safe #138 Check-in route from the server-provided local date. */
export function createNotificationOccurrenceRoute(
  handoff: NotificationOccurrenceHandoff,
): string {
  return `/schedule/occurrences/${encodeURIComponent(handoff.occurrenceId)}?date=${encodeURIComponent(handoff.occurrenceLocalDate)}`
}
