import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  executeOcr,
  getJobStatus,
  getOcrJob,
  getOcrResult,
  isJobStatusResponse,
  uploadPrescription,
  type JobStatusResponse,
  type OcrJobResponse,
} from '../api/prescriptions'
import { ApiError } from '../api/client'
import AiJobStatusState from '../components/AiJobStatusState'
import { Button, Card, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import { adaptOcrJobStatus } from '../features/ai-jobs/ocrJobAdapter'
import {
  clearOcrJobRecovery,
  loadOcrJobRecovery,
  saveOcrJobRecovery,
  type OcrJobRecoveryTarget,
} from '../features/ai-jobs/ocrJobRecovery'
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
// Frontend의 기술적 무한-polling 방지용이며 Backend Job timeout을 정의하지 않습니다.
const OCR_POLL_MAX_ATTEMPTS = 80

type OcrPollingTarget = OcrJobRecoveryTarget

type OcrPollingSnapshot =
  | {
      kind: 'ASYNC'
      body: JobStatusResponse
      retryAfterSeconds: number | null
    }
  | {
      kind: 'LEGACY'
      body: OcrJobResponse
      retryAfterSeconds: null
    }

type OcrIntakeIntent = {
  file: File
  documentId: string | null
  idempotencyKey: string
}

type OcrCompletionError = {
  error: unknown
  canRetry: boolean
}

function createOcrIdempotencyKey(): string {
  // 키 원문은 현재 화면의 OCR intent lifecycle 안에서만 유지합니다.
  return `ocr:${globalThis.crypto.randomUUID()}`
}

function getOcrResponseStatus(response: OcrPollingSnapshot): AiJobViewStatus {
  return response.kind === 'ASYNC'
    ? response.body.data.status
    : adaptOcrJobStatus(response.body.data.ocr_status)
}

function getOcrRetryDelayMs(
  response: OcrPollingSnapshot,
  status: AiJobViewStatus,
): number | null {
  if (status !== 'RETRY_WAIT' || response.kind !== 'ASYNC') return null

  const retryAfterSeconds =
    response.body.data.retry_after_seconds ?? response.retryAfterSeconds

  return retryAfterSeconds === null
    ? null
    : Math.max(retryAfterSeconds, 1) * 1000
}

function canRetryResultRequest(error: unknown): boolean {
  return error instanceof TypeError ||
    (error instanceof ApiError && error.status >= 500)
}

function PrescriptionUploadPage() {
  const navigate = useNavigate()
  const inputId = useId()
  const filenameId = useId()
  const [file, setFile] = useState<File | null>(null)
  const [isFilenameExpanded, setIsFilenameExpanded] = useState(false)
  const [pollingTarget, setPollingTarget] =
    useState<OcrPollingTarget | null>(loadOcrJobRecovery)
  const [message, setMessage] = useState('')
  const [isPreparing, setIsPreparing] = useState(false)
  const [completionError, setCompletionError] =
    useState<OcrCompletionError | null>(null)
  const [pollingRestartKey, setPollingRestartKey] = useState(0)
  const [completionRestartKey, setCompletionRestartKey] = useState(0)
  const preparationRequestRef = useRef(0)
  const preparationControllerRef = useRef<AbortController | null>(null)
  const intakeIntentRef = useRef<OcrIntakeIntent | null>(null)

  const fetchOcrJob = useCallback(async (
    pollingKey: string,
    signal: AbortSignal,
  ): Promise<OcrPollingSnapshot> => {
    if (!pollingTarget || pollingTarget.pollingKey !== pollingKey) {
      throw new Error('OCR polling target changed')
    }

    if (pollingTarget.kind === 'ASYNC') {
      const response = await getJobStatus(pollingKey, signal)
      return { kind: 'ASYNC', ...response }
    }

    return {
      kind: 'LEGACY',
      body: await getOcrJob(pollingTarget.jobId, signal),
      retryAfterSeconds: null,
    }
  }, [pollingTarget])
  const pollingState = useJobPolling<OcrPollingSnapshot>({
    jobKey: pollingTarget?.pollingKey ?? null,
    fetcher: fetchOcrJob,
    getStatus: getOcrResponseStatus,
    getDelayMs: getOcrRetryDelayMs,
    intervalMs: OCR_POLL_INTERVAL_MS,
    maxAttempts: OCR_POLL_MAX_ATTEMPTS,
    restartKey: pollingRestartKey,
  })

  useEffect(
    () => () => {
      preparationRequestRef.current += 1
      preparationControllerRef.current?.abort()
    },
    [],
  )

  useEffect(() => {
    if (
      pollingState.status !== 'COMPLETED' ||
      !pollingTarget ||
      !pollingState.data ||
      pollingState.jobKey !== pollingTarget.pollingKey
    ) return

    const snapshot = pollingState.data
    const controller = new AbortController()
    let isActive = true

    const openReview = (ocrResponse: OcrJobResponse) => {
      if (!isActive) return

      if (
        ocrResponse.data.document_id !== pollingTarget.documentId ||
        ocrResponse.data.ocr_status !== 'COMPLETED'
      ) {
        clearOcrJobRecovery()
        setCompletionError({
          error: new Error('OCR result is not reviewable'),
          canRetry: false,
        })
        return
      }

      clearOcrJobRecovery()
      navigate(
        `/prescriptions/review?document_id=${pollingTarget.documentId}&job_id=${ocrResponse.data.job_id}`,
        { state: { ocrResponse } },
      )
    }

    if (snapshot.kind === 'LEGACY') {
      openReview(snapshot.body)
      return () => {
        isActive = false
      }
    }

    const resultUrl = snapshot.body.data.result_url
    if (!resultUrl) {
      clearOcrJobRecovery()
      setCompletionError({
        error: new Error('Completed OCR Job has no result URL'),
        canRetry: false,
      })
      return () => {
        isActive = false
      }
    }

    void getOcrResult(resultUrl, controller.signal)
      .then(openReview)
      .catch((error: unknown) => {
        if (!isActive || controller.signal.aborted) return
        setCompletionError({
          error,
          canRetry: canRetryResultRequest(error),
        })
      })

    return () => {
      isActive = false
      controller.abort()
    }
  }, [
    completionRestartKey,
    navigate,
    pollingState.data,
    pollingState.jobKey,
    pollingState.status,
    pollingTarget,
  ])

  useEffect(() => {
    if (
      pollingState.jobKey === pollingTarget?.pollingKey &&
      (pollingState.status === 'FAILED' || pollingState.status === 'STALE')
    ) {
      clearOcrJobRecovery()
    }
  }, [pollingState.jobKey, pollingState.status, pollingTarget])

  const expireOcrSession = useCallback(() => {
    clearOcrJobRecovery()
    localStorage.removeItem('access_token')
    navigate('/login', { replace: true })
  }, [navigate])

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = event.target.files?.[0] ?? null
    setFile(selectedFile)
    setIsFilenameExpanded(false)
    setPollingTarget(null)
    setCompletionError(null)
    setMessage('')
    clearOcrJobRecovery()
    intakeIntentRef.current = selectedFile
      ? {
          file: selectedFile,
          documentId: null,
          idempotencyKey: createOcrIdempotencyKey(),
        }
      : null
  }

  const handleUpload = async () => {
    if (!file) {
      setMessage('처방전 파일을 선택해 주세요.')
      return
    }

    const requestId = ++preparationRequestRef.current
    preparationControllerRef.current?.abort()
    const preparationController = new AbortController()
    preparationControllerRef.current = preparationController
    const isCurrentRequest = () => preparationRequestRef.current === requestId

    try {
      setIsPreparing(true)
      setMessage('')
      setPollingTarget(null)
      setCompletionError(null)

      let intent = intakeIntentRef.current
      if (!intent || intent.file !== file) {
        intent = {
          file,
          documentId: null,
          idempotencyKey: createOcrIdempotencyKey(),
        }
        intakeIntentRef.current = intent
      }

      let documentId = intent.documentId
      if (!documentId) {
        const uploadResponse = await uploadPrescription(file)
        if (!isCurrentRequest()) return
        documentId = uploadResponse.data.document_id
        intent.documentId = documentId
      }

      const ocrResponse = await executeOcr(
        documentId,
        intent.idempotencyKey,
        preparationController.signal,
      )
      if (!isCurrentRequest()) return

      if (
        isJobStatusResponse(ocrResponse) &&
        !ocrResponse.data.status_url.trim()
      ) {
        throw new Error('OCR Job status URL is missing')
      }

      const nextPollingTarget: OcrPollingTarget = isJobStatusResponse(ocrResponse)
        ? {
            kind: 'ASYNC',
            documentId,
            jobId: ocrResponse.data.job_id,
            pollingKey: ocrResponse.data.status_url,
          }
        : {
            kind: 'LEGACY',
            documentId,
            jobId: ocrResponse.data.job_id,
            pollingKey: `legacy:${ocrResponse.data.job_id}`,
          }
      saveOcrJobRecovery(nextPollingTarget)
      setPollingTarget(nextPollingTarget)
    } catch (error) {
      if (!isCurrentRequest()) return
      if (error instanceof ApiError && error.status === 401) {
        expireOcrSession()
        return
      }
      setMessage(getJobRequestErrorPresentation(error).description)
    } finally {
      if (isCurrentRequest()) {
        setIsPreparing(false)
        preparationControllerRef.current = null
      }
    }
  }

  const resetToUpload = () => {
    preparationRequestRef.current += 1
    preparationControllerRef.current?.abort()
    preparationControllerRef.current = null
    setIsPreparing(false)
    setPollingTarget(null)
    setFile(null)
    setIsFilenameExpanded(false)
    setCompletionError(null)
    setMessage('')
    intakeIntentRef.current = null
    clearOcrJobRecovery()
  }

  const resumePolling = () => {
    setCompletionError(null)
    setPollingRestartKey((current) => current + 1)
  }

  const retryCompletedResult = () => {
    setCompletionError(null)
    setCompletionRestartKey((current) => current + 1)
  }

  if (isPreparing || pollingTarget) {
    const isCurrentPollingTarget =
      pollingState.jobKey === pollingTarget?.pollingKey
    let status: Exclude<AiJobViewStatus, 'COMPLETED'> | 'REQUEST_ERROR' | 'POLL_TIMEOUT' = 'PENDING'
    let presentation: AiJobPresentation = getJobStatusPresentation('PENDING')
    let onAction: (() => void) | undefined

    if (completionError) {
      status = 'REQUEST_ERROR'
      presentation = getJobRequestErrorPresentation(completionError.error)
      if (
        completionError.error instanceof ApiError &&
        completionError.error.status === 401
      ) {
        onAction = expireOcrSession
      } else if (completionError.canRetry) {
        presentation = { ...presentation, actionLabel: '상태 다시 확인하기' }
        onAction = retryCompletedResult
      } else {
        onAction = resetToUpload
      }
    } else if (isCurrentPollingTarget && pollingState.phase === 'ERROR') {
      status = 'REQUEST_ERROR'
      presentation = getJobRequestErrorPresentation(pollingState.error)
      const terminalRequestError =
        pollingState.error instanceof ApiError &&
        [401, 403, 404, 409].includes(pollingState.error.status)
      if (terminalRequestError) {
        onAction = pollingState.error instanceof ApiError && pollingState.error.status === 401
          ? expireOcrSession
          : resetToUpload
      } else {
        presentation = { ...presentation, actionLabel: '상태 다시 확인하기' }
        onAction = resumePolling
      }
    } else if (isCurrentPollingTarget && pollingState.phase === 'TIMED_OUT') {
      status = 'POLL_TIMEOUT'
      presentation = {
        ...getPollingTimeoutPresentation(),
        actionLabel: '상태 다시 확인하기',
      }
      onAction = resumePolling
    } else if (isCurrentPollingTarget && pollingState.status === 'FAILED') {
      status = 'FAILED'
      const failureCode = pollingState.data?.kind === 'ASYNC'
        ? pollingState.data.body.data.error?.code
        : pollingState.data?.body.data.error_code
      presentation = getJobFailurePresentation(failureCode)
      onAction = resetToUpload
    } else if (isCurrentPollingTarget && pollingState.status === 'STALE') {
      status = 'STALE'
      presentation = getJobStatusPresentation('STALE')
      onAction = resetToUpload
    } else if (
      isCurrentPollingTarget &&
      pollingState.status &&
      pollingState.status !== 'FAILED' &&
      pollingState.status !== 'STALE' &&
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
          <div className={`upload-zone mvp-upload__zone ${file ? 'selected' : ''}`}>
            <label className="mvp-upload__picker" htmlFor={inputId}>
              <span className="mvp-upload__zone-icon" aria-hidden="true">
                <span />
              </span>
              <strong
                id={file ? filenameId : undefined}
                className={
                  file
                    ? `mvp-upload__filename${isFilenameExpanded ? ' mvp-upload__filename--expanded' : ''}`
                    : undefined
                }
              >
                {file?.name ?? '사진 촬영 또는 파일 선택'}
              </strong>
              <small>{file ? '선택 완료 · 눌러서 변경' : 'JPG · PNG · PDF / 최대 30MB'}</small>
            </label>
            {file && (
              <button
                type="button"
                className="mvp-upload__filename-toggle"
                aria-expanded={isFilenameExpanded}
                aria-controls={filenameId}
                onClick={() => setIsFilenameExpanded((current) => !current)}
              >
                {isFilenameExpanded ? '파일명 접기' : '전체 파일명 보기'}
              </button>
            )}
          </div>

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
