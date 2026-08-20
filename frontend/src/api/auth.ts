import { apiRequest } from './client'

export type SignupRequest = {
  email: string
  password: string
  name: string
  gender: string
  birth_date: string
  phone_number: string
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
