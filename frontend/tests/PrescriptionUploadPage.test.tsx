import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ApiError } from '../src/api/client'
import {
  executeOcr,
  getJobStatus,
  getOcrJob,
  getOcrResult,
  uploadPrescription,
  type JobStatusResponse,
} from '../src/api/prescriptions'
import PrescriptionUploadPage from '../src/pages/PrescriptionUploadPage'

const mvpPageStyles = readFileSync(
  join(process.cwd(), 'src/pages/MvpPages.css'),
  'utf8',
)

vi.mock('../src/api/prescriptions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/prescriptions')>()

  return {
    ...actual,
    uploadPrescription: vi.fn(),
    executeOcr: vi.fn(),
    getJobStatus: vi.fn(),
    getOcrJob: vi.fn(),
    getOcrResult: vi.fn(),
  }
})

const documentId = '11111111-1111-4111-8111-111111111111'
const commonJobId = '22222222-2222-4222-8222-222222222222'
const ocrJobId = '33333333-3333-4333-8333-333333333333'
const statusUrl = `/api/v1/jobs/${commonJobId}`
const resultUrl = `/api/v1/ocr-jobs/${ocrJobId}`

function ocrResponse(status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED') {
  return {
    data: {
      job_id: ocrJobId,
      document_id: documentId,
      ocr_status: status,
      error_code: status === 'FAILED' ? 'OCR_FAILED' : null,
      error_message:
        status === 'FAILED'
          ? '민감한 OCR 원문과 내부 오류 상세를 포함한 Backend 메시지'
          : null,
      created_at: '2026-08-24T00:00:00Z',
      completed_at:
        status === 'COMPLETED' || status === 'FAILED'
          ? '2026-08-24T00:00:03Z'
          : null,
      fields: [],
    },
  }
}

function jobStatusResponse(
  status: JobStatusResponse['data']['status'],
  overrides: Partial<JobStatusResponse['data']> = {},
): JobStatusResponse {
  return {
    data: {
      job_id: commonJobId,
      job_type: 'OCR',
      status,
      domain_type: 'OCR_JOB',
      domain_id: ocrJobId,
      prescription_version_id: null,
      status_url: statusUrl,
      result_url: status === 'COMPLETED' ? resultUrl : null,
      retry_after_seconds: null,
      error: null,
      created_at: '2026-09-06T00:00:00Z',
      updated_at: '2026-09-06T00:00:01Z',
      ...overrides,
    },
  }
}

function polledJobStatus(
  status: JobStatusResponse['data']['status'],
  overrides: Partial<JobStatusResponse['data']> = {},
) {
  return {
    body: jobStatusResponse(status, overrides),
    retryAfterSeconds: overrides.retry_after_seconds ?? null,
  }
}

