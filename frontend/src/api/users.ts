import { apiRequest } from './client'

export type UserGender = 'MALE' | 'FEMALE'

export type CurrentUser = {
  id: string
  name: string
  email: string
  phone_number: string | null
  birthday: string | null
  gender: UserGender | null
  created_at: string
}

export type UpdateCurrentUserRequest = {
  name: string
  email: string
}

export async function getCurrentUser() {
  return apiRequest<CurrentUser>('/api/v1/users/me')
}

export async function updateCurrentUser(data: UpdateCurrentUserRequest) {
  return apiRequest<CurrentUser>('/api/v1/users/me', {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })
}
