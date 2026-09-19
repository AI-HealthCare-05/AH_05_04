import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  MemoryRouter,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router-dom'
import type {
  ExtractedField,
  OcrJobResponse,
} from '../src/api/prescriptions'
import { ApiError } from '../src/api/client'
import { getOcrConsent } from '../src/api/ocrConsent'
import { createGuide, type GuideResponse } from '../src/api/guides'
import PrescriptionReviewPage from '../src/pages/PrescriptionReviewPage'

const prescriptionReviewStyles = readFileSync(
  join(process.cwd(), 'src/pages/PrescriptionReviewPage.css'),
  'utf8',
)
import {
  confirmPrescription,
  createManualMedication,
  getOcrJob,
  getPrescriptionDocumentFile,
  getPrescriptionNormalizedImage,
  updateExtractedField,
} from '../src/api/prescriptions'

vi.mock('../src/api/prescriptions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/prescriptions')>()

  return {
    ...actual,
    confirmPrescription: vi.fn(),
    createManualMedication: vi.fn(),
    getOcrJob: vi.fn(),
    getPrescriptionDocumentFile: vi.fn(),
    getPrescriptionNormalizedImage: vi.fn(),
    updateExtractedField: vi.fn(),
  }
})

vi.mock('../src/api/guides', () => ({
  createGuide: vi.fn(),
}))

vi.mock('../src/api/ocrConsent', () => ({ getOcrConsent: vi.fn() }))

const displayedMedicationFields = [
  'MEDICATION_NAME',
  'MEDICATION_STRENGTH',
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
    MEDICATION_STRENGTH: '100mg',
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
    normalized_value: null,
    normalization_version: null,
    confirmed_value: confirmed ? value : null,
    confidence_score: 0.99,
    confirmation_status: confirmed ? 'CONFIRMED' : 'UNCONFIRMED',
  }
}

