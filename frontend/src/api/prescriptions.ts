import { apiRequest } from './client'

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
