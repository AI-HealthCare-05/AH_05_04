import { afterEach, describe, expect, it, vi } from 'vitest'
import { getChatSessionForPrescription } from '../src/api/chat'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Chat rediscovery API', () => {
  it('확정 처방에 연결된 ACTIVE Chat session을 조회한다', async () => {
    const prescriptionId = '11111111-1111-4111-8111-111111111111'
    const responseBody = {
      data: {
        session_id: '22222222-2222-4222-8222-222222222222',
        prescription_id: prescriptionId,
        session_status: 'ACTIVE',
        created_at: '2026-09-08T00:00:00Z',
      },
    }
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      getChatSessionForPrescription(prescriptionId),
    ).resolves.toEqual(responseBody)
    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/prescriptions/${prescriptionId}/chat-session`,
      expect.any(Object),
    )
  })
})
