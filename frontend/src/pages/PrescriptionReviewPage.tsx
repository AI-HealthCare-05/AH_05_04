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
import { DoseyMascot } from '../design-system/DoseyMascot'
import { createGuide } from '../api/guides'
import '../design-system/prototype.css'
import './PrescriptionReviewPage.css'

const fieldLabels: Record<string, string> = {
  PRESCRIBED_DATE: '처방일',
  MEDICATION_NAME: '약물이름',

  // 약 이름에 붙은 제품 함량을 별도로 검수합니다.
  MEDICATION_STRENGTH: '제품함량',

  DOSE_VALUE: '1회 복용량',
  FREQUENCY_PER_DAY: '하루횟수',
  TIMING: '복용조건',
  DURATION_DAYS: '투약일수',
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

// 처방일과 필수 약품 필드는 값의 유무와 관계없이 반드시 확인해야 합니다.
// 선택 필드는 값이 입력된 경우에만 최종 확인 대상으로 취급합니다.
const requiredReviewFieldTypes = new Set<string>([
  'PRESCRIBED_DATE',
  ...requiredMedicationFieldTypes,
])

type ReviewSectionKey = 'prescription-date' | `medication-${number}`

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
  // CONFIRMED + null은 사용자가 선택 필드를
  // “값 없음”으로 확인한 상태입니다.
  if (field.confirmation_status === 'CONFIRMED') {
    return field.confirmed_value?.trim() ?? ''
  }

  // 처방일만 Backend가 만든 YYYY-MM-DD 값을 사용합니다.
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
  const confirmedValue = field.confirmed_value?.trim() ?? ''

  return (
    field.confirmation_status === 'CONFIRMED' &&
    draftValue === confirmedValue
  )
}

