import { afterEach, describe, expect, it, vi } from 'vitest'
import { getGuideForPrescription } from '../src/api/guides'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Guide rediscovery API', () => {
  it('확정 처방에 연결된 Guide를 기존 GuideResponse로 조회한다', async () => {
    const prescriptionId = '44444444-4444-4444-8444-444444444444'
    const responseBody = {
      data: {
        guide_id: '55555555-5555-4555-8555-555555555555',
        prescription_id: prescriptionId,
        generation_status: 'COMPLETED',
        content: '합성 복약 가이드',
        model_name: 'guide-model',
        prompt_version: 'guide-prompt-v1',
        requested_at: '2026-09-07T08:00:00Z',
        completed_at: '2026-09-07T08:00:03Z',
      },
    }
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getGuideForPrescription(prescriptionId)).resolves.toEqual(
      responseBody,
    )
    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/prescriptions/${prescriptionId}/guide`,
      expect.any(Object),
    )
  })
})
