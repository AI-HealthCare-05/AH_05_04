import {
  apiBlobRequest,
  apiRequest,
  apiRequestWithResponse,
} from './client'
import type { JobStatusResponse } from '../types/jobs'

export type { JobStatusResponse } from '../types/jobs'

export type UploadStatus = 'UPLOADED' | 'FAILED'

export type OcrJobStatus =
  | 'PENDING'
  | 'PROCESSING'
  | 'COMPLETED'
  | 'FAILED'

export type ExtractedField = {
  field_id: string
  field_type: string
  medication_index: number
  raw_value: string | null

  normalized_value: string | null
  normalization_version: string | null

  confirmed_value: string | null
  confidence_score: number | null
  confirmation_status: string
}

export type PrescriptionDocumentUploadResponse = {
  data: {
    document_id: string
    upload_status: UploadStatus
    uploaded_at: string
  }
  message: string
}

export type OcrJobResponse = {
  data: {
    job_id: string
    document_id: string
    ocr_status: OcrJobStatus
    error_code: string | null
    error_message?: string | null
    engine_name: string | null
    model_version: string | null
    prompt_version: string | null
    created_at: string
    completed_at: string | null
    fields: ExtractedField[]
  }
}

export type OcrIntakeResponse = JobStatusResponse | OcrJobResponse

export function isJobStatusResponse(
  response: OcrIntakeResponse,
): response is JobStatusResponse {
  return 'status' in response.data && 'status_url' in response.data
}

export function isLegacyOcrJobResponse(
  response: OcrIntakeResponse,
): response is OcrJobResponse {
  return 'ocr_status' in response.data && 'fields' in response.data
}

export async function uploadPrescription(
  file: File,
): Promise<PrescriptionDocumentUploadResponse> {
  const formData = new FormData()

  formData.append('file', file)
  formData.append('document_type', 'PRESCRIPTION')

  return apiRequest<PrescriptionDocumentUploadResponse>(
    '/api/v1/documents',
    {
      method: 'POST',
      body: formData,
    },
  )
}

export async function executeOcr(
  documentId: string,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<OcrIntakeResponse> {
  return apiRequest<OcrIntakeResponse>(
    `/api/v1/documents/${documentId}/ocr-jobs`,
    {
      method: 'POST',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({
        force_reprocess: false,
      }),
    },
  )
}

function parseRetryAfterSeconds(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value)) {
    return null
  }

  const seconds = Number(value)

  return Number.isSafeInteger(seconds) && seconds >= 0 ? seconds : null
}

export async function getJobStatus(
  statusUrl: string,
  signal?: AbortSignal,
): Promise<{
  body: JobStatusResponse
  retryAfterSeconds: number | null
}> {
  const response = await apiRequestWithResponse<JobStatusResponse>(
    statusUrl,
    { signal },
  )
  const bodyRetryAfterSeconds = response.data.data.retry_after_seconds
  const headerRetryAfterSeconds = parseRetryAfterSeconds(
    response.headers.get('Retry-After'),
  )

  if (
    bodyRetryAfterSeconds !== null &&
    headerRetryAfterSeconds !== null &&
    bodyRetryAfterSeconds !== headerRetryAfterSeconds
  ) {
    throw new Error('Retry-After header does not match retry_after_seconds')
  }

  return {
    body: response.data,
    retryAfterSeconds: bodyRetryAfterSeconds ?? headerRetryAfterSeconds,
  }
}

export async function getOcrResult(
  resultUrl: string,
  signal?: AbortSignal,
): Promise<OcrJobResponse> {
  return apiRequest<OcrJobResponse>(resultUrl, { signal })
}

export async function getOcrJob(
  jobId: string,
  signal?: AbortSignal,
): Promise<OcrJobResponse> {
  return apiRequest<OcrJobResponse>(
    `/api/v1/ocr-jobs/${jobId}`,
    { signal },
  )
}

export type Medication = {
  // 처방전에서 사용자가 확인한 이름입니다.
  medication_name: string

  // 제품 함량이며 1회 복용량과 구분합니다.
  strength_text: string | null

  dose_value: number | null
  dose_unit: string | null
  frequency_per_day: number | null
  timing_text: string | null
  duration_days: number | null
  display_order: number
}

export type PrescriptionResponse = {
  data: {
    prescription_id: string
    document_id: string
    prescribed_date: string
    confirmed_at: string
    medications: Medication[]
  }
}

export type ExtractedFieldResponse = {
  data: ExtractedField
}

export async function updateExtractedField(
  fieldId: string,
  confirmedValue: string | null,
): Promise<ExtractedFieldResponse> {
  return apiRequest<ExtractedFieldResponse>(
    `/api/v1/extracted-fields/${fieldId}`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        confirmed_value: confirmedValue,
      }),
    },
  )
}

export async function confirmPrescription(
  documentId: string,
): Promise<PrescriptionResponse> {
  return apiRequest<PrescriptionResponse>(
    `/api/v1/documents/${documentId}/prescription`,
    {
      method: 'POST',
    },
  )
}

export async function getPrescriptionDocumentFile(
  documentId: string,
): Promise<Blob> {
  return apiBlobRequest(
    `/api/v1/documents/${documentId}/file`,
  )
}
