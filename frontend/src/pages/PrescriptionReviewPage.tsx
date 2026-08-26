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
import { createGuide } from '../api/guides'
import '../design-system/prototype.css'
import './PrescriptionReviewPage.css'

const fieldLabels: Record<string, string> = {
  PRESCRIBED_DATE: '처방일',
  MEDICATION_NAME: '처방전 약 이름',

  // 약 이름에 붙은 제품 함량을 별도로 검수합니다.
  MEDICATION_STRENGTH: '제품 함량',

  DOSE_VALUE: '1회 복용량',
  DOSE_UNIT: '복용 단위',
  FREQUENCY_PER_DAY: '하루 횟수',
  TIMING: '복용 조건',
  DURATION_DAYS: '복용 기간',
}

const fieldOrder: Record<string, number> = {
  MEDICATION_NAME: 1,
  MEDICATION_STRENGTH: 2,
  DOSE_VALUE: 3,
  DOSE_UNIT: 4,
  FREQUENCY_PER_DAY: 5,
  DURATION_DAYS: 6,
  TIMING: 7,
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
  if (field.confirmed_value?.trim()) {
    return field.confirmed_value
  }

  // 처방일만 Backend가 만든 YYYY-MM-DD 값을 사용합니다.
  // 약물명은 처방전 표기를 보존해야 하므로 정규화 값으로 바꾸지 않습니다.
  if (
    field.field_type === 'PRESCRIBED_DATE' &&
    field.normalized_value?.trim()
  ) {
    return field.normalized_value
  }

  return field.raw_value ?? ''
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
    if (fieldType === 'PRESCRIBED_DATE') {
      const isIsoDate = /^\d{4}-\d{2}-\d{2}$/.test(value)
      const parsedDate = new Date(`${value}T00:00:00Z`)

      if (isIsoDate && !Number.isNaN(parsedDate.getTime())) {
        const [year, month, day] = value.split('-').map(Number)

        if (
          parsedDate.getUTCFullYear() === year &&
          parsedDate.getUTCMonth() + 1 === month &&
          parsedDate.getUTCDate() === day
        ) {
          return null
        }
      }

      return '처방일은 YYYY-MM-DD 형식으로 입력해 주세요.'
    }
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
  const guideCreationRequestRef = useRef<symbol | null>(null)
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
  const [isCreatingGuide, setIsCreatingGuide] = useState(false)
  const [guideCreationError, setGuideCreationError] = useState<string | null>(
    null,
  )
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
    setIsCreatingGuide(false)
    setGuideCreationError(null)
    guideCreationRequestRef.current = null
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
      guideCreationRequestRef.current = null
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

  const handleCreateGuide = async (prescriptionId: string) => {
    if (guideCreationRequestRef.current) return

    const requestToken = Symbol('guide-creation')
    const guideRequestKey = reviewRequestKey
    guideCreationRequestRef.current = requestToken

    try {
      setIsCreatingGuide(true)
      setGuideCreationError(null)
      const response = await createGuide(prescriptionId)
      if (
        latestReviewRequestKeyRef.current !== guideRequestKey ||
        guideCreationRequestRef.current !== requestToken
      ) {
        return
      }
      navigate(`/guides/${response.data.guide_id}`)
    } catch (error) {
      if (
        latestReviewRequestKeyRef.current !== guideRequestKey ||
        guideCreationRequestRef.current !== requestToken
      ) {
        return
      }
      setGuideCreationError(
        error instanceof ApiError
          ? error.message
          : '복약 가이드를 만드는 중 오류가 발생했습니다.',
      )
    } finally {
      if (guideCreationRequestRef.current === requestToken) {
        guideCreationRequestRef.current = null
        if (latestReviewRequestKeyRef.current === guideRequestKey) {
          setIsCreatingGuide(false)
        }
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
      void handleCreateGuide(response.data.prescription_id)
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
        <MobileShell
          title="Dosey 도지"
          onBack={() => navigate('/prescriptions/upload')}
          backPlacement="content"
          hideNavigation
        >
          <main className="app-scroll prescription-review prescription-review__state-screen">
            <div role="status">
              <Card className="prescription-review__state-card">
                <span className="prescription-review__loading-mark" aria-hidden="true" />
                <p className="prescription-review__state-eyebrow">OCR 결과 불러오는 중</p>
                <h1>처방전 검수를 준비하고 있어요</h1>
                <p>처방전 검수 정보를 불러오고 있어요.</p>
              </Card>
            </div>
          </main>
        </MobileShell>
      </div>
    )
  }

  if (blockingState) {
    return (
      <div className="prescription-review-page">
        <MobileShell
          title="Dosey 도지"
          onBack={() => navigate('/prescriptions/upload')}
          backPlacement="content"
          hideNavigation
        >
          <main className="app-scroll prescription-review prescription-review__state-screen">
            <div className="prescription-review__error prescription-review__error--blocking" role="alert">
              <span className="prescription-review__warning-mark" aria-hidden="true" />
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
          title="Dosey 도지"
          onBack={() => navigate('/prescriptions/upload')}
          backPlacement="content"
          hideNavigation
        >
          <main className="app-scroll prescription-review prescription-review__state-screen">
            <Card className="prescription-review__complete prescription-review__state-card">
              <span className="prescription-review__complete-mark" aria-hidden="true" />
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
    const guideStatus = isCreatingGuide
      ? '가이드 생성 중'
      : guideCreationError
        ? '가이드 생성 필요'
        : '확정 완료'

    return (
      <div className="prescription-review-page">
        <MobileShell
          title="Dosey 도지"
          onBack={() => navigate('/prescriptions/upload')}
          backPlacement="content"
          hideNavigation
        >
          <main className="app-scroll prescription-review prescription-review__state-screen">
            <Card className="prescription-review__complete prescription-review__state-card">
              <span className="prescription-review__complete-mark" aria-hidden="true" />
              <StatusBadge tone={guideCreationError ? 'attention' : 'neutral'}>
                {guideStatus}
              </StatusBadge>
              <h2>처방정보가 확정되었어요</h2>
              <p>
                {isCreatingGuide
                  ? '확정된 처방정보로 복약 가이드를 만들고 있어요.'
                  : guideCreationError
                    ? '처방은 확정되었지만 복약 가이드를 만들지 못했어요. 가이드 생성만 다시 시도할 수 있습니다.'
                    : '확정된 처방정보는 더 이상 수정할 수 없습니다.'}
              </p>
              <strong>
                등록된 약물 {prescription.data.medications.length}개
              </strong>
              {guideCreationError && (
                <p className="prescription-review__guide-error" role="alert">
                  {guideCreationError}
                </p>
              )}
              {(isCreatingGuide || guideCreationError) && (
                <Button
                  fullWidth
                  className="prescription-review__guide-action"
                  disabled={isCreatingGuide}
                  onClick={() =>
                    handleCreateGuide(prescription.data.prescription_id)
                  }
                >
                  {isCreatingGuide
                    ? '가이드 생성 중...'
                    : '가이드 생성 다시 시도'}
                </Button>
              )}
            </Card>
          </main>
        </MobileShell>
      </div>
    )
  }

  return (
    <div className="prescription-review-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate('/prescriptions/upload')}
        backPlacement="content"
        hideNavigation
      >
        <main className="app-scroll prescription-review">
          <section className="prescription-review__intro">
            <div className="prescription-review__success-icon" aria-hidden="true">
              <span />
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
                <span className="prescription-review__source-heading">
                  <span className="prescription-review__document-mark" aria-hidden="true" />
                  <span>
                    <strong>원본 처방전 확인</strong>
                    <small>인식 결과와 직접 대조해 주세요</small>
                  </span>
                </span>
                <span className="prescription-review__source-action">원본 보기</span>
              </summary>
              <iframe src={documentUrl} title="원본 처방전" />
            </details>
          )}

          {prescriptionFields.length > 0 && (
            <Card className="prescription-review__card prescription-review__card--prescription">
              <div className="prescription-review__card-title">
                <div className="prescription-review__med-icon" aria-hidden="true">
                  <span className="prescription-review__calendar-mark" />
                </div>
                <div className="prescription-review__card-heading">
                  <span>처방 정보</span>
                  <strong>처방일</strong>
                </div>
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
              <h2>
                {draftValues[
                  prescriptionFields.find(
                    (field) => field.field_type === 'PRESCRIBED_DATE',
                  )?.field_id ?? ''
                ] || '처방일 확인 필요'}
              </h2>
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
            const medicationStrength = group.fields.find(
              (field) => field.field_type === 'MEDICATION_STRENGTH',
            )

            const nameValue = medicationName
              ? draftValues[medicationName.field_id]?.trim() ?? ''
              : ''

            const strengthValue = medicationStrength
              ? draftValues[medicationStrength.field_id]?.trim() ?? ''
              : ''

            // 편집 필드는 분리하지만 카드 제목에서는 처방전 표기처럼 함께 보여줍니다.
            const summaryValue = [nameValue, strengthValue]
              .filter(Boolean)
              .join(' ')

            const getMedicationValue = (fieldType: string) => {
              const field = group.fields.find(
                (item) => item.field_type === fieldType,
              )
              return field ? draftValues[field.field_id]?.trim() ?? '' : ''
            }
            const doseValue = getMedicationValue('DOSE_VALUE')
            const doseUnit = getMedicationValue('DOSE_UNIT')
            const frequency = getMedicationValue('FREQUENCY_PER_DAY')
            const duration = getMedicationValue('DURATION_DAYS')
            const timing = getMedicationValue('TIMING')
            const medicationSummary = [
              doseValue ? `1회 ${doseValue}${doseUnit ? ` ${doseUnit}` : ''}` : '',
              frequency ? `하루 ${frequency}회` : '',
              duration ? `${duration}일 복용` : '',
              timing,
            ].filter(Boolean)

            return (
              <Card className="prescription-review__card" key={group.index}>
                <div className="prescription-review__card-title">
                  <div className="prescription-review__med-icon" aria-hidden="true">
                    <span className="prescription-review__med-dot" />
                  </div>
                  <div className="prescription-review__card-heading">
                    <span>처방약 {groupIndex + 1}</span>
                    <strong>약물 정보</strong>
                  </div>
                  <StatusBadge tone={allConfirmed ? 'neutral' : 'attention'}>
                    {allConfirmed ? '확인 완료' : '확인 필요'}
                  </StatusBadge>
                </div>
                <h2>{summaryValue || '약 이름 확인 필요'}</h2>
                <p className="prescription-review__card-summary">
                  {medicationSummary.length > 0
                    ? medicationSummary.join(' · ')
                    : '세부 복용 정보를 확인해 주세요'}
                </p>
                <details className="prescription-review__editor">
                  <summary>
                    <span>세부 항목 확인 및 수정</span>
                    <span className="prescription-review__editor-chevron" aria-hidden="true" />
                  </summary>
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
                {isConfirming ? '처방 확정 중...' : '확정하고 가이드 만들기'}
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