function makePlaceholderField(
  fieldType: string,
  medicationIndex: number,
): ExtractedField {
  return {
    ...makeField(fieldType, medicationIndex, false),
    raw_value: null,
    normalized_value: null,
    normalization_version: null,
    confirmed_value: null,
    confidence_score: null,
    confirmation_status: 'UNCONFIRMED',
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

function makeManualMedicationFields(medicationIndex = 2) {
  const values: Record<string, string | null> = {
    MEDICATION_NAME: '직접입력약정',
    MEDICATION_STRENGTH: '50mg',
    DOSE_VALUE: '0.5',
    DOSE_UNIT: '정',
    FREQUENCY_PER_DAY: '2',
    DURATION_DAYS: '5',
    TIMING: '저녁 식후',
  }

  return displayedMedicationFields.map<ExtractedField>((fieldType) => ({
    field_id: `manual-${fieldType}-${medicationIndex}`,
    field_type: fieldType,
    medication_index: medicationIndex,
    raw_value: null,
    normalized_value: null,
    normalization_version: 'manual-entry@1',
    confirmed_value: values[fieldType],
    confidence_score: null,
    confirmation_status: 'CONFIRMED',
  }))
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
  ocrStatus: OcrJobResponse['data']['ocr_status'] = 'COMPLETED',
): OcrJobResponse {
  return {
    data: {
      job_id: 'job-1',
      document_id: documentId,
      ocr_status: ocrStatus,
      error_code: null,
      engine_name: 'CLOVA_OCR',
      model_version: null,
      prompt_version: null,
      created_at: '2026-08-22T00:00:00Z',
      completed_at: '2026-08-22T00:00:01Z',
      fields,
      source_image: {
        normalized: false,
        width: null,
        height: null,
        url: null,
      },
    },
  }
}

function renderPage(prefetchedOcrResponse?: OcrJobResponse) {
  const reviewPath =
    '/prescriptions/review?document_id=document-1&job_id=job-1'

  return render(
    <MemoryRouter
      initialEntries={[
        prefetchedOcrResponse
          ? {
              pathname: '/prescriptions/review',
              search: '?document_id=document-1&job_id=job-1',
              state: { ocrResponse: prefetchedOcrResponse },
            }
          : reviewPath,
      ]}
    >
      <Routes>
        <Route
          path="/prescriptions/review"
          element={<PrescriptionReviewPage />}
        />
        <Route path="/guides/:guideId" element={<GuideRouteProbe />} />
        <Route
          path="/prescriptions/upload"
          element={<div>처방전 업로드 화면</div>}
        />
      </Routes>
    </MemoryRouter>,
  )
}

function GuideRouteProbe() {
  const { guideId } = useParams()
  return <div>Guide route: {guideId}</div>
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
  return screen.findByRole('button', {
    name: '처방전 확정 및 가이드 만들기',
  })
}

function fillManualMedicationForm(name = '직접입력약정') {
  fireEvent.change(screen.getByLabelText('약물이름'), {
    target: { value: name },
  })
  fireEvent.change(screen.getByLabelText('제품함량'), {
    target: { value: '50mg' },
  })
  fireEvent.change(screen.getByLabelText('1회 복용량'), {
    target: { value: '0.5' },
  })
  fireEvent.change(screen.getByLabelText('복용단위'), {
    target: { value: '정' },
  })
  fireEvent.change(screen.getByLabelText('하루횟수'), {
    target: { value: '2' },
  })
  fireEvent.change(screen.getByLabelText('복용조건'), {
    target: { value: '저녁 식후' },
  })
  fireEvent.change(screen.getByLabelText('투약일수'), {
    target: { value: '5' },
  })
}

function makePendingFields(medicationCount = 1) {
  return makeCompleteFields(medicationCount).map((field) => ({
    ...field,
    confirmed_value: null,
    confirmation_status: 'UNCONFIRMED',
  }))
}

function mockPatchConfirmation() {
  vi.mocked(updateExtractedField).mockImplementation(
    async (fieldId, confirmedValue) => {
      const field = makeCompleteFields(3).find(
        (candidate) => candidate.field_id === fieldId,
      )

      if (!field) throw new Error(`unexpected field: ${fieldId}`)

      return {
        data: {
          ...field,
          confirmed_value: confirmedValue,
          confirmation_status: 'CONFIRMED',
        },
      }
    },
  )
}

function makeGuideResponse(
  guideId = 'guide-1',
  prescriptionId = 'prescription-1',
): GuideResponse {
  return {
    data: {
      guide_id: guideId,
      prescription_id: prescriptionId,
      generation_status: 'COMPLETED',
      content: '테스트 가이드',
      model_name: 'guide-model',
      prompt_version: 'guide-prompt-v1',
      requested_at: '2026-08-22T00:00:03Z',
      completed_at: '2026-08-22T00:00:04Z',
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getOcrConsent).mockResolvedValue({
    data: {
      purpose: 'OCR', status: 'GRANTED', effective: true, reason: null,
      current_policy_version: 'ocr-test.v1', accepted_policy_version: 'ocr-test.v1',
      granted_at: '2026-09-14T00:00:00Z', withdrawn_at: null,
    },
  })
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:prescription'),
    revokeObjectURL: vi.fn(),
  })
  vi.mocked(getPrescriptionDocumentFile).mockResolvedValue(
    new Blob(['prescription']),
  )
  vi.mocked(getPrescriptionNormalizedImage).mockResolvedValue(
    new Blob(['normalized-prescription']),
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
  vi.mocked(createGuide).mockRejectedValue(
    new Error('guide creation failed'),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('PrescriptionReviewPage confirmation gate', () => {
  it('LLM 전송 최소화 상태여도 AI 구조화 notice를 노출하지 않는다', async () => {
    const response = makeOcrResponse(makeCompleteFields())
    response.data.llm_processing = 'SKIPPED_MINIMIZATION'
    renderPage(response)

    // 계약은 유지하되 사용자 UI에서는 제거됐다.
    expect(
      await screen.findByText('도지는 처방 내용을 바꾸지 않아요.'),
    ).toBeTruthy()
    expect(screen.queryByText('AI 구조화를 생략했어요')).toBeNull()
    expect(screen.queryByText(/외부 LLM에 보내지 않았습니다/)).toBeNull()
  })

  it('상단 안내는 Figma 최종 문구 2문장만 표시한다', async () => {
    renderPage(makeOcrResponse(makeCompleteFields()))

    expect(
      await screen.findByText('도지는 처방 내용을 바꾸지 않아요.'),
    ).toBeTruthy()
    expect(
      screen.getByText(
        '원본 처방전과 인식된 내용을 직접 비교해 주세요.',
      ),
    ).toBeTruthy()

    const notices = document.querySelectorAll(
      '.prescription-review__notice',
    )
    expect(notices).toHaveLength(1)

    // 최종 문구는 2문장뿐이며 세 번째 상태 안내는 제거됐다.
    const notice = notices[0] as HTMLElement
    expect(notice.children).toHaveLength(2)
    expect(
      notice.querySelector('.prescription-review__state-guidance'),
    ).toBeNull()
    expect(
      screen.queryByText(/각 항목의 검토 완료를 눌러 주세요/),
    ).toBeNull()
    expect(
      screen.queryByText(/수정 중인 정보는 검토 완료가 해제돼요/),
    ).toBeNull()
  })

  it('상단 안내는 Figma 기준 연한 회청색 계열이다', () => {
    const scopedRule = prescriptionReviewStyles.match(
      /\.prescription-review__content \.prescription-review__notice \{[^}]*\}/,
    )?.[0]

    expect(scopedRule).toContain('background: #f4f7fa')
    expect(scopedRule).toContain('border-color: #d3dde6')
    // 노란색 계열이 남아 있으면 안 된다.
    expect(scopedRule).not.toContain('#fff9e8')
    expect(scopedRule).not.toContain('#f0d77a')

    // 크기/줄바꿈 규격은 유지한다.
    expect(scopedRule).toContain('min-height: 84px')
    expect(scopedRule).toContain('padding: 16px')
    expect(scopedRule).toContain('word-break: keep-all')

    const strongRule = prescriptionReviewStyles.match(
      /\.prescription-review__content \.prescription-review__notice strong \{[^}]*\}/,
    )?.[0]
    const spanRule = prescriptionReviewStyles.match(
      /\.prescription-review__content \.prescription-review__notice span \{[^}]*\}/,
    )?.[0]

    expect(strongRule).toContain('color: #2b3a47')
    expect(spanRule).toContain('color: #55687a')

    // error notice는 기존 경고 톤을 유지한다.
    expect(prescriptionReviewStyles).toContain('#fff7f0')
    expect(prescriptionReviewStyles).toContain('#e0a36d')
  })

  it('철회 후에는 prefetched OCR 결과도 검수 화면에 표시하지 않는다', async () => {
    vi.mocked(getOcrConsent).mockResolvedValue({ data: {
      purpose: 'OCR', status: 'WITHDRAWN', effective: false, reason: 'WITHDRAWN',
      current_policy_version: 'ocr-test.v1', accepted_policy_version: 'ocr-test.v1',
      granted_at: null, withdrawn_at: '2026-09-14T00:00:00Z',
    } })
    renderPage(makeOcrResponse(makeCompleteFields()))
    expect(await screen.findByText('현재 OCR 동의가 유효하지 않아 기존 OCR 결과를 표시하지 않습니다.')).toBeTruthy()
    expect(getPrescriptionDocumentFile).not.toHaveBeenCalled()
  })

  it('result_url에서 미리 받은 OCR fields를 재조회 없이 DOC-03에 표시한다', async () => {
    const prefetchedResult = makeOcrResponse(makeCompleteFields())

    renderPage(prefetchedResult)

    expect(
      await screen.findByRole('heading', {
        name: '처방약 1 100mg',
        level: 2,
      }),
    ).toBeTruthy()
    expect(getOcrJob).not.toHaveBeenCalled()
    expect(getPrescriptionDocumentFile).toHaveBeenCalledWith('document-1')
  })

  it('DOC-03 Dosey 구조에 실제 OCR 처방일과 약물 요약을 표시한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )

    renderPage()

    expect(
      await screen.findByRole('heading', { name: 'Dosey 도지' }),
    ).toBeTruthy()
    expect(screen.getByText('전체 인식 성공')).toBeTruthy()
    expect(screen.getByText('원본 처방전 보기')).toBeTruthy()
    expect(screen.getByText('2026.08.22')).toBeTruthy()
    expect(
      screen.getByRole('heading', {
        name: '처방약 1 100mg',
        level: 2,
      }),
    ).toBeTruthy()
    expect(screen.getByText('1회 복용량')).toBeTruthy()
    expect(screen.getByText('하루횟수')).toBeTruthy()
    expect(screen.getByText('복용단위')).toBeTruthy()
    expect(screen.getByText('정')).toBeTruthy()
    expect(screen.getByText('제품함량').tagName).toBe('DT')
    const medicationLabelRule = prescriptionReviewStyles.match(
      /\.prescription-review__medication-values dt\s*\{([^}]*)\}/,
    )?.[1]
    expect(medicationLabelRule).toContain('font-size: 12px')
    expect(medicationLabelRule).toContain('line-height: 16px')
    expect(screen.getByText('약 1/1개 검토 완료')).toBeTruthy()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('100')
  })

  it('조회 상태에서 긴 약물이름 전체를 줄임표 없이 표시한다', async () => {
    const longMedicationName =
      '아주긴합성약물이름으로모바일화면에서도전체확인이가능해야하는정'
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(
        withMedicationName(makeCompleteFields(), longMedicationName),
      ),
    )

    renderPage()

    const heading = await screen.findByRole('heading', {
      name: `${longMedicationName} 100mg`,
      level: 2,
    })

    expect(heading.textContent).toBe(`${longMedicationName} 100mg`)
    expect(heading.getAttribute('title')).toBeNull()
  })

  it('원본 처방전 details를 열고 닫아도 iframe 조회를 유지한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )

    renderPage()

    const summary = await screen.findByText('원본 처방전 보기')
    const details = summary.closest('details')
    const iframe = screen.getByTitle('원본 처방전')

    expect(details?.open).toBe(false)
    fireEvent.click(summary)
    expect(details?.open).toBe(true)
    expect(iframe.getAttribute('src')).toBe('blob:prescription')

    fireEvent.click(summary)
    expect(details?.open).toBe(false)
  })

  it('STATE-01에서 처방일과 약별 검토 완료 전 원본 대조를 잠근다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(makePendingFields()))

    renderPage()

    expect(await screen.findByText('약 0/1개 검토 완료')).toBeTruthy()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('0')
    const acknowledgement = screen.getByRole<HTMLInputElement>('checkbox')
    expect(acknowledgement.disabled).toBe(true)
    expect(screen.getByText('원본 처방전의 모든 항목을 직접 확인했습니다.')).toBeTruthy()
    expect(screen.queryByText(/원본 대조 (필요|완료)/)).toBeNull()
    expect((await getConfirmationButton() as HTMLButtonElement).disabled).toBe(true)
  })

  it('STATE-02 수정 진입 즉시 약 검토 완료와 원본 확인을 해제한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(makeCompleteFields()))
    mockPatchConfirmation()

    renderPage()

    const acknowledgement = await screen.findByRole<HTMLInputElement>('checkbox')
    expect(acknowledgement.disabled).toBe(false)
    fireEvent.click(acknowledgement)
    expect((await getConfirmationButton() as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))

    expect(screen.getByText('수정 시작과 동시에 이 약의 검토 완료가 해제됐습니다.')).toBeTruthy()
    expect(acknowledgement.checked).toBe(false)
    expect(acknowledgement.disabled).toBe(true)
    expect(screen.getByLabelText<HTMLInputElement>('약물이름').value).toBe('처방약 1')

    fireEvent.click(screen.getByRole('button', { name: '취소' }))

    expect(screen.getByText('약 0/1개 검토 완료')).toBeTruthy()
    expect(acknowledgement.disabled).toBe(true)
    expect(screen.getByRole('button', { name: '검토 완료' })).toBeTruthy()
  })

  it('약별 수정완료는 변경 필드만 기존 PATCH 계약으로 저장한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(makeCompleteFields()))
    mockPatchConfirmation()

    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '수정하기' }))
    expect(screen.getByLabelText<HTMLInputElement>('복용단위').value).toBe('정')
    fireEvent.change(screen.getByLabelText('약물이름'), {
      target: { value: '수정된 약' },
    })
    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

    await waitFor(() =>
      expect(updateExtractedField).toHaveBeenCalledWith(
        'MEDICATION_NAME-1',
        '수정된 약',
      ),
    )
    expect(updateExtractedField).toHaveBeenCalledTimes(1)
    expect(await screen.findByRole('heading', { name: '수정된 약 100mg' })).toBeTruthy()
  })

  it('STATE-03과 STATE-04는 전체 검토 후에도 사용자의 원본 대조 선택을 요구한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(makePendingFields()))
    mockPatchConfirmation()

    renderPage()

    const reviewButtons = await screen.findAllByRole('button', {
      name: '검토 완료',
    })
    fireEvent.click(reviewButtons[0])
    await waitFor(() =>
      expect(updateExtractedField).toHaveBeenCalledWith(
        'PRESCRIBED_DATE-0',
        '2026-08-22',
      ),
    )

    fireEvent.click(screen.getByRole('button', { name: '검토 완료' }))
    await waitFor(() => expect(updateExtractedField).toHaveBeenCalledTimes(8))
    expect(updateExtractedField).toHaveBeenCalledWith('DOSE_UNIT-1', '정')

    const acknowledgement = screen.getByRole<HTMLInputElement>('checkbox')
    const confirmButton = await getConfirmationButton() as HTMLButtonElement
    expect(acknowledgement.disabled).toBe(false)
    expect(acknowledgement.checked).toBe(false)
    expect(confirmButton.disabled).toBe(true)

    fireEvent.click(acknowledgement)

    expect(acknowledgement.checked).toBe(true)
    expect(confirmButton.disabled).toBe(false)
    expect(screen.getByText('원본 처방전의 모든 항목을 직접 확인했습니다.')).toBeTruthy()
    expect(screen.queryByText(/원본 대조 (필요|완료)/)).toBeNull()
  })

  it('처방일 raw_value가 대시 형식이 아니어도 Backend가 만든 normalized_value로 확정한다', async () => {
    const fields = makePendingFields().map((field) =>
      field.field_type === 'PRESCRIBED_DATE'
        ? { ...field, raw_value: '2026.08.22', normalized_value: '2026-08-22' }
        : field,
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))
    mockPatchConfirmation()

    renderPage()

    const reviewButtons = await screen.findAllByRole('button', {
      name: '검토 완료',
    })
    fireEvent.click(reviewButtons[0])

    await waitFor(() =>
      expect(updateExtractedField).toHaveBeenCalledWith(
        'PRESCRIBED_DATE-0',
        '2026-08-22',
      ),
    )
  })

  it('선택 필드를 비우면 PR #96 계약대로 confirmed_value null을 전송한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(makeCompleteFields()))
    mockPatchConfirmation()

    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '수정하기' }))
    fireEvent.change(screen.getByLabelText('제품함량'), {
      target: { value: '' },
    })
    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

    await waitFor(() =>
      expect(updateExtractedField).toHaveBeenCalledWith(
        'MEDICATION_STRENGTH-1',
        null,
      ),
    )
  })

  it.each([
    ['PENDING', 'OCR 작업을 기다리고 있어요'],
    ['PROCESSING', '처방전을 인식하고 있어요'],
    ['FAILED', '처방전 인식에 실패했어요'],
  ] as const)(
    'OCR 상태가 %s이면 검수 화면을 차단한다',
    async (ocrStatus, expectedTitle) => {
      vi.mocked(getOcrJob).mockResolvedValue(
        makeOcrResponse(makeCompleteFields(), 'document-1', ocrStatus),
      )

      renderPage()

      expect(await screen.findByText(expectedTitle)).toBeTruthy()
      expect(getPrescriptionDocumentFile).not.toHaveBeenCalled()
      expect(screen.queryByLabelText('처방전 약 이름')).toBeNull()
      expect(screen.queryByRole('button', { name: '확정하고 가이드 만들기' })).toBeNull()
    },
  )

  it('OCR_JOB_NOT_COMPLETED 오류는 OCR 완료 대기 상태로 안내한다', async () => {
    vi.mocked(getOcrJob).mockRejectedValue(
      new ApiError(
        409,
        'OCR 처리가 아직 완료되지 않았습니다.',
        'OCR_JOB_NOT_COMPLETED',
      ),
    )

    renderPage()

    expect(
      await screen.findByText('OCR 검수가 아직 준비되지 않았어요'),
    ).toBeTruthy()
    expect(screen.getByText('OCR 처리가 아직 완료되지 않았습니다.')).toBeTruthy()
    expect(getPrescriptionDocumentFile).not.toHaveBeenCalled()
  })

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

    expect(await screen.findByText('문서 B 처방약 100mg')).toBeTruthy()
    expect(screen.queryByText('문서 A 처방약 100mg')).toBeNull()

    await act(async () => documentAFile.resolve(new Blob(['document-a'])))

    await waitFor(() => {
      expect(screen.getByText('문서 B 처방약 100mg')).toBeTruthy()
      expect(screen.queryByText('문서 A 처방약 100mg')).toBeNull()
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

    expect(await screen.findByText('문서 A 처방약 100mg')).toBeTruthy()
    const acknowledgement = screen.getByRole('checkbox')
    fireEvent.click(acknowledgement)
    expect(acknowledgement).toHaveProperty('checked', true)

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    const prescribedDateInput = screen.getByLabelText('처방일')
    fireEvent.change(prescribedDateInput, {
      target: { value: '2026-08-23' },
    })
    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))
    expect(await screen.findByText('검토 정보를 저장하는 중 오류가 발생했습니다.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '문서 B로 이동' }))

    expect(
      await screen.findByText('처방전 검수 정보를 불러오고 있어요.'),
    ).toBeTruthy()
    expect(screen.queryByText('검토 정보를 저장하는 중 오류가 발생했습니다.')).toBeNull()
    expect(screen.queryByRole('checkbox')).toBeNull()
    await waitFor(() =>
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:prescription'),
    )

    await act(async () => documentBFile.resolve(new Blob(['document-b'])))

    expect(await screen.findByText('문서 B 처방약 100mg')).toBeTruthy()
    expect(screen.getByRole('checkbox')).toHaveProperty('checked', false)
    expect(screen.queryByText('문서 A 처방약 100mg')).toBeNull()
  })

  it('일부 약만 확인된 경우 처방을 확정할 수 없다', async () => {
    const fields = makeCompleteFields(2).map((field) =>
      field.medication_index === 2
        ? { ...field, confirmed_value: null, confirmation_status: 'UNCONFIRMED' }
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

    const { container } = renderPage()

    expect(
      await screen.findByText('필수 처방 항목이 누락됐어요'),
    ).toBeTruthy()
    expect(screen.getByText('필수 처방 항목 인식 누락')).toBeTruthy()
    expect(screen.getByText('처방전을 다시 업로드해 주세요')).toBeTruthy()
    expect(
      container.querySelector(
        '.prescription-review__status-icon.is-warning',
      )?.textContent,
    ).toBe('!')
    expect(
      container.querySelector('.prescription-review__status-icon.is-success'),
    ).toBeNull()
    expect(screen.getByText(/OCR을 다시 실행해 주세요/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))
    expect(screen.queryByLabelText('투약일수')).toBeNull()
    expect(updateExtractedField).not.toHaveBeenCalled()
    expect(await getConfirmationButton()).toHaveProperty('disabled', true)
  })

  it('PRESCRIBED_DATE가 OCR 결과에 없으면 누락 상태로 안내하고 확정을 차단한다', async () => {
    const fields = makeCompleteFields().filter(
      (field) => field.field_type !== 'PRESCRIBED_DATE',
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))

    const { container } = renderPage()

    expect(
      await screen.findByText('필수 처방 항목이 누락됐어요'),
    ).toBeTruthy()
    expect(screen.getByText('필수 처방 항목 인식 누락')).toBeTruthy()
    expect(
      container.querySelector(
        '.prescription-review__status-icon.is-warning',
      )?.textContent,
    ).toBe('!')
    expect(
      container.querySelector('.prescription-review__status-icon.is-success'),
    ).toBeNull()
    expect(screen.getByText(/처방일·약 이름/)).toBeTruthy()
    expect(screen.queryByLabelText('처방일')).toBeNull()
    expect(screen.getByRole('checkbox')).toHaveProperty('disabled', true)
    expect(await getConfirmationButton()).toHaveProperty('disabled', true)
  })

  it('PRESCRIBED_DATE placeholder를 직접 입력해 기존 PATCH로 저장한 뒤 검토 완료할 수 있다', async () => {
    const placeholder = makePlaceholderField('PRESCRIBED_DATE', 0)
    const fields = makeCompleteFields().map((field) =>
      field.field_type === 'PRESCRIBED_DATE' ? placeholder : field,
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))
    vi.mocked(updateExtractedField).mockResolvedValue({
      data: {
        ...placeholder,
        confirmed_value: '2026-08-22',
        confirmation_status: 'CONFIRMED',
      },
    })

    const { container } = renderPage()

    expect(
      await screen.findByText('일부 필수 항목 인식 누락'),
    ).toBeTruthy()
    expect(
      container.querySelector(
        '.prescription-review__status-icon.is-warning',
      )?.textContent,
    ).toBe('!')
    expect(
      container.querySelector('.prescription-review__status-icon.is-success'),
    ).toBeNull()
    expect(
      screen.getByText(/OCR이 인식하지 못해 직접 입력이 필요한 필드예요/),
    ).toBeTruthy()
    const acknowledgement = screen.getByRole<HTMLInputElement>('checkbox')
    const confirmButton = await getConfirmationButton()
    expect(acknowledgement.disabled).toBe(true)
    expect(confirmButton).toHaveProperty('disabled', true)

    const prescribedDateInput = screen.getByLabelText<HTMLInputElement>('처방일')
    expect(prescribedDateInput.value).toBe('')
    expect(prescribedDateInput.placeholder).toBe('필수 입력')
    expect(
      screen.getByText(/OCR이 인식하지 못해 직접 입력이 필요한 필드예요/),
    ).toBeTruthy()

    fireEvent.change(prescribedDateInput, {
      target: { value: '2026-08-22' },
    })
    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

    await waitFor(() =>
      expect(updateExtractedField).toHaveBeenCalledWith(
        'PRESCRIBED_DATE-0',
        '2026-08-22',
      ),
    )
    expect(updateExtractedField).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(acknowledgement.disabled).toBe(false))
    expect(confirmButton).toHaveProperty('disabled', true)

    fireEvent.click(acknowledgement)

    expect(confirmButton).toHaveProperty('disabled', false)
  })

  it.each([
    ['DOSE_VALUE', '1회 복용량', '0.5'],
    ['FREQUENCY_PER_DAY', '하루횟수', '3'],
    ['DURATION_DAYS', '투약일수', '7'],
  ] as const)(
    '약물 필수 %s placeholder를 직접 입력해 기존 PATCH로 저장할 수 있다',
    async (fieldType, fieldLabel, confirmedValue) => {
      const placeholder = makePlaceholderField(fieldType, 1)
      const fields = makeCompleteFields().map((field) =>
        field.field_type === fieldType ? placeholder : field,
      )
      vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))
      vi.mocked(updateExtractedField).mockResolvedValue({
        data: {
          ...placeholder,
          confirmed_value: confirmedValue,
          confirmation_status: 'CONFIRMED',
        },
      })

      renderPage()

      expect(
        await screen.findByText('일부 필수 항목 인식 누락'),
      ).toBeTruthy()
      expect(
        screen.getByText(/OCR이 인식하지 못해 직접 입력이 필요한 필드예요/),
      ).toBeTruthy()
      const acknowledgement = screen.getByRole<HTMLInputElement>('checkbox')
      const confirmButton = await getConfirmationButton()
      expect(acknowledgement.disabled).toBe(true)
      expect(confirmButton).toHaveProperty('disabled', true)

      const input = screen.getByLabelText<HTMLInputElement>(fieldLabel)
      expect(input.value).toBe('')
      expect(input.placeholder).toBe('필수 입력')

      fireEvent.change(input, { target: { value: confirmedValue } })
      fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

      await waitFor(() =>
        expect(updateExtractedField).toHaveBeenCalledWith(
          `${fieldType}-1`,
          confirmedValue,
        ),
      )
      expect(updateExtractedField).toHaveBeenCalledTimes(1)
      await waitFor(() => expect(acknowledgement.disabled).toBe(false))
      expect(confirmButton).toHaveProperty('disabled', true)
    },
  )

  it('정상 MEDICATION_NAME row는 required set에 있어도 placeholder로 판정하지 않는다', async () => {
    const fields = makeCompleteFields().map((field) =>
      field.field_type === 'MEDICATION_NAME'
        ? {
            ...field,
            confirmed_value: null,
            confirmation_status: 'UNCONFIRMED' as const,
          }
        : field,
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))

    const { container } = renderPage()

    expect(await screen.findByText('처방약 1 100mg')).toBeTruthy()
    expect(
      container.querySelector(
        '.prescription-review__status-icon.is-success',
      )?.textContent,
    ).toBe('✓')
    expect(screen.queryByText('일부 필수 항목 인식 누락')).toBeNull()
    expect(
      screen.queryByText(/OCR이 인식하지 못해 직접 입력이 필요한 필드예요/),
    ).toBeNull()
    expect(screen.queryByLabelText('약물이름')).toBeNull()
    expect(updateExtractedField).not.toHaveBeenCalled()
  })

  it('완전 null MEDICATION_NAME row는 직접 입력을 열지 않고 재업로드 상태로 차단한다', async () => {
    const fields = makeCompleteFields().map((field) =>
      field.field_type === 'MEDICATION_NAME'
        ? makePlaceholderField('MEDICATION_NAME', field.medication_index)
        : field,
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))

    renderPage()

    expect(await screen.findByText('약 이름을 인식하지 못했어요')).toBeTruthy()
    expect(
      screen.getByText('약 이름은 직접 입력해 검수를 진행할 수 없습니다.'),
    ).toBeTruthy()
    expect(screen.getByText(/OCR을 다시 실행해 주세요/)).toBeTruthy()
    expect(
      screen.getByRole('button', { name: '처방전 다시 업로드하기' }),
    ).toBeTruthy()
    expect(screen.queryByLabelText('약물이름')).toBeNull()
    expect(screen.queryByText('일부 필수 항목 인식 누락')).toBeNull()
    expect(getPrescriptionDocumentFile).not.toHaveBeenCalled()
    expect(updateExtractedField).not.toHaveBeenCalled()
  })

  it('값이 있는 선택 필드는 저장되어야 최종 확인할 수 있다.', async () => {
    const fields = makeCompleteFields().map((field) =>
      field.field_type === 'DOSE_UNIT'
        ? { ...field, confirmed_value: null, confirmation_status: 'UNCONFIRMED' }
        : field,
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))

    renderPage()

    const acknowledgement = await screen.findByRole('checkbox')
    expect(acknowledgement).toHaveProperty('disabled', true)
    expect(await getConfirmationButton()).toHaveProperty('disabled', true)
    expect(screen.getByText('복용단위')).toBeTruthy()
    expect(screen.getByText('정')).toBeTruthy()
    expect(updateExtractedField).not.toHaveBeenCalled()
  })

  it('잘못 인식된 DOSE_UNIT은 사용자가 수정한 값만 confirmed_value로 저장한다', async () => {
    const fields = makeCompleteFields().map((field) =>
      field.field_type === 'DOSE_UNIT'
        ? {
            ...field,
            raw_value: '캡슐',
            confirmed_value: null,
            confirmation_status: 'UNCONFIRMED' as const,
          }
        : field,
    )
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

    expect(await screen.findByText('캡슐')).toBeTruthy()
    expect(updateExtractedField).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))
    const doseUnitInput = screen.getByLabelText<HTMLInputElement>('복용단위')
    expect(doseUnitInput.value).toBe('캡슐')
    fireEvent.change(doseUnitInput, { target: { value: '정' } })
    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

    await waitFor(() =>
      expect(updateExtractedField).toHaveBeenCalledWith('DOSE_UNIT-1', '정'),
    )
    expect(updateExtractedField).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['TIMING', '복용조건'],
    ['MEDICATION_STRENGTH', '제품함량'],
    ['DOSE_UNIT', '복용단위'],
  ] as const)(
    '값이 없는 선택 %s 필드는 저장하지 않아도 최종 확인할 수 있다',
    async (fieldType, fieldLabel) => {
      const fields = makeCompleteFields().map((field) =>
        field.field_type === fieldType
          ? {
              ...field,
              raw_value: null,
              confirmed_value: null,
              confirmation_status: 'UNCONFIRMED',
            }
          : field,
      )
      vi.mocked(getOcrJob).mockResolvedValue(
        makeOcrResponse(fields),
      )

      renderPage()

      const acknowledgement = await screen.findByRole<HTMLInputElement>('checkbox')
      const confirmButton = await getConfirmationButton()
      expect(screen.getByText(fieldLabel)).toBeTruthy()
      expect(screen.queryByText('일부 필수 항목 인식 누락')).toBeNull()
      expect(acknowledgement.disabled).toBe(false)
      expect(confirmButton).toHaveProperty('disabled', true)

      fireEvent.click(acknowledgement)

      expect(confirmButton).toHaveProperty('disabled', false)
      expect(updateExtractedField).not.toHaveBeenCalled()
    },
  )

  it('선택 필드가 OCR 응답에서 생략되어도 검토 완료와 확정을 차단하지 않는다', async () => {
    const optionalFieldTypes = new Set([
      'MEDICATION_STRENGTH',
      'DOSE_UNIT',
      'TIMING',
    ])
    const fields = makeCompleteFields().filter(
      (field) => !optionalFieldTypes.has(field.field_type),
    )
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))

    renderPage()

    const acknowledgement = await screen.findByRole<HTMLInputElement>('checkbox')
    const confirmButton = await getConfirmationButton()
    expect(acknowledgement.disabled).toBe(false)
    expect(confirmButton).toHaveProperty('disabled', true)

    fireEvent.click(acknowledgement)

    expect(confirmButton).toHaveProperty('disabled', false)
    expect(updateExtractedField).not.toHaveBeenCalled()
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

    const acknowledgement = await screen.findByRole<HTMLInputElement>('checkbox')
    const confirmButton = await getConfirmationButton()
    expect(confirmButton).toHaveProperty('disabled', true)

    fireEvent.click(acknowledgement)
    expect(confirmButton).toHaveProperty('disabled', false)

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    const prescribedDateInput = screen.getByLabelText('처방일')
    fireEvent.change(prescribedDateInput, { target: { value: '2026-08-23' } })
    expect(acknowledgement).toHaveProperty('checked', false)
    expect(acknowledgement).toHaveProperty('disabled', true)
    expect(confirmButton).toHaveProperty('disabled', true)

    const dateSaveButton = screen.getByRole('button', { name: '수정완료' })
    fireEvent.click(dateSaveButton)
    await waitFor(() => expect(dateSaveButton).toHaveProperty('disabled', true))

    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))
    const medicationNameInput = screen.getByLabelText('약물이름')
    fireEvent.change(medicationNameInput, { target: { value: '동시 저장 약' } })
    const medicationSaveButton = screen.getByRole('button', { name: '수정완료' })
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

  it('필드 저장 중에는 같은 input만 잠그고 다른 필드는 독립적으로 편집한다', async () => {
    const fields = makeCompleteFields()
    const saveRequest = createDeferred<{ data: ExtractedField }>()
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(fields))
    vi.mocked(updateExtractedField).mockImplementation(
      () => saveRequest.promise,
    )

    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '수정하기' }))
    const medicationNameInput = screen.getByLabelText('약물이름')
    const doseInput = screen.getByLabelText('1회 복용량')

    fireEvent.change(medicationNameInput, {
      target: { value: '저장 요청한 약 이름' },
    })
    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

    await waitFor(() =>
      expect(medicationNameInput).toHaveProperty('disabled', true),
    )
    expect(doseInput).toHaveProperty('disabled', false)

    const medicationName = fields.find(
      (field) => field.field_type === 'MEDICATION_NAME',
    )
    if (!medicationName) throw new Error('medication name field not found')

    await act(async () =>
      saveRequest.resolve({
        data: {
          ...medicationName,
          confirmed_value: '저장 요청한 약 이름',
          confirmation_status: 'CONFIRMED',
        },
      }),
    )

    expect(
      await screen.findByRole('heading', {
        name: '저장 요청한 약 이름 100mg',
      }),
    ).toBeTruthy()
    expect(screen.queryByLabelText('약물이름')).toBeNull()
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
      '하루횟수',
      '0',
      '하루 횟수는 0보다 큰 정수로 입력해 주세요.',
    ],
    ['하루횟수', '-1', '하루 횟수는 정수 형식으로 입력해 주세요.'],
    ['하루횟수', '2.5', '하루 횟수는 정수 형식으로 입력해 주세요.'],
    [
      '투약일수',
      '0',
      '복용 기간은 0보다 큰 정수로 입력해 주세요.',
    ],
    ['투약일수', '-7', '복용 기간은 정수 형식으로 입력해 주세요.'],
    ['투약일수', '7.5', '복용 기간은 정수 형식으로 입력해 주세요.'],
  ])('%s의 유효하지 않은 숫자 값 %s은 PATCH 전에 차단한다', async (
    fieldLabel,
    invalidValue,
    errorMessage,
  ) => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '수정하기' }))
    const numericInput = screen.getByLabelText(fieldLabel)
    fireEvent.change(numericInput, { target: { value: invalidValue } })
    const numericField = numericInput.closest('.prescription-review__edit-field')
    if (!numericField) throw new Error('numeric field not found')

    expect(screen.getByRole('button', { name: '수정완료' })).toHaveProperty(
      'disabled',
      true,
    )

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

      fireEvent.click(await screen.findByRole('button', { name: '수정하기' }))
      const doseInput = screen.getByLabelText('1회 복용량')
      fireEvent.change(doseInput, { target: { value: validValue } })
      fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

      await waitFor(() =>
        expect(updateExtractedField).toHaveBeenCalledWith(
          'DOSE_VALUE-1',
          validValue,
        ),
      )
    },
  )

  it('PRESCRIPTION_REQUIRED_FIELD_MISSING 오류는 재업로드 상태로 전환한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(confirmPrescription).mockRejectedValue(
      new ApiError(
        422,
        '처방 확정 필수 항목이 누락되었습니다.',
        'PRESCRIPTION_REQUIRED_FIELD_MISSING',
      ),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(await getConfirmationButton())

    expect(
      await screen.findByText('처방 확정에 필요한 항목이 부족해요'),
    ).toBeTruthy()
    expect(screen.queryByLabelText('처방전 약 이름')).toBeNull()
    expect(screen.queryByRole('button', { name: '확정하고 가이드 만들기' })).toBeNull()
  })

  it('PRESCRIPTION_ALREADY_CONFIRMED 오류 후에는 편집 UI를 제공하지 않는다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(confirmPrescription).mockRejectedValue(
      new ApiError(
        409,
        '이미 확정된 처방입니다.',
        'PRESCRIPTION_ALREADY_CONFIRMED',
      ),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(await getConfirmationButton())

    expect(await screen.findByText('이미 확정된 처방이에요')).toBeTruthy()
    expect(screen.queryByLabelText('처방전 약 이름')).toBeNull()
    expect(screen.queryByText('세부 항목 확인 및 수정')).toBeNull()
    expect(screen.queryByRole('button', { name: '확정하고 가이드 만들기' })).toBeNull()
  })

  it('EXTRACTED_FIELD_NOT_FOUND 오류는 최신 OCR 재검수 상태로 전환한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(updateExtractedField).mockRejectedValue(
      new ApiError(
        404,
        '검수 항목을 찾을 수 없습니다.',
        'EXTRACTED_FIELD_NOT_FOUND',
      ),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '수정하기' }))
    fireEvent.change(screen.getByLabelText('약물이름'), {
      target: { value: '수정 약' },
    })
    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

    expect(
      await screen.findByText('검수하던 항목을 찾을 수 없어요'),
    ).toBeTruthy()
    expect(screen.queryByLabelText('처방전 약 이름')).toBeNull()
    expect(screen.queryByRole('button', { name: '확정하고 가이드 만들기' })).toBeNull()
  })

  it('VALIDATION_FAILED 오류는 입력값 수정 안내를 표시하고 편집을 유지한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(updateExtractedField).mockRejectedValue(
      new ApiError(
        422,
        '입력 형식을 확인해 주세요.',
        'VALIDATION_FAILED',
      ),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '수정하기' }))
    fireEvent.change(screen.getByLabelText('약물이름'), {
      target: { value: '수정 약' },
    })
    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

    expect(await screen.findByText('입력값을 확인해 주세요')).toBeTruthy()
    expect(screen.getByText('입력 형식을 확인해 주세요.')).toBeTruthy()
    expect(screen.getByLabelText('약물이름')).toBeTruthy()
    expect(await getConfirmationButton()).toBeTruthy()
  })

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

    expect(screen.getByRole('button', { name: '처방 확정 중...' })).toHaveProperty(
      'disabled',
      true,
    )
    expect(screen.getByRole('checkbox')).toHaveProperty('disabled', true)

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
    expect(screen.queryByLabelText('처방전 약 이름')).toBeNull()
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
      screen.queryByRole('button', { name: '확정하고 가이드 만들기' }),
    ).toBeNull()
  })

  it('처방 확정 성공 후 실제 prescription_id로 Guide를 생성하고 Guide route로 이동한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(createGuide).mockResolvedValue(
      makeGuideResponse('guide-created', 'prescription-1'),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(await getConfirmationButton())

    await waitFor(() =>
      expect(confirmPrescription).toHaveBeenCalledWith('document-1'),
    )
    await waitFor(() =>
      expect(createGuide).toHaveBeenCalledWith('prescription-1'),
    )
    expect(
      await screen.findByText('Guide route: guide-created'),
    ).toBeTruthy()
    expect(confirmPrescription).toHaveBeenCalledTimes(1)
    expect(createGuide).toHaveBeenCalledTimes(1)
  })

  it('Guide 생성 중 중복 요청을 막는다', async () => {
    const guideCreation = createDeferred<GuideResponse>()
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(createGuide).mockImplementation(() => guideCreation.promise)

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    const confirmButton = await getConfirmationButton()
    fireEvent.click(confirmButton)
    fireEvent.click(confirmButton)

    await waitFor(() => expect(createGuide).toHaveBeenCalledTimes(1))
    expect(confirmPrescription).toHaveBeenCalledTimes(1)

    const creatingButton = await screen.findByRole('button', {
      name: '가이드 생성 중...',
    })
    expect(creatingButton).toHaveProperty('disabled', true)
    fireEvent.click(creatingButton)
    expect(createGuide).toHaveBeenCalledTimes(1)

    await act(async () => {
      guideCreation.resolve(
        makeGuideResponse('guide-created-once', 'prescription-1'),
      )
      await guideCreation.promise
    })

    expect(
      await screen.findByText('Guide route: guide-created-once'),
    ).toBeTruthy()
  })

  it('Guide 생성 중 화면을 나가면 늦은 성공 응답이 Guide 화면으로 이동시키지 않는다', async () => {
    const guideCreation = createDeferred<GuideResponse>()
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(createGuide).mockImplementation(() => guideCreation.promise)

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(await getConfirmationButton())

    await waitFor(() => expect(createGuide).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: '이전 화면' }))
    expect(screen.getByText('처방전 업로드 화면')).toBeTruthy()

    await act(async () => {
      guideCreation.resolve(
        makeGuideResponse('late-guide', 'prescription-1'),
      )
      await guideCreation.promise
    })

    expect(screen.getByText('처방전 업로드 화면')).toBeTruthy()
    expect(screen.queryByText('Guide route: late-guide')).toBeNull()
  })

  it('Guide 생성 실패 후 확정 처방을 유지하고 같은 prescription_id로 Guide만 재시도한다', async () => {
    const retryCreation = createDeferred<GuideResponse>()
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(createGuide)
      .mockRejectedValueOnce(new Error('first guide failure'))
      .mockImplementationOnce(() => retryCreation.promise)

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(await getConfirmationButton())

    expect(await screen.findByText('처방정보가 확정되었어요')).toBeTruthy()
    expect(
      await screen.findByText('복약 가이드를 만드는 중 오류가 발생했습니다.'),
    ).toBeTruthy()
    expect(screen.queryByLabelText('처방전 약 이름')).toBeNull()
    expect(confirmPrescription).toHaveBeenCalledTimes(1)
    expect(createGuide).toHaveBeenNthCalledWith(1, 'prescription-1')

    const retryButton = screen.getByRole('button', {
      name: '가이드 생성 다시 시도',
    })
    fireEvent.click(retryButton)
    fireEvent.click(retryButton)

    await waitFor(() => expect(createGuide).toHaveBeenCalledTimes(2))
    expect(confirmPrescription).toHaveBeenCalledTimes(1)
    expect(createGuide).toHaveBeenNthCalledWith(2, 'prescription-1')
    expect(
      screen.getByRole('button', { name: '가이드 생성 중...' }),
    ).toHaveProperty('disabled', true)

    await act(async () => {
      retryCreation.resolve(
        makeGuideResponse('guide-after-retry', 'prescription-1'),
      )
      await retryCreation.promise
    })

    expect(
      await screen.findByText('Guide route: guide-after-retry'),
    ).toBeTruthy()
    expect(confirmPrescription).toHaveBeenCalledTimes(1)
    expect(createGuide).toHaveBeenCalledTimes(2)
  })

  it('버튼 문구와 실제 처방 확정 동작이 일치한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )

    renderPage()

    fireEvent.click(await screen.findByRole('checkbox'))
    const confirmButton = await getConfirmationButton()
    expect(confirmButton.textContent).toBe('처방전 확정 및 가이드 만들기')

    fireEvent.click(confirmButton)

    await waitFor(() =>
      expect(confirmPrescription).toHaveBeenCalledWith('document-1'),
    )
  })
})

