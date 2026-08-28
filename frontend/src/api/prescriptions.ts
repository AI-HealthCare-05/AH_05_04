import { apiBlobRequest, apiRequest } from './client'

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
    engine_name: string | null
    model_version: string | null
    prompt_version: string | null
    created_at: string
    completed_at: string | null
    fields: ExtractedField[]
  }
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
): Promise<OcrJobResponse> {
  return apiRequest<OcrJobResponse>(
    `/api/v1/documents/${documentId}/ocr-jobs`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        force_reprocess: false,
      }),
    },
  )
}

export async function getOcrJob(
  jobId: string,
): Promise<OcrJobResponse> {
  return apiRequest<OcrJobResponse>(
    `/api/v1/ocr-jobs/${jobId}`,
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
