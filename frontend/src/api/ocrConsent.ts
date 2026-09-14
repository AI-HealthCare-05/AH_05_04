import { apiRequest } from './client'

export type OcrConsentState = {
  purpose: 'OCR'
  status: 'MISSING' | 'GRANTED' | 'WITHDRAWN'
  effective: boolean
  reason: 'MISSING_CONSENT' | 'WITHDRAWN' | 'POLICY_VERSION_MISMATCH' | null
  current_policy_version: string
  accepted_policy_version: string | null
  granted_at: string | null
  withdrawn_at: string | null
}

type OcrConsentResponse = { data: OcrConsentState }

export async function getOcrConsent(signal?: AbortSignal): Promise<OcrConsentResponse> {
  return apiRequest<OcrConsentResponse>('/api/v1/users/me/consents/OCR', { signal })
}

export async function grantOcrConsent(policyVersion: string): Promise<OcrConsentResponse> {
  return apiRequest<OcrConsentResponse>('/api/v1/users/me/consents/OCR', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ policy_version: policyVersion }),
  })
}

export async function withdrawOcrConsent(): Promise<OcrConsentResponse> {
  return apiRequest<OcrConsentResponse>('/api/v1/users/me/consents/OCR', { method: 'DELETE' })
}