describe('PrescriptionReviewPage manual medication add', () => {
  it('기존 약물을 편집 중이거나 미저장한 경우 추가 진입을 차단한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    renderPage()

    const addButton = await screen.findByRole('button', { name: '약물 추가' })
    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))

    expect(addButton).toHaveProperty('disabled', true)
    fireEvent.change(screen.getByLabelText('약물이름'), {
      target: { value: '미저장 약물명' },
    })
    expect(addButton).toHaveProperty('disabled', true)
    expect(createManualMedication).not.toHaveBeenCalled()
  })

  it('필수·선택 필드를 구분하고 client validation 실패 시 API를 호출하지 않는다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '약물 추가' }))
    expect(screen.getAllByText('필수')).toHaveLength(4)
    expect(screen.getAllByText('선택')).toHaveLength(3)
    expect(screen.getByRole('checkbox')).toHaveProperty('disabled', true)

    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))

    expect(await screen.findByText('약물이름을 입력해 주세요.')).toBeTruthy()
    expect(
      screen.getByLabelText('1회 복용량').getAttribute('aria-invalid'),
    ).toBe('true')
    expect(createManualMedication).not.toHaveBeenCalled()
  })

  it('Backend NUMERIC(10,3) 범위의 1.001을 부동소수점 오차 없이 허용한다', async () => {
    const originalFields = makeCompleteFields()
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(originalFields))
    vi.mocked(createManualMedication).mockResolvedValue(
      makeOcrResponse([...originalFields, ...makeManualMedicationFields()]),
    )
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '약물 추가' }))
    fillManualMedicationForm()
    fireEvent.change(screen.getByLabelText('1회 복용량'), {
      target: { value: '1.001' },
    })
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))

    await waitFor(() => expect(createManualMedication).toHaveBeenCalledTimes(1))
    expect(vi.mocked(createManualMedication).mock.calls[0][1].dose_value).toBe(
      '1.001',
    )
  })

  it('Backend NUMERIC(10,3)을 넘는 소수점 4자리 값은 거부한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '약물 추가' }))
    fillManualMedicationForm()
    fireEvent.change(screen.getByLabelText('1회 복용량'), {
      target: { value: '1.0001' },
    })
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))

    expect(
      await screen.findByText(
        '1회 복용량은 0보다 큰 숫자로, 소수점 아래 3자리까지 입력해 주세요.',
      ),
    ).toBeTruthy()
    expect(createManualMedication).not.toHaveBeenCalled()
  })

  it('저장 응답의 전체 field 목록으로 갱신하고 새 약물을 기존 검수 흐름에 포함한다', async () => {
    const originalFields = makeCompleteFields()
    const response = makeOcrResponse([
      ...originalFields,
      ...makeManualMedicationFields(),
    ])
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(originalFields))
    vi.mocked(createManualMedication).mockResolvedValue(response)
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '약물 추가' }))
    fillManualMedicationForm()
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))

    await waitFor(() => expect(createManualMedication).toHaveBeenCalledTimes(1))
    expect(createManualMedication).toHaveBeenCalledWith(
      'job-1',
      {
        medication_name: '직접입력약정',
        medication_strength: '50mg',
        dose_value: '0.5',
        dose_unit: '정',
        frequency_per_day: '2',
        timing: '저녁 식후',
        duration_days: '5',
      },
      expect.stringMatching(
        /^manual-medication:[0-9a-f]{8}-[0-9a-f-]{27}$/,
      ),
    )
    const newMedicationHeading = await screen.findByRole('heading', {
      name: '직접입력약정 50mg',
    })
    const newMedicationCard = newMedicationHeading.closest('section')
    expect(newMedicationCard).not.toBeNull()
    expect(screen.getByText('약 1/2개 검토 완료')).toBeTruthy()
    expect(within(newMedicationCard!).getByText('검토 전')).toBeTruthy()

    fireEvent.click(
      within(newMedicationCard!).getByRole('button', { name: '검토 완료' }),
    )
    expect(await screen.findByText('약 2/2개 검토 완료')).toBeTruthy()
    expect(screen.getByRole('checkbox')).toHaveProperty('disabled', false)
  })

  it('동일 요청 재시도에서 Idempotency-Key를 재사용한다', async () => {
    const originalFields = makeCompleteFields()
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(originalFields))
    vi.mocked(createManualMedication)
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce(
        makeOcrResponse([...originalFields, ...makeManualMedicationFields()]),
      )
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '약물 추가' }))
    fillManualMedicationForm()
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))
    await waitFor(() => expect(createManualMedication).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))
    await waitFor(() => expect(createManualMedication).toHaveBeenCalledTimes(2))

    expect(vi.mocked(createManualMedication).mock.calls[0][2]).toBe(
      vi.mocked(createManualMedication).mock.calls[1][2],
    )
  })

  it('새로운 약물 추가 시도에서는 새 Idempotency-Key를 생성한다', async () => {
    const originalFields = makeCompleteFields()
    const firstManualFields = makeManualMedicationFields(2)
    vi.mocked(getOcrJob).mockResolvedValue(makeOcrResponse(originalFields))
    vi.mocked(createManualMedication)
      .mockResolvedValueOnce(
        makeOcrResponse([...originalFields, ...firstManualFields]),
      )
      .mockResolvedValueOnce(
        makeOcrResponse([
          ...originalFields,
          ...firstManualFields,
          ...makeManualMedicationFields(3),
        ]),
      )
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '약물 추가' }))
    fillManualMedicationForm('첫번째약')
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))
    await screen.findByRole('heading', { name: '직접입력약정 50mg' })

    fireEvent.click(screen.getByRole('button', { name: '약물 추가' }))
    fillManualMedicationForm('셋번째약')
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))
    await waitFor(() => expect(createManualMedication).toHaveBeenCalledTimes(2))

    expect(vi.mocked(createManualMedication).mock.calls[0][2]).not.toBe(
      vi.mocked(createManualMedication).mock.calls[1][2],
    )
  })

  it('저장 중에는 폼을 잠그고 저장 상태를 표시한다', async () => {
    const deferred = createDeferred<OcrJobResponse>()
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(createManualMedication).mockImplementation(() => deferred.promise)
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '약물 추가' }))
    fillManualMedicationForm()
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))

    const savingButton = await screen.findByRole('button', { name: '저장 중...' })
    expect(savingButton).toHaveProperty('disabled', true)
    expect(screen.getByLabelText('약물이름')).toHaveProperty('disabled', true)

    await act(async () => {
      deferred.resolve(
        makeOcrResponse([
          ...makeCompleteFields(),
          ...makeManualMedicationFields(),
        ]),
      )
      await deferred.promise
    })
  })

  it.each([
    [
      'CONCURRENT_UPDATE_IN_PROGRESS',
      '약물을 저장하고 있는 요청이 있어요',
    ],
    ['IDEMPOTENCY_KEY_CONFLICT', '저장 요청을 다시 확인해 주세요'],
    ['IDEMPOTENCY_KEY_REQUIRED', '저장 요청을 준비하지 못했어요'],
    ['IDEMPOTENCY_KEY_INVALID', '저장 요청을 준비하지 못했어요'],
    ['VALIDATION_FAILED', '입력값을 확인해 주세요'],
  ])('%s를 Backend 용어 없는 안내로 표시한다', async (code, title) => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(createManualMedication).mockRejectedValue(
      new ApiError(409, 'backend internal message', code),
    )
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '약물 추가' }))
    fillManualMedicationForm()
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))

    expect(await screen.findByText(title)).toBeTruthy()
    expect(screen.queryByText('backend internal message')).toBeNull()
  })

  it.each([
    ['OCR_JOB_NOT_FOUND', '처방전 정보를 찾을 수 없어요'],
    ['MEDICAL_DOCUMENT_NOT_FOUND', '처방전 정보를 찾을 수 없어요'],
    ['OCR_JOB_NOT_COMPLETED', 'OCR 검수가 아직 준비되지 않았어요'],
    ['PRESCRIPTION_ALREADY_CONFIRMED', '이미 확정된 처방이에요'],
  ])('%s에서 안전한 차단 상태로 전환한다', async (code, title) => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(createManualMedication).mockRejectedValue(
      new ApiError(409, 'backend internal message', code),
    )
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '약물 추가' }))
    fillManualMedicationForm()
    fireEvent.click(screen.getByRole('button', { name: '약물 저장' }))

    expect(await screen.findByText(title)).toBeTruthy()
    expect(screen.queryByRole('button', { name: '약물 추가' })).toBeNull()
  })
})

