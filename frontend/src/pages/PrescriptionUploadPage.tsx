import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  executeOcr,
  getOcrJob,
  uploadPrescription,
  type OcrJobResponse,
} from '../api/prescriptions'
import { ApiError } from '../api/client'
import AiJobStatusState from '../components/AiJobStatusState'
import { Button, Card, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import { adaptOcrJobStatus } from '../features/ai-jobs/ocrJobAdapter'
import {
  getJobFailurePresentation,
  getJobRequestErrorPresentation,
  getJobStatusPresentation,
  getPollingTimeoutPresentation,
  type AiJobPresentation,
  type AiJobViewStatus,
} from '../features/ai-jobs/jobState'
import { useJobPolling } from '../features/ai-jobs/useJobPolling'
import '../design-system/prototype.css'
import './MvpPages.css'

const OCR_POLL_INTERVAL_MS = 1000
const OCR_POLL_MAX_ATTEMPTS = 80

type OcrPollingTarget = {
  documentId: string
  jobId: string
}

function getOcrResponseStatus(response: OcrJobResponse): AiJobViewStatus {
  return adaptOcrJobStatus(response.data.ocr_status)
}

function PrescriptionUploadPage() {
  const navigate = useNavigate()
  const inputId = useId()
  const [file, setFile] = useState<File | null>(null)
  const [pollingTarget, setPollingTarget] = useState<OcrPollingTarget | null>(null)
  const [message, setMessage] = useState('')
  const [isPreparing, setIsPreparing] = useState(false)
  const preparationRequestRef = useRef(0)

  const fetchOcrJob = useCallback(
    (jobId: string, signal: AbortSignal) => getOcrJob(jobId, signal),
    [],
  )
  const pollingState = useJobPolling<OcrJobResponse>({
    jobKey: pollingTarget?.jobId ?? null,
    fetcher: fetchOcrJob,
    getStatus: getOcrResponseStatus,
    intervalMs: OCR_POLL_INTERVAL_MS,
    maxAttempts: OCR_POLL_MAX_ATTEMPTS,
  })

  useEffect(
    () => () => {
      preparationRequestRef.current += 1
    },
    [],
  )

  useEffect(() => {
    if (
      pollingState.status !== 'COMPLETED' ||
      !pollingTarget ||
      pollingState.jobKey !== pollingTarget.jobId
    ) return

    navigate(
      `/prescriptions/review?document_id=${pollingTarget.documentId}&job_id=${pollingTarget.jobId}`,
    )
  }, [navigate, pollingState.jobKey, pollingState.status, pollingTarget])

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = event.target.files?.[0] ?? null
    setFile(selectedFile)
    setPollingTarget(null)
    setMessage('')
  }

  const handleUpload = async () => {
    if (!file) {
      setMessage('처방전 파일을 선택해 주세요.')
      return
    }

    const requestId = ++preparationRequestRef.current
    const isCurrentRequest = () => preparationRequestRef.current === requestId

    try {
      setIsPreparing(true)
      setMessage('')
      setPollingTarget(null)
      const uploadResponse = await uploadPrescription(file)
      if (!isCurrentRequest()) return
      const ocrResponse = await executeOcr(uploadResponse.data.document_id)
      if (!isCurrentRequest()) return

      setPollingTarget({
        documentId: uploadResponse.data.document_id,
        jobId: ocrResponse.data.job_id,
      })
    } catch (error) {
      if (!isCurrentRequest()) return
      setMessage(
        error instanceof ApiError
          ? error.message
          : '처방전 처리 중 오류가 발생했습니다.',
      )
    } finally {
      if (isCurrentRequest()) {
        setIsPreparing(false)
      }
    }
  }

  const resetToUpload = () => {
    setPollingTarget(null)
    setFile(null)
    setMessage('')
  }

  if (isPreparing || pollingTarget) {
    const isCurrentPollingTarget = pollingState.jobKey === pollingTarget?.jobId
    let status: Exclude<AiJobViewStatus, 'COMPLETED'> | 'REQUEST_ERROR' | 'POLL_TIMEOUT' = 'PENDING'
    let presentation: AiJobPresentation = getJobStatusPresentation('PENDING')
    let onAction: (() => void) | undefined

    if (isCurrentPollingTarget && pollingState.phase === 'ERROR') {
      status = 'REQUEST_ERROR'
      presentation = getJobRequestErrorPresentation(pollingState.error)
      onAction =
        pollingState.error instanceof ApiError && pollingState.error.status === 401
          ? () => navigate('/login')
          : resetToUpload
    } else if (isCurrentPollingTarget && pollingState.phase === 'TIMED_OUT') {
      status = 'POLL_TIMEOUT'
      presentation = getPollingTimeoutPresentation()
      onAction = resetToUpload
    } else if (isCurrentPollingTarget && pollingState.status === 'FAILED') {
      status = 'FAILED'
      presentation = getJobFailurePresentation(pollingState.data?.data.error_code)
      onAction = resetToUpload
    } else if (
      isCurrentPollingTarget &&
      pollingState.status &&
      pollingState.status !== 'FAILED' &&
      pollingState.status !== 'COMPLETED'
    ) {
      status = pollingState.status
      presentation = getJobStatusPresentation(pollingState.status)
    }

    return (
      <div className="mvp-page mvp-ai-job-page">
        <MobileShell
          title="Dosey 도지"
          onBack={resetToUpload}
          brandMark={<DoseyMascot variant="header" />}
          backPlacement="content"
          hideNavigation
        >
          <main className="app-scroll mvp-page__content mvp-page__content--no-nav ai-job-page__content">
            <AiJobStatusState
              status={status}
              presentation={presentation}
              onAction={onAction}
            />
          </main>
        </MobileShell>
      </div>
    )
  }

  return (
    <div className="mvp-page mvp-upload-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate('/')}
        brandMark={<DoseyMascot variant="header" />}
        backPlacement="content"
        hideNavigation
      >
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-upload__content">
          <h1 className="mvp-page__title">처방전을 등록해 주세요</h1>
          <p className="mvp-page__description">
            촬영하거나 저장한 처방전을 읽은 뒤 원본과 인식 결과를 직접 비교합니다.
          </p>

          <Card className="mvp-upload__summary">
            <span>
              <strong>처방전</strong>
              <small>OCR 인식 · 복약 가이드 연결</small>
            </span>
          </Card>

          <input
            id={inputId}
            className="mvp-upload__input"
            type="file"
            accept="image/jpeg,image/png,application/pdf"
            onChange={handleFileChange}
          />
          <label className={`upload-zone mvp-upload__zone ${file ? 'selected' : ''}`} htmlFor={inputId}>
            <span className="mvp-upload__zone-icon" aria-hidden="true">
              <span />
            </span>
            <strong>{file?.name ?? '사진 촬영 또는 파일 선택'}</strong>
            <small>{file ? '선택 완료 · 눌러서 변경' : 'JPG · PNG · PDF / 최대 10MB'}</small>
          </label>

          <div className="notice mvp-upload__notice">
            <strong>개인정보를 확인해 주세요.</strong><br />
            주민등록번호는 가리고 문서 전체가 선명하게 보이도록 촬영해 주세요.
          </div>

          {message && <p className="mvp-form__message" role="alert">{message}</p>}

          <Button fullWidth disabled={!file} onClick={handleUpload}>
            처방전 읽기
          </Button>
        </main>
      </MobileShell>
    </div>
  )
}

export default PrescriptionUploadPage
