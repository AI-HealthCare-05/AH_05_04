const ACCESS_TOKEN_STORAGE_KEY = 'access_token'
const AUTH_SESSION_STORAGE_KEY = 'dosey_auth_session'
const LEGACY_AUTH_SESSION_GENERATION_STORAGE_KEY =
  'dosey_auth_session_generation'
const LOGOUT_INTENT_STORAGE_KEY = 'dosey_logout_intent'
const LEGACY_AUTH_SESSION_GENERATION = 'legacy-session'

export type AuthSession = {
  accessToken: string
  generation: string
}

export type LogoutIntent = {
  id: string
  accessToken: string | null
  sessionGeneration: string | null
}

function isAuthSession(value: unknown): value is AuthSession {
  return (
    typeof value === 'object' &&
    value !== null &&
    'accessToken' in value &&
    typeof value.accessToken === 'string' &&
    value.accessToken.length > 0 &&
    'generation' in value &&
    typeof value.generation === 'string' &&
    value.generation.length > 0
  )
}

function readCanonicalAuthSession(): AuthSession | null | undefined {
  const stored = localStorage.getItem(AUTH_SESSION_STORAGE_KEY)
  if (stored === null) return undefined

  try {
    const value: unknown = JSON.parse(stored)
    return isAuthSession(value) ? value : null
  } catch {
    return null
  }
}

function writeCanonicalAuthSession(session: AuthSession | null) {
  localStorage.setItem(AUTH_SESSION_STORAGE_KEY, JSON.stringify(session))
}

export function getAuthenticationSession(): AuthSession | null {
  const canonicalSession = readCanonicalAuthSession()
  if (canonicalSession !== undefined) return canonicalSession

  const accessToken = localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)
  if (!accessToken) return null

  return {
    accessToken,
    generation:
      localStorage.getItem(LEGACY_AUTH_SESSION_GENERATION_STORAGE_KEY) ??
      LEGACY_AUTH_SESSION_GENERATION,
  }
}

export function getStoredAccessToken() {
  return getAuthenticationSession()?.accessToken ?? null
}

export function replaceStoredAccessToken(
  staleAccessToken: string,
  expectedGeneration: string,
  nextAccessToken: string,
) {
  const currentSession = getAuthenticationSession()
  if (
    currentSession?.accessToken !== staleAccessToken ||
    currentSession.generation !== expectedGeneration
  ) {
    return false
  }

  writeCanonicalAuthSession({
    accessToken: nextAccessToken,
    generation: expectedGeneration,
  })
  localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, nextAccessToken)
  localStorage.removeItem(LEGACY_AUTH_SESSION_GENERATION_STORAGE_KEY)
  return true
}

export function startAuthenticatedSession(accessToken: string) {
  writeCanonicalAuthSession({
    accessToken,
    generation: crypto.randomUUID(),
  })
  localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, accessToken)
  localStorage.removeItem(LEGACY_AUTH_SESSION_GENERATION_STORAGE_KEY)
  localStorage.removeItem(LOGOUT_INTENT_STORAGE_KEY)
}

export function clearAuthenticationStorage() {
  writeCanonicalAuthSession(null)
  localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY)
  localStorage.removeItem(LEGACY_AUTH_SESSION_GENERATION_STORAGE_KEY)
}

export function beginLogoutIntent(): LogoutIntent {
  const session = getAuthenticationSession()
  const intent = {
    id: crypto.randomUUID(),
    accessToken: session?.accessToken ?? null,
    sessionGeneration: session?.generation ?? null,
  }

  localStorage.setItem(LOGOUT_INTENT_STORAGE_KEY, JSON.stringify(intent))
  return intent
}

export function isCurrentLogoutIntent(intent: LogoutIntent) {
  return localStorage.getItem(LOGOUT_INTENT_STORAGE_KEY) === JSON.stringify(intent)
}

export function completeLogoutIntent(intent: LogoutIntent) {
  if (isCurrentLogoutIntent(intent)) {
    localStorage.removeItem(LOGOUT_INTENT_STORAGE_KEY)
  }
}
