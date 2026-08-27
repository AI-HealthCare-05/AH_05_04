import { afterEach, describe, expect, it, vi } from 'vitest'
import { signup } from '../src/api/auth'

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('signup API', () => {
  it('#65 계약 외 필드를 실제 request body에 포함하지 않는다', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ detail: '회원가입 완료' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await signup({
      name: '홍길동',
      email: 'dosey@example.com',
      password: 'Password1!',
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:8000/api/v1/auth/signup')
    expect(options?.method).toBe('POST')

    const requestBody = JSON.parse(String(options?.body)) as Record<
      string,
      unknown
    >
    expect(requestBody).toEqual({
      name: '홍길동',
      email: 'dosey@example.com',
      password: 'Password1!',
    })
    expect(requestBody).not.toHaveProperty('gender')
    expect(requestBody).not.toHaveProperty('birth_date')
    expect(requestBody).not.toHaveProperty('phone_number')
  })
})
