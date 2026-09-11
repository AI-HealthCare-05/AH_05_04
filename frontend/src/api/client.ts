import type { ApiErrorDetail, ApiErrorResponse } from '../types/api'
import {
  getAuthenticationSession,
  replaceStoredAccessToken,
} from '../features/auth/authStorage'

type ApiRequestOptions = RequestInit & {
  accessToken?: string
}

type TokenRefreshResponse = {
  access_token: string
}

export type ApiResponse<T> = {
  data: T
  headers: Headers
  status: number
}

type UnknownErrorBody = {
  code?: unknown
  message?: unknown
  details?: unknown
  trace_id?: unknown
  detail?: unknown
}

export class ApiError extends Error {
  status: number
  code: string
  details: ApiErrorDetail[]
  traceId?: string

  constructor(
    status: number,
    message: string,
    code = 'HTTP_ERROR',
    details: ApiErrorDetail[] = [],
    traceId?: string,
  ) {
    super(message)

    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.traceId = traceId
  }
}

const TOKEN_REFRESH_PATH = '/api/v1/auth/token/refresh'
const AUTH_SESSION_LOCK_NAME = 'dosey-auth-session'
let pendingTokenRefresh: Promise<string> | null = null
let pendingTokenRefreshFor: string | null = null
let pendingTokenRefreshGeneration: string | null = null
let authSessionQueue: Promise<void> = Promise.resolve()

class AuthSessionChangedError extends Error {
  constructor() {
    super('Authentication session changed during token refresh')
    this.name = 'AuthSessionChangedError'
  }
}

function getApiBaseUrl(): string {
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL

  if (!apiBaseUrl) {
    throw new Error('VITE_API_BASE_URL is not configured')
  }

  return apiBaseUrl
}

function resolveApiUrl(path: string): string {
  const apiBaseUrl = getApiBaseUrl()
  const baseUrl = new URL(
    apiBaseUrl.endsWith('/') ? apiBaseUrl : `${apiBaseUrl}/`,
  )
  const resolvedUrl = new URL(path, baseUrl)

  if (resolvedUrl.origin !== baseUrl.origin) {
    throw new Error('Cross-origin API response URL is not allowed')
  }

  return resolvedUrl.toString()
}

function isApiErrorResponse(body: UnknownErrorBody): body is ApiErrorResponse {
  return (
    typeof body.code === 'string' &&
    typeof body.message === 'string' &&
    Array.isArray(body.details) &&
    typeof body.trace_id === 'string'
  )
}

async function createApiError(response: Response): Promise<ApiError> {
  const fallbackMessage = `API request failed with status ${response.status}`

  try {
    const text = await response.text()

    if (!text) {
      return new ApiError(response.status, fallbackMessage)
    }

    try {
      const body = JSON.parse(text) as UnknownErrorBody

      if (isApiErrorResponse(body)) {
        return new ApiError(
          response.status,
          body.message,
          body.code,
          body.details,
          body.trace_id,
        )
      }

      if (typeof body.detail === 'string') {
        return new ApiError(response.status, body.detail)
      }

      return new ApiError(response.status, fallbackMessage)
    } catch {
      return new ApiError(response.status, fallbackMessage)
    }
  } catch {
    return new ApiError(response.status, fallbackMessage)
  }
}

function canRefreshAccessToken(path: string, accessToken: string | undefined) {
  if (accessToken !== undefined) return false

  return !new URL(resolveApiUrl(path)).pathname.startsWith('/api/v1/auth/')
}

function isTokenRefreshResponse(value: unknown): value is TokenRefreshResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'access_token' in value &&
    typeof value.access_token === 'string' &&
    value.access_token.length > 0
  )
}

async function startTokenRefresh(
  staleAccessToken: string,
  sessionGeneration: string,
): Promise<string> {
  const response = await fetch(resolveApiUrl(TOKEN_REFRESH_PATH), {
    method: 'GET',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
    },
  })

  if (!response.ok) {
    throw await createApiError(response)
  }

  const body: unknown = await response.json()
  if (!isTokenRefreshResponse(body)) {
    throw new Error('Token refresh response is invalid')
  }

  if (
    !replaceStoredAccessToken(
      staleAccessToken,
      sessionGeneration,
      body.access_token,
    )
  ) {
    throw new AuthSessionChangedError()
  }

  return body.access_token
}

export function runWithAuthSessionLock<T>(task: () => Promise<T>): Promise<T> {
  if (navigator.locks) {
    return navigator.locks.request(AUTH_SESSION_LOCK_NAME, task)
  }

  const result = authSessionQueue.then(task, task)
  authSessionQueue = result.then(
    () => undefined,
    () => undefined,
  )
  return result
}

