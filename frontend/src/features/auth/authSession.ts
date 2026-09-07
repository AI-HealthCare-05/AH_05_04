import { ApiError } from '../../api/client'
import { clearOcrJobRecovery } from '../ai-jobs/ocrJobRecovery'

const AUTH_ERROR_CODES = new Set([
  'UNAUTHORIZED',
  'INVALID_TOKEN',
  'EXPIRED_TOKEN',
])

export function isStaleTokenError(error: unknown) {
  return (
    error instanceof ApiError &&
    (error.status === 401 || AUTH_ERROR_CODES.has(error.code))
  )
}

export function clearAuthenticatedSession() {
  localStorage.removeItem('access_token')
  clearOcrJobRecovery()
}
