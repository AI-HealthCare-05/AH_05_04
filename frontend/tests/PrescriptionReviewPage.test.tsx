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
import { MemoryRouter, useNavigate } from 'react-router-dom'
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

function withMedicationName(fields: ExtractedField[], name: string) {
  return fields.map((field) =>
    field.field_type === 'MEDICATION_NAME'
      ? { ...field, raw_value: name, confirmed_value: name }
      : field,
  )
}

function createDeferred<T>() {
  let resolve: (value: T) => void = () => undefined
  let reject: (reason?: unknown) => void = () => undefined
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })

  return { promise, resolve, reject }
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
      <PrescriptionReviewPage />
    </MemoryRouter>,
  )
}

function RouteSwitchHarness() {
  const navigate = useNavigate()

  return (
    <>
      <button
        type="button"
        onClick={() =>
          navigate(
            '/prescriptions/review?document_id=document-b&job_id=job-b',
          )
        }
      >
        문서 B로 이동
      </button>
      <PrescriptionReviewPage />
    </>
  )
}

function renderReroutablePage() {
  return render(
    <MemoryRouter
      initialEntries={[
        '/prescriptions/review?document_id=document-a&job_id=job-a',
      ]}
    >
      <RouteSwitchHarness />
    </MemoryRouter>,
  )
}

