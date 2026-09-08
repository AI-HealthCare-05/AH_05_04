import { ApiError } from '../../api/client'
import { clearOcrJobRecovery } from '../ai-jobs/ocrJobRecovery'

const AUTH_ERROR_CODES = new Set([
  'UNAUTHORIZED',
  'INVALID_TOKEN',
  'EXPIRED_TOKEN',
])
const CHAT_SESSION_STORAGE_PREFIX = 'dosey_chat_session:'

function clearChatSessionRecovery() {
  for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
    const key = sessionStorage.key(index)
    if (key?.startsWith(CHAT_SESSION_STORAGE_PREFIX)) {
      sessionStorage.removeItem(key)
    }
  }
}

export function isStaleTokenError(error: unknown) {
  return (
    error instanceof ApiError &&
    (error.status === 401 || AUTH_ERROR_CODES.has(error.code))
  )
}

export function clearAuthenticatedSession() {
  localStorage.removeItem('access_token')
  clearOcrJobRecovery()
  clearChatSessionRecovery()
}
