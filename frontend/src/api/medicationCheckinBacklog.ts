import { ApiError, apiRequest } from './client'

// 계약 정본:
// - backend/app/apis/v1/medication_checkin_backlog_routers.py
// - backend/app/dtos/medication_checkin_backlog.py

export type UnconfirmedCheckinItem = {
  checkin_id: string
  occurrence_id: string
  prescription_id: string
  prescription_version_id: string
  prescription_version_medication_id: string
  medication_name: string
  strength_text: string | null
  scheduled_local_date: string
  scheduled_at: string
  confirmation_deadline_at: string
  status: 'UNCONFIRMED'
  revision: number
}

export type UnconfirmedCheckinResponse = {
  data: {
    items: UnconfirmedCheckinItem[]
    next_cursor: string | null
  }
}

export type GetUnconfirmedCheckinsInput = {
  limit?: number
  cursor?: string
  signal?: AbortSignal
}

export async function getUnconfirmedCheckins({
  limit = 20,
  cursor,
  signal,
}: GetUnconfirmedCheckinsInput = {}): Promise<UnconfirmedCheckinResponse> {
  const query = new URLSearchParams({ limit: String(limit) })
  if (cursor) query.set('cursor', cursor)

  return apiRequest<UnconfirmedCheckinResponse>(
    `/api/v1/medication-checkins/unconfirmed?${query.toString()}`,
    { signal },
  )
}

export function isUnconfirmedCursorNotFoundError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 404 &&
    error.code === 'CHECKIN_CURSOR_NOT_FOUND'
  )
}