describe('PrescriptionReviewPage #809 source highlight', () => {
  const normalizedSourceImage = {
    normalized: true,
    width: 1200,
    height: 1600,
    url: '/api/v1/documents/document-1/normalized-file',
  }

  function withSourceImage(
    response: OcrJobResponse,
    sourceImage: OcrJobResponse['data']['source_image'],
  ): OcrJobResponse {
    return { ...response, data: { ...response.data, source_image: sourceImage } }
  }

  function withSourceLocation(
    fields: ExtractedField[],
    fieldId: string,
    sourceLocation: ExtractedField['source_location'],
  ) {
    return fields.map((field) =>
      field.field_id === fieldId ? { ...field, source_location: sourceLocation } : field,
    )
  }

  function makeHighlightableResponse(
    sourceLocation: ExtractedField['source_location'] = {
      page: 1,
      bbox: [120, 320, 240, 40],
    },
  ) {
    const fields = withSourceLocation(
      makeCompleteFields(),
      'PRESCRIBED_DATE-0',
      sourceLocation,
    )

    return withSourceImage(makeOcrResponse(fields), normalizedSourceImage)
  }

  async function openViewerAndLoadImage() {
    const image = (await screen.findByTitle('원본 처방전')) as HTMLImageElement
    fireEvent.load(image)
    // 처방일 input은 수정 모드에서만 렌더된다.
    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    return image
  }

  it('normalized 응답이면 iframe 대신 img viewer를 사용하고 정규화 URL을 인증 fetch한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeHighlightableResponse())
    renderPage()

    const image = await screen.findByTitle('원본 처방전')

    expect(image.tagName).toBe('IMG')
    expect(image.getAttribute('src')).toBe('blob:prescription')
    expect(getPrescriptionNormalizedImage).toHaveBeenCalledWith(
      '/api/v1/documents/document-1/normalized-file',
    )
    // 원본 /file 위에는 정규화 좌표를 겹치지 않는다.
    expect(getPrescriptionDocumentFile).not.toHaveBeenCalled()
  })

  it('유효한 source_location 필드를 선택하면 표시 비율로 변환된 bbox를 강조한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeHighlightableResponse())
    renderPage()
    await openViewerAndLoadImage()

    expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()

    fireEvent.focus(screen.getByLabelText('처방일'))

    const highlight = await screen.findByTestId('prescription-source-highlight')

    // 1200x1600 기준 [120, 320, 240, 40] -> 10% / 20% / 20% / 2.5%
    expect(highlight.style.left).toBe('10%')
    expect(highlight.style.top).toBe('20%')
    expect(highlight.style.width).toBe('20%')
    expect(highlight.style.height).toBe('2.5%')
  })

  it('필드 클릭으로도 viewer를 열고 강조한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeHighlightableResponse())
    renderPage()
    await openViewerAndLoadImage()

    fireEvent.click(screen.getByLabelText('처방일'))

    expect(
      await screen.findByTestId('prescription-source-highlight'),
    ).toBeTruthy()
  })

  it('이미지 로딩 성공 전에는 강조하지 않는다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeHighlightableResponse())
    renderPage()

    await screen.findByTitle('원본 처방전')
    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))

    expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
  })

  it('이미지 로딩에 실패하면 강조를 해제한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeHighlightableResponse())
    renderPage()
    const image = await openViewerAndLoadImage()

    fireEvent.focus(screen.getByLabelText('처방일'))
    expect(
      await screen.findByTestId('prescription-source-highlight'),
    ).toBeTruthy()

    fireEvent.error(image)

    await waitFor(() => {
      expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
    })
    // fail-closed여도 검수는 계속 가능하다.
    expect(screen.getByLabelText('처방일')).toBeTruthy()
  })

  it.each([
    ['source_location이 null', null],
    ['page가 1이 아님', { page: 2, bbox: [120, 320, 240, 40] }],
    ['bbox 폭이 0', { page: 1, bbox: [120, 320, 0, 40] }],
    ['bbox 값이 음수', { page: 1, bbox: [-10, 320, 240, 40] }],
    ['bbox가 이미지 밖으로 넘침', { page: 1, bbox: [1100, 320, 240, 40] }],
    ['bbox 세로가 이미지 밖으로 넘침', { page: 1, bbox: [120, 1500, 240, 400] }],
    ['bbox 값이 유한하지 않음', { page: 1, bbox: [120, 320, Number.NaN, 40] }],
    ['bbox 길이가 부족함', { page: 1, bbox: [120, 320, 240] }],
  ])('%s이면 강조하지 않고 검수는 계속 가능하다', async (_label, sourceLocation) => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeHighlightableResponse(
        sourceLocation as ExtractedField['source_location'],
      ),
    )
    renderPage()
    await openViewerAndLoadImage()

    fireEvent.focus(screen.getByLabelText('처방일'))

    expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
    expect(screen.getByLabelText('처방일')).toBeTruthy()
  })

  it.each([
    ['normalized=false', { normalized: false, width: null, height: null, url: null }],
    ['width가 0', { normalized: true, width: 0, height: 1600, url: '/normalized' }],
    ['height가 null', { normalized: true, width: 1200, height: null, url: '/normalized' }],
    ['url이 없음', { normalized: true, width: 1200, height: 1600, url: null }],
  ])(
    '%s이면 기존 원본 iframe preview로 fallback하고 강조하지 않는다',
    async (_label, sourceImage) => {
      const fields = withSourceLocation(
        makeCompleteFields(),
        'PRESCRIBED_DATE-0',
        { page: 1, bbox: [120, 320, 240, 40] },
      )
      vi.mocked(getOcrJob).mockResolvedValue(
        withSourceImage(
          makeOcrResponse(fields),
          sourceImage as OcrJobResponse['data']['source_image'],
        ),
      )
      renderPage()

      const viewer = await screen.findByTitle('원본 처방전')

      expect(viewer.tagName).toBe('IFRAME')
      expect(getPrescriptionDocumentFile).toHaveBeenCalledWith('document-1')
      expect(getPrescriptionNormalizedImage).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole('button', { name: '수정' }))
      fireEvent.focus(screen.getByLabelText('처방일'))
      expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
    },
  )

  it('source_image가 없는 legacy 응답도 기존 iframe preview를 유지한다', async () => {
    // 계약상 source_image는 항상 존재하지만 런타임 방어를 확인한다.
    const legacyResponse = makeOcrResponse(makeCompleteFields())
    delete (legacyResponse.data as { source_image?: unknown }).source_image
    vi.mocked(getOcrJob).mockResolvedValue(legacyResponse)
    renderPage()

    const viewer = await screen.findByTitle('원본 처방전')

    expect(viewer.tagName).toBe('IFRAME')
    expect(getPrescriptionNormalizedImage).not.toHaveBeenCalled()
  })

  it('정규화 이미지 fetch 실패는 검수와 확정을 막지 않는다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeHighlightableResponse())
    vi.mocked(getPrescriptionNormalizedImage).mockRejectedValue(
      new ApiError(404, '정규화본 없음', 'MEDICAL_DOCUMENT_NOT_FOUND'),
    )
    renderPage()

    // 검수 UI는 정상 동작하고 차단 상태로 전환되지 않는다.
    expect(
      await screen.findByRole('button', { name: '수정' }),
    ).toBeTruthy()
    expect(screen.queryByText('처방전 정보를 찾을 수 없어요')).toBeNull()
    expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()

    // 편집 진입도 계속 가능하다.
    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    expect(screen.getByLabelText('처방일')).toBeTruthy()
  })

  it('원본 파일 fetch 실패도 검수를 차단하지 않는다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    vi.mocked(getPrescriptionDocumentFile).mockRejectedValue(
      new ApiError(404, '원본 없음', 'MEDICAL_DOCUMENT_NOT_FOUND'),
    )
    renderPage()

    expect(
      await screen.findByRole('button', { name: '수정' }),
    ).toBeTruthy()
    expect(screen.queryByText('처방전 정보를 찾을 수 없어요')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    expect(screen.getByLabelText('처방일')).toBeTruthy()
  })

  it('언마운트 시 정규화 이미지 object URL을 해제한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeHighlightableResponse())
    const view = renderPage()

    await screen.findByTitle('원본 처방전')
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)

    view.unmount()

    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:prescription')
  })

  it('source_location이 있어도 필드를 자동으로 확인 처리하지 않는다', async () => {
    const fields = withSourceLocation(
      makeCompleteFields(),
      'PRESCRIBED_DATE-0',
      { page: 1, bbox: [120, 320, 240, 40] },
    ).map((field) =>
      field.field_id === 'PRESCRIBED_DATE-0'
        ? { ...field, confirmed_value: null, confirmation_status: 'UNCONFIRMED' }
        : field,
    )
    vi.mocked(getOcrJob).mockResolvedValue(
      withSourceImage(makeOcrResponse(fields), normalizedSourceImage),
    )
    renderPage()
    await openViewerAndLoadImage()

    fireEvent.focus(screen.getByLabelText('처방일'))
    await screen.findByTestId('prescription-source-highlight')

    // 강조만으로 확정 버튼이 열리지 않는다.
    expect(updateExtractedField).not.toHaveBeenCalled()
    const confirmButton = screen.getByRole('button', {
      name: '처방전 확정 및 가이드 만들기',
    })
    expect((confirmButton as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('PrescriptionReviewPage #809 contract & selection change', () => {
  const normalizedSourceImage = {
    normalized: true,
    width: 1000,
    height: 2000,
    url: '/api/v1/documents/document-1/normalized-file',
  }

  it('source_image는 항상 존재하는 required 계약이다', async () => {
    const response = makeOcrResponse(makeCompleteFields())

    expect(response.data).toHaveProperty('source_image')
    expect(response.data.source_image).toMatchObject({
      normalized: expect.any(Boolean),
    })

    vi.mocked(getOcrJob).mockResolvedValue(response)
    renderPage()

    // 계약을 그대로 흘려도 검수 화면이 정상 동작한다.
    expect(await screen.findByTitle('원본 처방전')).toBeTruthy()
  })

  it('선택한 field가 바뀌면 overlay bbox도 즉시 바뀐다', async () => {
    const fields = makeCompleteFields().map((field) => {
      if (field.field_id === 'PRESCRIBED_DATE-0') {
        return {
          ...field,
          source_location: { page: 1, bbox: [100, 200, 300, 100] as [number, number, number, number] },
        }
      }
      if (field.field_id === 'MEDICATION_NAME-1') {
        return {
          ...field,
          source_location: { page: 1, bbox: [500, 1000, 200, 200] as [number, number, number, number] },
        }
      }
      return field
    })

    vi.mocked(getOcrJob).mockResolvedValue({
      ...makeOcrResponse(fields),
      data: { ...makeOcrResponse(fields).data, source_image: normalizedSourceImage },
    })
    renderPage()

    const image = await screen.findByTitle('원본 처방전')
    fireEvent.load(image)

    // 처방일 선택
    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))

    let highlight = await screen.findByTestId('prescription-source-highlight')
    // 1000x2000 기준 [100,200,300,100] -> 10% / 10% / 30% / 5%
    expect(highlight.style.left).toBe('10%')
    expect(highlight.style.top).toBe('10%')
    expect(highlight.style.width).toBe('30%')
    expect(highlight.style.height).toBe('5%')

    // 약물이름으로 선택 변경
    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))
    fireEvent.focus(screen.getByLabelText('약물이름'))

    await waitFor(() => {
      highlight = screen.getByTestId('prescription-source-highlight')
      // [500,1000,200,200] -> 50% / 50% / 20% / 10%
      expect(highlight.style.left).toBe('50%')
    })
    expect(highlight.style.top).toBe('50%')
    expect(highlight.style.width).toBe('20%')
    expect(highlight.style.height).toBe('10%')
  })
})