async function getConfirmationButton() {
  return screen.findByRole('button', { name: '처방 확정' })
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
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('PrescriptionReviewPage confirmation gate', () => {
  it('이전 문서 응답이 늦게 완료되어도 최신 문서 상태를 덮어쓰지 않는다', async () => {
    const documentAFields = withMedicationName(
      makeCompleteFields(),
      '문서 A 처방약',
    )
    const documentBFields = withMedicationName(
      makeCompleteFields(),
      '문서 B 처방약',
    )
    const documentAFile = createDeferred<Blob>()
    const documentBBlob = new Blob(['document-b'])

    vi.mocked(getOcrJob).mockImplementation((requestedJobId) =>
      Promise.resolve(
        requestedJobId === 'job-a'
          ? makeOcrResponse(documentAFields, 'document-a')
          : makeOcrResponse(documentBFields, 'document-b'),
      ),
    )
    vi.mocked(getPrescriptionDocumentFile).mockImplementation(
      (requestedDocumentId) =>
        requestedDocumentId === 'document-a'
          ? documentAFile.promise
          : Promise.resolve(documentBBlob),
    )

    renderReroutablePage()

    await waitFor(() =>
      expect(getPrescriptionDocumentFile).toHaveBeenCalledWith('document-a'),
    )
    fireEvent.click(screen.getByRole('button', { name: '문서 B로 이동' }))

    expect(await screen.findByText('문서 B 처방약')).toBeTruthy()
    expect(screen.queryByText('문서 A 처방약')).toBeNull()

    await act(async () => documentAFile.resolve(new Blob(['document-a'])))

    await waitFor(() => {
      expect(screen.getByText('문서 B 처방약')).toBeTruthy()
      expect(screen.queryByText('문서 A 처방약')).toBeNull()
    })
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    expect(URL.createObjectURL).toHaveBeenCalledWith(documentBBlob)
  })

  it('문서가 변경되면 이전 확인·오류 상태와 object URL을 초기화한다', async () => {
    const documentAFields = withMedicationName(
      makeCompleteFields(),
      '문서 A 처방약',
    )
    const documentBFields = withMedicationName(
      makeCompleteFields(),
      '문서 B 처방약',
    )
    const documentBFile = createDeferred<Blob>()

    vi.mocked(getOcrJob).mockImplementation((requestedJobId) =>
      Promise.resolve(
        requestedJobId === 'job-a'
          ? makeOcrResponse(documentAFields, 'document-a')
          : makeOcrResponse(documentBFields, 'document-b'),
      ),
    )
    vi.mocked(getPrescriptionDocumentFile).mockImplementation(
      (requestedDocumentId) =>
        requestedDocumentId === 'document-a'
          ? Promise.resolve(new Blob(['document-a']))
          : documentBFile.promise,
    )
    vi.mocked(updateExtractedField).mockRejectedValue(
      new Error('document-a save failed'),
    )

    renderReroutablePage()

    expect(await screen.findByText('문서 A 처방약')).toBeTruthy()
    const acknowledgement = screen.getByRole('checkbox')
    fireEvent.click(acknowledgement)
    expect(acknowledgement).toHaveProperty('checked', true)

    const prescribedDateInput = screen.getByLabelText('처방일')
    const prescribedDateField = prescribedDateInput.closest(
      '.prescription-review__field',
    )
    if (!prescribedDateField) throw new Error('prescribed date field not found')
    fireEvent.click(within(prescribedDateField).getByRole('button'))
    expect(await screen.findByText('필드 저장 중 오류가 발생했습니다.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '문서 B로 이동' }))

    expect(
      await screen.findByText('처방전 검수 정보를 불러오고 있어요.'),
    ).toBeTruthy()
    expect(screen.queryByText('필드 저장 중 오류가 발생했습니다.')).toBeNull()
    expect(screen.queryByRole('checkbox')).toBeNull()
    await waitFor(() =>
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:prescription'),
    )

    await act(async () => documentBFile.resolve(new Blob(['document-b'])))

    expect(await screen.findByText('문서 B 처방약')).toBeTruthy()
    expect(screen.getByRole('checkbox')).toHaveProperty('checked', false)
    expect(screen.queryByText('문서 A 처방약')).toBeNull()
  })

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
      '0',
      '1회 복용량은 0보다 큰 숫자로 입력해 주세요.',
    ],
    [
      '1회 복용량',
      '-0.5',
      '1회 복용량은 0보다 큰 숫자로 입력해 주세요.',
    ],
    [
      '1회 복용량',
      '1.2.3',
      '1회 복용량은 숫자 형식으로 입력해 주세요.',
    ],
    [
      '하루 횟수',
      '0',
      '하루 횟수는 0보다 큰 정수로 입력해 주세요.',
    ],
    ['하루 횟수', '-1', '하루 횟수는 정수 형식으로 입력해 주세요.'],
    ['하루 횟수', '2.5', '하루 횟수는 정수 형식으로 입력해 주세요.'],
    [
      '복용 기간',
      '0',
      '복용 기간은 0보다 큰 정수로 입력해 주세요.',
    ],
    ['복용 기간', '-7', '복용 기간은 정수 형식으로 입력해 주세요.'],
    ['복용 기간', '7.5', '복용 기간은 정수 형식으로 입력해 주세요.'],
  ])('%s의 유효하지 않은 숫자 값 %s은 PATCH 전에 차단한다', async (
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

  it.each(['1e2', '1_0.5'])(
    'Backend가 허용하는 DOSE_VALUE %s은 PATCH에 전달한다',
    async (validValue) => {
      const fields = makeCompleteFields()
      vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))
      vi.mocked(updateExtractedField).mockImplementation(
        async (fieldId, confirmedValue) => {
          const field = fields.find((candidate) => candidate.field_id === fieldId)
          if (!field) throw new Error('field not found')
          return {
            data: {
              ...field,
              confirmed_value: confirmedValue,
              confirmation_status: 'CONFIRMED',
            },
          }
        },
      )

      renderPage()

      const doseInput = await screen.findByLabelText('1회 복용량')
      fireEvent.change(doseInput, { target: { value: validValue } })
      const doseField = doseInput.closest('.prescription-review__field')
      if (!doseField) throw new Error('dose field not found')

      fireEvent.click(within(doseField).getByRole('button'))

      await waitFor(() =>
        expect(updateExtractedField).toHaveBeenCalledWith(
          'DOSE_VALUE-1',
          validValue,
        ),
      )
    },
  )

  it('처방 확정 요청 중에는 모든 OCR 입력과 저장 버튼을 비활성화한다', async () => {
    const fields = makeCompleteFields()
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))

    let resolveConfirmation: (
      value: Awaited<ReturnType<typeof confirmPrescription>>,
    ) => void = () => undefined
    vi.mocked(confirmPrescription).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveConfirmation = resolve
        }),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(await getConfirmationButton())

    await waitFor(() => expect(confirmPrescription).toHaveBeenCalledTimes(1))

    for (const input of screen.getAllByRole('textbox')) {
      expect(input).toHaveProperty('disabled', true)
    }

    for (const field of document.querySelectorAll('.prescription-review__field')) {
      expect(within(field as HTMLElement).getByRole('button')).toHaveProperty(
        'disabled',
        true,
      )
    }

    await act(async () =>
      resolveConfirmation({
        data: {
          prescription_id: 'prescription-1',
          document_id: 'document-1',
          prescribed_date: '2026-08-22',
          confirmed_at: '2026-08-22T00:00:02Z',
          medications: [],
        },
      }),
    )
  })

  it('처방 확정 완료 후에는 OCR 편집 UI와 저장 동작을 제공하지 않는다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(await getConfirmationButton())

    expect(await screen.findByText('처방정보가 확정되었어요')).toBeTruthy()
    expect(screen.queryByLabelText('약 이름')).toBeNull()
    expect(screen.queryByText('세부 항목 확인 및 수정')).toBeNull()
    expect(screen.queryByRole('button', { name: /수정 저장|확인/ })).toBeNull()
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
      screen.queryByRole('button', { name: '처방 확정' }),
    ).toBeNull()
  })

  it('버튼 문구와 실제 처방 확정 동작이 일치한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    const confirmButton = await getConfirmationButton()
    expect(screen.queryByText(/가이드 만들기/)).toBeNull()

    fireEvent.click(confirmButton)

    await waitFor(() =>
      expect(confirmPrescription).toHaveBeenCalledWith('document-1'),
    )
  })
})
