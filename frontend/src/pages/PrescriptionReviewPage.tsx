import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  confirmPrescription,
  getOcrJob,
  getPrescriptionDocumentFile,
  updateExtractedField,
  type ExtractedField,
  type PrescriptionResponse,
} from '../api/prescriptions'
import {
  Button,
  Card,
  MobileShell,
  StatusBadge,
} from '../design-system/components'
import '../design-system/prototype.css'
import './PrescriptionReviewPage.css'

const fieldLabels: Record<string, string> = {
  PRESCRIBED_DATE: '처방일',
  MEDICATION_NAME: '약 이름',
  DOSE_VALUE: '1회 복용량',
  DOSE_UNIT: '복용 단위',
  FREQUENCY_PER_DAY: '하루 횟수',
  TIMING: '복용 조건',
  DURATION_DAYS: '복용 기간',
}

const fieldOrder: Record<string, number> = {
  MEDICATION_NAME: 1,
  DOSE_VALUE: 2,
  DOSE_UNIT: 3,
  FREQUENCY_PER_DAY: 4,
  DURATION_DAYS: 5,
  TIMING: 6,
}

const requiredMedicationFieldTypes = [
  'MEDICATION_NAME',
  'DOSE_VALUE',
  'FREQUENCY_PER_DAY',
  'DURATION_DAYS',
] as const

type BlockingAction = 'UPLOAD' | 'RETRY_LATER'

type ReviewBlockingState = {
  title: string
  message: string
  nextAction: string
  action: BlockingAction
}

type ReviewMessage = {
  title: string
  message: string
  nextAction: string
}

type ReviewErrorState =
  | { kind: 'ALREADY_CONFIRMED' }
  | { kind: 'BLOCKING'; state: ReviewBlockingState }
  | { kind: 'INLINE'; state: ReviewMessage }

function getIncompleteOcrState(ocrStatus: string): ReviewBlockingState {
  if (ocrStatus === 'FAILED') {
    return {
      title: '처방전 인식에 실패했어요',
      message: '완료된 OCR 결과가 없어 검수를 시작할 수 없습니다.',
      nextAction: '처방전을 다시 업로드하거나 OCR을 다시 실행해 주세요.',
      action: 'UPLOAD',
    }
  }

  if (ocrStatus === 'PROCESSING') {
    return {
      title: '처방전을 인식하고 있어요',
      message: 'OCR 작업이 완료되기 전에는 검수하거나 확정할 수 없습니다.',
      nextAction: 'OCR 처리가 완료된 뒤 다시 확인해 주세요.',
      action: 'RETRY_LATER',
    }
  }

  return {
    title: 'OCR 작업을 기다리고 있어요',
    message: 'OCR 작업이 완료되기 전에는 검수하거나 확정할 수 없습니다.',
    nextAction: 'OCR 처리가 완료된 뒤 다시 확인해 주세요.',
    action: 'RETRY_LATER',
  }
}

function getApiBlockingState(error: ApiError): ReviewBlockingState | null {
  if (error.code === 'OCR_JOB_NOT_COMPLETED') {
    return {
      title: 'OCR 검수가 아직 준비되지 않았어요',
      message: error.message,
      nextAction: 'OCR 처리가 완료된 뒤 다시 확인해 주세요.',
      action: 'RETRY_LATER',
    }
  }

  if (error.code === 'PRESCRIPTION_REQUIRED_FIELD_MISSING') {
    return {
      title: '처방 확정에 필요한 항목이 부족해요',
      message: error.message,
      nextAction: '처방전을 다시 업로드하거나 OCR을 다시 실행해 주세요.',
      action: 'UPLOAD',
    }
  }

  if (error.code === 'EXTRACTED_FIELD_NOT_FOUND') {
    return {
      title: '검수하던 항목을 찾을 수 없어요',
      message: error.message,
      nextAction: '최신 OCR 결과로 다시 검수해 주세요.',
      action: 'UPLOAD',
    }
  }

  return null
}