describe('PrescriptionReviewPage #809 overlay containing block & inner scroll', () => {
  const tallSourceImage = {
    normalized: true,
    width: 1000,
    height: 2000,
    url: '/api/v1/documents/document-1/normalized-file',
  }

  function makeTallResponse() {
    const fields = makeCompleteFields().map((field) => {
      if (field.field_id === 'PRESCRIBED_DATE-0') {
        return {
          ...field,
          source_location: {
            page: 1,
            bbox: [100, 200, 300, 100] as [number, number, number, number],
          },
        }
      }
      if (field.field_id === 'MEDICATION_NAME-1') {
        return {
          ...field,
          source_location: {
            page: 1,
            bbox: [500, 1000, 200, 200] as [number, number, number, number],
          },
        }
      }
      return field
    })

    const base = makeOcrResponse(fields)
    return { ...base, data: { ...base.data, source_image: tallSourceImage } }
  }

  function stubGeometry() {
    const viewer = screen.getByTestId('prescription-source-canvas')
      .parentElement as HTMLDivElement
    const canvas = screen.getByTestId(
      'prescription-source-canvas',
    ) as HTMLDivElement

    // 이미지가 viewer보다 긴 상황: 표시 높이 2000px, viewer 높이 420px
    Object.defineProperties(canvas, {
      clientHeight: { configurable: true, value: 2000 },
      clientWidth: { configurable: true, value: 390 },
      scrollHeight: { configurable: true, value: 2000 },
      scrollWidth: { configurable: true, value: 390 },
    })
    Object.defineProperties(viewer, {
      clientHeight: { configurable: true, value: 420 },
      clientWidth: { configurable: true, value: 390 },
    })

    const scrollTo = vi.fn()
    viewer.scrollTo = scrollTo as unknown as HTMLDivElement['scrollTo']

    return { viewer, canvas, scrollTo }
  }

  beforeEach(() => {
    // rAF 콜백을 동기 실행해 레이아웃 후 스크롤 계산을 검증한다.
    vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((cb) => {
      cb(0)
      return 0
    })
  })

  it('overlay containing block은 viewer가 아니라 source-canvas다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeTallResponse())
    renderPage()

    const image = await screen.findByTitle('원본 처방전')
    fireEvent.load(image)
    stubGeometry()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))

    const canvas = screen.getByTestId('prescription-source-canvas')
    const highlight = await screen.findByTestId('prescription-source-highlight')

    // highlight와 img는 canvas의 자식이어야 한다.
    expect(highlight.parentElement).toBe(canvas)
    expect(image.parentElement).toBe(canvas)

    // canvas가 position:relative를 갖고, scroll 컨테이너는 갖지 않는다.
    const canvasRule = prescriptionReviewStyles.match(
      /\.prescription-review__source-canvas\s*\{[^}]*\}/,
    )?.[0]
    const viewerRule = prescriptionReviewStyles.match(
      /\.prescription-review__source-viewer\s*\{[^}]*\}/,
    )?.[0]

    expect(canvasRule).toContain('position: relative')
    expect(viewerRule).toContain('overflow: auto')
    expect(viewerRule).not.toContain('position: relative')
  })

  it('이미지가 viewer보다 길면 선택한 bbox 위치로 viewer 내부를 scroll한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeTallResponse())
    renderPage()

    const image = await screen.findByTitle('원본 처방전')
    fireEvent.load(image)
    const { scrollTo } = stubGeometry()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))

    // boxTop=200, boxHeight=100 -> 중앙정렬 200+50-210 = 40
    expect(scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ top: 40 }),
    )
  })

  it('선택 field가 바뀌면 내부 scroll 위치도 새 bbox에 맞게 바뀐다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeTallResponse())
    renderPage()

    const image = await screen.findByTitle('원본 처방전')
    fireEvent.load(image)
    const { scrollTo } = stubGeometry()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))
    expect(scrollTo).toHaveBeenLastCalledWith(
      expect.objectContaining({ top: 40 }),
    )

    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))
    fireEvent.focus(screen.getByLabelText('약물이름'))

    // boxTop=1000, boxHeight=200 -> 1000+100-210 = 890
    await waitFor(() => {
      expect(scrollTo).toHaveBeenLastCalledWith(
        expect.objectContaining({ top: 890 }),
      )
    })
  })

  it('scroll 위치는 스크롤 가능 범위를 벗어나지 않는다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeTallResponse())
    renderPage()

    const image = await screen.findByTitle('원본 처방전')
    fireEvent.load(image)
    const { scrollTo } = stubGeometry()

    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))
    fireEvent.focus(screen.getByLabelText('약물이름'))

    const call = scrollTo.mock.calls.at(-1)?.[0] as { top: number }
    // maxScrollTop = 2000 - 420 = 1580
    expect(call.top).toBeGreaterThanOrEqual(0)
    expect(call.top).toBeLessThanOrEqual(1580)
  })
})

