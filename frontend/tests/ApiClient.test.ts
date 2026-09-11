import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiBlobRequest, apiRequest } from '../src/api/client'
import {
  beginLogoutIntent,
  getAuthenticationSession,
  startAuthenticatedSession,
} from '../src/features/auth/authStorage'

const UNAUTHORIZED_BODY = JSON.stringify({
  code: 'EXPIRED_TOKEN',
  message: '로그인이 필요합니다.',
  details: [],
  trace_id: 'synthetic-trace-id',
})
const originalLocks = navigator.locks

function unauthorizedResponse() {
  return new Response(UNAUTHORIZED_BODY, {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  })
}

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  Object.defineProperty(navigator, 'locks', {
    configurable: true,
    value: {
      request: async (_name: string, task: () => Promise<unknown>) => task(),
    },
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
  Object.defineProperty(navigator, 'locks', {
    configurable: true,
    value: originalLocks,
  })
})

describe('access token refresh', () => {
  it('새 로그인 세션을 단일 레코드로 먼저 발행하고 로그아웃 의도를 마지막에 해제한다', () => {
    localStorage.setItem('access_token', 'previous-account-token')
    beginLogoutIntent()
    const observedSessions: Array<ReturnType<typeof getAuthenticationSession>> = []
    let tokenWhenLogoutIntentCleared: string | null = null
    const originalSetItem = Storage.prototype.setItem
    const originalRemoveItem = Storage.prototype.removeItem
    const setItemSpy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(function (key, value) {
        originalSetItem.call(this, key, value)
        if (key === 'dosey_auth_session') {
          observedSessions.push(getAuthenticationSession())
        }
      })
    const removeItemSpy = vi
      .spyOn(Storage.prototype, 'removeItem')
      .mockImplementation(function (key) {
        if (key === 'dosey_logout_intent') {
          tokenWhenLogoutIntentCleared = localStorage.getItem('access_token')
        }
        originalRemoveItem.call(this, key)
      })

    try {
      startAuthenticatedSession('next-account-token')
    } finally {
      setItemSpy.mockRestore()
      removeItemSpy.mockRestore()
    }

    expect(observedSessions[0]).toMatchObject({
      accessToken: 'next-account-token',
    })
    expect(tokenWhenLogoutIntentCleared).toBe('next-account-token')
    expect(getAuthenticationSession()?.accessToken).toBe('next-account-token')
  })

  it('보호 API 401 뒤 refresh cookie로 토큰을 갱신하고 원요청을 한 번 재시도한다', async () => {
    localStorage.setItem('access_token', 'expired-access-token')
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(jsonResponse({ access_token: 'fresh-access-token' }))
      .mockResolvedValueOnce(jsonResponse({ name: '합성 사용자' }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(apiRequest<{ name: string }>('/api/v1/users/me')).resolves.toEqual({
      name: '합성 사용자',
    })

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[1]).toEqual([
      'http://localhost:8000/api/v1/auth/token/refresh',
      expect.objectContaining({
        method: 'GET',
        credentials: 'include',
        headers: expect.objectContaining({ Accept: 'application/json' }),
      }),
    ])
    expect(fetchMock.mock.calls[2][1]?.headers).toEqual(
      expect.objectContaining({ Authorization: 'Bearer fresh-access-token' }),
    )
    expect(localStorage.getItem('access_token')).toBe('fresh-access-token')
  })

  it('동시 401 요청은 refresh rotation을 한 번만 수행하고 둘 다 새 토큰으로 재시도한다', async () => {
    localStorage.setItem('access_token', 'expired-access-token')
    let resolveRefresh!: (response: Response) => void
    const refreshResponse = new Promise<Response>((resolve) => {
      resolveRefresh = resolve
    })
    const fetchMock = vi.fn<typeof fetch>((input, options) => {
      const url = String(input)
      const authorization = (options?.headers as Record<string, string> | undefined)
        ?.Authorization

      if (url.endsWith('/auth/token/refresh')) return refreshResponse
      if (authorization === 'Bearer expired-access-token') {
        return Promise.resolve(unauthorizedResponse())
      }

      return Promise.resolve(jsonResponse({ ok: true }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const first = apiRequest<{ ok: boolean }>('/api/v1/users/me')
    const second = apiRequest<{ ok: boolean }>('/api/v1/prescriptions/latest')

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([url]) =>
          String(url).endsWith('/auth/token/refresh'),
        ),
      ).toHaveLength(1)
    })
    resolveRefresh(jsonResponse({ access_token: 'fresh-access-token' }))

    await expect(Promise.all([first, second])).resolves.toEqual([
      { ok: true },
      { ok: true },
    ])
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith('/auth/token/refresh'),
      ),
    ).toHaveLength(1)
  })

  it('다른 탭의 refresh 완료 후 느게 도착한 401은 같은 세션의 새 토큰으로 재시도한다', async () => {
    startAuthenticatedSession('expired-access-token')
    let resolveLateUnauthorized!: (response: Response) => void
    const lateUnauthorized = new Promise<Response>((resolve) => {
      resolveLateUnauthorized = resolve
    })
    const fetchMock = vi.fn<typeof fetch>((input, options) => {
      const url = String(input)
      const authorization = (options?.headers as Record<string, string> | undefined)
        ?.Authorization

      if (url.endsWith('/auth/token/refresh')) {
        return Promise.resolve(jsonResponse({ access_token: 'fresh-access-token' }))
      }
      if (
        url.endsWith('/prescriptions/latest') &&
        authorization === 'Bearer expired-access-token'
      ) {
        return lateUnauthorized
      }
      if (authorization === 'Bearer expired-access-token') {
        return Promise.resolve(unauthorizedResponse())
      }
      return Promise.resolve(jsonResponse({ ok: true }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const firstTab = apiRequest<{ ok: boolean }>('/api/v1/users/me')
    const secondTab = apiRequest<{ ok: boolean }>('/api/v1/prescriptions/latest')
    await expect(firstTab).resolves.toEqual({ ok: true })
    resolveLateUnauthorized(unauthorizedResponse())

    await expect(secondTab).resolves.toEqual({ ok: true })
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith('/auth/token/refresh'),
      ),
    ).toHaveLength(1)
    expect(fetchMock.mock.calls.at(-1)?.[1]?.headers).toEqual(
      expect.objectContaining({ Authorization: 'Bearer fresh-access-token' }),
    )
  })

  it('브라우저 auth lock 안에서 refresh rotation을 수행한다', async () => {
    localStorage.setItem('access_token', 'expired-access-token')
    const lockRequest = vi.fn(
      async (_name: string, task: () => Promise<unknown>) => task(),
    )
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: { request: lockRequest },
    })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(jsonResponse({ access_token: 'fresh-access-token' }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)

    await apiRequest('/api/v1/users/me')

    expect(lockRequest).toHaveBeenCalledWith(
      'dosey-auth-session',
      expect.any(Function),
    )
  })

  it('서로 다른 탭 모듈도 브라우저 auth lock으로 rotation을 한 번만 수행한다', async () => {
    let lockQueue: Promise<unknown> = Promise.resolve()
    const lockRequest = vi.fn(
      (_name: string, task: () => Promise<unknown>) => {
        const result = lockQueue.then(task, task)
        lockQueue = result.then(
          () => undefined,
          () => undefined,
        )
        return result
      },
    )
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: { request: lockRequest },
    })
    localStorage.setItem('access_token', 'expired-access-token')
    const fetchMock = vi.fn<typeof fetch>((_input, options) => {
      const authorization = (options?.headers as Record<string, string> | undefined)
        ?.Authorization

      if (!authorization) {
        return Promise.resolve(jsonResponse({ access_token: 'fresh-access-token' }))
      }
      if (authorization === 'Bearer expired-access-token') {
        return Promise.resolve(unauthorizedResponse())
      }
      return Promise.resolve(jsonResponse({ ok: true }))
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.resetModules()
    const firstTab = await import('../src/api/client')
    vi.resetModules()
    const secondTab = await import('../src/api/client')

    await expect(
      Promise.all([
        firstTab.apiRequest('/api/v1/users/me'),
        secondTab.apiRequest('/api/v1/prescriptions/latest'),
      ]),
    ).resolves.toEqual([{ ok: true }, { ok: true }])
    expect(
      fetchMock.mock.calls.filter(([, options]) =>
        !(options?.headers as Record<string, string> | undefined)?.Authorization,
      ),
    ).toHaveLength(1)
  })

  it('refresh 대기 중 로그아웃되면 새 토큰을 저장하거나 원요청을 재시도하지 않는다', async () => {
    localStorage.setItem('access_token', 'expired-access-token')
    let resolveRefresh!: (response: Response) => void
    const refreshResponse = new Promise<Response>((resolve) => {
      resolveRefresh = resolve
    })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockReturnValueOnce(refreshResponse)
    vi.stubGlobal('fetch', fetchMock)

    const request = apiRequest('/api/v1/users/me')
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    localStorage.removeItem('access_token')
    resolveRefresh(jsonResponse({ access_token: 'late-access-token' }))

    await expect(request).rejects.toThrow(
      'Authentication session changed during token refresh',
    )
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('원요청 재시도 중 다른 로그인 세션이 시작되면 이전 응답을 폐기한다', async () => {
    localStorage.setItem('access_token', 'expired-access-token')
    let resolveRetry!: (response: Response) => void
    const retryResponse = new Promise<Response>((resolve) => {
      resolveRetry = resolve
    })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(jsonResponse({ access_token: 'fresh-access-token' }))
      .mockReturnValueOnce(retryResponse)
    vi.stubGlobal('fetch', fetchMock)

    const request = apiRequest('/api/v1/users/me')
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    startAuthenticatedSession('other-account-token')
    resolveRetry(jsonResponse({ name: '이전 사용자' }))

    await expect(request).rejects.toThrow(
      'Authentication session changed during token refresh',
    )
    expect(localStorage.getItem('access_token')).toBe('other-account-token')
  })

  it('원요청 성공 응답이 다른 로그인 세션 시작 후 도착하면 폐기한다', async () => {
    startAuthenticatedSession('first-account-token')
    let resolveRequest!: (response: Response) => void
    const requestResponse = new Promise<Response>((resolve) => {
      resolveRequest = resolve
    })
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(requestResponse)
    vi.stubGlobal('fetch', fetchMock)

    const request = apiRequest('/api/v1/users/me')
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    startAuthenticatedSession('second-account-token')
    resolveRequest(jsonResponse({ name: '이전 사용자' }))

    await expect(request).rejects.toThrow(
      'Authentication session changed during token refresh',
    )
    expect(localStorage.getItem('access_token')).toBe('second-account-token')
  })

  it('refresh가 401이면 원요청을 다시 보내지 않고 인증 오류를 전달한다', async () => {
    localStorage.setItem('access_token', 'expired-access-token')
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(unauthorizedResponse())
    vi.stubGlobal('fetch', fetchMock)

    const request = apiRequest('/api/v1/users/me')

    await expect(request).rejects.toMatchObject<ApiError>({
      status: 401,
      code: 'EXPIRED_TOKEN',
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('브라우저 auth lock을 지원하지 않으면 자동 refresh를 fail-closed한다', async () => {
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: undefined,
    })
    localStorage.setItem('access_token', 'expired-access-token')
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(unauthorizedResponse())
    vi.stubGlobal('fetch', fetchMock)

    await expect(apiRequest('/api/v1/users/me')).rejects.toMatchObject<ApiError>({
      status: 401,
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('access_token')).toBe('expired-access-token')
  })

  it('멱등 mutation 재시도는 같은 body와 Idempotency-Key를 유지한다', async () => {
    localStorage.setItem('access_token', 'expired-access-token')
    const body = JSON.stringify({ synthetic: true })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(jsonResponse({ access_token: 'fresh-access-token' }))
      .mockResolvedValueOnce(jsonResponse({ accepted: true }))
    vi.stubGlobal('fetch', fetchMock)

    await apiRequest('/api/v1/documents/synthetic-id/ocr-jobs', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': 'synthetic-logical-attempt:0001',
      },
      body,
    })

    const firstOptions = fetchMock.mock.calls[0][1]
    const retryOptions = fetchMock.mock.calls[2][1]
    expect(firstOptions?.body).toBe(body)
    expect(retryOptions?.body).toBe(body)
    expect(firstOptions?.headers).toEqual(
      expect.objectContaining({
        'Idempotency-Key': 'synthetic-logical-attempt:0001',
      }),
    )
    expect(retryOptions?.headers).toEqual(
      expect.objectContaining({
        'Idempotency-Key': 'synthetic-logical-attempt:0001',
        Authorization: 'Bearer fresh-access-token',
      }),
    )
  })

  it('재사용할 수 없는 stream body는 자동 재시도하지 않는다', async () => {
    localStorage.setItem('access_token', 'expired-access-token')
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(unauthorizedResponse())
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      apiRequest('/api/v1/synthetic-stream', {
        method: 'POST',
        body: new ReadableStream(),
      }),
    ).rejects.toMatchObject<ApiError>({ status: 401 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('로그인 401은 refresh 대상이 아니며 기존 로그인 오류를 그대로 전달한다', async () => {
    localStorage.setItem('access_token', 'stale-access-token')
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(unauthorizedResponse())
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      apiRequest('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          email: 'synthetic@example.com',
          password: 'Synthetic1!',
        }),
      }),
    ).rejects.toMatchObject<ApiError>({ status: 401 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('파일 응답도 401 뒤 새 토큰으로 한 번만 재요청한다', async () => {
    localStorage.setItem('access_token', 'expired-access-token')
    const file = new Blob(['synthetic-file'], { type: 'text/plain' })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(jsonResponse({ access_token: 'fresh-access-token' }))
      .mockResolvedValueOnce(new Response(file, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const response = await apiBlobRequest('/api/v1/documents/synthetic-id/file')

    expect(response).toBeInstanceOf(Blob)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[2][1]?.headers).toEqual(
      expect.objectContaining({ Authorization: 'Bearer fresh-access-token' }),
    )
  })
})
