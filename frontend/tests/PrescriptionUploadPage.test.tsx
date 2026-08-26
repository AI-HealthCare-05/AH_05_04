import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import {
  executeOcr,
  getOcrJob,
  uploadPrescription,
} from '../src/api/prescriptions'
import PrescriptionUploadPage from '../src/pages/PrescriptionUploadPage'

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
  return render(
    <MemoryRouter initialEntries={['/prescriptions/upload']}>
      <Routes>
        <Route path="/prescriptions/upload" element={<PrescriptionUploadPage />} />
        <Route path="/prescriptions/review" element={<div>OCR 검수 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function selectPrescriptionFile(container: HTMLElement) {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')
  if (!input) throw new Error('file input not found')
  fireEvent.change(input, {
    target: { files: [new File(['prescription'], 'prescription.png', { type: 'image/png' })] },
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
  it('PROCESSING 상태를 재조회하고 COMPLETED에서 기존 review route로 이동한다', async () => {
    vi.mocked(getOcrJob)
      .mockResolvedValueOnce(ocrResponse('PROCESSING'))
      .mockResolvedValueOnce(ocrResponse('COMPLETED'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(
      await screen.findByText('OCR 검수 화면', {}, { timeout: 2500 }),
    ).toBeTruthy()
    expect(uploadPrescription).toHaveBeenCalledTimes(1)
    expect(executeOcr).toHaveBeenCalledWith(documentId)
    expect(getOcrJob).toHaveBeenCalledTimes(2)
    expect(getOcrJob).toHaveBeenCalledWith(jobId)
  })

  it('FAILED 상태에서는 polling을 중단하고 재업로드 안내를 표시한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(ocrResponse('FAILED'))
    const { container } = renderPage()

    selectPrescriptionFile(container)
    fireEvent.click(screen.getByRole('button', { name: '처방전 읽기' }))

    expect(
      await screen.findByText(/파일을 확인한 뒤 다시 시도해 주세요/),
    ).toBeTruthy()
    await waitFor(() => expect(getOcrJob).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('OCR 검수 화면')).toBeNull()
  })
})
