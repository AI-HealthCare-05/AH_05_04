import { afterEach, describe, expect, it, vi } from 'vitest'
import { login, logout, signup } from '../src/api/auth'
import { runWithAuthSessionLock } from '../src/api/client'
import {
  clearAuthenticationStorage,
  startAuthenticatedSession,
} from '../src/features/auth/authStorage'

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

describe('login API', () => {
  it('email과 password만 기존 로그인 endpoint로 전송한다', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ access_token: 'synthetic-token' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await login({
      email: 'dosey@example.com',
      password: 'Password1!',
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:8000/api/v1/auth/login')
    expect(options?.method).toBe('POST')
    expect(JSON.parse(String(options?.body))).toEqual({
      email: 'dosey@example.com',
      password: 'Password1!',
    })
  })
})

describe('logout API', () => {
  it('기존 인증 토큰으로 logout endpoint에 POST한다', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ detail: '로그아웃 완료' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem('access_token', 'fixture-access-token')

    const request = logout()
    clearAuthenticationStorage()
    await request

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/auth/logout',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer fixture-access-token',
        }),
      }),
    )
  })

  it('다른 탭의 새 로그인이 먼저 완료되면 지연된 이전 세션 로그아웃을 보내지 않는다', async () => {
    let releaseLogin!: () => void
    let loginLockStarted!: () => void
    const loginStarted = new Promise<void>((resolve) => {
      loginLockStarted = resolve
    })
    const loginRelease = new Promise<void>((resolve) => {
      releaseLogin = resolve
    })
    let lockQueue: Promise<unknown> = Promise.resolve()
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: {
        request: (_name: string, task: () => Promise<unknown>) => {
          const result = lockQueue.then(task, task)
          lockQueue = result.then(
            () => undefined,
            () => undefined,
          )
          return result
        },
      },
    })
    const fetchMock = vi.fn<typeof fetch>()
    vi.stubGlobal('fetch', fetchMock)
    startAuthenticatedSession('first-account-token')

    const newerLogin = runWithAuthSessionLock(async () => {
      loginLockStarted()
      await loginRelease
      startAuthenticatedSession('second-account-token')
    })
    await loginStarted
    const delayedLogout = logout()
    clearAuthenticationStorage()
    releaseLogin()

    await newerLogin
    await expect(delayedLogout).resolves.toBeUndefined()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(localStorage.getItem('access_token')).toBe('second-account-token')
  })
})
