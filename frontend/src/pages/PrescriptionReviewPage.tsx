import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import type { NavigateFunction } from 'react-router-dom'
import { ApiError } from '../api/client'
import { getOcrConsent } from '../api/ocrConsent'
import {
  confirmPrescription,
  createManualMedication,
  getOcrJob,
  getPrescriptionDocumentFile,
  getPrescriptionNormalizedImage,
  updateExtractedField,
  type ExtractedField,
  type CreateManualMedicationRequest,
  type OcrJobResponse,
  type OcrSourceImage,
  type OcrSourceLocation,
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

export type PrescriptionReviewServices = {
  getOcrConsent: typeof getOcrConsent
  getOcrJob: typeof getOcrJob
  getPrescriptionDocumentFile: typeof getPrescriptionDocumentFile
  getPrescriptionNormalizedImage: typeof getPrescriptionNormalizedImage
  updateExtractedField: typeof updateExtractedField
  confirmPrescription: typeof confirmPrescription
  createManualMedication: typeof createManualMedication
  createGuide: typeof createGuide
}

export type PrescriptionReviewPreviewState = {
  documentId: string
  jobId: string
  userConfirmed?: boolean
  manualAddMode?: 'form' | 'validation'
  unreviewedMedicationIndexes?: number[]
}

export type PrescriptionReviewPageProps = {
  services?: PrescriptionReviewServices
  previewState?: PrescriptionReviewPreviewState
  navigation?: NavigateFunction
}

const defaultPrescriptionReviewServices: PrescriptionReviewServices = {
  getOcrConsent,
  getOcrJob,
  getPrescriptionDocumentFile,
  getPrescriptionNormalizedImage,
  updateExtractedField,
  confirmPrescription,
  createManualMedication,
  createGuide,
}

const fieldLabels: Record<string, string> = {
  PRESCRIBED_DATE: '처방일',
  MEDICATION_NAME: '약물이름',

  // 약 이름에 붙은 제품 함량을 별도로 검수합니다.
  MEDICATION_STRENGTH: '제품함량',

  DOSE_VALUE: '1회 복용량',
  DOSE_UNIT: '복용단위',
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

const manuallyEnterableOcrPlaceholderFieldTypes = new Set<string>([
  'PRESCRIBED_DATE',
  'DOSE_VALUE',
  'FREQUENCY_PER_DAY',
  'DURATION_DAYS',
])

const manualEntryNotice =
  'OCR이 인식하지 못해 직접 입력이 필요한 필드예요.'

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

  if (
    error.code === 'OCR_JOB_NOT_FOUND' ||
    error.code === 'MEDICAL_DOCUMENT_NOT_FOUND'
  ) {
    return {
      title: '처방전 정보를 찾을 수 없어요',
      message: '처방전 검수 정보를 다시 불러와 주세요.',
      nextAction: '처방전을 다시 업로드해 주세요.',
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

function isUnconfirmedEmptyOcrField(field: ExtractedField) {
  return (
    field.confirmation_status === 'UNCONFIRMED' &&
    field.raw_value === null &&
    field.normalized_value === null &&
    field.confidence_score === null &&
    field.confirmed_value === null
  )
}

function isRequiredOcrPlaceholder(field: ExtractedField) {
  return (
    manuallyEnterableOcrPlaceholderFieldTypes.has(field.field_type) &&
    isUnconfirmedEmptyOcrField(field)
  )
}

const SOURCE_MIN_SCALE = 1
const SOURCE_MAX_SCALE = 3

/**
 * #809 pinch pan 범위 제한. 확대된 canvas가 viewer 밖으로 완전히
 * 빠져나가지 않도록 [viewer - scaled, 0] 구간으로 자른다.
 */
function clampSourcePan(
  panX: number,
  panY: number,
  scale: number,
  viewer: HTMLElement | null,
  canvas: HTMLElement | null,
) {
  if (!viewer || !canvas) return { x: 0, y: 0 }
  if (scale <= SOURCE_MIN_SCALE) return { x: 0, y: 0 }

  const viewerWidth = viewer.clientWidth
  const viewerHeight = viewer.clientHeight
  const scaledWidth = canvas.clientWidth * scale
  const scaledHeight = canvas.clientHeight * scale

  const minX = Math.min(0, viewerWidth - scaledWidth)
  const minY = Math.min(0, viewerHeight - scaledHeight)

  return {
    x: Math.min(0, Math.max(panX, minX)),
    y: Math.min(0, Math.max(panY, minY)),
  }
}

function getPointerDistance(points: { x: number; y: number }[]) {
  const [a, b] = points
  return Math.hypot(a.x - b.x, a.y - b.y)
}

function isUnrecoverableMedicationNameField(field: ExtractedField) {
  return (
    field.field_type === 'MEDICATION_NAME' &&
    isUnconfirmedEmptyOcrField(field)
  )
}

/**
 * #809 강조 표시용 정규화 이미지 메타데이터 검증.
 * normalized=true이고 양수 크기와 URL이 모두 있을 때만 통과한다(fail-closed).
 */
type ValidatedSourceImage = {
  width: number
  height: number
  url: string
}

function getValidatedSourceImage(
  sourceImage: OcrSourceImage | null | undefined,
): ValidatedSourceImage | null {
  if (!sourceImage || sourceImage.normalized !== true) return null

  const { width, height, url } = sourceImage

  if (typeof url !== 'string' || url.trim() === '') return null
  if (typeof width !== 'number' || !Number.isFinite(width) || width <= 0) {
    return null
  }
  if (typeof height !== 'number' || !Number.isFinite(height) || height <= 0) {
    return null
  }

  return { width, height, url }
}

/**
 * #809 근거 좌표 검증. 계약상 page는 항상 1이며 좌표는 정규화 이미지 픽셀 기준이다.
 * 다른 페이지·비정상 값·이미지 바깥 좌표는 강조하지 않는다(fail-closed).
 * 이 값은 provenance 표시 전용이며 자동 확인/승인 판단에 사용하지 않는다.
 */
function getValidatedSourceBox(
  sourceLocation: OcrSourceLocation | null | undefined,
  sourceImage: ValidatedSourceImage,
): { x: number; y: number; width: number; height: number } | null {
  if (!sourceLocation) return null
  if (sourceLocation.page !== 1) return null
  if (!Array.isArray(sourceLocation.bbox) || sourceLocation.bbox.length !== 4) {
    return null
  }

  const [x, y, width, height] = sourceLocation.bbox

  if (![x, y, width, height].every((value) =>
    typeof value === 'number' && Number.isFinite(value),
  )) {
    return null
  }

  if (width <= 0 || height <= 0) return null
  if (x < 0 || y < 0) return null
  if (x + width > sourceImage.width) return null
  if (y + height > sourceImage.height) return null

  return { x, y, width, height }
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

const emptyManualMedication: CreateManualMedicationRequest = {
  medication_name: '',
  medication_strength: '',
  dose_value: '',
  dose_unit: '',
  frequency_per_day: '',
  timing: '',
  duration_days: '',
}

function hasAtMostDecimalPlaces(value: string, maximumPlaces: number) {
  const normalizedValue = value.replaceAll('_', '')
  const [coefficient, exponentText] = normalizedValue.toLowerCase().split('e')
  const unsignedCoefficient = coefficient.replace(/^[+-]/, '')
  const fractionLength = unsignedCoefficient.split('.')[1]?.length ?? 0
  const digits = unsignedCoefficient.replace('.', '')
  const trailingZeroCount = digits.match(/0+$/)?.[0].length ?? 0
  const decimalExponent =
    BigInt(exponentText ?? '0') -
    BigInt(fractionLength) +
    BigInt(trailingZeroCount)

  return decimalExponent >= -BigInt(maximumPlaces)
}

function validateManualMedication(payload: CreateManualMedicationRequest) {
  const errors: Record<string, string> = {}
  const medicationName = payload.medication_name.trim()
  const doseValue = payload.dose_value.trim()
  const frequencyPerDay = payload.frequency_per_day.trim()
  const durationDays = payload.duration_days.trim()

  if (!medicationName) {
    errors.medication_name = '약물이름을 입력해 주세요.'
  } else if (medicationName.length > 255) {
    errors.medication_name = '약물이름은 255자 이하로 입력해 주세요.'
  }

  const doseNumber = Number(doseValue.replaceAll('_', ''))
  const doseFormatError = getNumericFieldError('DOSE_VALUE', doseValue)
  const doseHasAtMostThreeDecimals =
    !doseFormatError && hasAtMostDecimalPlaces(doseValue, 3)
  if (
    !doseValue ||
    doseFormatError ||
    doseNumber > 9_999_999.999 ||
    !doseHasAtMostThreeDecimals
  ) {
    errors.dose_value =
      '1회 복용량은 0보다 큰 숫자로, 소수점 아래 3자리까지 입력해 주세요.'
  }

  for (const [key, value, label] of [
    ['frequency_per_day', frequencyPerDay, '하루 횟수'],
    ['duration_days', durationDays, '투약일수'],
  ] as const) {
    if (
      !/^[0-9]+$/.test(value) ||
      Number(value) <= 0 ||
      Number(value) > 2_147_483_647
    ) {
      errors[key] = `${label}는 0보다 큰 정수로 입력해 주세요.`
    }
  }

  for (const [key, value, maxLength, label] of [
    ['medication_strength', payload.medication_strength, 100, '제품함량'],
    ['dose_unit', payload.dose_unit, 50, '복용단위'],
    ['timing', payload.timing, 255, '복용조건'],
  ] as const) {
    if ((value ?? '').trim().length > maxLength) {
      errors[key] = `${label}은(는) ${maxLength}자 이하로 입력해 주세요.`
    }
  }
  return errors
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

function PrescriptionReviewPage({
  services = defaultPrescriptionReviewServices,
  previewState,
  navigation,
}: PrescriptionReviewPageProps = {}) {
  const routerNavigate = useNavigate()
  const navigate = navigation ?? routerNavigate
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const documentId = previewState?.documentId ?? searchParams.get('document_id')
  const jobId = previewState?.jobId ?? searchParams.get('job_id')
  const prefetchedOcrResponse = (
    location.state as { ocrResponse?: OcrJobResponse } | null
  )?.ocrResponse
  const reviewRequestKey = `${documentId ?? ''}:${jobId ?? ''}`
  const latestReviewRequestKeyRef = useRef(reviewRequestKey)
  const guideCreationRequestRef = useRef<symbol | null>(null)
  latestReviewRequestKeyRef.current = reviewRequestKey

  const [fields, setFields] = useState<ExtractedField[]>([])
  const [draftValues, setDraftValues] = useState<Record<string, string>>({})
  const [documentUrl, setDocumentUrl] = useState<string | null>(null)

  // #809 정규화 이미지 overlay 상태. viewer 실패는 검수를 막지 않는다.
  const [sourceImage, setSourceImage] =
    useState<ValidatedSourceImage | null>(null)
  const [sourceImageUrl, setSourceImageUrl] = useState<string | null>(null)
  const [isSourceImageLoaded, setIsSourceImageLoaded] = useState(false)
  const [activeSourceFieldId, setActiveSourceFieldId] =
    useState<string | null>(null)
  const [isSourceViewerOpen, setIsSourceViewerOpen] = useState(false)
  const sourceViewerRef = useRef<HTMLDivElement | null>(null)
  const sourceCanvasRef = useRef<HTMLDivElement | null>(null)

  // #809 pinch zoom: viewer 내부에서만 확대/이동한다.
  const [sourceScale, setSourceScale] = useState(SOURCE_MIN_SCALE)
  const [sourcePan, setSourcePan] = useState({ x: 0, y: 0 })
  const activeSourcePointersRef = useRef(
    new Map<number, { x: number; y: number }>(),
  )
  const sourcePinchRef = useRef<{
    distance: number
    scale: number
    panX: number
    panY: number
  } | null>(null)
  const sourcePanStartRef = useRef<{
    x: number
    y: number
    panX: number
    panY: number
  } | null>(null)
  // scale=1에서는 touch-action:none 때문에 브라우저 스크롤이 없으므로
  // viewer.scrollTop/Left를 직접 움직인다.
  const sourceScrollStartRef = useRef<{
    x: number
    y: number
    scrollTop: number
    scrollLeft: number
  } | null>(null)

  /**
   * #809 highlight lifetime: 선택이 끝나면 명시적으로 null로 되돌린다.
   * blur, 수정 취소, 수정완료, 수정모드 종료에서 모두 호출한다.
   */
  const clearSourceSelection = useCallback(() => {
    setActiveSourceFieldId(null)
  }, [])

  const resetSourceZoom = useCallback(() => {
    activeSourcePointersRef.current.clear()
    sourcePinchRef.current = null
    sourcePanStartRef.current = null
    sourceScrollStartRef.current = null
    setSourceScale(SOURCE_MIN_SCALE)
    setSourcePan({ x: 0, y: 0 })
  }, [])

  // details를 닫거나 정규화 이미지가 바뀌면 zoom 상태를 초기화한다.
  useEffect(() => {
    if (!isSourceViewerOpen) resetSourceZoom()
  }, [isSourceViewerOpen, resetSourceZoom])

  useEffect(() => {
    resetSourceZoom()
  }, [sourceImageUrl, resetSourceZoom])

  const handleSourcePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const pointers = activeSourcePointersRef.current
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })

      if (typeof event.currentTarget.setPointerCapture === 'function') {
        try {
          event.currentTarget.setPointerCapture(event.pointerId)
        } catch {
          // capture 미지원이어도 gesture는 계속 동작한다.
        }
      }

      if (pointers.size === 2) {
        sourcePanStartRef.current = null
        sourceScrollStartRef.current = null
        sourcePinchRef.current = {
          distance: getPointerDistance([...pointers.values()]),
          scale: sourceScale,
          panX: sourcePan.x,
          panY: sourcePan.y,
        }
        return
      }

      if (pointers.size === 1) {
        if (sourceScale > SOURCE_MIN_SCALE) {
          sourceScrollStartRef.current = null
          sourcePanStartRef.current = {
            x: event.clientX,
            y: event.clientY,
            panX: sourcePan.x,
            panY: sourcePan.y,
          }
          return
        }

        // scale=1: touch-action:none이므로 세로 스크롤을 직접 재현한다.
        const viewer = sourceViewerRef.current
        sourcePanStartRef.current = null
        sourceScrollStartRef.current = viewer
          ? {
              x: event.clientX,
              y: event.clientY,
              scrollTop: viewer.scrollTop,
              scrollLeft: viewer.scrollLeft,
            }
          : null
      }
    },
    [sourcePan.x, sourcePan.y, sourceScale],
  )

  const handleSourcePointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const pointers = activeSourcePointersRef.current
      if (!pointers.has(event.pointerId)) return
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })

      const viewer = sourceViewerRef.current
      const canvas = sourceCanvasRef.current
      const pinch = sourcePinchRef.current

      if (pointers.size >= 2 && pinch && pinch.distance > 0) {
        const distance = getPointerDistance([...pointers.values()].slice(0, 2))
        const nextScale = Math.min(
          SOURCE_MAX_SCALE,
          Math.max(SOURCE_MIN_SCALE, (pinch.scale * distance) / pinch.distance),
        )

        setSourceScale(nextScale)
        setSourcePan(
          nextScale <= SOURCE_MIN_SCALE
            ? { x: 0, y: 0 }
            : clampSourcePan(pinch.panX, pinch.panY, nextScale, viewer, canvas),
        )
        return
      }

      const panStart = sourcePanStartRef.current
      if (pointers.size === 1 && panStart && sourceScale > SOURCE_MIN_SCALE) {
        setSourcePan(
          clampSourcePan(
            panStart.panX + (event.clientX - panStart.x),
            panStart.panY + (event.clientY - panStart.y),
            sourceScale,
            viewer,
            canvas,
          ),
        )
        return
      }

      // scale=1: 브라우저 대신 viewer scroll을 직접 이동시킨다.
      const scrollStart = sourceScrollStartRef.current
      if (pointers.size === 1 && scrollStart && viewer) {
        viewer.scrollTop = scrollStart.scrollTop - (event.clientY - scrollStart.y)
        viewer.scrollLeft =
          scrollStart.scrollLeft - (event.clientX - scrollStart.x)
      }
    },
    [sourceScale],
  )

  const handleSourcePointerEnd = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const pointers = activeSourcePointersRef.current
      pointers.delete(event.pointerId)

      if (pointers.size < 2) sourcePinchRef.current = null
      if (pointers.size === 0) {
        sourcePanStartRef.current = null
        sourceScrollStartRef.current = null
        // scale이 1로 돌아왔다면 pan도 원점으로 되돌린다.
        if (sourceScale <= SOURCE_MIN_SCALE) setSourcePan({ x: 0, y: 0 })
      }
    },
    [sourceScale],
  )
  const [prescription, setPrescription] =
    useState<PrescriptionResponse | null>(null)
  const [message, setMessage] = useState<ReviewMessage | null>(null)
  // llm_processing은 계약/데이터로 계속 보존한다.
  // Figma 최신 기준에서 사용자 노출 notice만 제거했다.
  const [, setLlmProcessing] = useState<OcrJobResponse['data']['llm_processing']>(null)
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
  const [userConfirmed, setUserConfirmed] = useState(
    previewState?.userConfirmed ?? false,
  )
  const [isManualAddOpen, setIsManualAddOpen] = useState(
    Boolean(previewState?.manualAddMode),
  )
  const [manualAddValues, setManualAddValues] =
    useState<CreateManualMedicationRequest>(emptyManualMedication)
  const [manualAddErrors, setManualAddErrors] = useState<Record<string, string>>(
    () =>
      previewState?.manualAddMode === 'validation'
        ? validateManualMedication(emptyManualMedication)
        : {},
  )
  const [isAddingMedication, setIsAddingMedication] = useState(false)
  const manualAddKeyRef = useRef<{ signature: string; key: string } | null>(null)

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

  const hasRequiredOcrPlaceholders = useMemo(
    () => fields.some(isRequiredOcrPlaceholder),
    [fields],
  )
  const hasStructurallyMissingRequiredFields =
    hasMissingPrescribedDateField || hasMissingRequiredMedicationFields
  const hasRequiredRecognitionIssue =
    hasRequiredOcrPlaceholders || hasStructurallyMissingRequiredFields

  const reviewReadyForAcknowledgement =
    prescribedDateConfirmed &&
    allRequiredMedicationFieldsConfirmed &&
    allReviewTargetFieldsConfirmed &&
    !hasMissingPrescribedDateField &&
    !hasMissingRequiredMedicationFields &&
    !hasUnsavedChanges &&
    savingFieldIds.size === 0 &&
    editingSections.size === 0 &&
    revokedReviewSections.size === 0 &&
    !isManualAddOpen &&
    !isAddingMedication

  const canConfirmPrescription = useMemo(() => {
    return (
      reviewReadyForAcknowledgement &&
      userConfirmed
    )
  }, [reviewReadyForAcknowledgement, userConfirmed])

  useEffect(() => {
    let isDisposed = false
    let objectUrl: string | null = null
    let normalizedObjectUrl: string | null = null
    const isLatestRequest = () =>
      !isDisposed &&
      latestReviewRequestKeyRef.current === reviewRequestKey

    setFields([])
    setDraftValues({})
    setDocumentUrl(null)
    setSourceImage(null)
    setSourceImageUrl(null)
    setIsSourceImageLoaded(false)
    setActiveSourceFieldId(null)
    setIsSourceViewerOpen(false)
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
    setUserConfirmed(previewState?.userConfirmed ?? false)
    setIsManualAddOpen(Boolean(previewState?.manualAddMode))
    setManualAddValues(emptyManualMedication)
    setManualAddErrors(
      previewState?.manualAddMode === 'validation'
        ? validateManualMedication(emptyManualMedication)
        : {},
    )
    setIsAddingMedication(false)
    manualAddKeyRef.current = null
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

    const resolvedDocumentId = documentId
    const resolvedJobId = jobId

    async function loadReviewData() {
      try {
        const consent = (await services.getOcrConsent()).data
        if (!isLatestRequest()) return
        if (!consent.effective) {
          setBlockingState({
            title: '처방전 검수를 진행할 수 없어요',
            message: '현재 OCR 동의가 유효하지 않아 기존 OCR 결과를 표시하지 않습니다.',
            nextAction: '처방전 처리 동의를 확인해 주세요.',
            action: 'UPLOAD',
          })
          return
        }
        const canUsePrefetchedResult =
          prefetchedOcrResponse?.data.document_id === resolvedDocumentId &&
          prefetchedOcrResponse.data.job_id === resolvedJobId &&
          prefetchedOcrResponse.data.ocr_status === 'COMPLETED'
        const ocrResponse = canUsePrefetchedResult
          ? prefetchedOcrResponse
          : await services.getOcrJob(resolvedJobId)
        if (!isLatestRequest()) return

        if (ocrResponse.data.document_id !== resolvedDocumentId) {
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
        setLlmProcessing(ocrResponse.data.llm_processing)

        if (ocrResponse.data.fields.some(isUnrecoverableMedicationNameField)) {
          setBlockingState({
            title: '약 이름을 인식하지 못했어요',
            message: '약 이름은 직접 입력해 검수를 진행할 수 없습니다.',
            nextAction: '처방전을 다시 업로드하거나 OCR을 다시 실행해 주세요.',
            action: 'UPLOAD',
          })
          return
        }

        const nextFields = ocrResponse.data.fields
        const placeholderSections = new Set<ReviewSectionKey>(
          nextFields
            .filter(isRequiredOcrPlaceholder)
            .map<ReviewSectionKey>((field) =>
              field.medication_index === 0
                ? 'prescription-date'
                : `medication-${field.medication_index}`,
            ),
        )
        const revokedReviewSections = new Set<ReviewSectionKey>([
          ...placeholderSections,
          ...(previewState?.unreviewedMedicationIndexes ?? []).map(
            (index) => `medication-${index}` as const,
          ),
        ])

        setFields(nextFields)
        setDraftValues(
          Object.fromEntries(
            nextFields.map((field) => [
              field.field_id,
              getSavedDisplayValue(field),
            ]),
          ),
        )
        setEditingSections(placeholderSections)
        setRevokedReviewSections(revokedReviewSections)

        // #809: viewer 로딩 실패는 검수/확정을 막지 않는다. 검수 상태를 먼저
        // 확정한 뒤 별도 경로에서 이미지를 받는다.
        const validatedSourceImage = getValidatedSourceImage(
          ocrResponse.data.source_image,
        )

        if (validatedSourceImage) {
          // 정규화본이 있으면 정규화 이미지만 overlay 대상으로 사용한다.
          // 원본 /file 위에는 절대 bbox를 겹치지 않는다.
          try {
            const normalizedBlob =
              await services.getPrescriptionNormalizedImage(
                validatedSourceImage.url,
              )
            if (!isLatestRequest()) return

            const nextNormalizedUrl = URL.createObjectURL(normalizedBlob)
            if (!isLatestRequest()) {
              URL.revokeObjectURL(nextNormalizedUrl)
              return
            }
            normalizedObjectUrl = nextNormalizedUrl
            setSourceImage(validatedSourceImage)
            setSourceImageUrl(nextNormalizedUrl)
          } catch {
            // fail-closed: 강조 없이 계속 검수한다.
            if (!isLatestRequest()) return
            setSourceImage(null)
            setSourceImageUrl(null)
          }
          return
        }

        // legacy/PDF: 기존 원본 iframe preview를 유지하고 강조는 제공하지 않는다.
        try {
          const documentBlob =
            await services.getPrescriptionDocumentFile(resolvedDocumentId)
          if (!isLatestRequest()) return

          const nextObjectUrl = URL.createObjectURL(documentBlob)
          if (!isLatestRequest()) {
            URL.revokeObjectURL(nextObjectUrl)
            return
          }
          objectUrl = nextObjectUrl
          setDocumentUrl(nextObjectUrl)
        } catch {
          // fail-closed: 원본 미리보기 없이 검수는 계속 가능하다.
          if (!isLatestRequest()) return
          setDocumentUrl(null)
        }
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
      if (normalizedObjectUrl) URL.revokeObjectURL(normalizedObjectUrl)
    }
  }, [
    applyReviewError,
    documentId,
    jobId,
    prefetchedOcrResponse,
    previewState?.manualAddMode,
    previewState?.unreviewedMedicationIndexes,
    previewState?.userConfirmed,
    reviewRequestKey,
    services,
  ])

  const startEditing = (sectionKey: ReviewSectionKey) => {
    if (isManualAddOpen || isAddingMedication) return
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
    clearSourceSelection()
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
      isManualAddOpen ||
      isAddingMedication ||
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
      clearSourceSelection()
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
        const response = await services.updateExtractedField(
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
      clearSourceSelection()
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
      const response = await services.createGuide(prescriptionId)
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
      const response = await services.confirmPrescription(documentId)
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

  const handleManualMedicationAdd = async () => {
    if (
      !jobId ||
      prescription ||
      isAddingMedication ||
      hasUnsavedChanges ||
      editingSections.size > 0 ||
      savingSections.size > 0
    ) return
    const payload: CreateManualMedicationRequest = {
      medication_name: manualAddValues.medication_name.trim(),
      dose_value: manualAddValues.dose_value.trim(),
      frequency_per_day: manualAddValues.frequency_per_day.trim(),
      duration_days: manualAddValues.duration_days.trim(),
      medication_strength: manualAddValues.medication_strength?.trim() || null,
      dose_unit: manualAddValues.dose_unit?.trim() || null,
      timing: manualAddValues.timing?.trim() || null,
    }
    const errors = validateManualMedication(payload)
    setManualAddErrors(errors)
    if (Object.keys(errors).length) return
    const signature = JSON.stringify(payload)
    if (!manualAddKeyRef.current || manualAddKeyRef.current.signature !== signature) {
      manualAddKeyRef.current = {
        signature,
        key: `manual-medication:${globalThis.crypto.randomUUID()}`,
      }
    }
    const requestKey = reviewRequestKey
    const existingMedicationIndexes = new Set(
      fields
        .filter((field) => field.medication_index > 0)
        .map((field) => field.medication_index),
    )
    setIsAddingMedication(true)
    setMessage(null)
    try {
      const response = await services.createManualMedication(
        jobId,
        payload,
        manualAddKeyRef.current.key,
      )
      if (latestReviewRequestKeyRef.current !== requestKey) return

      const nextFields = response.data.fields
      const addedSectionKeys = new Set<ReviewSectionKey>(
        nextFields
          .filter(
            (field) =>
              field.medication_index > 0 &&
              !existingMedicationIndexes.has(field.medication_index),
          )
          .map((field) => `medication-${field.medication_index}` as const),
      )
      setFields(nextFields)
      setDraftValues(
        Object.fromEntries(
          nextFields.map((field) => [
            field.field_id,
            getSavedDisplayValue(field),
          ]),
        ),
      )
      setRevokedReviewSections((current) =>
        new Set([...current, ...addedSectionKeys]),
      )
      setIsManualAddOpen(false)
      setManualAddValues(emptyManualMedication)
      setManualAddErrors({})
      manualAddKeyRef.current = null
      setUserConfirmed(false)
    } catch (error) {
      if (latestReviewRequestKeyRef.current !== requestKey) return

      if (
        error instanceof ApiError &&
        error.code === 'CONCURRENT_UPDATE_IN_PROGRESS'
      ) {
        setMessage({
          title: '약물을 저장하고 있는 요청이 있어요',
          message: '입력한 내용은 그대로 유지했어요.',
          nextAction: '잠시 후 다시 저장해 주세요.',
        })
      } else if (
        error instanceof ApiError &&
        error.code === 'IDEMPOTENCY_KEY_CONFLICT'
      ) {
        manualAddKeyRef.current = null
        setMessage({
          title: '저장 요청을 다시 확인해 주세요',
          message: '이전 요청과 현재 입력 내용이 달라 저장하지 않았어요.',
          nextAction: '입력값을 확인한 뒤 다시 저장해 주세요.',
        })
      } else if (
        error instanceof ApiError &&
        (error.code === 'IDEMPOTENCY_KEY_REQUIRED' ||
          error.code === 'IDEMPOTENCY_KEY_INVALID')
      ) {
        manualAddKeyRef.current = null
        setMessage({
          title: '저장 요청을 준비하지 못했어요',
          message: '입력한 내용은 그대로 유지했어요.',
          nextAction: '다시 저장해 주세요.',
        })
      } else if (
        error instanceof ApiError &&
        error.code === 'VALIDATION_FAILED'
      ) {
        setMessage({
          title: '입력값을 확인해 주세요',
          message: '저장할 수 없는 항목이 있어요.',
          nextAction: '필수값과 숫자 형식을 확인한 뒤 다시 저장해 주세요.',
        })
      } else {
        applyReviewError(
          error,
          '약물을 추가하는 중 오류가 발생했습니다.',
        )
      }
    } finally {
      if (latestReviewRequestKeyRef.current === requestKey) {
        setIsAddingMedication(false)
      }
    }
  }

  // #809: 강조 가능한 필드만 모아 둔다. 이미지 로딩 성공 전에는 강조하지 않는다.
  const highlightableSourceBoxes = useMemo(() => {
    if (!sourceImage || !sourceImageUrl || !isSourceImageLoaded) {
      return new Map<string, { x: number; y: number; width: number; height: number }>()
    }

    const entries = fields.flatMap((field) => {
      const box = getValidatedSourceBox(field.source_location, sourceImage)
      return box ? ([[field.field_id, box]] as const) : []
    })

    return new Map(entries)
  }, [fields, isSourceImageLoaded, sourceImage, sourceImageUrl])

  const activeSourceBox =
    activeSourceFieldId === null
      ? null
      : highlightableSourceBoxes.get(activeSourceFieldId) ?? null

  const handleSourceFieldSelect = useCallback(
    (fieldId: string) => {
      const box = highlightableSourceBoxes.get(fieldId)
      if (!box || !sourceImage) return

      setActiveSourceFieldId(fieldId)
      setIsSourceViewerOpen(true)

      const prefersReducedMotion =
        typeof window !== 'undefined' &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches
      const behavior: ScrollBehavior = prefersReducedMotion ? 'auto' : 'smooth'

      // 1) viewer 자체를 페이지 viewport에 노출한다.
      if (typeof sourceViewerRef.current?.scrollIntoView === 'function') {
        sourceViewerRef.current.scrollIntoView({ behavior, block: 'nearest' })
      }

      // 2) viewer 내부 scroll을 선택된 bbox 위치로 옮긴다.
      //    details가 방금 열린 경우 레이아웃 확정 후 계산해야 한다.
      requestAnimationFrame(() => {
        const viewer = sourceViewerRef.current
        const canvas = sourceCanvasRef.current
        if (!viewer || !canvas) return

        // 실제 렌더된 이미지 영역(canvas) 기준으로 표시 좌표를 환산한다.
        const displayedHeight = canvas.clientHeight || canvas.offsetHeight || 0
        const displayedWidth = canvas.clientWidth || canvas.offsetWidth || 0
        if (displayedHeight <= 0) return

        const boxTop = (box.y / sourceImage.height) * displayedHeight
        const boxHeight = (box.height / sourceImage.height) * displayedHeight
        const boxLeft = (box.x / sourceImage.width) * displayedWidth
        const boxWidth = (box.width / sourceImage.width) * displayedWidth

        // bbox가 viewer 중앙에 오도록 하되 스크롤 가능 범위로 자른다.
        const maxScrollTop = Math.max(0, canvas.scrollHeight - viewer.clientHeight)
        const desiredTop = boxTop + boxHeight / 2 - viewer.clientHeight / 2
        const top = Math.min(Math.max(desiredTop, 0), maxScrollTop)

        const maxScrollLeft = Math.max(0, canvas.scrollWidth - viewer.clientWidth)
        const desiredLeft = boxLeft + boxWidth / 2 - viewer.clientWidth / 2
        const left = Math.min(Math.max(desiredLeft, 0), maxScrollLeft)

        if (typeof viewer.scrollTo === 'function') {
          viewer.scrollTo({ top, left, behavior })
        } else {
          viewer.scrollTop = top
          viewer.scrollLeft = left
        }
      })
    },
    [highlightableSourceBoxes, sourceImage],
  )

  const renderEditField = (field: ExtractedField) => {
    const draftValue = draftValues[field.field_id] ?? ''
    const isSaving = savingFieldIds.has(field.field_id)
    const fieldError = fieldErrors[field.field_id] ??
      getFieldValidationError(field, draftValue)
    const helpText = [
      isRequiredOcrPlaceholder(field) ? manualEntryNotice : null,
      fieldError,
    ].filter(Boolean).join(' ')
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
            aria-label={getFieldLabel(field.field_type)}
            value={draftValue}
            inputMode={inputMode}
            placeholder={
              requiredReviewFieldTypes.has(field.field_type)
                ? '필수 입력'
                : '선택 입력'
            }
            aria-invalid={Boolean(fieldError)}
            data-has-source-location={
              highlightableSourceBoxes.has(field.field_id) ? 'true' : undefined
            }
            disabled={
              isSaving ||
              isAddingMedication ||
              isConfirming ||
              Boolean(prescription)
            }
            onFocus={() => handleSourceFieldSelect(field.field_id)}
            onClick={() => handleSourceFieldSelect(field.field_id)}
            onBlur={clearSourceSelection}
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
          {helpText}
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
    const dateNeedsManualEntry = Boolean(
      prescribedDateField && isRequiredOcrPlaceholder(prescribedDateField),
    )
    const reviewed = Boolean(prescriptionDateReviewed)

    return (
      <section className="prescription-review__prescription-card">
        <div className="prescription-review__prescription-heading">
          <h2>처방 정보</h2>
          {sourceImageUrl && sourceImage ? (
            <details
              className="prescription-review__source"
              open={isSourceViewerOpen}
              onToggle={(event) => {
                setIsSourceViewerOpen(event.currentTarget.open)
              }}
            >
              <summary
                onClick={(event) => {
                  // open을 state로 제어하므로 기본 토글 대신 state를 바꾼다.
                  event.preventDefault()
                  setIsSourceViewerOpen((current) => !current)
                }}
              >
                원본 처방전 보기
              </summary>
              <div
                className="prescription-review__source-viewer"
                ref={sourceViewerRef}
                data-testid="prescription-source-viewer"
                data-scale={sourceScale}
                onPointerDown={handleSourcePointerDown}
                onPointerMove={handleSourcePointerMove}
                onPointerUp={handleSourcePointerEnd}
                onPointerCancel={handleSourcePointerEnd}
              >
                <div
                  className="prescription-review__source-canvas"
                  data-testid="prescription-source-canvas"
                  ref={sourceCanvasRef}
                  style={{
                    transform: `translate(${sourcePan.x}px, ${sourcePan.y}px) scale(${sourceScale})`,
                    transformOrigin: '0 0',
                  }}
                >
                  <img
                    className="prescription-review__source-image"
                    src={sourceImageUrl}
                    alt="원본 처방전"
                    title="원본 처방전"
                    onLoad={() => setIsSourceImageLoaded(true)}
                    onError={() => {
                      // fail-closed: 이미지 로딩 실패 시 강조하지 않는다.
                      setIsSourceImageLoaded(false)
                      setActiveSourceFieldId(null)
                    }}
                  />
                  {activeSourceBox ? (
                    <span
                      className="prescription-review__source-highlight"
                      data-testid="prescription-source-highlight"
                      aria-hidden="true"
                      style={{
                        left: `${(activeSourceBox.x / sourceImage.width) * 100}%`,
                        top: `${(activeSourceBox.y / sourceImage.height) * 100}%`,
                        width: `${(activeSourceBox.width / sourceImage.width) * 100}%`,
                        height: `${(activeSourceBox.height / sourceImage.height) * 100}%`,
                      }}
                    />
                  ) : null}
                </div>
              </div>
            </details>
          ) : documentUrl ? (
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
              {dateError && (
                <small role="alert">
                  {dateNeedsManualEntry ? manualEntryNotice : dateError}
                </small>
              )}
            </div>
            <div className="prescription-review__section-actions">
              <Button
                variant="secondary"
                disabled={
                  !prescribedDateField ||
                  isManualAddOpen ||
                  isAddingMedication
                }
                onClick={() => startEditing(prescriptionSectionKey)}
              >
                수정
              </Button>
              {!reviewed && (
                <Button
                  disabled={
                    !prescribedDateField ||
                    Boolean(dateError) ||
                    isSaving ||
                    isManualAddOpen ||
                    isAddingMedication
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
      'DOSE_UNIT',
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
              {group.fields.map(renderEditField)}
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
                const field = group.fields.find(
                  (candidate) => candidate.field_type === fieldType,
                )
                const value = getValue(fieldType)
                const isRequiredMissing =
                  requiredReviewFieldTypes.has(fieldType) && !value
                const needsManualEntry = Boolean(
                  field && isRequiredOcrPlaceholder(field),
                )
                return (
                  <div className={isRequiredMissing ? 'is-error' : ''} key={fieldType}>
                    <dt>{getFieldLabel(fieldType)}</dt>
                    <dd>{formatFieldValue(fieldType, value)}</dd>
                    {isRequiredMissing && (
                      <small>
                        {needsManualEntry
                          ? manualEntryNotice
                          : `${getFieldLabel(fieldType)}을(를) 입력해 주세요.`}
                      </small>
                    )}
                  </div>
                )
              })}
            </dl>
            <div className="prescription-review__section-actions">
              <Button
                variant="secondary"
                disabled={isManualAddOpen || isAddingMedication}
                onClick={() => startEditing(sectionKey)}
              >
                수정하기
              </Button>
              {!reviewed && (
                <Button
                  disabled={
                    hasValidationError ||
                    isSaving ||
                    isManualAddOpen ||
                    isAddingMedication
                  }
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
            <div
              className={`prescription-review__status-icon ${
                hasRequiredRecognitionIssue ? 'is-warning' : 'is-success'
              }`}
              aria-hidden="true"
            >
              {hasRequiredRecognitionIssue ? '!' : '✓'}
            </div>
            <div>
              <p>
                {hasStructurallyMissingRequiredFields
                  ? '필수 처방 항목 인식 누락'
                  : hasRequiredOcrPlaceholders
                    ? '일부 필수 항목 인식 누락'
                    : '전체 인식 성공'}
              </p>
              <h1>
                {hasStructurallyMissingRequiredFields
                  ? '처방전을 다시 업로드해 주세요'
                  : hasRequiredOcrPlaceholders
                    ? '누락된 항목을 직접 입력해 주세요'
                    : '처방전과 같은지 확인해 주세요'}
              </h1>
            </div>
          </section>

          <div className="prescription-review__notice">
            <strong>도지는 처방 내용을 바꾸지 않아요.</strong>
            <span>원본 처방전과 인식된 내용을 직접 비교해 주세요.</span>
          </div>

          {hasStructurallyMissingRequiredFields && (
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
            className="prescription-review__manual-add"
            aria-label="약물 추가"
          >
              {!isManualAddOpen ? (
                <Button
                  variant="secondary"
                  disabled={
                    isConfirming ||
                    editingSections.size > 0 ||
                    hasUnsavedChanges ||
                    savingSections.size > 0 ||
                    Boolean(prescription)
                  }
                  onClick={() => {
                    setIsManualAddOpen(true)
                    setMessage(null)
                    setUserConfirmed(false)
                  }}
                >
                  약물 추가
                </Button>
              ) : (
                <Card className="prescription-review__manual-add-card">
                  <div className="prescription-review__manual-add-heading">
                    <div>
                      <h2>약물 추가</h2>
                      <p>처방전에서 누락된 약물을 직접 입력해 주세요.</p>
                    </div>
                    <span><strong>*</strong> 필수 입력</span>
                  </div>
                  <div className="prescription-review__edit-grid">
                    {([
                      ['medication_name', '약물이름', true], ['medication_strength', '제품함량', false],
                      ['dose_value', '1회 복용량', true], ['dose_unit', '복용단위', false],
                      ['frequency_per_day', '하루횟수', true], ['timing', '복용조건', false], ['duration_days', '투약일수', true],
                    ] as const).map(([key, label, required]) => (
                      <label
                        className={`prescription-review__edit-field ${
                          key === 'medication_name' || key === 'duration_days'
                            ? 'prescription-review__edit-field--wide'
                            : ''
                        }`}
                        htmlFor={`manual-${key}`}
                        key={key}
                      >
                        <span>
                          {label}
                          <em>{required ? '필수' : '선택'}</em>
                        </span>
                        <input
                          id={`manual-${key}`}
                          aria-label={label}
                          placeholder={required ? '필수 입력' : '선택 입력'}
                          inputMode={
                            key === 'dose_value'
                              ? 'decimal'
                              : key === 'frequency_per_day' ||
                                  key === 'duration_days'
                                ? 'numeric'
                                : undefined
                          }
                          value={manualAddValues[key] ?? ''}
                          aria-invalid={Boolean(manualAddErrors[key])}
                          aria-describedby={`manual-error-${key}`}
                          disabled={isAddingMedication}
                          onChange={(event) => {
                            setManualAddValues((current) => ({
                              ...current,
                              [key]: event.target.value,
                            }))
                            setManualAddErrors((current) => {
                              const next = { ...current }
                              delete next[key]
                              return next
                            })
                            setUserConfirmed(false)
                          }}
                        />
                        {manualAddErrors[key] && (
                          <small
                            className="is-error"
                            id={`manual-error-${key}`}
                            role="alert"
                          >
                            {manualAddErrors[key]}
                          </small>
                        )}
                      </label>
                    ))}
                  </div>
                  <div className="prescription-review__section-actions">
                    <Button
                      variant="secondary"
                      disabled={isAddingMedication}
                      onClick={() => {
                        setIsManualAddOpen(false)
                        setManualAddValues(emptyManualMedication)
                        setManualAddErrors({})
                        manualAddKeyRef.current = null
                      }}
                    >
                      취소
                    </Button>
                    <Button
                      disabled={isAddingMedication}
                      onClick={handleManualMedicationAdd}
                    >
                      {isAddingMedication ? '저장 중...' : '약물 저장'}
                    </Button>
                  </div>
                </Card>
              )}
          </section>

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
