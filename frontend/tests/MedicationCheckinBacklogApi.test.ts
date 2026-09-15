import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import {
  getUnconfirmedCheckins,
  isUnconfirmedCursorNotFoundError,
  type UnconfirmedCheckinResponse,
} from '../src/api/medicationCheckinBacklog'

const cursor = '11111111-1111-4111-8111-111111111111'

function response(): UnconfirmedCheckinResponse {
  return {
    data: {
      items: [],
      next_cursor: null,
    },
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('UNCONFIRMED backlog API adapter', () => {
  it('uses the registered endpoint and omits an absent cursor', async () => {
    const body = response()
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getUnconfirmedCheckins()).resolves.toEqual(body)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/medication-checkins/unconfirmed?limit=20',
      expect.any(Object),
    )
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain('cursor=')
  })

  it('passes the contract limit and cursor without changing them', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(response()), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getUnconfirmedCheckins({ limit: 50, cursor })

    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/medication-checkins/unconfirmed?limit=50&cursor=${cursor}`,
      expect.any(Object),
    )
  })

  it('distinguishes cursor 404 from other hidden 404 responses', () => {
    expect(
      isUnconfirmedCursorNotFoundError(
        new ApiError(404, 'hidden', 'CHECKIN_CURSOR_NOT_FOUND'),
      ),
    ).toBe(true)
    expect(
      isUnconfirmedCursorNotFoundError(
        new ApiError(404, 'hidden', 'MEDICATION_OCCURRENCE_NOT_FOUND'),
      ),
    ).toBe(false)
    expect(isUnconfirmedCursorNotFoundError(new Error('boom'))).toBe(false)
  })
})
