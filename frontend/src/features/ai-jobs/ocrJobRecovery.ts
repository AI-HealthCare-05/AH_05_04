export type OcrJobRecoveryTarget = {
  kind: 'ASYNC' | 'LEGACY'
  documentId: string
  jobId: string
  pollingKey: string
}

const OCR_JOB_RECOVERY_STORAGE_KEY = 'dosey_ocr_job_recovery:v1'

function isOcrJobRecoveryTarget(
  value: unknown,
): value is OcrJobRecoveryTarget {
  if (!value || typeof value !== 'object') return false

  const candidate = value as Partial<OcrJobRecoveryTarget>

  return (
    (candidate.kind === 'ASYNC' || candidate.kind === 'LEGACY') &&
    typeof candidate.documentId === 'string' &&
    candidate.documentId.length > 0 &&
    typeof candidate.jobId === 'string' &&
    candidate.jobId.length > 0 &&
    typeof candidate.pollingKey === 'string' &&
    candidate.pollingKey.length > 0
  )
}

export function loadOcrJobRecovery(): OcrJobRecoveryTarget | null {
  const storedValue = sessionStorage.getItem(OCR_JOB_RECOVERY_STORAGE_KEY)
  if (!storedValue) return null

  try {
    const parsedValue: unknown = JSON.parse(storedValue)
    if (isOcrJobRecoveryTarget(parsedValue)) return parsedValue
  } catch {
    // Invalid or stale session data is discarded below.
  }

  clearOcrJobRecovery()
  return null
}

export function saveOcrJobRecovery(target: OcrJobRecoveryTarget): void {
  sessionStorage.setItem(
    OCR_JOB_RECOVERY_STORAGE_KEY,
    JSON.stringify(target),
  )
}

export function clearOcrJobRecovery(): void {
  sessionStorage.removeItem(OCR_JOB_RECOVERY_STORAGE_KEY)
}
