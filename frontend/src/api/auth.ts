import { apiRequest, runWithAuthSessionLock } from './client'
import {
  beginLogoutIntent,
  completeLogoutIntent,
  isCurrentLogoutIntent,
} from '../features/auth/authStorage'

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
  const intent = beginLogoutIntent()

  return runWithAuthSessionLock(async () => {
    if (!isCurrentLogoutIntent(intent)) return undefined

    try {
      return await apiRequest<{ detail: string }>('/api/v1/auth/logout', {
        method: 'POST',
        ...(intent.accessToken ? { accessToken: intent.accessToken } : {}),
      })
    } finally {
      completeLogoutIntent(intent)
    }
  })
}
