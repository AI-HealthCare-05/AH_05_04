import type { ApiErrorResponse } from '../types/api'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

if (!API_BASE_URL) {
  throw new Error('VITE_API_BASE_URL is not configured')
}

type ApiRequestOptions = RequestInit & {
  accessToken?: string
}

export class ApiError extends Error {
  status: number
  code: string
  details: ApiErrorResponse['details']
  traceId: string

  constructor(status: number, response: ApiErrorResponse) {
    super(response.message)

    this.name = 'ApiError'
    this.status = status
    this.code = response.code
    this.details = response.details
    this.traceId = response.trace_id
  }
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { accessToken, headers, ...requestOptions } = options

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...requestOptions,
    headers: {
      Accept: 'application/json',
      ...headers,
      ...(accessToken
        ? {
            Authorization: `Bearer ${accessToken}`,
          }
        : {}),
    },
  })

  if (!response.ok) {
    const errorResponse = (await response.json()) as ApiErrorResponse
    throw new ApiError(response.status, errorResponse)
  }

  return (await response.json()) as T
}
