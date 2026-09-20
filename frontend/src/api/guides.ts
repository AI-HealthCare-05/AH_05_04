import { apiRequest } from './client'

export type GuideStatus = 'GENERATING' | 'COMPLETED' | 'FAILED'

export type GuideReleaseDecision = 'PASS' | 'LIMITED' | 'REJECTED' | 'STALE'

export type GuideFallbackCode =
  | 'NO_APPROVED_EVIDENCE'
  | 'CONFLICTING_EVIDENCE'
  | 'PROVIDER_TIMEOUT'
  | 'DEPENDENCY_UNAVAILABLE'
  | 'VALIDATION_FAILED'
  | 'PRESCRIPTION_STALE'
  | 'EXECUTION_CONTEXT_STALE'
  | 'UNSUPPORTED_REQUEST'

export type GuideCitation = {
  source_type: 'LIFESTYLE_GUIDELINE'
  source_code: string
  source_version: string
  locator: string
  display_order: number
}

export type GuideData = {
  guide_id: string
  prescription_id: string
  prescription_version_id: string
  generation_status: GuideStatus
  content: string | null
  model_name: string | null
  prompt_version: string | null
  release_decision: GuideReleaseDecision | null
  release_is_current: boolean | null
  fallback_code: GuideFallbackCode | null
  fallback_text: string | null
  citations: GuideCitation[]
  requested_at: string
  completed_at: string | null
}

export type GuideResponse = {
  data: GuideData
}

export async function createGuide(
  prescriptionId: string,
): Promise<GuideResponse> {
  return apiRequest<GuideResponse>('/api/v1/guides', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      prescription_id: prescriptionId,
    }),
  })
}

export async function getGuide(guideId: string): Promise<GuideResponse> {
  return apiRequest<GuideResponse>(`/api/v1/guides/${guideId}`)
}

export async function getGuideForPrescription(
  prescriptionId: string,
): Promise<GuideResponse> {
  return apiRequest<GuideResponse>(
    `/api/v1/prescriptions/${prescriptionId}/guide`,
  )
}
