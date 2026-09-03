import { useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { signup } from '../api/auth'
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
  const [form, setForm] = useState<SignupForm>({
    email: '',
    password: '',
    name: '',
  })
  const [fieldErrors, setFieldErrors] = useState<SignupFieldErrors>({})
  const [message, setMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const isSubmittingRef = useRef(false)
  const nameInputRef = useRef<HTMLInputElement>(null)
  const emailInputRef = useRef<HTMLInputElement>(null)
  const passwordInputRef = useRef<HTMLInputElement>(null)

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target
    const fieldName = name as keyof SignupForm
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

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    if (isSubmittingRef.current) return

    const validationErrors = validateSignup(form)
    if (Object.keys(validationErrors).length > 0) {
      setFieldErrors(validationErrors)
      setMessage('')
      focusField(Object.keys(validationErrors)[0] as keyof SignupForm | undefined)
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
      navigate('/login')
    } catch (error) {
      if (error instanceof ApiError) {
        const emailConflict = error.details.some(
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

            <Button fullWidth type="submit" disabled={isSubmitting}>
              {isSubmitting ? '가입 중...' : '가입 완료'}
            </Button>
          </form>
        </main>
      </MobileShell>
    </div>
  )
}

export default SignupPage