describe('PrescriptionReviewPage #809 highlight lifetime', () => {
  const lifetimeSourceImage = {
    normalized: true,
    width: 1000,
    height: 2000,
    url: '/api/v1/documents/document-1/normalized-file',
  }

  function makeLifetimeResponse() {
    const fields = makeCompleteFields().map((field) => {
      if (field.field_id === 'PRESCRIBED_DATE-0') {
        return {
          ...field,
          source_location: {
            page: 1,
            bbox: [100, 200, 300, 100] as [number, number, number, number],
          },
        }
      }
      if (field.field_id === 'MEDICATION_NAME-1') {
        return {
          ...field,
          source_location: {
            page: 1,
            bbox: [500, 1000, 200, 200] as [number, number, number, number],
          },
        }
      }
      return field
    })

    const base = makeOcrResponse(fields)
    return { ...base, data: { ...base.data, source_image: lifetimeSourceImage } }
  }

  async function setupLoadedViewer() {
    vi.mocked(getOcrJob).mockResolvedValue(makeLifetimeResponse())
    renderPage()
    const image = await screen.findByTitle('원본 처방전')
    fireEvent.load(image)
    return image
  }

  it('field focus 시 highlight를 표시한다', async () => {
    await setupLoadedViewer()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))

    expect(
      await screen.findByTestId('prescription-source-highlight'),
    ).toBeTruthy()
  })

  it('field blur 시 highlight를 제거한다', async () => {
    await setupLoadedViewer()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    const input = screen.getByLabelText('처방일')
    fireEvent.focus(input)
    await screen.findByTestId('prescription-source-highlight')

    fireEvent.blur(input)

    await waitFor(() => {
      expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
    })
  })

  it('field A blur 후 field B focus 시 새 bbox로 교체된다', async () => {
    await setupLoadedViewer()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    const dateInput = screen.getByLabelText('처방일')
    fireEvent.focus(dateInput)

    let highlight = await screen.findByTestId('prescription-source-highlight')
    expect(highlight.style.top).toBe('10%')

    fireEvent.blur(dateInput)
    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))
    fireEvent.focus(screen.getByLabelText('약물이름'))

    await waitFor(() => {
      highlight = screen.getByTestId('prescription-source-highlight')
      expect(highlight.style.top).toBe('50%')
    })
  })

  it('수정 취소 시 highlight를 제거한다', async () => {
    await setupLoadedViewer()

    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))
    fireEvent.focus(screen.getByLabelText('약물이름'))
    await screen.findByTestId('prescription-source-highlight')

    fireEvent.click(screen.getByRole('button', { name: '취소' }))

    await waitFor(() => {
      expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
    })
  })

  it('수정완료 시 highlight를 제거한다', async () => {
    await setupLoadedViewer()

    fireEvent.click(screen.getByRole('button', { name: '수정하기' }))
    const input = screen.getByLabelText('약물이름')
    fireEvent.focus(input)
    await screen.findByTestId('prescription-source-highlight')

    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

    await waitFor(() => {
      expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
    })
  })

  it('처방일 수정모드 종료 시 highlight를 제거한다', async () => {
    await setupLoadedViewer()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))
    await screen.findByTestId('prescription-source-highlight')

    fireEvent.click(screen.getByRole('button', { name: '수정완료' }))

    await waitFor(() => {
      expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
    })
  })

  it('highlight 스타일은 원문 가독성을 해치지 않는 밝은 노랑 계열이다', () => {
    const rule = prescriptionReviewStyles.match(
      /\.prescription-review__source-highlight\s*\{[^}]*\}/,
    )?.[0]

    expect(rule).toContain('#f2c94c')
    expect(rule).toContain('rgba(255, 230, 120, 0.2)')
    expect(rule).toContain('pointer-events: none')
    expect(rule).not.toContain('box-shadow')
  })
})

