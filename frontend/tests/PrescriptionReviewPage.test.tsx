import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type {
  ExtractedField,
  OcrJobResponse,
} from '../src/api/prescriptions'
import PrescriptionReviewPage from '../src/pages/PrescriptionReviewPage'
import {
  confirmPrescription,
  getOcrJob,
  getPrescriptionDocumentFile,
  updateExtractedField,
} from '../src/api/prescriptions'
import { createGuide } from '../src/api/guides'

vi.mock('../src/api/prescriptions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/prescriptions')>()

  return {
    ...actual,
    confirmPrescription: vi.fn(),
    getOcrJob: vi.fn(),
    getPrescriptionDocumentFile: vi.fn(),
    updateExtractedField: vi.fn(),
  }
})

vi.mock('../src/api/guides', () => ({
  createGuide: vi.fn(),
}))

const displayedMedicationFields = [
  'MEDICATION_NAME',
  'DOSE_VALUE',
  'DOSE_UNIT',
  'FREQUENCY_PER_DAY',
  'DURATION_DAYS',
  'TIMING',
] as const

function getValidFieldValue(fieldType: string, medicationIndex: number) {
  const values: Record<string, string> = {
    PRESCRIBED_DATE: '2026-08-22',
    MEDICATION_NAME: `처방약 ${medicationIndex}`,
    DOSE_VALUE: '0.5',
    DOSE_UNIT: '정',
    FREQUENCY_PER_DAY: '3',
    DURATION_DAYS: '7',
    TIMING: '식후',
  }

  return values[fieldType] ?? `${fieldType}-${medicationIndex}`
}

function makeField(
  fieldType: string,
  medicationIndex: number,
  confirmed = true,
): ExtractedField {
  const value = getValidFieldValue(fieldType, medicationIndex)

  return {
    field_id: `${fieldType}-${medicationIndex}`,
    field_type: fieldType,
    medication_index: medicationIndex,
    raw_value: value,
    confirmed_value: confirmed ? value : null,
    confidence_score: 0.99,
    confirmation_status: confirmed ? 'CONFIRMED' : 'PENDING',
  }
}

function makeCompleteFields(medicationCount = 1) {
  const fields = [makeField('PRESCRIBED_DATE', 0)]

  for (let index = 1; index <= medicationCount; index += 1) {
    fields.push(
      ...displayedMedicationFields.map((fieldType) =>
        makeField(fieldType, index),
      ),
    )
  }

  return fields
}

