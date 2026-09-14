import { useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { signup, requestEmailVerification, confirmEmailVerification } from '../api/auth'
import { ApiError } from '../api/client'
import { Button, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

type SignupForm = {
  email: string
  password: string
  name: string
}

type SignupFieldErrors = Partial<Record<keyof SignupForm, string>>

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const PASSWORD_PATTERN = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^a-zA-Z0-9]).{8,72}$/
const NETWORK_ERROR_MESSAGE = '네트워크 연결을 확인하고 다시 시도해 주세요.'

function validateSignup(form: SignupForm): SignupFieldErrors {
  const errors: SignupFieldErrors = {}
  const name = form.name.trim()
  const email = form.email.trim()

  if (!name) {
    errors.name = '이름을 입력해 주세요.'
  } else if (name.length > 20) {
    errors.name = '이름을 20자 이하로 입력해 주세요.'
  }

  if (!email) {
    errors.email = '이메일을 입력해 주세요.'
  } else if (email.length > 40 || !EMAIL_PATTERN.test(email)) {
    errors.email = '올바른 이메일 주소를 40자 이하로 입력해 주세요.'
  }

  if (!PASSWORD_PATTERN.test(form.password)) {
    errors.password =
      '8자 이상이며 대문자·소문자·숫자·특수문자를 포함해 주세요.'
  }

  return errors
}