describe('PrescriptionReviewPage #809 pinch zoom', () => {
  const zoomSourceImage = {
    normalized: true,
    width: 1000,
    height: 2000,
    url: '/api/v1/documents/document-1/normalized-file',
  }

  function makeZoomResponse() {
    const fields = makeCompleteFields().map((field) =>
      field.field_id === 'PRESCRIBED_DATE-0'
        ? {
            ...field,
            source_location: {
              page: 1,
              bbox: [100, 200, 300, 100] as [number, number, number, number],
            },
          }
        : field,
    )
    const base = makeOcrResponse(fields)
    return { ...base, data: { ...base.data, source_image: zoomSourceImage } }
  }

  async function setupZoomViewer() {
    vi.mocked(getOcrJob).mockResolvedValue(makeZoomResponse())
    renderPage()
    const image = await screen.findByTitle('원본 처방전')
    fireEvent.load(image)

    const viewer = screen.getByTestId('prescription-source-viewer')
    const canvas = screen.getByTestId('prescription-source-canvas')

    Object.defineProperties(viewer, {
      clientWidth: { configurable: true, value: 390 },
      clientHeight: { configurable: true, value: 420 },
    })
    Object.defineProperties(canvas, {
      clientWidth: { configurable: true, value: 390 },
      clientHeight: { configurable: true, value: 780 },
    })

    return { viewer, canvas, image }
  }

  function pinch(
    viewer: HTMLElement,
    fromDistance: number,
    toDistance: number,
  ) {
    fireEvent.pointerDown(viewer, { pointerId: 1, clientX: 0, clientY: 0 })
    fireEvent.pointerDown(viewer, {
      pointerId: 2,
      clientX: fromDistance,
      clientY: 0,
    })
    fireEvent.pointerMove(viewer, {
      pointerId: 2,
      clientX: toDistance,
      clientY: 0,
    })
  }

  function releasePinch(viewer: HTMLElement) {
    fireEvent.pointerUp(viewer, { pointerId: 2 })
    fireEvent.pointerUp(viewer, { pointerId: 1 })
  }

  function getScale(canvas: HTMLElement) {
    const match = canvas.style.transform.match(/scale\(([\d.]+)\)/)
    return match ? Number(match[1]) : null
  }

  function getPan(canvas: HTMLElement) {
    const match = canvas.style.transform.match(
      /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/,
    )
    return match ? { x: Number(match[1]), y: Number(match[2]) } : null
  }

  it('초기 scale은 1이고 pan은 원점이다', async () => {
    const { canvas } = await setupZoomViewer()

    expect(getScale(canvas)).toBe(1)
    expect(getPan(canvas)).toEqual({ x: 0, y: 0 })
  })

  it('pinch gesture로 scale이 증가한다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    pinch(viewer, 100, 200)

    await waitFor(() => expect(getScale(canvas)).toBe(2))
  })

  it('scale은 최대 3으로 clamp된다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    pinch(viewer, 100, 1000)

    await waitFor(() => expect(getScale(canvas)).toBe(3))
  })

  it('scale은 최소 1로 clamp된다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    pinch(viewer, 400, 10)

    await waitFor(() => expect(getScale(canvas)).toBe(1))
  })

  it('scale > 1에서 한 손가락 drag로 pan이 변경된다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    pinch(viewer, 100, 200)
    await waitFor(() => expect(getScale(canvas)).toBe(2))
    releasePinch(viewer)

    fireEvent.pointerDown(viewer, { pointerId: 3, clientX: 200, clientY: 300 })
    fireEvent.pointerMove(viewer, { pointerId: 3, clientX: 150, clientY: 200 })

    await waitFor(() => {
      const pan = getPan(canvas)
      expect(pan?.x).toBe(-50)
      expect(pan?.y).toBe(-100)
    })
  })

  it('pan은 확대된 canvas 범위를 벗어나지 않게 clamp된다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    pinch(viewer, 100, 200)
    await waitFor(() => expect(getScale(canvas)).toBe(2))
    releasePinch(viewer)

    // 양의 방향으로 크게 끌어도 0을 넘지 않는다.
    fireEvent.pointerDown(viewer, { pointerId: 4, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(viewer, { pointerId: 4, clientX: 900, clientY: 900 })

    await waitFor(() => expect(getPan(canvas)).toEqual({ x: 0, y: 0 }))

    // 음의 방향 한계: viewer(390x420) - scaled(780x1560)
    fireEvent.pointerMove(viewer, { pointerId: 4, clientX: -5000, clientY: -5000 })

    await waitFor(() => {
      const pan = getPan(canvas)
      expect(pan?.x).toBe(390 - 780)
      expect(pan?.y).toBe(420 - 1560)
    })
  })

  it('scale이 1로 돌아오면 pan이 초기화된다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    pinch(viewer, 100, 200)
    await waitFor(() => expect(getScale(canvas)).toBe(2))
    releasePinch(viewer)

    fireEvent.pointerDown(viewer, { pointerId: 5, clientX: 200, clientY: 300 })
    fireEvent.pointerMove(viewer, { pointerId: 5, clientX: 100, clientY: 100 })
    fireEvent.pointerUp(viewer, { pointerId: 5 })
    await waitFor(() => expect(getPan(canvas)).not.toEqual({ x: 0, y: 0 }))

    // 다시 축소해 scale 1로 되돌린다.
    pinch(viewer, 400, 10)
    await waitFor(() => expect(getScale(canvas)).toBe(1))
    releasePinch(viewer)

    await waitFor(() => expect(getPan(canvas)).toEqual({ x: 0, y: 0 }))
  })

  it('details를 닫으면 zoom 상태가 초기화된다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    pinch(viewer, 100, 300)
    await waitFor(() => expect(getScale(canvas)).toBe(3))
    releasePinch(viewer)

    // summary 클릭으로 details를 닫는다.
    const details = viewer.closest('details') as HTMLDetailsElement
    const summary = details.querySelector('summary') as HTMLElement
    fireEvent.click(summary)
    if (details.open) {
      details.open = false
      details.dispatchEvent(new Event('toggle', { bubbles: true }))
    }

    await waitFor(() => {
      const target = screen.getByTestId('prescription-source-canvas')
      expect(getScale(target)).toBe(1)
      expect(getPan(target)).toEqual({ x: 0, y: 0 })
    })
  })

  it('확대 상태에서도 highlight는 source-canvas 내부에 유지된다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))
    const highlight = await screen.findByTestId('prescription-source-highlight')
    expect(highlight.parentElement).toBe(canvas)

    pinch(viewer, 100, 200)
    await waitFor(() => expect(getScale(canvas)).toBe(2))

    // 좌표는 source image 기준 비율을 그대로 유지한다.
    expect(highlight.parentElement).toBe(canvas)
    expect(highlight.style.top).toBe('10%')
    expect(highlight.style.left).toBe('10%')
  })

  it('touch-action은 항상 none으로 고정되어 최초 pinch부터 viewer가 소유한다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    // gesture 시작 시점에 값이 정해지므로 도중 전환에 의존하지 않는다.
    expect(viewer.getAttribute('style') ?? '').not.toMatch(/touch-action/)

    const rule = prescriptionReviewStyles.match(
      /\.prescription-review__source-viewer\s*\{[^}]*\}/,
    )?.[0]
    expect(rule).toContain('touch-action: none')
    expect(rule).not.toContain('touch-action: pan-y')
    expect(rule).toContain('overscroll-behavior: contain')

    // scale=1에서 곧바로 두 손가락 pinch를 시작해도 viewer가 확대된다.
    pinch(viewer, 100, 200)
    await waitFor(() => expect(getScale(canvas)).toBe(2))
    expect(viewer.getAttribute('style') ?? '').not.toMatch(/touch-action/)
  })

  it('scale=1에서 한 손가락 세로 drag는 viewer scrollTop으로 처리된다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    viewer.scrollTop = 100
    expect(getScale(canvas)).toBe(1)

    fireEvent.pointerDown(viewer, { pointerId: 9, clientX: 50, clientY: 300 })
    fireEvent.pointerMove(viewer, { pointerId: 9, clientX: 50, clientY: 240 })

    // 위로 60px 끌면 60px 더 스크롤된다.
    expect(viewer.scrollTop).toBe(160)

    // transform pan은 scale=1이므로 움직이지 않는다.
    expect(getPan(canvas)).toEqual({ x: 0, y: 0 })

    fireEvent.pointerUp(viewer, { pointerId: 9 })
  })

  it('pointercancel이 와도 pointer 상태를 정리하고 이후 gesture가 동작한다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    fireEvent.pointerDown(viewer, { pointerId: 11, clientX: 0, clientY: 0 })
    fireEvent.pointerDown(viewer, { pointerId: 12, clientX: 100, clientY: 0 })
    fireEvent.pointerCancel(viewer, { pointerId: 12 })
    fireEvent.pointerCancel(viewer, { pointerId: 11 })

    // 정리 후 새 pinch가 정상 동작한다.
    pinch(viewer, 100, 200)
    await waitFor(() => expect(getScale(canvas)).toBe(2))
  })

  it('pinch 종료 후 한 손가락 pan이 정상 동작한다', async () => {
    const { viewer, canvas } = await setupZoomViewer()

    pinch(viewer, 100, 200)
    await waitFor(() => expect(getScale(canvas)).toBe(2))
    releasePinch(viewer)

    fireEvent.pointerDown(viewer, { pointerId: 13, clientX: 300, clientY: 400 })
    fireEvent.pointerMove(viewer, { pointerId: 13, clientX: 260, clientY: 340 })

    await waitFor(() => {
      const pan = getPan(canvas)
      expect(pan?.x).toBe(-40)
      expect(pan?.y).toBe(-60)
    })
  })

  it('legacy/PDF fallback viewer에는 zoom handler가 없다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(
      makeOcrResponse(makeCompleteFields()),
    )
    renderPage()

    const viewer = await screen.findByTitle('원본 처방전')

    expect(viewer.tagName).toBe('IFRAME')
    expect(screen.queryByTestId('prescription-source-viewer')).toBeNull()
    expect(screen.queryByTestId('prescription-source-canvas')).toBeNull()
  })
})

