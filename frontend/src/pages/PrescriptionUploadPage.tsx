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

function getUploadFailureMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) return '로그인 정보를 다시 확인한 뒤 시도해 주세요.'
    if (error.status >= 500) return '서버 응답이 원활하지 않아요. 잠시 후 다시 시도해 주세요.'
  }

  if (error instanceof TypeError) {
    return '네트워크 연결을 확인한 뒤 다시 시도해 주세요.'
  }

  return '선택한 처방전을 처리하지 못했어요. 다시 시도해 주세요.'
}

type UploadSource = 'camera' | 'file'

function UploadMethodIcon({ source }: { source: UploadSource }) {
  if (source === 'camera') {
    return (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M5 7.5h3l1.3-2h5.4l1.3 2h3a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9.5a2 2 0 0 1 2-2Z" />
        <circle cx="12" cy="13.5" r="3.5" />
      </svg>
    )
  }

  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <circle cx="8" cy="9" r="1.5" />
      <path d="m5.5 17 4-4 3 3 2.5-2.5 3.5 3.5" />
    </svg>
  )
}

function PrescriptionUploadPage() {
  const navigate = useNavigate()
  const inputId = useId()
  const filenameId = useId()
  const contractId = useId()
  const [file, setFile] = useState<File | null>(null)
  const [uploadSource, setUploadSource] = useState<UploadSource | null>(null)
  const [isFilenameExpanded, setIsFilenameExpanded] = useState(false)
  const [pollingTarget, setPollingTarget] = useState<OcrPollingTarget | null>(null)
  const [message, setMessage] = useState('')
  const [hasUploadFailed, setHasUploadFailed] = useState(false)
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

  const handleFileChange = (
    source: UploadSource,
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const selectedFile = event.target.files?.[0] ?? null
    if (!selectedFile) return

    setFile(selectedFile)
    setUploadSource(source)
    setIsFilenameExpanded(false)
    setPollingTarget(null)
    setMessage('')
    setHasUploadFailed(false)
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
      setMessage(getUploadFailureMessage(error))
      setHasUploadFailed(true)
    } finally {
      if (isCurrentRequest()) {
        setIsPreparing(false)
      }
    }
  }

  const resetToUpload = () => {
    setPollingTarget(null)
    setFile(null)
    setUploadSource(null)
    setIsFilenameExpanded(false)
    setMessage('')
    setHasUploadFailed(false)
  }

  if (hasUploadFailed) {
    return (
      <div className="mvp-page mvp-upload-page">
        <MobileShell
          title="Dosey 도지"
          onBack={resetToUpload}
          brandMark={<DoseyMascot variant="header" />}
          backPlacement="content"
          hideNavigation
        >
          <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-upload__failure">
            <span className="mvp-upload__failure-icon" aria-hidden="true" />
            <h1>
              {uploadSource === 'camera'
                ? '처방전을 촬영하지 못했어요'
                : '처방전을 등록하지 못했어요'}
            </h1>
            <p role="alert">{message}</p>
            <Button fullWidth onClick={resetToUpload}>
              다시 선택하기
            </Button>
            <Button fullWidth variant="secondary" onClick={() => navigate('/')}>
              홈으로 돌아가기
            </Button>
          </main>
        </MobileShell>
      </div>
    )
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
          <p className="mvp-page__description">등록 방법을 선택해 주세요.</p>

          <Card className="mvp-upload__summary">
            <span>
              <strong>처방전</strong>
              <small>OCR 인식 · 복약 가이드 연결</small>
            </span>
          </Card>

          <div className="mvp-upload__methods" role="group" aria-label="처방전 등록 방법">
            <input
              id={`${inputId}-camera`}
              className="mvp-upload__input"
              type="file"
              accept="image/jpeg,image/png"
              capture="environment"
              aria-describedby={contractId}
              onChange={(event) => handleFileChange('camera', event)}
            />
            <label
              className={`mvp-upload__method ${uploadSource === 'camera' ? 'selected' : ''}`}
              htmlFor={`${inputId}-camera`}
              onClick={() => setUploadSource('camera')}
            >
              <span className="mvp-upload__method-icon" aria-hidden="true">
                <UploadMethodIcon source="camera" />
              </span>
              <span>
                <strong>카메라로 촬영하기</strong>
                <small>지금 처방전을 직접 촬영해요</small>
              </span>
              <span className="mvp-upload__radio" aria-hidden="true" />
            </label>

            <input
              id={`${inputId}-file`}
              className="mvp-upload__input"
              type="file"
              accept="image/jpeg,image/png,application/pdf"
              aria-describedby={contractId}
              onChange={(event) => handleFileChange('file', event)}
            />
            <label
              className={`mvp-upload__method ${uploadSource === 'file' ? 'selected' : ''}`}
              htmlFor={`${inputId}-file`}
              onClick={() => setUploadSource('file')}
            >
              <span className="mvp-upload__method-icon" aria-hidden="true">
                <UploadMethodIcon source="file" />
              </span>
              <span>
                <strong>저장된 처방전 선택하기</strong>
                <small>사진이나 파일로 저장된 처방전을 불러와요</small>
              </span>
              <span className="mvp-upload__radio" aria-hidden="true" />
            </label>
          </div>

          <p id={contractId} className="mvp-upload__contract">
            지원 파일: JPG · JPEG · PNG · PDF / 최대 30MB
          </p>

          {file && (
            <div className="mvp-upload__selection">
              <strong
                id={filenameId}
                className={`mvp-upload__filename${isFilenameExpanded ? ' mvp-upload__filename--expanded' : ''}`}
              >
                {file.name}
              </strong>
              <button
                type="button"
                className="mvp-upload__filename-toggle"
                aria-expanded={isFilenameExpanded}
                aria-controls={filenameId}
                onClick={() => setIsFilenameExpanded((current) => !current)}
              >
                {isFilenameExpanded ? '파일명 접기' : '전체 파일명 보기'}
              </button>
            </div>
          )}

          <div className="mvp-upload__tip">
            <strong>처방전 촬영 팁</strong>
            <p>정방향으로 놓고, 밝은 곳에서 기울어지지 않게 촬영해 주세요.</p>
          </div>

          <div className="notice mvp-upload__notice">
            <strong>개인정보를 확인해 주세요</strong><br />
            주민등록번호 등 민감 정보는 가리고 촬영해 주세요.
          </div>

          {message && <p className="mvp-form__message" role="alert">{message}</p>}

          {file && (
            <Button fullWidth onClick={handleUpload}>
              처방전 읽기
            </Button>
          )}
        </main>
      </MobileShell>
    </div>
  )
}

export default PrescriptionUploadPage