function SignupPage() {
  const navigate = useNavigate()
  // Opt in only after email delivery is available (#494); noop must not block signup.
  const emailVerificationEnabled = import.meta.env.VITE_EMAIL_VERIFICATION_ENABLED === 'true'
  const [form, setForm] = useState<SignupForm>({
    email: '',
    password: '',
    name: '',
  })
  const [fieldErrors, setFieldErrors] = useState<SignupFieldErrors>({})
  const [message, setMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const isSubmittingRef = useRef(false)
  const [verification, setVerification] = useState<'idle' | 'requesting' | 'sent' | 'confirming' | 'verified'>('idle')
  const [token, setToken] = useState('')
  const [verificationError, setVerificationError] = useState('')
  const verificationBusyRef = useRef(false)
  const verificationVersionRef = useRef(0)
  const tokenInputRef = useRef<HTMLInputElement>(null)
  const verificationRequestRef = useRef<HTMLButtonElement>(null)
  const verificationBusy = verification === 'requesting' || verification === 'confirming'
  const nameInputRef = useRef<HTMLInputElement>(null)
  const emailInputRef = useRef<HTMLInputElement>(null)
  const passwordInputRef = useRef<HTMLInputElement>(null)

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target
    const fieldName = name as keyof SignupForm
    if (name === 'email') {
      verificationVersionRef.current += 1
      setVerification('idle')
      setToken('')
      setVerificationError('')
    }
    setForm((prev) => ({ ...prev, [name]: value }))
    setFieldErrors((prev) => {
      if (!(fieldName in prev)) return prev

      const next = { ...prev }
      delete next[fieldName]
      return next
    })
    setMessage('')
  }

  const focusField = (field: keyof SignupForm | undefined) => {
    if (field === 'name') nameInputRef.current?.focus()
    if (field === 'email') emailInputRef.current?.focus()
    if (field === 'password') passwordInputRef.current?.focus()
  }

  const handleVerification = async (action: 'request' | 'confirm') => {
    if (verificationBusyRef.current || isSubmittingRef.current) return
    const emailError = validateSignup(form).email
    if (emailError) {
      setFieldErrors((prev) => ({ ...prev, email: emailError }))
      focusField('email')
      return
    }
    if (action === 'confirm' && !token.trim()) {
      setVerificationError('이메일로 받은 인증 코드를 입력해 주세요.')
      tokenInputRef.current?.focus()
      return
    }
    const version = verificationVersionRef.current
    verificationBusyRef.current = true
    setVerificationError('')
    setVerification(action === 'request' ? 'requesting' : 'confirming')
    try {
      if (action === 'request') await requestEmailVerification(form.email.trim())
      else await confirmEmailVerification(form.email.trim(), token.trim())
      if (version !== verificationVersionRef.current) return
      setVerification(action === 'request' ? 'sent' : 'verified')
      if (action === 'confirm') {
        setToken('')
        passwordInputRef.current?.focus()
      } else {
        // The token field stays mounted so focus also works on the first request.
        tokenInputRef.current?.focus()
      }
    } catch (error) {
      if (version !== verificationVersionRef.current) return
      setVerification(action === 'request' ? 'idle' : 'sent')
      const invalidToken = error instanceof ApiError && error.status === 422 &&
        error.code === 'VALIDATION_FAILED' && error.details.some(
          (detail) => detail.field === 'token' && detail.reason === 'EMAIL_VERIFICATION_TOKEN_INVALID',
        )
      setVerificationError(invalidToken
        ? '인증을 완료하지 못했습니다. 코드를 확인하거나 인증 안내를 다시 요청해 주세요.'
        : '이메일 인증을 처리하지 못했습니다. 연결을 확인하고 다시 시도해 주세요.')
      if (action === 'confirm') tokenInputRef.current?.focus()
    } finally {
      verificationBusyRef.current = false
    }
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    if (isSubmittingRef.current || verificationBusyRef.current) return

    const validationErrors = validateSignup(form)
    if (Object.keys(validationErrors).length > 0) {
      setFieldErrors(validationErrors)
      setMessage('')
      focusField(Object.keys(validationErrors)[0] as keyof SignupForm | undefined)
      return
    }

    if (emailVerificationEnabled && verification !== 'verified') {
      setVerificationError('회원가입 전에 이메일 인증을 완료해 주세요.')
      verificationRequestRef.current?.focus()
      return
    }

    try {
      isSubmittingRef.current = true
      setIsSubmitting(true)
      setFieldErrors({})
      setMessage('')
      await signup({
        name: form.name.trim(),
        email: form.email.trim(),
        password: form.password,
      })
      navigate('/login', {
        state: { fromSignup: true },
      })
    } catch (error) {
      if (error instanceof ApiError) {
        const emailConflict = (error.status === 409 && error.code === 'CONFLICT') || error.details.some(
          (detail) => detail.field === 'email' && detail.reason === 'ALREADY_EXISTS',
        )

        if (emailConflict) {
          setFieldErrors({ email: error.message })
          setMessage('')
          focusField('email')
        } else {
          setMessage(error.message)
        }
      } else {
        setMessage(NETWORK_ERROR_MESSAGE)
      }
    } finally {
      isSubmittingRef.current = false
      setIsSubmitting(false)
    }
  }

  return (
    <div className="mvp-page mvp-auth-page mvp-signup-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate('/start')}
        brandMark={<DoseyMascot variant="header" />}
        backPlacement="content"
        hideNavigation
      >
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-auth">
          <header className="mvp-auth__intro">
            <h1 className="mvp-page__title">
              Dosey 도지와 복약<br />관리를 시작해 주세요
            </h1>
            <p className="mvp-page__description">
              의료정보는 본인 확인과 동의 후 안전하게 관리합니다.
            </p>
          </header>

          <form className="mvp-form mvp-auth__form" onSubmit={handleSubmit} noValidate>
            <div className="mvp-auth__fields">
              <div className="mvp-form__field">
                <label htmlFor="signup-name">이름</label>
                <input
                  id="signup-name"
                  ref={nameInputRef}
                  name="name"
                  placeholder="이름을 입력해 주세요"
                  autoComplete="name"
                  required
                  maxLength={20}
                  aria-invalid={Boolean(fieldErrors.name)}
                  aria-describedby={fieldErrors.name ? 'signup-name-error' : undefined}
                  value={form.name}
                  onChange={handleChange}
                />
                {fieldErrors.name && (
                  <span className="mvp-form__field-error" id="signup-name-error" role="alert">
                    {fieldErrors.name}
                  </span>
                )}
              </div>
              <div className="mvp-form__field">
                <label htmlFor="signup-email">이메일</label>
                <input
                  id="signup-email"
                  ref={emailInputRef}
                  name="email"
                  type="email"
                  readOnly={verificationBusy || isSubmitting}
                  placeholder="이메일을 입력해 주세요"
                  autoComplete="email"
                  required
                  maxLength={40}
                  aria-invalid={Boolean(fieldErrors.email)}
                  aria-describedby={fieldErrors.email ? 'signup-email-error' : undefined}
                  value={form.email}
                  onChange={handleChange}
                />
                {fieldErrors.email && (
                  <span className="mvp-form__field-error" id="signup-email-error" role="alert">
                    {fieldErrors.email}
                  </span>
                )}
              </div>
              {emailVerificationEnabled && <div className="mvp-form__field" aria-busy={verificationBusy}>
                <button ref={verificationRequestRef} type="button" className="ds-button full-width"
                  disabled={verificationBusy || isSubmitting || verification === 'verified'}
                  onClick={() => void handleVerification('request')}>
                  {verification === 'requesting' ? '인증 요청 중...' : verification === 'verified' ? '이메일 인증 완료' : verification === 'idle' ? '인증 요청' : '인증 안내 다시 요청'}
                </button>
                <p id="signup-verification-status" role="status">
                  {verification === 'verified' ? '이메일 인증이 완료되었습니다.' :
                    verification === 'sent' || verification === 'confirming'
                      ? '인증 안내를 요청했습니다. 이메일로 받은 코드를 입력해 주세요. 안내가 오지 않으면 스팸함을 확인하거나 다시 요청해 주세요.'
                      : '회원가입을 위해 이메일 인증을 진행해 주세요.'}
                </p>
                <label htmlFor="signup-token">이메일 인증 코드</label>
                <input id="signup-token" ref={tokenInputRef} value={token}
                  autoComplete="one-time-code" spellCheck={false} autoCapitalize="none"
                  readOnly={verificationBusy || isSubmitting || verification === 'verified'}
                  aria-invalid={Boolean(verificationError)}
                  aria-describedby={`signup-verification-status${verificationError ? ' signup-verification-error' : ''}`}
                  onChange={(event) => { setToken(event.target.value); setVerificationError('') }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      if (verification === 'sent') void handleVerification('confirm')
                    }
                  }} />
                <Button type="button" fullWidth
                  disabled={verification !== 'sent' || isSubmitting}
                  onClick={() => void handleVerification('confirm')}>
                  {verification === 'confirming' ? '인증 확인 중...' : '인증 확인'}
                </Button>
                {verificationError && <span id="signup-verification-error" className="mvp-form__field-error" role="alert">{verificationError}</span>}
              </div>}
              <div className="mvp-form__field">
                <label htmlFor="signup-password">비밀번호</label>
                <input
                  id="signup-password"
                  ref={passwordInputRef}
                  name="password"
                  type="password"
                  placeholder="8자 이상 입력"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  maxLength={72}
                  aria-invalid={Boolean(fieldErrors.password)}
                  aria-describedby={fieldErrors.password ? 'signup-password-error' : undefined}
                  value={form.password}
                  onChange={handleChange}
                />
                {fieldErrors.password && (
                  <span className="mvp-form__field-error" id="signup-password-error" role="alert">
                    {fieldErrors.password}
                  </span>
                )}
              </div>
            </div>
            <div className="notice attention mvp-auth__notice">
              서비스 이용약관, 개인정보 수집·이용, 민감정보 처리에 필수 동의합니다.
            </div>

            {message && <p className="mvp-form__message" role="alert">{message}</p>}

            <Button fullWidth type="submit" disabled={isSubmitting || verificationBusy}>
              {isSubmitting ? '가입 중...' : '가입 완료'}
            </Button>
          </form>
        </main>
      </MobileShell>
    </div>
  )
}

export default SignupPage
