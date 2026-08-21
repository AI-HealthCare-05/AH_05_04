import { useEffect, useMemo, useState } from 'react'
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

function getFieldLabel(fieldType: string) {
  return fieldLabels[fieldType] ?? fieldType
}

function getSavedDisplayValue(field: ExtractedField) {
  return field.confirmed_value ?? field.raw_value ?? ''
}

function PrescriptionReviewPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const documentId = searchParams.get('document_id')
  const jobId = searchParams.get('job_id')

  const [fields, setFields] = useState<ExtractedField[]>([])
  const [draftValues, setDraftValues] = useState<Record<string, string>>({})
  const [documentUrl, setDocumentUrl] = useState<string | null>(null)
  const [prescription, setPrescription] =
    useState<PrescriptionResponse | null>(null)
  const [message, setMessage] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [savingFieldId, setSavingFieldId] = useState<string | null>(null)
  const [isConfirming, setIsConfirming] = useState(false)
  const [userConfirmed, setUserConfirmed] = useState(false)

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

  const isFieldConfirmed = (field: ExtractedField) => {
    const draftValue = draftValues[field.field_id]?.trim() ?? ''
    return (
      Boolean(field.confirmed_value?.trim()) &&
      draftValue === field.confirmed_value?.trim()
    )
  }

  const confirmedFieldCount = useMemo(
    () =>
      fields.filter((field) => {
        const draftValue = draftValues[field.field_id]?.trim() ?? ''
        return (
          Boolean(field.confirmed_value?.trim()) &&
          draftValue === field.confirmed_value?.trim()
        )
      }).length,
    [draftValues, fields],
  )

  const canConfirmPrescription = useMemo(() => {
    const prescribedDateConfirmed = fields.some(
      (field) =>
        field.medication_index === 0 &&
        field.field_type === 'PRESCRIBED_DATE' &&
        Boolean(field.confirmed_value?.trim()),
    )
    const medicationNameConfirmed = fields.some(
      (field) =>
        field.medication_index > 0 &&
        field.field_type === 'MEDICATION_NAME' &&
        Boolean(field.confirmed_value?.trim()),
    )

    return (
      prescribedDateConfirmed &&
      medicationNameConfirmed &&
      !hasUnsavedChanges &&
      userConfirmed
    )
  }, [fields, hasUnsavedChanges, userConfirmed])

  useEffect(() => {
    if (!documentId || !jobId) {
      setMessage('처방전 검수에 필요한 정보가 없습니다.')
      setIsLoading(false)
      return
    }

    let objectUrl: string | null = null

    async function loadReviewData() {
      try {
        setIsLoading(true)
        setMessage('')

        const [ocrResponse, documentBlob] = await Promise.all([
          getOcrJob(jobId as string),
          getPrescriptionDocumentFile(documentId as string),
        ])

        setFields(ocrResponse.data.fields)
        setDraftValues(
          Object.fromEntries(
            ocrResponse.data.fields.map((field) => [
              field.field_id,
              getSavedDisplayValue(field),
            ]),
          ),
        )

        objectUrl = URL.createObjectURL(documentBlob)
        setDocumentUrl(objectUrl)
      } catch (error) {
        setMessage(
          error instanceof ApiError
            ? error.message
            : '처방전 검수 정보를 불러오는 중 오류가 발생했습니다.',
        )
      } finally {
        setIsLoading(false)
      }
    }

    void loadReviewData()

    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [documentId, jobId])

  const handleSaveField = async (field: ExtractedField) => {
    const value = draftValues[field.field_id]?.trim()

    if (!value) {
      setMessage('확인할 값을 입력해 주세요.')
      return
    }

    try {
      setSavingFieldId(field.field_id)
      setMessage('')
      const response = await updateExtractedField(field.field_id, value)

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
      setMessage(
        error instanceof ApiError
          ? error.message
          : '필드 저장 중 오류가 발생했습니다.',
      )
    } finally {
      setSavingFieldId(null)
    }
  }

  const handleConfirmPrescription = async () => {
    if (!documentId || !canConfirmPrescription) return

    try {
      setIsConfirming(true)
      setMessage('')
      setPrescription(await confirmPrescription(documentId))
    } catch (error) {
      setMessage(
        error instanceof ApiError
          ? error.message
          : '처방 확정 중 오류가 발생했습니다.',
      )
    } finally {
      setIsConfirming(false)
    }
  }

  const renderField = (field: ExtractedField) => {
    const draftValue = draftValues[field.field_id] ?? ''
    const confirmed = isFieldConfirmed(field)
    const isSaving = savingFieldId === field.field_id
    const rawValue = field.raw_value?.trim() ?? ''

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
            onChange={(event) => {
              setDraftValues((current) => ({
                ...current,
                [field.field_id]: event.target.value,
              }))
              setUserConfirmed(false)
            }}
            aria-describedby={`field-help-${field.field_id}`}
          />
          <Button
            variant={confirmed ? 'secondary' : 'primary'}
            disabled={isSaving}
            onClick={() => handleSaveField(field)}
          >
            {isSaving ? '저장 중' : confirmed ? '수정 저장' : '확인'}
          </Button>
        </div>

        <p id={`field-help-${field.field_id}`}>
          {confirmed
            ? '사용자가 확인하고 저장한 값입니다.'
            : rawValue
              ? `OCR 인식값: ${rawValue}`
              : '인식된 값이 없습니다. 원본을 보고 직접 입력해 주세요.'}
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

          {message && (
            <div className="prescription-review__error" role="alert">
              <strong>확인이 필요해요</strong>
              <span>{message}</span>
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
                    prescriptionFields.every(isFieldConfirmed)
                      ? 'neutral'
                      : 'attention'
                  }
                >
                  {prescriptionFields.every(isFieldConfirmed)
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
            const allConfirmed = group.fields.every(isFieldConfirmed)
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
                  disabled={hasUnsavedChanges}
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
                disabled={!canConfirmPrescription || isConfirming}
                onClick={handleConfirmPrescription}
              >
                {isConfirming ? '확정 중...' : '확정하고 가이드 만들기'}
              </Button>

              <p className="prescription-review__progress">
                {confirmedFieldCount}/{fields.length}개 항목 저장 완료
              </p>
            </>
          )}

          {prescription && (
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
          )}
        </main>
      </MobileShell>
    </div>
  )
}

export default PrescriptionReviewPage
