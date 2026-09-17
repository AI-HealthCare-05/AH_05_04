import { apiRequest } from './client'

export type SignupRequest = {
  email: string
  password: string
  name: string
  consents?: SignupConsentRequest[]
}

export type SignupConsentPurpose = 'OCR' | 'GUIDE' | 'CHAT' | 'NOTIFICATION'

export type SignupConsentRequest = {
  purpose: SignupConsentPurpose
}

export type LoginRequest = {
  email: string
  password: string
}

export type LoginResponse = {
  access_token: string
}

export async function signup(data: SignupRequest) {
  const request = {
    ...data,
    consents: data.consents ?? [],
  }

  return apiRequest<{ detail: string }>('/api/v1/auth/signup', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })
}

export async function login(data: LoginRequest) {
  return apiRequest<LoginResponse>('/api/v1/auth/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })
}

export async function logout() {
  return apiRequest<{ detail: string }>('/api/v1/auth/logout', {
    method: 'POST',
  })
}

export async function requestAccountWithdrawal(password: string, accessToken: string) {
  return apiRequest<{ detail: string }>('/api/v1/auth/account/withdrawal', {
    method: 'POST',
    accessToken,
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ password, confirmed: true }),
  })
}

// Discard the LOCAL-only token so consumers cannot depend on or display it.
export async function requestEmailVerification(email: string): Promise<void> {
  await apiRequest('/api/v1/auth/email-verification/request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
}

export async function confirmEmailVerification(email: string, token: string): Promise<void> {
  await apiRequest('/api/v1/auth/email-verification/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, token }),
  })
}

export type PasswordResetRequestResponse = {
  detail: string
  // LOCAL 환경에서만 채워진다. 계정 존재 여부가 새지 않도록 그 외 환경에서는 항상 비어있다.
  reset_token?: string | null
}

export async function requestPasswordReset(email: string): Promise<PasswordResetRequestResponse> {
  return apiRequest<PasswordResetRequestResponse>('/api/v1/auth/password-reset/request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
}

export async function confirmPasswordReset(token: string, newPassword: string): Promise<{ detail: string }> {
  return apiRequest<{ detail: string }>('/api/v1/auth/password-reset/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, new_password: newPassword }),
  })
}