function renderPage() {
  function ReviewRoute() {
    const location = useLocation()

    return <div>OCR 검수 화면 {location.search}</div>
  }

  return render(
    <MemoryRouter initialEntries={['/prescriptions/upload']}>
      <Routes>
        <Route path="/prescriptions/upload" element={<PrescriptionUploadPage />} />
        <Route path="/prescriptions/review" element={<ReviewRoute />} />
        <Route path="/login" element={<div>로그인 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function selectPrescriptionFile(
  container: HTMLElement,
  filename = 'prescription.png',
) {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')
  if (!input) throw new Error('file input not found')
  fireEvent.change(input, {
    target: { files: [new File(['prescription'], filename, { type: 'image/png' })] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(uploadPrescription).mockResolvedValue({
    data: {
      document_id: documentId,
      upload_status: 'UPLOADED',
      uploaded_at: '2026-08-24T00:00:00Z',
    },
    message: 'uploaded',
  })
  vi.mocked(executeOcr).mockResolvedValue(jobStatusResponse('PENDING'))
})

afterEach(() => {
  vi.restoreAllMocks()
  cleanup()
})

describe('PrescriptionUploadPage OCR polling', () => {
  it('#227 긴 파일명을 기본 2줄로 제한하고 전체 파일명과 확장자를 펼쳐 확인할 수 있다', () => {
    const longFilename =
      'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_FINAL-2026.png'
    const { container } = renderPage()

    selectPrescriptionFile(container, longFilename)

    const filename = screen.getByText(longFilename)
    expect(filename.classList.contains('mvp-upload__filename')).toBe(true)
    expect(filename.classList.contains('mvp-upload__filename--expanded')).toBe(false)
    expect(filename.textContent).toBe(longFilename)
    const filenameRule = mvpPageStyles.match(
      /\.mvp-upload__filename\s*\{([^}]*)\}/,
    )?.[1]
    expect(filenameRule).toContain('overflow: hidden')
    expect(filenameRule).toContain('overflow-wrap: anywhere')
    expect(filenameRule).toContain('word-break: break-word')
    expect(filenameRule).toContain('-webkit-line-clamp: 2')

    const toggle = screen.getByRole('button', { name: '전체 파일명 보기' })
    expect(toggle.tagName).toBe('BUTTON')
    expect(toggle.getAttribute('type')).toBe('button')
    expect(toggle.tabIndex).toBe(0)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.getAttribute('aria-controls')).toBe(filename.id)

    fireEvent.click(toggle)

    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('button', { name: '파일명 접기' })).toBeTruthy()
    expect(filename.classList.contains('mvp-upload__filename--expanded')).toBe(true)
    expect(filename.textContent?.endsWith('.png')).toBe(true)
    const expandedFilenameRule = mvpPageStyles.match(
      /\.mvp-upload__filename--expanded\s*\{([^}]*)\}/,
    )?.[1]
    expect(expandedFilenameRule).toContain('display: block')
    expect(expandedFilenameRule).toContain('overflow: visible')

    fireEvent.click(screen.getByRole('button', { name: '파일명 접기' }))

    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(filename.classList.contains('mvp-upload__filename--expanded')).toBe(false)
  })

  it('PENDING → PROCESSING → COMPLETED 후 document_id와 job_id를 유지해 review route로 이동한다', async () => {
    vi.mocked(getJobStatus)
      .mockResolvedValueOnce(polledJobStatus('PENDING'))
      .mockResolvedValueOnce(polledJobStatus('PROCESSING'))
      .mockResolvedValueOnce(polledJobStatus('COMPLETED'))
    vi.mocked(getOcrResult).mockResolvedValue(ocrResponse('COMPLETED'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(
      await screen.findByText(/OCR 검수 화면/, {}, { timeout: 3500 }),
    ).toBeTruthy()
    expect(uploadPrescription).toHaveBeenCalledTimes(1)
    const idempotencyKey = vi.mocked(executeOcr).mock.calls[0]?.[1]
    expect(idempotencyKey).toMatch(/^ocr:[A-Za-z0-9._:-]{16,}$/)
    expect(executeOcr).toHaveBeenCalledWith(
      documentId,
      idempotencyKey,
      expect.any(AbortSignal),
    )
    expect(getJobStatus).toHaveBeenCalledTimes(3)
    expect(getJobStatus).toHaveBeenCalledWith(
      statusUrl,
      expect.any(AbortSignal),
    )
    expect(getOcrResult).toHaveBeenCalledWith(
      resultUrl,
      expect.any(AbortSignal),
    )
    expect(screen.getByText(new RegExp(`document_id=${documentId}`))).toBeTruthy()
    expect(screen.getByText(new RegExp(`job_id=${ocrJobId}`))).toBeTruthy()
  })

  it('PROCESSING → FAILED unknown code에서는 polling을 중단하고 안전한 fallback UI를 표시한다', async () => {
    vi.mocked(getJobStatus)
      .mockResolvedValueOnce(polledJobStatus('PROCESSING'))
      .mockResolvedValueOnce(polledJobStatus('FAILED', {
        error: {
          code: 'INTERNAL_ERROR',
          message: '민감한 OCR 원문과 내부 오류 상세를 포함한 Backend 메시지',
        },
      }))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(
      await screen.findByText('작업을 완료하지 못했어요', {}, { timeout: 2500 }),
    ).toBeTruthy()
    await waitFor(() => expect(getJobStatus).toHaveBeenCalledTimes(2))
    expect(
      screen.getByText(
        '현재 작업을 완료하지 못했어요. 이전 화면에서 다시 확인해 주세요.',
      ),
    ).toBeTruthy()
    expect(screen.queryByText('OCR_FAILED')).toBeNull()
    expect(
      screen.queryByText(/민감한 OCR 원문|내부 오류 상세/),
    ).toBeNull()
    expect(screen.queryByText(/OCR 검수 화면/)).toBeNull()
  })

  it('polling network failure를 안전한 연결 오류 UI로 표시한다', async () => {
    vi.mocked(getJobStatus).mockRejectedValue(new TypeError('Failed to fetch'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByText('네트워크 연결을 확인해 주세요')).toBeTruthy()
    expect(getJobStatus).toHaveBeenCalledTimes(1)
  })

  it('polling 5xx를 Backend FAILED와 구분한 서버 오류 UI로 표시한다', async () => {
    vi.mocked(getJobStatus).mockRejectedValue(
      new ApiError(503, 'provider detail', 'DEPENDENCY_DOWN'),
    )
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByText('서버 응답이 원활하지 않아요')).toBeTruthy()
    expect(screen.queryByText('provider detail')).toBeNull()
  })

  it('STALE을 과거 결과로 소비하지 않고 명시적 재등록 경계로 종료한다', async () => {
    vi.mocked(getJobStatus).mockResolvedValue(polledJobStatus('STALE'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByText('최신 정보 확인이 필요해요')).toBeTruthy()
    expect(screen.getByRole('button', { name: '최신 정보 확인하기' })).toBeTruthy()
    expect(getJobStatus).toHaveBeenCalledTimes(1)
    expect(getOcrResult).not.toHaveBeenCalled()
  })

  it('COMPLETED에 result_url이 없으면 검수로 이동하지 않고 fail-closed 한다', async () => {
    vi.mocked(getJobStatus).mockResolvedValue(
      polledJobStatus('COMPLETED', { result_url: null }),
    )
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByText('작업 상태를 확인하지 못했어요')).toBeTruthy()
    expect(getOcrResult).not.toHaveBeenCalled()
    expect(screen.queryByText(/OCR 검수 화면/)).toBeNull()
  })

  it('네트워크 오류로 같은 OCR 접수를 다시 보낼 때 문서와 Idempotency-Key를 재사용한다', async () => {
    vi.mocked(executeOcr)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jobStatusResponse('PENDING'))
    vi.mocked(getJobStatus).mockImplementation(
      () => new Promise(() => undefined),
    )
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByText('인터넷 연결을 확인한 뒤 다시 시도해 주세요.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))
    await waitFor(() => expect(executeOcr).toHaveBeenCalledTimes(2))

    expect(uploadPrescription).toHaveBeenCalledTimes(1)
    const calls = vi.mocked(executeOcr).mock.calls
    expect(calls[0]?.[0]).toBe(documentId)
    expect(calls[1]?.[0]).toBe(documentId)
    expect(calls[1]?.[1]).toBe(calls[0]?.[1])
  })

  it('새 파일을 선택한 OCR intent에서는 새 Idempotency-Key를 생성한다', async () => {
    const nextDocumentId = '44444444-4444-4444-8444-444444444444'
    vi.mocked(uploadPrescription)
      .mockResolvedValueOnce({
        data: {
          document_id: documentId,
          upload_status: 'UPLOADED',
          uploaded_at: '2026-09-06T00:00:00Z',
        },
        message: 'uploaded',
      })
      .mockResolvedValueOnce({
        data: {
          document_id: nextDocumentId,
          upload_status: 'UPLOADED',
          uploaded_at: '2026-09-06T00:00:01Z',
        },
        message: 'uploaded',
      })
    vi.mocked(executeOcr)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const { container } = renderPage()

    selectPrescriptionFile(container, 'first.png')
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))
    await screen.findByRole('alert')

    selectPrescriptionFile(container, 'second.png')
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))
    await waitFor(() => expect(executeOcr).toHaveBeenCalledTimes(2))

    const calls = vi.mocked(executeOcr).mock.calls
    expect(calls[0]?.[1]).not.toBe(calls[1]?.[1])
  })

  it.each([
    [400, 'IDEMPOTENCY_KEY_REQUIRED'],
    [400, 'IDEMPOTENCY_KEY_INVALID'],
    [409, 'IDEMPOTENCY_KEY_CONFLICT'],
    [409, 'OCR_JOB_ALREADY_PROCESSING'],
  ])('%i %s 접수 오류에서 Backend raw message를 노출하지 않는다', async (status, code) => {
    vi.mocked(executeOcr).mockRejectedValue(
      new ApiError(status, '민감한 Backend 내부 메시지', code),
    )
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.queryByText(/민감한 Backend/)).toBeNull()
  })

  it('OCR 접수 401에서 만료된 로컬 세션을 지우고 로그인으로 이동한다', async () => {
    localStorage.setItem('access_token', 'expired-token')
    vi.mocked(executeOcr).mockRejectedValue(
      new ApiError(401, '만료된 토큰', 'EXPIRED_TOKEN'),
    )
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).toBeNull()
  })

  it('현재 develop Backend의 legacy OCR 응답도 기존 검수 경로로 연결한다', async () => {
    vi.mocked(executeOcr).mockResolvedValue(ocrResponse('PENDING'))
    vi.mocked(getOcrJob).mockResolvedValue(ocrResponse('COMPLETED'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByText(/OCR 검수 화면/)).toBeTruthy()
    expect(getOcrJob).toHaveBeenCalledWith(ocrJobId, expect.any(AbortSignal))
    expect(getJobStatus).not.toHaveBeenCalled()
    expect(getOcrResult).not.toHaveBeenCalled()
  })

  it('polling 중 unmount하면 in-flight 상태 조회를 abort한다', async () => {
    let requestSignal: AbortSignal | undefined
    vi.mocked(getJobStatus).mockImplementation((_url, signal) => {
      requestSignal = signal
      return new Promise(() => undefined)
    })
    const { container, unmount } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))
    await waitFor(() => expect(requestSignal).toBeDefined())

    expect(requestSignal?.aborted).toBe(false)
    unmount()
    expect(requestSignal?.aborted).toBe(true)
  })

  it('화면을 나갔다 돌아와도 저장한 status_url로 기존 Job polling을 재개한다', async () => {
    vi.mocked(getJobStatus).mockImplementation(
      () => new Promise(() => undefined),
    )
    const firstRender = renderPage()

    selectPrescriptionFile(firstRender.container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))
    await waitFor(() => expect(getJobStatus).toHaveBeenCalledTimes(1))
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).toContain(
      statusUrl,
    )
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).not.toContain(
      'ocr:',
    )

    firstRender.unmount()
    renderPage()
    await waitFor(() => expect(getJobStatus).toHaveBeenCalledTimes(2))

    expect(uploadPrescription).toHaveBeenCalledTimes(1)
    expect(executeOcr).toHaveBeenCalledTimes(1)
    expect(getJobStatus).toHaveBeenLastCalledWith(
      statusUrl,
      expect.any(AbortSignal),
    )
  })

  it('polling network 오류 후 새 Job을 만들지 않고 같은 status_url을 다시 확인한다', async () => {
    vi.mocked(getJobStatus)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(polledJobStatus('STALE'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))
    fireEvent.click(
      await screen.findByRole('button', { name: '상태 다시 확인하기' }),
    )

    expect(await screen.findByText('최신 정보 확인이 필요해요')).toBeTruthy()
    expect(getJobStatus).toHaveBeenCalledTimes(2)
    const statusCalls = vi.mocked(getJobStatus).mock.calls
    expect(statusCalls[0]?.[0]).toBe(statusUrl)
    expect(statusCalls[1]?.[0]).toBe(statusUrl)
    expect(uploadPrescription).toHaveBeenCalledTimes(1)
    expect(executeOcr).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).toBeNull()
  })

  it('result_url network 오류 후에도 새 Job 접수 없이 같은 결과 URL을 다시 조회한다', async () => {
    vi.mocked(getJobStatus).mockResolvedValue(polledJobStatus('COMPLETED'))
    vi.mocked(getOcrResult)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(ocrResponse('COMPLETED'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))
    fireEvent.click(
      await screen.findByRole('button', { name: '상태 다시 확인하기' }),
    )

    expect(await screen.findByText(/OCR 검수 화면/)).toBeTruthy()
    expect(getOcrResult).toHaveBeenCalledTimes(2)
    expect(vi.mocked(getOcrResult).mock.calls[0]?.[0]).toBe(resultUrl)
    expect(vi.mocked(getOcrResult).mock.calls[1]?.[0]).toBe(resultUrl)
    expect(uploadPrescription).toHaveBeenCalledTimes(1)
    expect(executeOcr).toHaveBeenCalledTimes(1)
  })

  it('result_url 401에서 로그인 안내 action으로 세션을 정리한다', async () => {
    localStorage.setItem('access_token', 'expired-token')
    vi.mocked(getJobStatus).mockResolvedValue(polledJobStatus('COMPLETED'))
    vi.mocked(getOcrResult).mockRejectedValue(
      new ApiError(401, '만료된 토큰', 'EXPIRED_TOKEN'),
    )
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))
    fireEvent.click(
      await screen.findByRole('button', { name: '로그인하기' }),
    )

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).toBeNull()
  })

  it('OCR 접수 중 이전 화면으로 나가면 request를 abort하고 늦은 응답을 무시한다', async () => {
    let requestSignal: AbortSignal | undefined
    let resolveIntake: ((value: JobStatusResponse) => void) | undefined
    vi.mocked(executeOcr).mockImplementation((_documentId, _key, signal) => {
      requestSignal = signal
      return new Promise((resolve) => {
        resolveIntake = resolve
      })
    })
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))
    await waitFor(() => expect(requestSignal).toBeDefined())

    fireEvent.click(screen.getByRole('button', { name: '이전 화면' }))
    expect(requestSignal?.aborted).toBe(true)

    await act(async () => {
      resolveIntake?.(jobStatusResponse('PENDING'))
      await Promise.resolve()
    })

    expect(screen.getByRole('button', { name: '처방전 읽기' })).toBeTruthy()
    expect(getJobStatus).not.toHaveBeenCalled()
    expect(screen.queryByText(/OCR 검수 화면/)).toBeNull()
  })
})
