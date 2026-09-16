import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { signup, requestEmailVerification, confirmEmailVerification } from '../src/api/auth'
import { ApiError } from '../src/api/client'
import SignupPage from '../src/pages/SignupPage'
import { useLocation } from 'react-router-dom'

vi.mock('../src/api/auth', () => ({
  signup: vi.fn(),
  requestEmailVerification: vi.fn(),
  confirmEmailVerification: vi.fn(),
}))

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubEnv('VITE_EMAIL_VERIFICATION_ENABLED', 'true')
  vi.mocked(requestEmailVerification).mockResolvedValue(undefined)
  vi.mocked(confirmEmailVerification).mockResolvedValue(undefined)
  localStorage.clear()
  vi.mocked(signup).mockResolvedValue({ detail: '회원가입 완료' })
})

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

describe('SignupPage', () => {
  function LoginStateProbe() {
    const location = useLocation()

    return (
      <div>
        로그인 화면
        <span>
          {String(
            (location.state as { fromSignup?: boolean } | null)?.fromSignup === true,
          )}
        </span>
      </div>
    )
  }

  function renderPage() {
    return render(
      <MemoryRouter initialEntries={['/signup']}>
        <Routes>
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/login" element={<LoginStateProbe />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  function acceptTerms() {
    fireEvent.click(screen.getByRole('checkbox', { name: '필수 약관에 동의합니다' }))
  }

  function fillValidForm() {
    if (!(screen.getByRole('checkbox', { name: '필수 약관에 동의합니다' }) as HTMLInputElement).checked) acceptTerms()
    fireEvent.change(screen.getByLabelText('이름'), {
      target: { value: '홍길동' },
    })
    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'dosey@example.com' },
    })
    fireEvent.change(screen.getByLabelText('비밀번호'), {
      target: { value: 'Password1!' },
    })
  }

  async function verifyEmail() {
    fireEvent.click(screen.getByRole('button', { name: '인증 요청' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '인증 확인' })).toHaveProperty('disabled', false))
    fireEvent.change(screen.getByLabelText('이메일 인증 코드'), { target: { value: 'synthetic-code' } })
    fireEvent.click(screen.getByRole('button', { name: '인증 확인' }))
    await screen.findByText('이메일 인증이 완료되었습니다.')
  }

  it('필수 약관 미동의는 CTA와 form 직접 제출을 차단한다', async () => {
    renderPage()
    fillValidForm()
    await verifyEmail()
    acceptTerms()
    const submit = screen.getByRole('button', { name: '가입 완료' })
    expect(submit).toHaveProperty('disabled', true)
    fireEvent.submit(submit.closest('form')!)
    expect(signup).not.toHaveBeenCalled()
    expect(screen.getByText('필수 약관에 동의해 주세요.')).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByRole('checkbox', { name: '필수 약관에 동의합니다' }))
    acceptTerms()
    expect(submit).toHaveProperty('disabled', false)
    fireEvent.click(submit)
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(signup).toHaveBeenCalledWith({
      name: '홍길동', email: 'dosey@example.com', password: 'Password1!', consents: [],
    })
  })

  it('약관 열기·확인은 동의나 API 호출 없이 입력·선택·인증 상태를 보존한다', async () => {
    renderPage()
    fillValidForm()
    await verifyEmail()
    acceptTerms()
    fireEvent.click(screen.getByRole('checkbox', { name: /도지에게 질문/ }))
    const requestCount = vi.mocked(requestEmailVerification).mock.calls.length
    const confirmCount = vi.mocked(confirmEmailVerification).mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: '약관 보기' }))
    expect(screen.getByText('검토용 문안 · 최종 법무/Privacy 승인 전')).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: '필수 약관 보기' }))
    expect(screen.queryByRole('button', { name: '가입 완료' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '확인' }))
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '약관 보기' }))
    expect(screen.getByRole('checkbox', { name: '필수 약관에 동의합니다' })).toHaveProperty('checked', false)
    expect(screen.getByRole('checkbox', { name: /도지에게 질문/ })).toHaveProperty('checked', true)
    expect(screen.getByLabelText('이름')).toHaveProperty('value', '홍길동')
    expect(screen.getByLabelText('비밀번호')).toHaveProperty('value', 'Password1!')
    expect(screen.getByText('이메일 인증이 완료되었습니다.')).toBeTruthy()
    expect(signup).not.toHaveBeenCalled()
    expect(requestEmailVerification).toHaveBeenCalledTimes(requestCount)
    expect(confirmEmailVerification).toHaveBeenCalledTimes(confirmCount)
  })

  it('모든 목적 선택 후 해제한 목적은 payload에서 제외한다', async () => {
    renderPage()
    fillValidForm()
    await verifyEmail()
    for (const name of [/처방전 인식/, /복약 안내/, /도지에게 질문/, /복약 알림/]) {
      fireEvent.click(screen.getByRole('checkbox', { name }))
    }
    fireEvent.click(screen.getByRole('checkbox', { name: /처방전 인식/ }))
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(signup).toHaveBeenCalledWith({
      name: '홍길동', email: 'dosey@example.com', password: 'Password1!',
      consents: [{ purpose: 'GUIDE' }, { purpose: 'CHAT' }, { purpose: 'NOTIFICATION' }],
    })
  })

  it.each([undefined, 'false', 'TRUE', '1'])('flag %s keeps noop/non-local signup available without verification calls', async (flag) => {
    vi.stubEnv('VITE_EMAIL_VERIFICATION_ENABLED', flag)
    vi.stubEnv('MODE', 'production')
    vi.stubEnv('PROD', true)
    renderPage()
    fillValidForm()
    expect(screen.queryByRole('button', { name: '인증 요청' })).toBeNull()
    expect(screen.queryByLabelText('이메일 인증 코드')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(signup).toHaveBeenCalledTimes(1)
    expect(signup).toHaveBeenCalledWith({
      name: '홍길동',
      email: 'dosey@example.com',
      password: 'Password1!',
      consents: [],
    })
    expect(requestEmailVerification).not.toHaveBeenCalled()
    expect(confirmEmailVerification).not.toHaveBeenCalled()
  })

  it('disabled flag preserves validation focus and final signup conflict', async () => {
    vi.stubEnv('VITE_EMAIL_VERIFICATION_ENABLED', 'false')
    vi.mocked(signup).mockRejectedValue(new ApiError(409, '이미 사용중인 이메일입니다.', 'CONFLICT'))
    renderPage()
    acceptTerms()
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    expect(document.activeElement).toBe(screen.getByLabelText('이름'))
    expect(signup).not.toHaveBeenCalled()
    fillValidForm()
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    await screen.findByText('이미 사용중인 이메일입니다.')
    expect(document.activeElement).toBe(screen.getByLabelText('이메일'))
    expect(requestEmailVerification).not.toHaveBeenCalled()
  })

  it('#395 회원가입 성공 후 로그인 화면에 fromSignup 상태를 전달한다', async () => {
    renderPage()
    fillValidForm()
    await verifyEmail()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(screen.getByText('true')).toBeTruthy()
  })

  it('AUTH-01 Figma 문구와 필수 입력을 렌더링한다', () => {
    renderPage()

    expect(
      screen.getByRole('heading', {
        name: 'Dosey 도지와 복약 관리를 시작해 주세요',
      }),
    ).toBeTruthy()
    expect(
      screen.getByText('기능별 동의 상태에 따라 필요한 정보만 처리합니다.'),
    ).toBeTruthy()
    expect(
      screen.getByText(
        '선택하지 않아도 가입할 수 있습니다. 선택한 기능만 동의 상태로 저장합니다.',
      ),
    ).toBeTruthy()
    expect(screen.getByLabelText('이름')).toHaveProperty('required', true)
    expect(screen.getByLabelText('이메일')).toHaveProperty('required', true)
    expect(screen.getByLabelText('비밀번호')).toHaveProperty('required', true)
    expect(screen.getAllByRole('checkbox')).toHaveLength(5)
    expect(screen.getByRole('checkbox', { name: '필수 약관에 동의합니다' })).toHaveProperty('required', true)
    for (const checkbox of screen.getAllByRole('checkbox').slice(1)) {
      expect(checkbox).toHaveProperty('checked', false)
      expect(checkbox).toHaveProperty('required', false)
    }
  })

  it('#224 정상 입력 중 현재 필드의 focus와 입력값을 유지한다', () => {
    renderPage()

    const nameInput = screen.getByLabelText('이름')
    const emailInput = screen.getByLabelText('이메일')

    fireEvent.change(nameInput, {
      target: { value: '홍길동' },
    })
    emailInput.focus()
    fireEvent.change(emailInput, {
      target: { value: 'd' },
    })

    expect(document.activeElement).toBe(emailInput)
    expect(nameInput).toHaveProperty('value', '홍길동')
    expect(emailInput).toHaveProperty('value', 'd')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it.each([
    ['name', '이름', '', '이름을 입력해 주세요.'],
    ['email', '이메일', 'invalid-email', '올바른 이메일 주소를 40자 이하로 입력해 주세요.'],
    ['password', '비밀번호', 'password', '8자 이상이며 대문자·소문자·숫자·특수문자를 포함해 주세요.'],
  ])('%s validation 오류를 입력과 연결하고 viewport 밖이면 focus·scroll한다', async (_field, label, invalidValue, errorMessage) => {
    renderPage()
    acceptTerms()
    fireEvent.change(screen.getByLabelText('이름'), { target: { value: '홍길동' } })
    fireEvent.change(screen.getByLabelText('이메일'), { target: { value: 'dosey@example.com' } })
    fireEvent.change(screen.getByLabelText('비밀번호'), { target: { value: 'Password1!' } })

    const invalidInput = screen.getByLabelText(label)
    fireEvent.change(invalidInput, { target: { value: invalidValue } })
    const scrollIntoView = vi.fn()
    invalidInput.scrollIntoView = scrollIntoView
    vi.spyOn(invalidInput, 'getBoundingClientRect').mockReturnValue({
      x: 20, y: -80, top: -80, right: 370, bottom: -32, left: 20,
      width: 350, height: 48, toJSON: () => ({}),
    })

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(document.activeElement).toBe(invalidInput)
    expect(invalidInput.getAttribute('aria-invalid')).toBe('true')
    const error = screen.getByText(errorMessage)
    expect(invalidInput.getAttribute('aria-describedby')).toBe(error.id)
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: 'smooth', block: 'center', inline: 'nearest',
    }))
    expect(signup).not.toHaveBeenCalled()
  })

  it('필수 약관 validation 오류를 checkbox와 연결하고 viewport 밖이면 focus·scroll한다', async () => {
    renderPage()
    fillValidForm()
    acceptTerms()
    const terms = screen.getByRole('checkbox', { name: '필수 약관에 동의합니다' })
    const scrollIntoView = vi.fn()
    terms.scrollIntoView = scrollIntoView
    vi.spyOn(terms, 'getBoundingClientRect').mockReturnValue({
      x: 20, y: 900, top: 900, right: 40, bottom: 920, left: 20,
      width: 20, height: 20, toJSON: () => ({}),
    })

    fireEvent.submit(screen.getByRole('button', { name: '가입 완료' }).closest('form')!)

    expect(document.activeElement).toBe(terms)
    expect(terms.getAttribute('aria-invalid')).toBe('true')
    const error = screen.getByText('필수 약관에 동의해 주세요.')
    expect(terms.getAttribute('aria-describedby')).toContain(error.id)
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: 'smooth', block: 'center', inline: 'nearest',
    }))
    expect(signup).not.toHaveBeenCalled()
  })

  it('빈 값과 Backend 비밀번호 정책 불일치 시 API를 호출하지 않고 오류를 연결한다', () => {
    renderPage()
    acceptTerms()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(signup).not.toHaveBeenCalled()
    expect(screen.getByLabelText('이름').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByLabelText('비밀번호').getAttribute('aria-invalid')).toBe('true')
    expect(document.activeElement).toBe(screen.getByLabelText('이름'))

    fireEvent.change(screen.getByLabelText('이름'), {
      target: { value: '홍길동' },
    })
    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'dosey@example.com' },
    })
    fireEvent.change(screen.getByLabelText('비밀번호'), {
      target: { value: 'password' },
    })
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(
      screen.getByText('8자 이상이며 대문자·소문자·숫자·특수문자를 포함해 주세요.'),
    ).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByLabelText('비밀번호'))
    expect(signup).not.toHaveBeenCalled()
  })

  it('#534 계약의 선택한 목적만 purpose 형태로 회원가입 요청에 포함한다', async () => {
    localStorage.setItem('existing_key', 'preserved')
    renderPage()
    fillValidForm()
    await verifyEmail()

    expect(screen.queryByLabelText('성별')).toBeNull()
    expect(screen.queryByLabelText('생년월일')).toBeNull()
    expect(screen.queryByLabelText('휴대전화')).toBeNull()
    fireEvent.click(screen.getByRole('checkbox', { name: /처방전 인식/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /복약 안내/ }))

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    await waitFor(() => {
      expect(signup).toHaveBeenCalledWith({
        name: '홍길동',
        email: 'dosey@example.com',
        password: 'Password1!',
        consents: [{ purpose: 'OCR' }, { purpose: 'GUIDE' }],
      })
    })
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('existing_key')).toBe('preserved')
  })

  it('#549 인증 만료 응답은 완료 상태를 해제하고 재인증 후 선택한 동의로 가입한다', async () => {
    vi.mocked(signup).mockRejectedValueOnce(
      new ApiError(409, 'unsafe-server-message', 'EMAIL_VERIFICATION_REQUIRED'),
    )
    renderPage()
    fillValidForm()
    await verifyEmail()
    fireEvent.click(screen.getByRole('checkbox', { name: /복약 안내/ }))
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    await screen.findByText('이메일 인증이 필요하거나 인증 유효 시간이 지났습니다. 인증 안내를 다시 요청하고 인증을 완료해 주세요.')
    expect(screen.queryByText('이메일 인증이 완료되었습니다.')).toBeNull()
    expect(screen.queryByText('unsafe-server-message')).toBeNull()
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('false')
    expect(screen.getByLabelText('이메일 인증 코드')).toHaveProperty('value', '')
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole('button', { name: '인증 요청' }))
    })
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    expect(signup).toHaveBeenCalledTimes(1)

    await verifyEmail()
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(signup).toHaveBeenLastCalledWith({
      name: '홍길동', email: 'dosey@example.com', password: 'Password1!',
      consents: [{ purpose: 'GUIDE' }],
    })
  })

  it('#549 Backend gate와 인증 UI 설정 불일치는 안내하고 인증 API를 자동 호출하지 않는다', async () => {
    vi.stubEnv('VITE_EMAIL_VERIFICATION_ENABLED', 'false')
    vi.mocked(signup).mockRejectedValueOnce(
      new ApiError(409, 'unsafe-server-message', 'EMAIL_VERIFICATION_REQUIRED'),
    )
    renderPage()
    fillValidForm()
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    await screen.findByText('회원가입에 이메일 인증이 필요합니다. 현재 인증 화면을 이용할 수 없으니 잠시 후 다시 시도해 주세요.')
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('false')
    expect(screen.queryByText('unsafe-server-message')).toBeNull()
    expect(requestEmailVerification).not.toHaveBeenCalled()
    expect(confirmEmailVerification).not.toHaveBeenCalled()
  })

  it('중복 이메일 Backend 오류를 이메일 입력에 연결한다', async () => {
    vi.mocked(signup).mockRejectedValue(
      new ApiError(409, '이미 사용중인 이메일입니다.', 'CONFLICT', [
        { field: 'email', reason: 'ALREADY_EXISTS' },
      ]),
    )
    renderPage()
    fillValidForm()
    await verifyEmail()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(await screen.findByText('이미 사용중인 이메일입니다.')).toBeTruthy()
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('true')
    expect(document.activeElement).toBe(screen.getByLabelText('이메일'))
  })

  it('네트워크 실패를 Backend 입력 오류와 구분해 안내한다', async () => {
    vi.mocked(signup).mockRejectedValue(new TypeError('Failed to fetch'))
    renderPage()
    fillValidForm()
    await verifyEmail()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(
      await screen.findByText('네트워크 연결을 확인하고 다시 시도해 주세요.'),
    ).toBeTruthy()
  })

  it('동의 policy 미설정은 서버 문구를 노출하지 않고 선택 해제 또는 재시도를 안내한다', async () => {
    vi.mocked(signup).mockRejectedValue(
      new ApiError(503, 'unsafe-server-message', 'CONSENT_POLICY_UNAVAILABLE'),
    )
    renderPage()
    fillValidForm()
    await verifyEmail()
    fireEvent.click(screen.getByRole('checkbox', { name: /처방전 인식/ }))

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))

    expect(
      await screen.findByText(
        '선택한 기능의 동의 안내를 준비하고 있어요. 해당 선택을 해제하거나 잠시 후 다시 시도해 주세요.',
      ),
    ).toBeTruthy()
    expect(screen.queryByText('unsafe-server-message')).toBeNull()
  })

  it('가입 요청 중 버튼을 비활성화해 중복 제출을 막는다', async () => {
    let resolveSignup: ((value: { detail: string }) => void) | undefined
    vi.mocked(signup).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSignup = resolve
        }),
    )
    renderPage()
    fillValidForm()
    await verifyEmail()

    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    const loadingButton = await screen.findByRole('button', { name: '가입 중...' })
    fireEvent.click(loadingButton)

    expect(loadingButton).toHaveProperty('disabled', true)
    expect(signup).toHaveBeenCalledTimes(1)

    resolveSignup?.({ detail: '회원가입 완료' })
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
  })
  it('인증 전 가입을 막고 요청 버튼으로 focus를 이동한다', () => {
    renderPage()
    fillValidForm()
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    expect(signup).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '인증 요청' }))
  })

  it('인증 요청은 이메일만 검증하고 잘못된 이메일에 focus한다', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: '인증 요청' }))
    expect(requestEmailVerification).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(screen.getByLabelText('이메일'))
    expect(screen.getAllByRole('alert')).toHaveLength(1)
  })

  it('요청 중 중복 클릭과 가입 제출을 막고 성공 후 코드에 focus한다', async () => {
    let resolveRequest!: () => void
    vi.mocked(requestEmailVerification).mockImplementation(() => new Promise<void>((resolve) => { resolveRequest = resolve }))
    renderPage()
    fillValidForm()
    fireEvent.click(screen.getByRole('button', { name: '인증 요청' }))
    fireEvent.click(screen.getByRole('button', { name: '인증 요청 중...' }))
    fireEvent.submit(screen.getByRole('button', { name: '가입 완료' }).closest('form')!)
    expect(requestEmailVerification).toHaveBeenCalledTimes(1)
    expect(signup).not.toHaveBeenCalled()
    expect(screen.getByLabelText('이메일')).toHaveProperty('readOnly', true)
    resolveRequest()
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText('이메일 인증 코드')))
    expect(requestEmailVerification).toHaveBeenCalledWith('dosey@example.com')
  })

  it('확인 중 Enter·중복 클릭·가입 제출을 막고 token을 지운다', async () => {
    let resolveConfirm!: () => void
    vi.mocked(confirmEmailVerification).mockImplementation(() => new Promise<void>((resolve) => { resolveConfirm = resolve }))
    renderPage()
    fillValidForm()
    fireEvent.click(screen.getByRole('button', { name: '인증 요청' }))
    await screen.findByRole('button', { name: '인증 안내 다시 요청' })
    const code = screen.getByLabelText('이메일 인증 코드')
    fireEvent.change(code, { target: { value: 'synthetic-code' } })
    fireEvent.keyDown(code, { key: 'Enter' })
    fireEvent.keyDown(code, { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: '인증 확인 중...' }))
    fireEvent.submit(screen.getByRole('button', { name: '가입 완료' }).closest('form')!)
    expect(confirmEmailVerification).toHaveBeenCalledTimes(1)
    expect(confirmEmailVerification).toHaveBeenCalledWith('dosey@example.com', 'synthetic-code')
    expect(signup).not.toHaveBeenCalled()
    resolveConfirm()
    await screen.findByText('이메일 인증이 완료되었습니다.')
    expect(code).toHaveProperty('value', '')
    expect(document.activeElement).toBe(screen.getByLabelText('비밀번호'))
  })

  it('인증 완료 후 이메일을 변경하면 인증과 코드를 초기화한다', async () => {
    renderPage()
    fillValidForm()
    await verifyEmail()
    fireEvent.change(screen.getByLabelText('이메일'), { target: { value: 'changed@example.com' } })
    expect(screen.queryByText('이메일 인증이 완료되었습니다.')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '가입 완료' }))
    expect(signup).not.toHaveBeenCalled()
  })

  it('token 오류는 중립 오류와 재요청을 제공한다', async () => {
    vi.mocked(confirmEmailVerification).mockRejectedValue(new ApiError(422, 'unsafe-server-message', 'VALIDATION_FAILED', [
      { field: 'token', reason: 'EMAIL_VERIFICATION_TOKEN_INVALID' },
    ]))
    renderPage()
    fillValidForm()
    fireEvent.click(screen.getByRole('button', { name: '인증 요청' }))
    await screen.findByRole('button', { name: '인증 안내 다시 요청' })
    fireEvent.change(screen.getByLabelText('이메일 인증 코드'), { target: { value: 'synthetic-invalid' } })
    fireEvent.click(screen.getByRole('button', { name: '인증 확인' }))
    await screen.findByText('인증을 완료하지 못했습니다. 코드를 확인하거나 인증 안내를 다시 요청해 주세요.')
    expect(screen.queryByText('unsafe-server-message')).toBeNull()
    expect(screen.getByLabelText('이메일').getAttribute('aria-invalid')).toBe('false')
    expect(document.activeElement).toBe(screen.getByLabelText('이메일 인증 코드'))
    fireEvent.click(screen.getByRole('button', { name: '인증 안내 다시 요청' }))
    await waitFor(() => expect(requestEmailVerification).toHaveBeenCalledTimes(2))
    expect(signup).not.toHaveBeenCalled()
  })

  it('빈 코드는 전송하지 않고 네트워크 요청 실패도 안전하게 재시도한다', async () => {
    vi.mocked(requestEmailVerification).mockRejectedValueOnce(new TypeError('sensitive-value'))
    renderPage()
    fillValidForm()
    fireEvent.click(screen.getByRole('button', { name: '인증 요청' }))
    await screen.findByText('이메일 인증을 처리하지 못했습니다. 연결을 확인하고 다시 시도해 주세요.')
    fireEvent.click(screen.getByRole('button', { name: '인증 요청' }))
    await screen.findByRole('button', { name: '인증 안내 다시 요청' })
    fireEvent.click(screen.getByRole('button', { name: '인증 확인' }))
    expect(confirmEmailVerification).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(screen.getByLabelText('이메일 인증 코드'))
  })

})
