import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ApiError } from '../src/api/client'
import {
  executeOcr,
  getOcrJob,
  uploadPrescription,
} from '../src/api/prescriptions'
import PrescriptionUploadPage from '../src/pages/PrescriptionUploadPage'

const mvpPageStyles = readFileSync(
  join(process.cwd(), 'src/pages/MvpPages.css'),
  'utf8',
)

vi.mock('../src/api/prescriptions', () => ({
  uploadPrescription: vi.fn(),
  executeOcr: vi.fn(),
  getOcrJob: vi.fn(),
}))

const documentId = '11111111-1111-4111-8111-111111111111'
const jobId = '22222222-2222-4222-8222-222222222222'

function ocrResponse(status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED') {
  return {
    data: {
      job_id: jobId,
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
  vi.mocked(executeOcr).mockResolvedValue(ocrResponse('PENDING'))
})

afterEach(() => {
  vi.restoreAllMocks()
  cleanup()
})

describe('PrescriptionUploadPage OCR polling', () => {
  it('#227 공백 없는 긴 파일명을 두 줄 안에서 카드 폭에 맞춰 표시한다', () => {
    const longFilename =
      'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_FINAL-2026.png'
    const { container } = renderPage()

    selectPrescriptionFile(container, longFilename)

    const filename = screen.getByText(longFilename)
    expect(filename.classList.contains('mvp-upload__filename')).toBe(true)
    const filenameRule = mvpPageStyles.match(
      /\.mvp-upload__filename\s*\{([^}]*)\}/,
    )?.[1]
    expect(filenameRule).toContain('overflow: hidden')
    expect(filenameRule).toContain('overflow-wrap: anywhere')
    expect(filenameRule).toContain('word-break: break-word')
    expect(filenameRule).toContain('-webkit-line-clamp: 2')
  })

  it('PENDING → PROCESSING → COMPLETED 후 document_id와 job_id를 유지해 review route로 이동한다', async () => {
    vi.mocked(getOcrJob)
      .mockResolvedValueOnce(ocrResponse('PENDING'))
      .mockResolvedValueOnce(ocrResponse('PROCESSING'))
      .mockResolvedValueOnce(ocrResponse('COMPLETED'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(
      await screen.findByText(/OCR 검수 화면/, {}, { timeout: 3500 }),
    ).toBeTruthy()
    expect(uploadPrescription).toHaveBeenCalledTimes(1)
    expect(executeOcr).toHaveBeenCalledWith(documentId)
    expect(getOcrJob).toHaveBeenCalledTimes(3)
    expect(getOcrJob).toHaveBeenCalledWith(jobId, expect.any(AbortSignal))
    expect(screen.getByText(new RegExp(`document_id=${documentId}`))).toBeTruthy()
    expect(screen.getByText(new RegExp(`job_id=${jobId}`))).toBeTruthy()
  })

  it('PROCESSING → FAILED unknown code에서는 polling을 중단하고 안전한 fallback UI를 표시한다', async () => {
    vi.mocked(getOcrJob)
      .mockResolvedValueOnce(ocrResponse('PROCESSING'))
      .mockResolvedValueOnce(ocrResponse('FAILED'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(
      await screen.findByText('작업을 완료하지 못했어요', {}, { timeout: 2500 }),
    ).toBeTruthy()
    await waitFor(() => expect(getOcrJob).toHaveBeenCalledTimes(2))
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
    vi.mocked(getOcrJob).mockRejectedValue(new TypeError('Failed to fetch'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByText('네트워크 연결을 확인해 주세요')).toBeTruthy()
    expect(getOcrJob).toHaveBeenCalledTimes(1)
  })

  it('polling 5xx를 Backend FAILED와 구분한 서버 오류 UI로 표시한다', async () => {
    vi.mocked(getOcrJob).mockRejectedValue(
      new ApiError(503, 'provider detail', 'DEPENDENCY_DOWN'),
    )
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(await screen.findByText('서버 응답이 원활하지 않아요')).toBeTruthy()
    expect(screen.queryByText('provider detail')).toBeNull()
  })
})
