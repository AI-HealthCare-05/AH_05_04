import type { ApiErrorDetail, ApiErrorResponse } from '../types/api'

type ApiRequestOptions = RequestInit & {
  accessToken?: string
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

function getApiBaseUrl(): string {
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL

  if (!apiBaseUrl) {
    throw new Error('VITE_API_BASE_URL is not configured')
  }

  return apiBaseUrl
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

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { accessToken, headers, ...requestOptions } = options

  const token =
    accessToken ?? localStorage.getItem('access_token')

  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...requestOptions,
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...headers,
      ...(token
        ? {
            Authorization: `Bearer ${token}`,
          }
        : {}),
    },
  })

  if (!response.ok) {
    throw await createApiError(response)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}
