import { apiRequest } from './client'

export const CONSENT_PURPOSES = ['OCR', 'GUIDE', 'CHAT', 'NOTIFICATION'] as const
export type ConsentPurpose = (typeof CONSENT_PURPOSES)[number]

export type UserConsent = {
  purpose: ConsentPurpose
  status: 'GRANTED' | 'WITHDRAWN' | null
  policy_version: string | null
  current_policy_version: string
  is_granted: boolean
  granted_at: string | null
  withdrawn_at: string | null
  updated_at: string | null
}

export function getUserConsents(signal?: AbortSignal) {
  return apiRequest<{ data: UserConsent[] }>('/api/v1/users/me/consents', { signal })
}

export function withdrawUserConsent(purpose: ConsentPurpose, policyVersion: string) {
  return apiRequest<{ data: UserConsent }>(`/api/v1/users/me/consents/${purpose}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'WITHDRAWN', policy_version: policyVersion }),
  })
}