function makeOcrResponse(
  fields: ExtractedField[],
  documentId = 'document-1',
): OcrJobResponse {
  return {
    data: {
      job_id: 'job-1',
      document_id: documentId,
      ocr_status: 'COMPLETED',
      error_code: null,
      created_at: '2026-08-22T00:00:00Z',
      completed_at: '2026-08-22T00:00:01Z',
      fields,
    },
  }
}

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={[
        '/prescriptions/review?document_id=document-1&job_id=job-1',
      ]}
    >
      <Routes>
        <Route
          path="/prescriptions/review"
          element={<PrescriptionReviewPage />}
        />
        <Route path="/guides/:guideId" element={<div>가이드 화면 도착</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function getConfirmationButton() {
  return screen.findByRole('button', { name: '확정하고 가이드 만들기' })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:prescription'),
    revokeObjectURL: vi.fn(),
  })
  vi.mocked(getPrescriptionDocumentFile).mockResolvedValue(
    new Blob(['prescription']),
  )
  vi.mocked(confirmPrescription).mockResolvedValue({
    data: {
      prescription_id: 'prescription-1',
      document_id: 'document-1',
      prescribed_date: '2026-08-22',
      confirmed_at: '2026-08-22T00:00:02Z',
      medications: [],
    },
  })
  vi.mocked(createGuide).mockResolvedValue({
    data: {
      guide_id: 'guide-1',
      prescription_id: 'prescription-1',
      generation_status: 'COMPLETED',
      content: '복약 가이드 내용',
      model_name: 'guide-model',
      prompt_version: 'guide-prompt-v1',
      requested_at: '2026-08-22T00:00:02Z',
      completed_at: '2026-08-22T00:00:03Z',
    },
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('PrescriptionReviewPage confirmation gate', () => {
  it('일부 약만 확인된 경우 처방을 확정할 수 없다', async () => {
    const fields = makeCompleteFields(2).map((field) =>
      field.medication_index === 2
        ? { ...field, confirmed_value: null, confirmation_status: 'PENDING' }
        : field,
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))

    renderPage()

    const confirmButton = await getConfirmationButton()
    const acknowledgement = screen.getByRole('checkbox')

    expect(acknowledgement).toHaveProperty('disabled', true)
    expect(confirmButton).toHaveProperty('disabled', true)
  })

  it('필수 항목이 OCR 결과에 없으면 재업로드를 안내하고 확정을 차단한다', async () => {
    const fields = makeCompleteFields().filter(
      (field) => field.field_type !== 'DURATION_DAYS',
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))

    renderPage()

    expect(
      await screen.findByText('필수 처방 항목이 누락됐어요'),
    ).toBeTruthy()
    expect(screen.getByText(/OCR을 다시 실행해 주세요/)).toBeTruthy()
    expect(await getConfirmationButton()).toHaveProperty('disabled', true)
  })

  it('화면에 존재하는 선택 필드도 모두 저장되어야 최종 확인할 수 있다', async () => {
    const fields = makeCompleteFields().map((field) =>
      field.field_type === 'DOSE_UNIT'
        ? { ...field, confirmed_value: null, confirmation_status: 'PENDING' }
        : field,
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))

    renderPage()

    const acknowledgement = await screen.findByRole('checkbox')
    expect(acknowledgement).toHaveProperty('disabled', true)
    expect(await getConfirmationButton()).toHaveProperty('disabled', true)
  })

  it('동시 저장 중 하나가 먼저 완료되어도 남은 요청이 있으면 확정을 비활성화한다', async () => {
    const fields = makeCompleteFields()
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))
    const saveResolvers = new Map<
      string,
      (value: { data: ExtractedField }) => void
    >()
    vi.mocked(updateExtractedField).mockImplementation(
      (fieldId) =>
        new Promise((resolve) => {
          const field = fields.find((candidate) => candidate.field_id === fieldId)
          if (!field) throw new Error('field not found')
          saveResolvers.set(fieldId, resolve)
        }),
    )

    renderPage()

    const acknowledgement = await screen.findByRole('checkbox')
    const confirmButton = await getConfirmationButton()
    expect(confirmButton).toHaveProperty('disabled', true)

    fireEvent.click(acknowledgement)
    expect(confirmButton).toHaveProperty('disabled', false)

    const prescribedDateInput = screen.getByLabelText('처방일')
    fireEvent.change(prescribedDateInput, { target: { value: '수정된 날짜' } })
    expect(acknowledgement).toHaveProperty('checked', false)
    expect(acknowledgement).toHaveProperty('disabled', true)
    expect(confirmButton).toHaveProperty('disabled', true)

    fireEvent.change(prescribedDateInput, {
      target: { value: '2026-08-22' },
    })
    fireEvent.click(acknowledgement)
    expect(confirmButton).toHaveProperty('disabled', false)

    const prescribedDateField = prescribedDateInput.closest(
      '.prescription-review__field',
    )
    const medicationNameInput = screen.getByLabelText('약 이름')
    const medicationNameField = medicationNameInput.closest(
      '.prescription-review__field',
    )
    if (!prescribedDateField || !medicationNameField) {
      throw new Error('field controls not found')
    }
    const dateSaveButton = within(prescribedDateField).getByRole('button')
    const medicationSaveButton = within(medicationNameField).getByRole('button')

    fireEvent.click(dateSaveButton)
    await waitFor(() => expect(dateSaveButton).toHaveProperty('disabled', true))
    fireEvent.click(medicationSaveButton)

    await waitFor(() => expect(confirmButton).toHaveProperty('disabled', true))
    await waitFor(() => expect(saveResolvers.size).toBe(2))

    const dateField = fields.find((field) => field.field_type === 'PRESCRIBED_DATE')
    const medicationNameFieldData = fields.find(
      (field) => field.field_type === 'MEDICATION_NAME',
    )
    const resolveDate = dateField && saveResolvers.get(dateField.field_id)
    const resolveMedication =
      medicationNameFieldData && saveResolvers.get(medicationNameFieldData.field_id)
    if (!dateField || !medicationNameFieldData || !resolveDate || !resolveMedication) {
      throw new Error('concurrent saves did not start')
    }

    await act(async () => resolveDate({ data: dateField }))
    expect(confirmButton).toHaveProperty('disabled', true)
    expect(acknowledgement).toHaveProperty('disabled', true)

    await act(async () => resolveMedication({ data: medicationNameFieldData }))
    await waitFor(() => expect(acknowledgement).toHaveProperty('disabled', false))
    expect(confirmButton).toHaveProperty('disabled', true)
  })

  it.each([
    [
      '1회 복용량',
      '1.2.3',
      '1회 복용량은 숫자 형식으로 입력해 주세요.',
    ],
    ['하루 횟수', '2.5', '하루 횟수는 정수 형식으로 입력해 주세요.'],
    ['복용 기간', '7.5', '복용 기간은 정수 형식으로 입력해 주세요.'],
  ])('%s의 잘못된 숫자 형식은 PATCH 전에 차단한다', async (
    fieldLabel,
    invalidValue,
    errorMessage,
  ) => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )

    renderPage()

    const numericInput = await screen.findByLabelText(fieldLabel)
    fireEvent.change(numericInput, { target: { value: invalidValue } })
    const numericField = numericInput.closest('.prescription-review__field')
    if (!numericField) throw new Error('numeric field not found')

    fireEvent.click(within(numericField).getByRole('button'))

    expect(
      await within(numericField).findByText(errorMessage),
    ).toBeTruthy()
    expect(updateExtractedField).not.toHaveBeenCalled()
  })

  it('URL 문서와 OCR 문서가 다르면 검수 화면을 차단한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields(), 'different-document'),
    )

    renderPage()

    expect(await screen.findByText('검수를 진행할 수 없어요')).toBeTruthy()
    expect(screen.getByText(/OCR 결과가 일치하지 않습니다/)).toBeTruthy()
    expect(getPrescriptionDocumentFile).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('button', { name: '확정하고 가이드 만들기' }),
    ).toBeNull()
  })

  it('처방 확정 후 실제 가이드를 생성하고 guide_id 화면으로 이동한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    const confirmButton = await getConfirmationButton()

    fireEvent.click(confirmButton)

    await waitFor(() =>
      expect(confirmPrescription).toHaveBeenCalledWith('document-1'),
    )
    await waitFor(() =>
      expect(createGuide).toHaveBeenCalledWith('prescription-1'),
    )
    expect(await screen.findByText('가이드 화면 도착')).toBeTruthy()
  })
})
