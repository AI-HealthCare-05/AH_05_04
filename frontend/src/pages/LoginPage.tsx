import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { login } from '../api/auth'
import { ApiError } from '../api/client'
import { clearOcrJobRecovery } from '../features/ai-jobs/ocrJobRecovery'
import { Button, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

type LoginForm = {
  email: string
  password: string
}

type LoginFieldErrors = Partial<Record<keyof LoginForm, string>>

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const NETWORK_ERROR_MESSAGE = '네트워크 연결을 확인하고 다시 시도해 주세요.'

function validateLogin(form: LoginForm): LoginFieldErrors {
  const errors: LoginFieldErrors = {}
  const email = form.email.trim()

  if (!email) {
    errors.email = '이메일을 입력해 주세요.'
  } else if (email.length > 40 || !EMAIL_PATTERN.test(email)) {
    errors.email = '올바른 이메일 주소를 40자 이하로 입력해 주세요.'
  }

  if (form.password.length < 8) {
    errors.password = '비밀번호를 8자 이상 입력해 주세요.'
  }

  return errors
}

function LoginPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState<LoginForm>({ email: '', password: '' })
  const [fieldErrors, setFieldErrors] = useState<LoginFieldErrors>({})
  const [hasCredentialError, setHasCredentialError] = useState(false)
  const [message, setMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const isSubmittingRef = useRef(false)
  const emailInputRef = useRef<HTMLInputElement>(null)
  const passwordInputRef = useRef<HTMLInputElement>(null)

  const clearFeedback = () => {
    setHasCredentialError(false)
    setMessage('')
  }

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target
    setForm((prev) => ({ ...prev, [name]: value }))
    setFieldErrors((prev) => ({ ...prev, [name]: undefined }))
    clearFeedback()
  }

  useEffect(() => {
    if (fieldErrors.email || hasCredentialError) {
      emailInputRef.current?.focus()
      return
    }

    if (fieldErrors.password) passwordInputRef.current?.focus()
  }, [fieldErrors, hasCredentialError])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    if (isSubmittingRef.current) return

    const validationErrors = validateLogin(form)
    if (Object.keys(validationErrors).length > 0) {
      setFieldErrors(validationErrors)
      clearFeedback()
      return
    }

    try {
      isSubmittingRef.current = true
      setIsSubmitting(true)
      setFieldErrors({})
      clearFeedback()
      const response = await login({
        email: form.email.trim(),
        password: form.password,
      })
      clearOcrJobRecovery()
      localStorage.setItem('access_token', response.access_token)
      navigate('/')
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : NETWORK_ERROR_MESSAGE)
      setHasCredentialError(error instanceof ApiError && error.status === 401)
    } finally {
      isSubmittingRef.current = false
      setIsSubmitting(false)
    }
  }

  return (
    <div className="mvp-page mvp-auth-page mvp-login-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate('/start')}
        brandMark={<DoseyMascot variant="header" />}
        backPlacement="content"
        hideNavigation
      >
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-auth">
          <header className="mvp-auth__intro">
            <h1 className="mvp-page__title">다시 만나서 반가워요</h1>
            <p className="mvp-page__description">
              로그인하고 Dosey 도지에서 복약 관리를 이어가세요.
            </p>
          </header>

          <form className="mvp-form mvp-auth__form" onSubmit={handleSubmit} noValidate>
            <div className="mvp-auth__fields">
              <div className="mvp-form__field">
                <label htmlFor="login-email">이메일</label>
                <input
                  ref={emailInputRef}
                  id="login-email"
                  name="email"
                  type="email"
                  placeholder="가입한 이메일을 입력해 주세요"
                  autoComplete="email"
                  required
                  maxLength={40}
                  aria-invalid={Boolean(fieldErrors.email) || hasCredentialError}
                  aria-describedby={fieldErrors.email ? 'login-email-error' : undefined}
                  value={form.email}
                  onChange={handleChange}
                />
                {fieldErrors.email && (
                  <span className="mvp-form__field-error" id="login-email-error" role="alert">
                    {fieldErrors.email}
                  </span>
                )}
              </div>
              <div className="mvp-form__field">
                <label htmlFor="login-password">비밀번호</label>
                <input
                  ref={passwordInputRef}
                  id="login-password"
                  name="password"
                  type="password"
                  placeholder="비밀번호를 입력해 주세요"
                  autoComplete="current-password"
                  required
                  minLength={8}
                  aria-invalid={Boolean(fieldErrors.password) || hasCredentialError}
                  aria-describedby={fieldErrors.password ? 'login-password-error' : undefined}
                  value={form.password}
                  onChange={handleChange}
                />
                {fieldErrors.password && (
                  <span className="mvp-form__field-error" id="login-password-error" role="alert">
                    {fieldErrors.password}
                  </span>
                )}
              </div>
            </div>

            {message && <p className="mvp-form__message" role="alert">{message}</p>}

            <Button fullWidth type="submit" disabled={isSubmitting}>
              {isSubmitting ? '로그인 중...' : '로그인'}
            </Button>
          </form>

          <p className="mvp-form__footer mvp-auth__signup-link">
            <Link to="/signup">계정이 없다면 회원가입</Link>
          </p>

          <div className="notice attention mvp-auth__notice">
            의료정보는 로그인한 본인만 볼 수 있어요.
          </div>
        </main>
      </MobileShell>
    </div>
  )
}

export default LoginPage