function getReviewErrorState(
  error: unknown,
  fallbackMessage: string,
): ReviewErrorState {
  if (error instanceof ApiError) {
    if (error.code === 'PRESCRIPTION_ALREADY_CONFIRMED') {
      return { kind: 'ALREADY_CONFIRMED' }
    }

    const blockingState = getApiBlockingState(error)
    if (blockingState) return { kind: 'BLOCKING', state: blockingState }

    if (error.code === 'VALIDATION_FAILED') {
      return {
        kind: 'INLINE',
        state: {
          title: '입력값을 확인해 주세요',
          message: error.message,
          nextAction: '원본 처방전과 대조한 뒤 표시된 항목을 수정해 주세요.',
        },
      }
    }

    return {
      kind: 'INLINE',
      state: {
        title: '확인이 필요해요',
        message: error.message,
        nextAction: '잠시 후 다시 시도해 주세요.',
      },
    }
  }

  return {
    kind: 'INLINE',
    state: {
      title: '확인이 필요해요',
      message: fallbackMessage,
      nextAction: '잠시 후 다시 시도해 주세요.',
    },
  }
}

function getFieldLabel(fieldType: string) {
  return fieldLabels[fieldType] ?? fieldType
}

function getSavedDisplayValue(field: ExtractedField) {
  return field.confirmed_value ?? field.raw_value ?? ''
}

function isFieldConfirmed(
  field: ExtractedField,
  draftValues: Record<string, string>,
) {
  const draftValue = draftValues[field.field_id]?.trim() ?? ''
  return (
    Boolean(field.confirmed_value?.trim()) &&
    draftValue === field.confirmed_value?.trim()
  )
}