describe('PrescriptionReviewPage #809 normalized viewer fallback', () => {
  const normalizedSourceImage = {
    normalized: true,
    width: 1000,
    height: 2000,
    url: '/api/v1/documents/document-1/normalized-file',
  }

  function makeNormalizedResponse() {
    const fields = makeCompleteFields().map((field) =>
      field.field_id === 'PRESCRIBED_DATE-0'
        ? {
            ...field,
            source_location: {
              page: 1,
              bbox: [100, 200, 300, 100] as [number, number, number, number],
            },
          }
        : field,
    )
    const base = makeOcrResponse(fields)
    return { ...base, data: { ...base.data, source_image: normalizedSourceImage } }
  }

  it('정규화 fetch 실패 시 original /file viewer로 fallback한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeNormalizedResponse())
    vi.mocked(getPrescriptionNormalizedImage).mockRejectedValue(
      new ApiError(404, '정규화본 없음', 'MEDICAL_DOCUMENT_NOT_FOUND'),
    )
    renderPage()

    const viewer = await screen.findByTitle('원본 처방전')

    expect(viewer.tagName).toBe('IFRAME')
    expect(getPrescriptionDocumentFile).toHaveBeenCalledWith('document-1')
    expect(screen.queryByTestId('prescription-source-canvas')).toBeNull()
  })

  it('정규화 fetch 실패 fallback 화면에는 highlight가 없다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeNormalizedResponse())
    vi.mocked(getPrescriptionNormalizedImage).mockRejectedValue(
      new ApiError(500, 'boom', 'INTERNAL'),
    )
    renderPage()

    await screen.findByTitle('원본 처방전')
    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))

    expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
  })

  it('정규화 decode 실패 시 original /file viewer로 전환한다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeNormalizedResponse())
    renderPage()

    const image = await screen.findByTitle('원본 처방전')
    expect(image.tagName).toBe('IMG')
    // 정상 fetch 단계에서는 original을 부르지 않는다.
    expect(getPrescriptionDocumentFile).not.toHaveBeenCalled()

    fireEvent.error(image)

    await waitFor(() => {
      expect(getPrescriptionDocumentFile).toHaveBeenCalledWith('document-1')
    })

    const viewer = await screen.findByTitle('원본 처방전')
    expect(viewer.tagName).toBe('IFRAME')
    expect(screen.queryByTestId('prescription-source-canvas')).toBeNull()
  })

  it('정규화 decode 실패 fallback 화면에도 highlight가 없다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeNormalizedResponse())
    renderPage()

    const image = await screen.findByTitle('원본 처방전')
    fireEvent.load(image)
    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.focus(screen.getByLabelText('처방일'))
    await screen.findByTestId('prescription-source-highlight')

    fireEvent.error(image)

    await waitFor(() => {
      expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
    })
    fireEvent.focus(screen.getByLabelText('처방일'))
    expect(screen.queryByTestId('prescription-source-highlight')).toBeNull()
  })

  it('original fallback까지 실패해도 검수 화면은 유지된다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeNormalizedResponse())
    vi.mocked(getPrescriptionNormalizedImage).mockRejectedValue(
      new ApiError(404, '정규화본 없음', 'MEDICAL_DOCUMENT_NOT_FOUND'),
    )
    vi.mocked(getPrescriptionDocumentFile).mockRejectedValue(
      new ApiError(404, '원본 없음', 'MEDICAL_DOCUMENT_NOT_FOUND'),
    )
    renderPage()

    // preview는 비활성이지만 검수는 계속 가능하다.
    expect(
      await screen.findByRole('button', { name: '수정' }),
    ).toBeTruthy()
    expect(screen.queryByText('처방전 정보를 찾을 수 없어요')).toBeNull()
    expect(screen.queryByTitle('원본 처방전')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    expect(screen.getByLabelText('처방일')).toBeTruthy()
  })

  it('정규화가 정상이면 original /file을 호출하지 않는다', async () => {
    vi.mocked(getOcrJob).mockResolvedValue(makeNormalizedResponse())
    renderPage()

    const image = await screen.findByTitle('원본 처방전')
    fireEvent.load(image)

    expect(image.tagName).toBe('IMG')
    expect(getPrescriptionDocumentFile).not.toHaveBeenCalled()
    expect(getPrescriptionNormalizedImage).toHaveBeenCalledWith(
      '/api/v1/documents/document-1/normalized-file',
    )
  })
})
