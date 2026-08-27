import { apiRequest } from './client'

export type GuideStatus = 'GENERATING' | 'COMPLETED' | 'FAILED'

export type GuideData = {
  guide_id: string
  prescription_id: string
  generation_status: GuideStatus
  content: string | null
  model_name: string | null
  prompt_version: string | null
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