function refreshAccessToken(
  staleAccessToken: string,
  sessionGeneration: string,
): Promise<string> {
  const currentSession = getAuthenticationSession()
  if (
    currentSession &&
    currentSession.accessToken !== staleAccessToken &&
    currentSession.generation === sessionGeneration
  ) {
    return Promise.resolve(currentSession.accessToken)
  }

  if (
    pendingTokenRefresh &&
    pendingTokenRefreshFor === staleAccessToken &&
    pendingTokenRefreshGeneration === sessionGeneration
  ) {
    return pendingTokenRefresh
  }

  const request = runWithAuthSessionLock(async () => {
    const lockedSession = getAuthenticationSession()
    if (
      !lockedSession ||
      lockedSession.generation !== sessionGeneration
    ) {
      throw new AuthSessionChangedError()
    }
    if (lockedSession.accessToken !== staleAccessToken) {
      return lockedSession.accessToken
    }

    return startTokenRefresh(staleAccessToken, sessionGeneration)
  }).finally(() => {
    if (pendingTokenRefresh !== request) return

    pendingTokenRefresh = null
    pendingTokenRefreshFor = null
    pendingTokenRefreshGeneration = null
  })
  pendingTokenRefresh = request
  pendingTokenRefreshFor = staleAccessToken
  pendingTokenRefreshGeneration = sessionGeneration

  return request
}

function requestHeaders(
  headers: HeadersInit | undefined,
  token: string | null,
  acceptJson: boolean,
) {
  return {
    ...(acceptJson ? { Accept: 'application/json' } : {}),
    ...headers,
    ...(token
      ? {
          Authorization: `Bearer ${token}`,
        }
      : {}),
  }
}

async function fetchApi(
  path: string,
  requestOptions: RequestInit,
  headers: HeadersInit | undefined,
  token: string | null,
  acceptJson: boolean,
) {
  return fetch(resolveApiUrl(path), {
    ...requestOptions,
    credentials: 'include',
    headers: requestHeaders(headers, token, acceptJson),
  })
}

async function fetchWithAccessTokenRefresh(
  path: string,
  requestOptions: RequestInit,
  headers: HeadersInit | undefined,
  accessToken: string | undefined,
  acceptJson: boolean,
) {
  const session = accessToken === undefined ? getAuthenticationSession() : null
  const token = accessToken ?? session?.accessToken ?? null
  const sessionGeneration = session?.generation ?? null
  const response = await fetchApi(
    path,
    requestOptions,
    headers,
    token,
    acceptJson,
  )

  if (
    accessToken === undefined &&
    token &&
    sessionGeneration &&
    getAuthenticationSession()?.generation !== sessionGeneration
  ) {
    throw new AuthSessionChangedError()
  }

  if (
    response.status !== 401 ||
    !token ||
    !sessionGeneration ||
    !canRefreshAccessToken(path, accessToken)
  ) {
    return response
  }

  if (!navigator.locks) return response

  if (
    typeof ReadableStream !== 'undefined' &&
    requestOptions.body instanceof ReadableStream
  ) {
    return response
  }

  const refreshedToken = await refreshAccessToken(token, sessionGeneration)
  const retryResponse = await fetchApi(
    path,
    requestOptions,
    headers,
    refreshedToken,
    acceptJson,
  )
  if (getAuthenticationSession()?.generation !== sessionGeneration) {
    throw new AuthSessionChangedError()
  }

  return retryResponse
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const response = await apiRequestWithResponse<T>(path, options)

  return response.data
}

export async function apiRequestWithResponse<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<ApiResponse<T>> {
  const { accessToken, headers, ...requestOptions } = options

  const response = await fetchWithAccessTokenRefresh(
    path,
    requestOptions,
    headers,
    accessToken,
    true,
  )

  if (!response.ok) {
    throw await createApiError(response)
  }

  if (response.status === 204) {
    return {
      data: undefined as T,
      headers: response.headers,
      status: response.status,
    }
  }

  return {
    data: (await response.json()) as T,
    headers: response.headers,
    status: response.status,
  }
}

export async function apiBlobRequest(
  path: string,
  options: ApiRequestOptions = {},
): Promise<Blob> {
  const { accessToken, headers, ...requestOptions } = options

  const response = await fetchWithAccessTokenRefresh(
    path,
    requestOptions,
    headers,
    accessToken,
    false,
  )

  if (!response.ok) {
    throw await createApiError(response)
  }

  return response.blob()
}
