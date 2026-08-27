import { afterEach, describe, expect, it, vi } from 'vitest'
import { getCurrentUser, updateCurrentUser } from '../src/api/users'

const responseBody = {
  id: '00000000-0000-4000-8000-000000000097',
  name: '테스트 사용자',
  email: 'profile@example.com',
  phone_number: null,
  birthday: null,
  gender: null,
  created_at: '2026-08-27T00:00:00Z',
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('users/me API', () => {
  it('로그인 사용자의 현재 정보를 GET으로 조회한다', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem('access_token', 'fixture-token')

    await expect(getCurrentUser()).resolves.toEqual(responseBody)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/users/me',
      expect.objectContaining({
        credentials: 'include',
        headers: expect.objectContaining({
          Authorization: 'Bearer fixture-token',
        }),
      }),
    )
  })

  it('PATCH body에 승인된 name과 email만 포함한다', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await updateCurrentUser({
      name: '테스트 사용자',
      email: 'profile@example.com',
    })

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:8000/api/v1/users/me')
    expect(options?.method).toBe('PATCH')
    expect(JSON.parse(String(options?.body))).toEqual({
      name: '테스트 사용자',
      email: 'profile@example.com',
    })
    expect(String(options?.body)).not.toContain('phone_number')
    expect(String(options?.body)).not.toContain('birthday')
    expect(String(options?.body)).not.toContain('gender')
  })
})
