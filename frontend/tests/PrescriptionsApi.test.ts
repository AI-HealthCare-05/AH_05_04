import { afterEach, describe, expect, it, vi } from 'vitest'
import { getOcrJob, type OcrJobResponse } from '../src/api/prescriptions'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('OCR Job API', () => {
  it('polling AbortSignal을 기존 OCR 조회 endpoint까지 전달한다', async () => {
    const responseBody: OcrJobResponse = {
      data: {
        job_id: '22222222-2222-4222-8222-222222222222',
        document_id: '11111111-1111-4111-8111-111111111111',
        ocr_status: 'PROCESSING',
        error_code: null,
        error_message: null,
        engine_name: null,
        model_version: null,
        prompt_version: null,
        created_at: '2026-08-31T00:00:00Z',
        completed_at: null,
        fields: [],
      },
    }
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const controller = new AbortController()
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      getOcrJob(responseBody.data.job_id, controller.signal),
    ).resolves.toEqual(responseBody)
    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/ocr-jobs/${responseBody.data.job_id}`,
      expect.objectContaining({ signal: controller.signal }),
    )
  })
})
