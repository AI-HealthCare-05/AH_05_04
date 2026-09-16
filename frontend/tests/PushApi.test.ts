import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  deletePushSubscription,
  getPushConfig,
  upsertPushSubscription,
} from '../src/api/push'

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('Web Push API', () => {
  it('승인된 #469 endpoint와 exact DTO만 사용한다', async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { public_key: 'fixture-public-key' } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { id: 'subscription-id', generation: 'generation-id' } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem('access_token', 'fixture-access-token')

    await getPushConfig()
    await upsertPushSubscription({
      endpoint: 'https://push.example.test/subscription',
      keys: { p256dh: 'synthetic-p256dh', auth: 'synthetic-auth' },
    })
    await deletePushSubscription('subscription-id')

    expect(fetchMock.mock.calls[0][0]).toBe('http://localhost:8000/api/v1/push/config')
    expect(fetchMock.mock.calls[1][0]).toBe('http://localhost:8000/api/v1/push/subscriptions')
    expect(fetchMock.mock.calls[1][1]?.method).toBe('PUT')
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      endpoint: 'https://push.example.test/subscription',
      keys: { p256dh: 'synthetic-p256dh', auth: 'synthetic-auth' },
    })
    expect(fetchMock.mock.calls[2][0]).toBe('http://localhost:8000/api/v1/push/subscriptions/subscription-id')
    expect(fetchMock.mock.calls[2][1]?.method).toBe('DELETE')
  })
})
