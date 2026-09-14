import { afterEach, describe, expect, it, vi } from 'vitest'
import { confirmEmailVerification, requestEmailVerification } from '../src/api/auth'
import { ApiError } from '../src/api/client'

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

describe('email verification adapters', () => {
  it.each([undefined, null, 'synthetic-local-only'])('discards response token %s and sends only email', async (verification_token) => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:8000')
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'ok', verification_token })))
    vi.stubGlobal('fetch', fetchMock)
    expect(await requestEmailVerification('synthetic@example.com')).toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/api/v1/auth/email-verification/request', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ email: 'synthetic@example.com' }),
    }))
  })

  it('sends email and token only and preserves canonical token failure', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:8000')
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'VALIDATION_FAILED', message: '인증 실패', trace_id: 'synthetic-trace',
      details: [{ field: 'token', reason: 'EMAIL_VERIFICATION_TOKEN_INVALID' }],
    }), { status: 422 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(confirmEmailVerification('synthetic@example.com', 'synthetic-code')).rejects.toMatchObject({
      status: 422, code: 'VALIDATION_FAILED', details: [{ field: 'token', reason: 'EMAIL_VERIFICATION_TOKEN_INVALID' }],
    } satisfies Partial<ApiError>)
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/api/v1/auth/email-verification/confirm', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ email: 'synthetic@example.com', token: 'synthetic-code' }),
    }))
  })
})
