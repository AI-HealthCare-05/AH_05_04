import { useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { signup, requestEmailVerification, confirmEmailVerification } from '../api/auth'
import type { SignupConsentPurpose } from '../api/auth'
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
const CONSENT_POLICY_UNAVAILABLE_MESSAGE =
  '선택한 기능의 동의 안내를 준비하고 있어요. 해당 선택을 해제하거나 잠시 후 다시 시도해 주세요.'
const CONSENT_OPTIONS: ReadonlyArray<{
  purpose: SignupConsentPurpose
  label: string
  description: string
}> = [
  {
    purpose: 'OCR',
    label: '처방전 인식',
    description: '처방전 인식에는 외부 OCR 서비스가 사용됩니다.',
  },
  {
    purpose: 'GUIDE',
    label: '복약 안내',
    description: '복약 안내 생성에는 외부 AI 서비스가 사용됩니다.',
  },
  {
    purpose: 'CHAT',
    label: '도지에게 질문',
    description: '답변 생성에는 질문과 필요한 복약정보가 외부 AI 서비스로 전달됩니다.',
  },
  {
    purpose: 'NOTIFICATION',
    label: '복약 알림',
    description: '동의한 설정에 따라 복약 알림을 보내드립니다.',
  },
]

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
  const [selectedConsentPurposes, setSelectedConsentPurposes] = useState<SignupConsentPurpose[]>([])
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

  const toggleConsent = (purpose: SignupConsentPurpose) => {
    setSelectedConsentPurposes((current) =>
      current.includes(purpose)
        ? current.filter((value) => value !== purpose)
        : [...current, purpose],
    )
    setMessage('')
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
        consents: selectedConsentPurposes.map((purpose) => ({ purpose })),
      })
      navigate('/login', {
        state: { fromSignup: true },
      })
    } catch (error) {
      if (error instanceof ApiError) {
        const consentPolicyUnavailable =
          error.status === 503 && error.code === 'CONSENT_POLICY_UNAVAILABLE'
        const emailConflict = (error.status === 409 && error.code === 'CONFLICT') || error.details.some(
          (detail) => detail.field === 'email' && detail.reason === 'ALREADY_EXISTS',
        )

        if (consentPolicyUnavailable) {
          setMessage(CONSENT_POLICY_UNAVAILABLE_MESSAGE)
        } else if (emailConflict) {
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
              기능별 동의 상태에 따라 필요한 정보만 처리합니다.
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
            <fieldset className="mvp-signup-consents">
              <legend>기능별 선택 동의</legend>
              <p className="mvp-signup-consents__intro">
                선택하지 않아도 가입할 수 있습니다. 선택한 기능만 동의 상태로 저장합니다.
              </p>
              {CONSENT_OPTIONS.map((option) => (
                <label className="mvp-signup-consents__option" key={option.purpose}>
                  <input
                    type="checkbox"
                    checked={selectedConsentPurposes.includes(option.purpose)}
                    disabled={isSubmitting || verificationBusy}
                    onChange={() => toggleConsent(option.purpose)}
                  />
                  <span>
                    <strong>{option.label}</strong>
                    <small>{option.description}</small>
                  </span>
                </label>
              ))}
            </fieldset>

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
