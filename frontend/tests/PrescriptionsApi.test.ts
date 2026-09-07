import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import {
  executeOcr,
  getJobStatus,
  getOcrJob,
  getOcrResult,
  isJobStatusResponse,
  isLegacyOcrJobResponse,
  type OcrJobResponse,
} from '../src/api/prescriptions'
import type { JobStatusResponse } from '../src/types/jobs'

const jobId = '22222222-2222-4222-8222-222222222222'
const documentId = '11111111-1111-4111-8111-111111111111'

function makeJobStatus(
  overrides: Partial<JobStatusResponse['data']> = {},
): JobStatusResponse {
  return {
    data: {
      job_id: jobId,
      job_type: 'OCR',
      status: 'PENDING',
      domain_type: 'OCR_JOB',
      domain_id: '33333333-3333-4333-8333-333333333333',
      prescription_version_id: null,
      status_url: `/api/v1/jobs/${jobId}`,
      result_url: null,
      retry_after_seconds: null,
      error: null,
      created_at: '2026-09-06T00:00:00Z',
      updated_at: '2026-09-06T00:00:00Z',
      ...overrides,
    },
  }
}

function makeLegacyOcrJob(): OcrJobResponse {
  return {
    data: {
      job_id: jobId,
      document_id: documentId,
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
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('OCR Job API', () => {
  it('sends a valid Idempotency-Key and AbortSignal with OCR intake', async () => {
    const responseBody = makeJobStatus()
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const controller = new AbortController()
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      executeOcr(documentId, 'ocr.189.intent:0001', controller.signal),
    ).resolves.toEqual(responseBody)
    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/documents/${documentId}/ocr-jobs`,
      expect.objectContaining({
        method: 'POST',
        signal: controller.signal,
        headers: expect.objectContaining({
          'Idempotency-Key': 'ocr.189.intent:0001',
        }),
      }),
    )
  })

  it('distinguishes the new async intake response from the legacy response', () => {
    const asyncResponse = makeJobStatus()
    const legacyResponse = makeLegacyOcrJob()

    expect(isJobStatusResponse(asyncResponse)).toBe(true)
    expect(isLegacyOcrJobResponse(asyncResponse)).toBe(false)
    expect(isJobStatusResponse(legacyResponse)).toBe(false)
    expect(isLegacyOcrJobResponse(legacyResponse)).toBe(true)
  })

  it('uses an opaque relative status_url and prefers body retry_after_seconds', async () => {
    const responseBody = makeJobStatus({
      status: 'RETRY_WAIT',
      retry_after_seconds: 7,
    })
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Retry-After': '7',
        },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      getJobStatus(responseBody.data.status_url),
    ).resolves.toEqual({ body: responseBody, retryAfterSeconds: 7 })
    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000${responseBody.data.status_url}`,
      expect.any(Object),
    )
  })

  it('fails closed when Retry-After disagrees with retry_after_seconds', async () => {
    const responseBody = makeJobStatus({
      status: 'RETRY_WAIT',
      retry_after_seconds: 7,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(JSON.stringify(responseBody), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'Retry-After': '11',
          },
        }),
      ),
    )

    await expect(
      getJobStatus(responseBody.data.status_url),
    ).rejects.toThrow('Retry-After header does not match retry_after_seconds')
  })

  it('reads Retry-After metadata when the body omits it', async () => {
    const responseBody = makeJobStatus({ status: 'RETRY_WAIT' })
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(JSON.stringify(responseBody), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'Retry-After': '13',
          },
        }),
      ),
    )

    await expect(getJobStatus(responseBody.data.status_url)).resolves.toEqual({
      body: responseBody,
      retryAfterSeconds: 13,
    })
  })

  it('accepts a same-origin absolute result_url without rebuilding it', async () => {
    const result = makeLegacyOcrJob()
    const resultUrl = `http://localhost:8000/api/v1/ocr-jobs/${jobId}`
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(result), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getOcrResult(resultUrl)).resolves.toEqual(result)
    expect(fetchMock).toHaveBeenCalledWith(resultUrl, expect.any(Object))
  })

  it('rejects a cross-origin backend-provided URL before fetching', async () => {
    const fetchMock = vi.fn<typeof fetch>()
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      getJobStatus(`https://example.invalid/api/v1/jobs/${jobId}`),
    ).rejects.toThrow('Cross-origin API response URL is not allowed')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('passes polling AbortSignal to the legacy OCR endpoint', async () => {
    const responseBody = makeLegacyOcrJob()
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

  it.each([
    [400, 'IDEMPOTENCY_KEY_REQUIRED'],
    [400, 'IDEMPOTENCY_KEY_INVALID'],
    [409, 'IDEMPOTENCY_KEY_CONFLICT'],
    [409, 'OCR_JOB_ALREADY_PROCESSING'],
  ])('preserves %i %s error envelopes', async (status, code) => {
    const errorBody = {
      code,
      message: 'backend message',
      details: [{ field: 'Idempotency-Key', reason: code }],
      trace_id: 'a1b2c3d4e5f647890123456789abcdef',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(JSON.stringify(errorBody), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    const error = await executeOcr(documentId, 'ocr.189.intent:0001').catch(
      (caught: unknown) => caught,
    )

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status,
      code,
      message: errorBody.message,
      details: errorBody.details,
      traceId: errorBody.trace_id,
    })
  })
})