// 필수 필드는 항상 확인 대상입니다.
// 선택 필드는 OCR 값이 있거나 사용자가 직접 값을 입력한 경우에만
// 저장 및 확인 대상으로 포함합니다.
function requiresUserConfirmation(
  field: ExtractedField,
  draftValues: Record<string, string>,
) {
  const draftValue = draftValues[field.field_id]?.trim() ?? ''

  return (
    requiredReviewFieldTypes.has(field.field_type) ||
    Boolean(draftValue)
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

function getFieldValidationError(field: ExtractedField, value: string) {
  if (requiredReviewFieldTypes.has(field.field_type) && !value.trim()) {
    return `${getFieldLabel(field.field_type)}을(를) 입력해 주세요.`
  }

  return value.trim()
    ? getNumericFieldError(field.field_type, value.trim())
    : null
}

function formatDateForDisplay(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? value.replaceAll('-', '.')
    : value
}

function formatFieldValue(fieldType: string, value: string) {
  if (!value.trim()) return '—'
  if (fieldType === 'PRESCRIBED_DATE') return formatDateForDisplay(value)
  if (fieldType === 'FREQUENCY_PER_DAY' && !value.endsWith('회')) {
    return `${value}회`
  }
  if (fieldType === 'DURATION_DAYS' && !value.endsWith('일')) {
    return `${value}일`
  }
  return value
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
  const [editingSections, setEditingSections] = useState<Set<ReviewSectionKey>>(
    () => new Set(),
  )
  const [revokedReviewSections, setRevokedReviewSections] = useState<
    Set<ReviewSectionKey>
  >(() => new Set())
  const [savingSections, setSavingSections] = useState<Set<ReviewSectionKey>>(
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

  // 필수 필드와 값이 있는 선택 필드만 최종 확인 대상으로 계산합니다.
  // 빈 TIMING, DOSE_UNIT, MEDICATION_STRENGTH 등 선택 필드는
  // 처방 확정을 막지 않습니다.
  const reviewTargetFields = useMemo(
    () =>
      fields.filter((field) =>
        requiresUserConfirmation(field, draftValues),
      ),
    [draftValues, fields],
  )

  const allReviewTargetFieldsConfirmed = useMemo(
    () =>
      reviewTargetFields.length > 0 &&
      reviewTargetFields.every((field) =>
        isFieldConfirmed(field, draftValues),
      ),
    [draftValues, reviewTargetFields],
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
    allReviewTargetFieldsConfirmed &&
    !hasMissingPrescribedDateField &&
    !hasMissingRequiredMedicationFields &&
    !hasUnsavedChanges &&
    savingFieldIds.size === 0 &&
    editingSections.size === 0 &&
    revokedReviewSections.size === 0

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
    setEditingSections(new Set())
    setRevokedReviewSections(new Set())
    setSavingSections(new Set())
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

  const startEditing = (sectionKey: ReviewSectionKey) => {
    setEditingSections((current) => new Set(current).add(sectionKey))
    setRevokedReviewSections((current) => new Set(current).add(sectionKey))
    setUserConfirmed(false)
  }

  const cancelEditing = (
    sectionKey: ReviewSectionKey,
    sectionFields: ExtractedField[],
  ) => {
    setDraftValues((current) => ({
      ...current,
      ...Object.fromEntries(
        sectionFields.map((field) => [
          field.field_id,
          getSavedDisplayValue(field),
        ]),
      ),
    }))
    setFieldErrors((current) => {
      const next = { ...current }
      for (const field of sectionFields) delete next[field.field_id]
      return next
    })
    setEditingSections((current) => {
      const next = new Set(current)
      next.delete(sectionKey)
      return next
    })
    setUserConfirmed(false)
  }

  const isSectionReviewed = (
    sectionKey: ReviewSectionKey,
    sectionFields: ExtractedField[],
  ) => {
    if (
      editingSections.has(sectionKey) ||
      revokedReviewSections.has(sectionKey)
    ) {
      return false
    }

    const targetFields = sectionFields.filter((field) =>
      requiresUserConfirmation(field, draftValues),
    )

    return (
      targetFields.length > 0 &&
      targetFields.every((field) => isFieldConfirmed(field, draftValues))
    )
  }

  const sectionHasValidationError = (sectionFields: ExtractedField[]) =>
    sectionFields.some((field) =>
      Boolean(
        getFieldValidationError(
          field,
          draftValues[field.field_id] ?? '',
        ),
      ),
    )

  const handleReviewSection = async (
    sectionKey: ReviewSectionKey,
    sectionFields: ExtractedField[],
  ) => {
    if (
      savingSections.has(sectionKey) ||
      isConfirming ||
      prescription
    ) {
      return
    }

    const validationErrors = Object.fromEntries(
      sectionFields.flatMap((field) => {
        const error = getFieldValidationError(
          field,
          draftValues[field.field_id] ?? '',
        )
        return error ? [[field.field_id, error]] : []
      }),
    )

    setFieldErrors((current) => {
      const next = { ...current }
      for (const field of sectionFields) delete next[field.field_id]
      return { ...next, ...validationErrors }
    })

    if (Object.keys(validationErrors).length > 0) {
      setUserConfirmed(false)
      return
    }

    const fieldsToSave = sectionFields.filter((field) => {
      const draftValue = draftValues[field.field_id]?.trim() ?? ''
      const savedValue = getSavedDisplayValue(field).trim()
      const changed = draftValue !== savedValue

      return (
        changed ||
        (requiresUserConfirmation(field, draftValues) &&
          !isFieldConfirmed(field, draftValues))
      )
    })

    if (fieldsToSave.length === 0) {
      setEditingSections((current) => {
        const next = new Set(current)
        next.delete(sectionKey)
        return next
      })
      setRevokedReviewSections((current) => {
        const next = new Set(current)
        next.delete(sectionKey)
        return next
      })
      setUserConfirmed(false)
      return
    }

    const saveRequestKey = reviewRequestKey
    setSavingSections((current) => new Set(current).add(sectionKey))
    setSavingFieldIds((current) => {
      const next = new Set(current)
      for (const field of fieldsToSave) next.add(field.field_id)
      return next
    })
    setMessage(null)

    try {
      for (const field of fieldsToSave) {
        const confirmedValue =
          draftValues[field.field_id]?.trim() || null
        const response = await updateExtractedField(
          field.field_id,
          confirmedValue,
        )
        if (latestReviewRequestKeyRef.current !== saveRequestKey) return

        setFields((current) =>
          current.map((item) =>
            item.field_id === field.field_id ? response.data : item,
          ),
        )
        setDraftValues((current) => ({
          ...current,
          [field.field_id]: getSavedDisplayValue(response.data),
        }))
      }

      setEditingSections((current) => {
        const next = new Set(current)
        next.delete(sectionKey)
        return next
      })
      setRevokedReviewSections((current) => {
        const next = new Set(current)
        next.delete(sectionKey)
        return next
      })
      setUserConfirmed(false)
    } catch (error) {
      if (latestReviewRequestKeyRef.current !== saveRequestKey) return
      applyReviewError(error, '검토 정보를 저장하는 중 오류가 발생했습니다.')
    } finally {
      if (latestReviewRequestKeyRef.current === saveRequestKey) {
        setSavingSections((current) => {
          const next = new Set(current)
          next.delete(sectionKey)
          return next
        })
        setSavingFieldIds((current) => {
          const next = new Set(current)
          for (const field of fieldsToSave) next.delete(field.field_id)
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

  const renderEditField = (field: ExtractedField) => {
    const draftValue = draftValues[field.field_id] ?? ''
    const isSaving = savingFieldIds.has(field.field_id)
    const fieldError = fieldErrors[field.field_id] ??
      getFieldValidationError(field, draftValue)
    const inputMode =
      field.field_type === 'DOSE_VALUE'
        ? 'decimal'
        : field.field_type === 'FREQUENCY_PER_DAY' ||
            field.field_type === 'DURATION_DAYS'
          ? 'numeric'
          : undefined

    const isWide =
      field.field_type === 'PRESCRIBED_DATE' ||
      field.field_type === 'MEDICATION_NAME' ||
      field.field_type === 'DURATION_DAYS'

    return (
      <label
        className={`prescription-review__edit-field ${
          isWide ? 'prescription-review__edit-field--wide' : ''
        }`}
        htmlFor={`field-${field.field_id}`}
        key={field.field_id}
      >
        <span>{getFieldLabel(field.field_type)}</span>
        <span className="prescription-review__edit-control">
          <input
            id={`field-${field.field_id}`}
            value={draftValue}
            inputMode={inputMode}
            placeholder={
              requiredReviewFieldTypes.has(field.field_type)
                ? '필수 입력'
                : '선택 입력'
            }
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
        </span>
        <small
          id={`field-help-${field.field_id}`}
          className={fieldError ? 'is-error' : ''}
          role={fieldError ? 'alert' : undefined}
        >
          {fieldError ?? ''}
        </small>
      </label>
    )
  }

  const prescribedDateField = prescriptionFields.find(
    (field) => field.field_type === 'PRESCRIBED_DATE',
  )
  const prescriptionSectionKey: ReviewSectionKey = 'prescription-date'
  const prescriptionDateReviewed =
    prescribedDateField &&
    isSectionReviewed(prescriptionSectionKey, [prescribedDateField])

  const reviewedMedicationCount = medicationGroups.filter((group) =>
    isSectionReviewed(`medication-${group.index}`, group.fields),
  ).length
  const medicationProgress = medicationGroups.length > 0
    ? Math.round((reviewedMedicationCount / medicationGroups.length) * 100)
    : 0
  const allMedicationGroupsReviewed =
    medicationGroups.length > 0 &&
    reviewedMedicationCount === medicationGroups.length

  const renderBadge = (
    state: 'reviewed' | 'editing' | 'required' | 'unreviewed',
  ) => {
    const label = state === 'reviewed'
      ? '✓ 검토 완료'
      : state === 'editing'
        ? '수정 중'
        : state === 'required'
          ? '확인 필요'
          : '검토 전'

    return (
      <span className={`prescription-review__badge is-${state}`}>
        {label}
      </span>
    )
  }

  const renderPrescriptionCard = () => {
    const isEditing = editingSections.has(prescriptionSectionKey)
    const isSaving = savingSections.has(prescriptionSectionKey)
    const dateValue = prescribedDateField
      ? draftValues[prescribedDateField.field_id] ?? ''
      : ''
    const dateError = prescribedDateField
      ? fieldErrors[prescribedDateField.field_id] ??
        getFieldValidationError(prescribedDateField, dateValue)
      : '처방일을 확인할 수 없습니다.'
    const reviewed = Boolean(prescriptionDateReviewed)

    return (
      <section className="prescription-review__prescription-card">
        <div className="prescription-review__prescription-heading">
          <h2>처방 정보</h2>
          {documentUrl ? (
            <details className="prescription-review__source">
              <summary>원본 처방전 보기</summary>
              <iframe src={documentUrl} title="원본 처방전" />
            </details>
          ) : (
            <button type="button" disabled>원본 처방전 보기</button>
          )}
        </div>

        <div className="prescription-review__section-status">
          <strong>처방일</strong>
          {renderBadge(
            isEditing
              ? 'editing'
              : dateError
                ? 'required'
                : reviewed
                  ? 'reviewed'
                  : 'unreviewed',
          )}
        </div>

        {isEditing && prescribedDateField ? (
          <>
            <div className="prescription-review__edit-grid prescription-review__edit-grid--date">
              {renderEditField(prescribedDateField)}
            </div>
            <div className="prescription-review__section-actions">
              <Button
                variant="secondary"
                disabled={isSaving}
                onClick={() =>
                  cancelEditing(prescriptionSectionKey, [prescribedDateField])
                }
              >
                취소
              </Button>
              <Button
                disabled={isSaving || Boolean(dateError)}
                onClick={() =>
                  handleReviewSection(
                    prescriptionSectionKey,
                    [prescribedDateField],
                  )
                }
              >
                {isSaving ? '저장 중...' : '수정완료'}
              </Button>
            </div>
          </>
        ) : (
          <>
            <div
              className={`prescription-review__date-value ${
                dateError ? 'is-error' : ''
              }`}
            >
              <strong>{formatDateForDisplay(dateValue) || '—'}</strong>
              {dateError && <small role="alert">{dateError}</small>}
            </div>
            <div className="prescription-review__section-actions">
              <Button
                variant="secondary"
                disabled={!prescribedDateField}
                onClick={() => startEditing(prescriptionSectionKey)}
              >
                수정
              </Button>
              {!reviewed && (
                <Button
                  disabled={
                    !prescribedDateField ||
                    Boolean(dateError) ||
                    isSaving
                  }
                  onClick={() =>
                    prescribedDateField &&
                    handleReviewSection(
                      prescriptionSectionKey,
                      [prescribedDateField],
                    )
                  }
                >
                  {isSaving ? '저장 중...' : '검토 완료'}
                </Button>
              )}
            </div>
          </>
        )}
      </section>
    )
  }

  const renderMedicationCard = (
    group: { index: number; fields: ExtractedField[] },
    groupIndex: number,
  ) => {
    const sectionKey: ReviewSectionKey = `medication-${group.index}`
    const isEditing = editingSections.has(sectionKey)
    const isSaving = savingSections.has(sectionKey)
    const reviewed = isSectionReviewed(sectionKey, group.fields)
    const missingRequiredLabels = requiredMedicationFieldTypes.flatMap(
      (fieldType) => {
        const field = group.fields.find(
          (candidate) => candidate.field_type === fieldType,
        )
        const invalid = !field || Boolean(
          getFieldValidationError(
            field,
            draftValues[field.field_id] ?? '',
          ),
        )
        return invalid ? [getFieldLabel(fieldType)] : []
      },
    )
    const validationErrorCount = missingRequiredLabels.length
    const hasValidationError =
      validationErrorCount > 0 || sectionHasValidationError(group.fields)
    const getValue = (fieldType: string) => {
      const field = group.fields.find(
        (candidate) => candidate.field_type === fieldType,
      )
      return field ? draftValues[field.field_id]?.trim() ?? '' : ''
    }
    const medicationName = getValue('MEDICATION_NAME')
    const strength = getValue('MEDICATION_STRENGTH')
    const title = [medicationName, strength].filter(Boolean).join(' ')
    const rows = [
      'MEDICATION_STRENGTH',
      'DOSE_VALUE',
      'FREQUENCY_PER_DAY',
      'TIMING',
      'DURATION_DAYS',
    ]

    return (
      <section
        className={`prescription-review__medication-card ${
          isEditing ? 'is-editing' : ''
        } ${hasValidationError ? 'has-error' : ''}`}
        key={group.index}
      >
        <div className="prescription-review__medication-heading">
          <span className="prescription-review__medication-index" aria-hidden="true">
            {reviewed ? '✓' : groupIndex + 1}
          </span>
          <h2>{title || '약 이름 확인 필요'}</h2>
          {renderBadge(
            isEditing
              ? 'editing'
              : hasValidationError
                ? 'required'
                : reviewed
                  ? 'reviewed'
                  : 'unreviewed',
          )}
        </div>

        {isEditing ? (
          <>
            <p className="prescription-review__editing-notice" role="status">
              수정 시작과 동시에 이 약의 검토 완료가 해제됐습니다.
            </p>
            <div className="prescription-review__edit-grid">
              {group.fields
                .filter((field) => field.field_type !== 'DOSE_UNIT')
                .map(renderEditField)}
            </div>
            <div className="prescription-review__section-actions">
              <Button
                variant="secondary"
                disabled={isSaving}
                onClick={() => cancelEditing(sectionKey, group.fields)}
              >
                취소
              </Button>
              <Button
                disabled={isSaving || hasValidationError}
                onClick={() => handleReviewSection(sectionKey, group.fields)}
              >
                {isSaving ? '저장 중...' : '수정완료'}
              </Button>
            </div>
          </>
        ) : (
          <>
            {validationErrorCount > 0 && (
              <p className="prescription-review__card-error" role="alert">
                필수값 {validationErrorCount}개 누락 · {missingRequiredLabels.join(', ')} 확인이 필요해요.
              </p>
            )}
            <dl className="prescription-review__medication-values">
              {rows.map((fieldType) => {
                const value = getValue(fieldType)
                const isRequiredMissing =
                  requiredReviewFieldTypes.has(fieldType) && !value
                return (
                  <div className={isRequiredMissing ? 'is-error' : ''} key={fieldType}>
                    <dt>{getFieldLabel(fieldType)}</dt>
                    <dd>{formatFieldValue(fieldType, value)}</dd>
                    {isRequiredMissing && (
                      <small>{getFieldLabel(fieldType)}을(를) 입력해 주세요.</small>
                    )}
                  </div>
                )
              })}
            </dl>
            <div className="prescription-review__section-actions">
              <Button
                variant="secondary"
                onClick={() => startEditing(sectionKey)}
              >
                수정하기
              </Button>
              {!reviewed && (
                <Button
                  disabled={hasValidationError || isSaving}
                  onClick={() => handleReviewSection(sectionKey, group.fields)}
                >
                  {isSaving ? '저장 중...' : '검토 완료'}
                </Button>
              )}
            </div>
          </>
        )}
      </section>
    )
  }

  if (isLoading) {
    return (
      <div className="prescription-review-page">
        <MobileShell
          title="Dosey 도지"
          onBack={() => navigate('/prescriptions/upload')}
          brandMark={<DoseyMascot variant="header" />}
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
          brandMark={<DoseyMascot variant="header" />}
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
          brandMark={<DoseyMascot variant="header" />}
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
          brandMark={<DoseyMascot variant="header" />}
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
        brandMark={<DoseyMascot variant="header" />}
        backPlacement="content"
        hideNavigation
      >
        <main className="app-scroll prescription-review prescription-review__content">
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
            <strong>
              {editingSections.size > 0
                ? '수정 중인 정보는 검토 완료가 해제돼요.'
                : prescriptionDateReviewed && allMedicationGroupsReviewed
                  ? '처방일과 모든 약의 검토를 완료했어요.'
                  : '처방일과 약별 정보를 확인해 주세요.'}
            </strong>
            <span>
              {editingSections.size > 0
                ? '입력값 저장 후 조회 상태에서 다시 검토 완료해 주세요.'
                : prescriptionDateReviewed && allMedicationGroupsReviewed
                  ? '원본 처방전과 직접 대조한 뒤 아래 항목을 체크해 주세요.'
                  : '값을 확인하거나 수정한 뒤, 처방일과 각 약의 검토 완료를 눌러 주세요.'}
            </span>
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

          {renderPrescriptionCard()}

          <section
            className="prescription-review__medication-progress"
            aria-label="약 검토 진행률"
          >
            <div>
              <strong>
                약 {reviewedMedicationCount}/{medicationGroups.length}개 검토 완료
              </strong>
              <span>{medicationProgress}%</span>
            </div>
            <span
              className="prescription-review__progress-track"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={medicationProgress}
            >
              <span style={{ width: `${medicationProgress}%` }} />
            </span>
          </section>

          {medicationGroups.map(renderMedicationCard)}

          {!prescription && (
            <>
              <label
                className="prescription-review__acknowledgement"
              >
                <input
                  type="checkbox"
                  checked={userConfirmed}
                  disabled={!reviewReadyForAcknowledgement || isConfirming}
                  onChange={(event) => setUserConfirmed(event.target.checked)}
                />
                <span>
                  원본 처방전의 모든 항목을 직접 확인했습니다.
                </span>
              </label>

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
                {isConfirming
                  ? '처방 확정 중...'
                  : '처방전 확정 및 가이드 만들기'}
              </Button>
            </>
          )}

        </main>
      </MobileShell>
    </div>
  )
}

export default PrescriptionReviewPage
