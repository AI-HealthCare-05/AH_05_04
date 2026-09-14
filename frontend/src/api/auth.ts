import { apiRequest } from './client'

export type SignupRequest = {
  email: string
  password: string
  name: string
}

export type LoginRequest = {
  email: string
  password: string
}

export type LoginResponse = {
  access_token: string
}

export async function signup(data: SignupRequest) {
  return apiRequest<{ detail: string }>('/api/v1/auth/signup', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
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
