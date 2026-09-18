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

/**
 * PATCH /api/v1/users/me 요청 본문.
 *
 * 계약(backend/app/dtos/users.py · UserUpdateRequest):
 * - 생략한 필드는 변경하지 않는다.
 * - null 은 기존 값을 비운다(미입력).
 * - 빈 문자열은 422 이므로 보내지 않는다.
 * 그래서 "변경하지 않음"은 undefined 로 두어 JSON 직렬화에서 빠지게 한다.
 */
export type UpdateCurrentUserRequest = {
  name?: string
  email?: string
  phone_number?: string | null
  birthday?: string | null
  gender?: UserGender | null
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