function getNumericFieldError(fieldType: string, value: string) {
  if (fieldType === 'DOSE_VALUE') {
    const isDecimalFormat =
      /^[+-]?(?:(?:\d(?:_?\d)*(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)(?:[eE][+-]?\d(?:_?\d)*)?)$/.test(value)
    const numericValue = Number(value.replaceAll('_', ''))

    if (!isDecimalFormat || !Number.isFinite(numericValue)) {
      return '1회 복용량은 숫자 형식으로 입력해 주세요.'
    }

    if (numericValue <= 0) {
      return '1회 복용량은 0보다 큰 숫자로 입력해 주세요.'
    }
  }

  if (fieldType === 'FREQUENCY_PER_DAY') {
    if (!/^[0-9]+$/.test(value)) {
      return '하루 횟수는 정수 형식으로 입력해 주세요.'
    }

    if (Number(value) <= 0) {
      return '하루 횟수는 0보다 큰 정수로 입력해 주세요.'
    }
  }

  if (fieldType === 'DURATION_DAYS') {
    if (!/^[0-9]+$/.test(value)) {
      return '복용 기간은 정수 형식으로 입력해 주세요.'
    }

    if (Number(value) <= 0) {
      return '복용 기간은 0보다 큰 정수로 입력해 주세요.'
    }
  }

  return null
}

function PrescriptionReviewPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const documentId = searchParams.get('document_id')
  const jobId = searchParams.get('job_id')
  const reviewRequestKey = `${documentId ?? ''}:${jobId ?? ''}`
  const latestReviewRequestKeyRef = useRef(reviewRequestKey)
  latestReviewRequestKeyRef.current = reviewRequestKey

  const [fields, setFields] = useState<ExtractedField[]>([])
  const [draftValues, setDraftValues] = useState<Record<string, string>>({})
  const [documentUrl, setDocumentUrl] = useState<string | null>(null)
  const [prescription, setPrescription] =
    useState<PrescriptionResponse | null>(null)
  const [message, setMessage] = useState<ReviewMessage | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [blockingState, setBlockingState] =
    useState<ReviewBlockingState | null>(null)
  const [isAlreadyConfirmed, setIsAlreadyConfirmed] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [savingFieldIds, setSavingFieldIds] = useState<Set<string>>(
    () => new Set(),
  )
  const [isConfirming, setIsConfirming] = useState(false)
  const [userConfirmed, setUserConfirmed] = useState(false)

  const applyReviewError = useCallback(
    (error: unknown, fallbackMessage: string) => {
      const errorState = getReviewErrorState(error, fallbackMessage)

      if (errorState.kind === 'ALREADY_CONFIRMED') {
        setMessage(null)
        setBlockingState(null)
        setIsAlreadyConfirmed(true)
        setUserConfirmed(false)
        return
      }

      if (errorState.kind === 'BLOCKING') {
        setMessage(null)
        setBlockingState(errorState.state)
        setUserConfirmed(false)
        return
      }

      setMessage(errorState.state)
    },
    [],
  )

  const prescriptionFields = useMemo(
    () => fields.filter((field) => field.medication_index === 0),
    [fields],
  )

  const medicationGroups = useMemo(() => {
    const groups: Record<number, ExtractedField[]> = {}

    for (const field of fields) {
      if (field.medication_index === 0) continue
      groups[field.medication_index] ??= []
      groups[field.medication_index].push(field)
    }

    return Object.entries(groups)
      .map(([index, groupFields]) => ({
        index: Number(index),
        fields: [...groupFields].sort(
          (a, b) =>
            (fieldOrder[a.field_type] ?? 999) -
            (fieldOrder[b.field_type] ?? 999),
        ),
      }))
      .sort((a, b) => a.index - b.index)
  }, [fields])

  const hasUnsavedChanges = useMemo(
    () =>
      fields.some((field) => {
        const draftValue = draftValues[field.field_id]?.trim() ?? ''
        return draftValue !== getSavedDisplayValue(field).trim()
      }),
    [draftValues, fields],
  )

  const confirmedFieldCount = useMemo(
    () => fields.filter((field) => isFieldConfirmed(field, draftValues)).length,
    [draftValues, fields],
  )

  const allDisplayedFieldsConfirmed = useMemo(
    () =>
      fields.length > 0 &&
      fields.every((field) => isFieldConfirmed(field, draftValues)),
    [draftValues, fields],
  )

  const hasMissingPrescribedDateField = useMemo(
    () =>
      !fields.some(
        (field) =>
          field.medication_index === 0 &&
          field.field_type === 'PRESCRIBED_DATE',
      ),
    [fields],
  )

  const hasMissingRequiredMedicationFields = useMemo(
    () =>
      medicationGroups.length === 0 ||
      medicationGroups.some((group) =>
        requiredMedicationFieldTypes.some(
          (fieldType) =>
            !group.fields.some((field) => field.field_type === fieldType),
        ),
      ),
    [medicationGroups],
  )

  const allRequiredMedicationFieldsConfirmed = useMemo(
    () =>
      medicationGroups.length > 0 &&
      medicationGroups.every((group) =>
        requiredMedicationFieldTypes.every((fieldType) => {
          const field = group.fields.find(
            (candidate) => candidate.field_type === fieldType,
          )
          return Boolean(field && isFieldConfirmed(field, draftValues))
        }),
      ),
    [draftValues, medicationGroups],
  )

  const prescribedDateConfirmed = useMemo(
    () =>
      fields.some(
        (field) =>
          field.medication_index === 0 &&
          field.field_type === 'PRESCRIBED_DATE' &&
          isFieldConfirmed(field, draftValues),
      ),
    [draftValues, fields],
  )

  const reviewReadyForAcknowledgement =
    prescribedDateConfirmed &&
    allRequiredMedicationFieldsConfirmed &&
    allDisplayedFieldsConfirmed &&
    !hasMissingPrescribedDateField &&
    !hasMissingRequiredMedicationFields &&
    !hasUnsavedChanges &&
    savingFieldIds.size === 0

  const canConfirmPrescription = useMemo(() => {
    return (
      reviewReadyForAcknowledgement &&
      userConfirmed
    )
  }, [reviewReadyForAcknowledgement, userConfirmed])

  useEffect(() => {
    let isDisposed = false
    let objectUrl: string | null = null
    const isLatestRequest = () =>
      !isDisposed &&
      latestReviewRequestKeyRef.current === reviewRequestKey

    setFields([])
    setDraftValues({})
    setDocumentUrl(null)
    setPrescription(null)
    setMessage(null)
    setFieldErrors({})
    setBlockingState(null)
    setIsAlreadyConfirmed(false)
    setSavingFieldIds(new Set())
    setIsConfirming(false)
    setUserConfirmed(false)
    setIsLoading(true)

    if (!documentId || !jobId) {
      setBlockingState({
        title: '검수 정보를 확인할 수 없어요',
        message: '처방전 검수에 필요한 정보가 없습니다.',
        nextAction: '처방전을 다시 업로드해 주세요.',
        action: 'UPLOAD',
      })
      setIsLoading(false)
      return () => {
        isDisposed = true
      }
    }

    async function loadReviewData() {
      try {
        const ocrResponse = await getOcrJob(jobId as string)
        if (!isLatestRequest()) return

        if (ocrResponse.data.document_id !== documentId) {
          setBlockingState({
            title: '검수를 진행할 수 없어요',
            message: '검수하려는 처방전과 OCR 결과가 일치하지 않습니다.',
            nextAction: '처방전을 다시 업로드하거나 OCR을 다시 실행해 주세요.',
            action: 'UPLOAD',
          })
          return
        }

        if (ocrResponse.data.ocr_status !== 'COMPLETED') {
          setBlockingState(
            getIncompleteOcrState(ocrResponse.data.ocr_status),
          )
          return
        }

        const documentBlob = await getPrescriptionDocumentFile(documentId)
        if (!isLatestRequest()) return

        setFields(ocrResponse.data.fields)
        setDraftValues(
          Object.fromEntries(
            ocrResponse.data.fields.map((field) => [
              field.field_id,
              getSavedDisplayValue(field),
            ]),
          ),
        )

        const nextObjectUrl = URL.createObjectURL(documentBlob)
        if (!isLatestRequest()) {
          URL.revokeObjectURL(nextObjectUrl)
          return
        }
        objectUrl = nextObjectUrl
        setDocumentUrl(nextObjectUrl)
      } catch (error) {
        if (!isLatestRequest()) return
        applyReviewError(
          error,
          '처방전 검수 정보를 불러오는 중 오류가 발생했습니다.',
        )
      } finally {
        if (isLatestRequest()) setIsLoading(false)
      }
    }

    void loadReviewData()

    return () => {
      isDisposed = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [applyReviewError, documentId, jobId, reviewRequestKey])

  const handleSaveField = async (field: ExtractedField) => {
    if (savingFieldIds.has(field.field_id) || isConfirming || prescription) {
      return
    }

    const saveRequestKey = reviewRequestKey
    const value = draftValues[field.field_id]?.trim()

    if (!value) {
      setFieldErrors((current) => ({
        ...current,
        [field.field_id]: '확인할 값을 입력해 주세요.',
      }))
      return
    }

    const numericFieldError = getNumericFieldError(field.field_type, value)

    if (numericFieldError) {
      setFieldErrors((current) => ({
        ...current,
        [field.field_id]: numericFieldError,
      }))
      return
    }

    try {
      setSavingFieldIds((current) => {
        const next = new Set(current)
        next.add(field.field_id)
        return next
      })
      setFieldErrors((current) => {
        const next = { ...current }
        delete next[field.field_id]
        return next
      })
      setMessage(null)
      const response = await updateExtractedField(field.field_id, value)
      if (latestReviewRequestKeyRef.current !== saveRequestKey) return

      setFields((current) =>
        current.map((item) =>
          item.field_id === field.field_id ? response.data : item,
        ),
      )
      setDraftValues((current) => ({
        ...current,
        [field.field_id]: response.data.confirmed_value ?? value,
      }))
      setUserConfirmed(false)
    } catch (error) {
      if (latestReviewRequestKeyRef.current !== saveRequestKey) return
      applyReviewError(error, '필드 저장 중 오류가 발생했습니다.')
    } finally {
      if (latestReviewRequestKeyRef.current === saveRequestKey) {
        setSavingFieldIds((current) => {
          const next = new Set(current)
          next.delete(field.field_id)
          return next
        })
      }
    }
  }

  const handleConfirmPrescription = async () => {
    if (
      !documentId ||
      !canConfirmPrescription ||
      isConfirming ||
      prescription
    ) {
      return
    }

    const confirmationRequestKey = reviewRequestKey

    try {
      setIsConfirming(true)
      setMessage(null)
      const response = await confirmPrescription(documentId)
      if (latestReviewRequestKeyRef.current !== confirmationRequestKey) return
      setPrescription(response)
    } catch (error) {
      if (latestReviewRequestKeyRef.current !== confirmationRequestKey) return
      applyReviewError(error, '처방 확정 중 오류가 발생했습니다.')
    } finally {
      if (latestReviewRequestKeyRef.current === confirmationRequestKey) {
        setIsConfirming(false)
      }
    }
  }

  const renderField = (field: ExtractedField) => {
    const draftValue = draftValues[field.field_id] ?? ''
    const confirmed = isFieldConfirmed(field, draftValues)
    const isSaving = savingFieldIds.has(field.field_id)
    const rawValue = field.raw_value?.trim() ?? ''
    const fieldError = fieldErrors[field.field_id]
    const inputMode =
      field.field_type === 'DOSE_VALUE'
        ? 'decimal'
        : field.field_type === 'FREQUENCY_PER_DAY' ||
            field.field_type === 'DURATION_DAYS'
          ? 'numeric'
          : undefined

    return (
      <div className="prescription-review__field" key={field.field_id}>
        <div className="prescription-review__field-heading">
          <label htmlFor={`field-${field.field_id}`}>
            {getFieldLabel(field.field_type)}
          </label>
          <span className={confirmed ? 'is-confirmed' : ''}>
            {confirmed ? '저장됨' : '확인 필요'}
          </span>
        </div>

        <div className="prescription-review__field-control">
          <input
            id={`field-${field.field_id}`}
            value={draftValue}
            inputMode={inputMode}
            aria-invalid={Boolean(fieldError)}
            disabled={isSaving || isConfirming || Boolean(prescription)}
            onChange={(event) => {
              setDraftValues((current) => ({
                ...current,
                [field.field_id]: event.target.value,
              }))
              setFieldErrors((current) => {
                const next = { ...current }
                delete next[field.field_id]
                return next
              })
              setUserConfirmed(false)
            }}
            aria-describedby={`field-help-${field.field_id}`}
          />
          <Button
            variant={confirmed ? 'secondary' : 'primary'}
            disabled={isSaving || isConfirming || Boolean(prescription)}
            onClick={() => handleSaveField(field)}
          >
            {isSaving ? '저장 중' : confirmed ? '수정 저장' : '확인'}
          </Button>
        </div>

        <p
          id={`field-help-${field.field_id}`}
          className={fieldError ? 'is-error' : ''}
          role={fieldError ? 'alert' : undefined}
        >
          {fieldError ?? (confirmed
            ? '사용자가 확인하고 저장한 값입니다.'
            : rawValue
              ? `OCR 인식값: ${rawValue}`
              : '인식된 값이 없습니다. 원본을 보고 직접 입력해 주세요.')}
        </p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="prescription-review-page">
        <MobileShell title="OCR 결과 확인" hideNavigation>
          <div className="app-scroll prescription-review__loading" role="status">
            처방전 검수 정보를 불러오고 있어요.
          </div>
        </MobileShell>
      </div>
    )
  }

  if (blockingState) {
    return (
      <div className="prescription-review-page">
        <MobileShell
          title="다섯알"
          onBack={() => navigate('/prescriptions/upload')}
          hideNavigation
        >
          <main className="app-scroll prescription-review prescription-review__blocked">
            <div className="prescription-review__error" role="alert">
              <strong>{blockingState.title}</strong>
              <span>{blockingState.message}</span>
              <span>{blockingState.nextAction}</span>
            </div>
            <Button
              fullWidth
              variant="secondary"
              onClick={() =>
                blockingState.action === 'UPLOAD'
                  ? navigate('/prescriptions/upload')
                  : navigate(0)
              }
            >
              {blockingState.action === 'UPLOAD'
                ? '처방전 다시 업로드하기'
                : '상태 다시 확인하기'}
            </Button>
          </main>
        </MobileShell>
      </div>
    )
  }

  if (isAlreadyConfirmed) {
    return (
      <div className="prescription-review-page">
        <MobileShell
          title="다섯알"
          onBack={() => navigate('/prescriptions/upload')}
          hideNavigation
        >
          <main className="app-scroll prescription-review">
            <Card className="prescription-review__complete">
              <StatusBadge>확정 완료</StatusBadge>
              <h2>이미 확정된 처방이에요</h2>
              <p>확정된 처방의 OCR 항목은 더 이상 수정할 수 없습니다.</p>
            </Card>
          </main>
        </MobileShell>
      </div>
    )
  }

  if (prescription) {
    return (
      <div className="prescription-review-page">
        <MobileShell
          title="다섯알"
          onBack={() => navigate('/prescriptions/upload')}
          hideNavigation
        >
          <main className="app-scroll prescription-review">
            <Card className="prescription-review__complete">
              <StatusBadge>확정 완료</StatusBadge>
              <h2>처방정보가 확정되었어요</h2>
              <p>
                확인된 처방정보만 이후 복약 가이드와 일정에 사용합니다.
              </p>
              <strong>
                등록된 약물 {prescription.data.medications.length}개
              </strong>
            </Card>
          </main>
        </MobileShell>
      </div>
    )
  }

  return (
    <div className="prescription-review-page">
      <MobileShell
        title="다섯알"
        onBack={() => navigate('/prescriptions/upload')}
        hideNavigation
      >
        <main className="app-scroll prescription-review">
          <section className="prescription-review__intro">
            <div className="prescription-review__success-icon" aria-hidden="true">
              ✓
            </div>
            <div>
              <p>전체 인식 성공</p>
              <h1>처방전과 같은지 확인해 주세요</h1>
            </div>
          </section>

          <div className="prescription-review__notice">
            아직 확정 전 정보예요. 누락되거나 잘못 읽은 값은 직접
            입력하고 원본과 대조해 주세요.
          </div>

          {(hasMissingPrescribedDateField ||
            hasMissingRequiredMedicationFields) && (
            <div className="prescription-review__error" role="alert">
              <strong>필수 처방 항목이 누락됐어요</strong>
              <span>
                처방일·약 이름·1회 복용량·하루 횟수·복용 기간을 모두 검수할
                수 있도록 처방전을 다시 업로드하거나 OCR을 다시 실행해 주세요.
              </span>
              <Button
                variant="secondary"
                onClick={() => navigate('/prescriptions/upload')}
              >
                다시 업로드하기
              </Button>
            </div>
          )}

          {message && (
            <div className="prescription-review__error" role="alert">
              <strong>{message.title}</strong>
              <span>{message.message}</span>
              <span>{message.nextAction}</span>
            </div>
          )}

          {documentUrl && (
            <details className="prescription-review__source">
              <summary>
                <span>원본 처방전 보기</span>
                <span>직접 대조하기</span>
              </summary>
              <iframe src={documentUrl} title="원본 처방전" />
            </details>
          )}

          {prescriptionFields.length > 0 && (
            <Card className="prescription-review__card">
              <div className="prescription-review__card-title">
                <div className="prescription-review__med-icon" aria-hidden="true">
                  ●
                </div>
                <strong>처방 정보</strong>
                <StatusBadge
                  tone={
                    prescriptionFields.every((field) =>
                      isFieldConfirmed(field, draftValues),
                    )
                      ? 'neutral'
                      : 'attention'
                  }
                >
                  {prescriptionFields.every((field) =>
                    isFieldConfirmed(field, draftValues),
                  )
                    ? '확인 완료'
                    : '확인 필요'}
                </StatusBadge>
              </div>
              <div className="prescription-review__fields">
                {prescriptionFields.map(renderField)}
              </div>
            </Card>
          )}

          {medicationGroups.map((group, groupIndex) => {
            const allConfirmed = group.fields.every((field) =>
              isFieldConfirmed(field, draftValues),
            )
            const medicationName = group.fields.find(
              (field) => field.field_type === 'MEDICATION_NAME',
            )
            const summaryValue = medicationName
              ? draftValues[medicationName.field_id]
              : ''

            return (
              <Card className="prescription-review__card" key={group.index}>
                <div className="prescription-review__card-title">
                  <div className="prescription-review__med-icon" aria-hidden="true">
                    ●
                  </div>
                  <strong>처방약 {groupIndex + 1}</strong>
                  <StatusBadge tone={allConfirmed ? 'neutral' : 'attention'}>
                    {allConfirmed ? '인식 완료' : '확인 필요'}
                  </StatusBadge>
                </div>
                <h2>{summaryValue || '확인된 약 이름'}</h2>
                <p className="prescription-review__card-summary">
                  1회량 · 하루 횟수 · 복용 기간 · 복용 조건
                </p>
                <details className="prescription-review__editor">
                  <summary>세부 항목 확인 및 수정</summary>
                  <div className="prescription-review__fields">
                    {group.fields.map(renderField)}
                  </div>
                </details>
              </Card>
            )
          })}

          {!prescription && (
            <>
              <label
                className={`prescription-review__acknowledgement ${
                  hasUnsavedChanges ? 'has-unsaved' : ''
                }`}
              >
                <input
                  type="checkbox"
                  checked={userConfirmed}
                  disabled={!reviewReadyForAcknowledgement || isConfirming}
                  onChange={(event) => setUserConfirmed(event.target.checked)}
                />
                <span>원본 처방전과 모든 항목을 직접 확인했습니다.</span>
              </label>

              {hasUnsavedChanges && (
                <p className="prescription-review__unsaved" role="status">
                  저장하지 않은 수정값이 있어요. 모든 수정값을 저장해 주세요.
                </p>
              )}

              <Button
                fullWidth
                className="prescription-review__confirm"
                disabled={
                  !canConfirmPrescription ||
                  isConfirming ||
                  savingFieldIds.size > 0
                }
                onClick={handleConfirmPrescription}
              >
                {isConfirming ? '처방 확정 중...' : '처방 확정'}
              </Button>

              <p className="prescription-review__progress">
                {confirmedFieldCount}/{fields.length}개 항목 저장 완료
              </p>
            </>
          )}

        </main>
      </MobileShell>
    </div>
  )
}

export default PrescriptionReviewPage
