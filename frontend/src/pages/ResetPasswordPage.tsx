import { useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { confirmPasswordReset } from '../api/auth'
import { ApiError } from '../api/client'
import { Button, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

const PASSWORD_PATTERN = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^a-zA-Z0-9]).{8,72}$/
const PASSWORD_POLICY_MESSAGE = '8자 이상이며 대문자·소문자·숫자·특수문자를 포함해 주세요.'
const NETWORK_ERROR_MESSAGE = '네트워크 연결을 확인하고 다시 시도해 주세요.'
const INVALID_TOKEN_MESSAGE =
  '재설정 링크가 만료되었거나 이미 사용됐어요. 비밀번호 재설정을 다시 요청해 주세요.'

type ResetForm = {
  newPassword: string
  confirmPassword: string
}

type FieldErrors = Partial<Record<keyof ResetForm, string>>

function validate(form: ResetForm): FieldErrors {
  const errors: FieldErrors = {}
  if (!PASSWORD_PATTERN.test(form.newPassword)) {
    errors.newPassword = PASSWORD_POLICY_MESSAGE
  }
  if (form.confirmPassword !== form.newPassword) {
    errors.confirmPassword = '비밀번호가 일치하지 않아요.'
  }
  return errors
}

function hasErrorDetail(error: ApiError, field: string, reason: string): boolean {
  return error.details.some((detail) => detail.field === field && detail.reason === reason)
}

function ResetPasswordPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''

  const [form, setForm] = useState<ResetForm>({ newPassword: '', confirmPassword: '' })
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [message, setMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isComplete, setIsComplete] = useState(false)
  const [isTokenInvalid, setIsTokenInvalid] = useState(false)

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target
    setForm((prev) => ({ ...prev, [name]: value }))
    setFieldErrors((prev) => ({ ...prev, [name]: undefined }))
    setMessage('')
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isSubmitting || !token) return

    const validationErrors = validate(form)
    if (Object.keys(validationErrors).length > 0) {
      setFieldErrors(validationErrors)
      return
    }

    try {
      setIsSubmitting(true)
      setMessage('')
      setFieldErrors({})
      await confirmPasswordReset(token, form.newPassword)
      setIsComplete(true)
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        if (hasErrorDetail(error, 'token', 'RESET_TOKEN_INVALID')) {
          setIsTokenInvalid(true)
        } else if (hasErrorDetail(error, 'new_password', 'PASSWORD_POLICY_VIOLATION')) {
          setFieldErrors({ newPassword: PASSWORD_POLICY_MESSAGE })
        } else {
          setMessage(error.message)
        }
      } else {
        setMessage(error instanceof ApiError ? error.message : NETWORK_ERROR_MESSAGE)
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const linkIsMissingOrInvalid = !token || isTokenInvalid

  return (
    <div className="mvp-page mvp-auth-page mvp-reset-password-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate('/login')}
        brandMark={<DoseyMascot variant="header" />}
        backPlacement="content"
        hideNavigation
      >
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-auth">
          {isComplete ? (
            <>
              <header className="mvp-auth__intro">
                <h1 className="mvp-page__title">비밀번호가 변경되었어요</h1>
                <p className="mvp-page__description">
                  새 비밀번호로 다시 로그인해 주세요.
                </p>
              </header>
              <Button fullWidth onClick={() => navigate('/login')}>
                로그인하러 가기
              </Button>
            </>
          ) : linkIsMissingOrInvalid ? (
            <>
              <header className="mvp-auth__intro">
                <h1 className="mvp-page__title">재설정 링크를 확인할 수 없어요</h1>
                <p className="mvp-page__description">{INVALID_TOKEN_MESSAGE}</p>
              </header>
              <Button fullWidth onClick={() => navigate('/forgot-password')}>
                비밀번호 재설정 다시 요청하기
              </Button>
            </>
          ) : (
            <>
              <header className="mvp-auth__intro">
                <h1 className="mvp-page__title">새 비밀번호를 설정해 주세요</h1>
                <p className="mvp-page__description">
                  다른 사람이 추측하기 어려운 비밀번호를 입력해 주세요.
                </p>
              </header>

              <form className="mvp-form mvp-auth__form" onSubmit={handleSubmit} noValidate>
                <div className="mvp-auth__fields">
                  <div className="mvp-form__field">
                    <label htmlFor="reset-password-new">새 비밀번호</label>
                    <input
                      id="reset-password-new"
                      name="newPassword"
                      type="password"
                      placeholder="새 비밀번호를 입력해 주세요"
                      autoComplete="new-password"
                      required
                      minLength={8}
                      maxLength={72}
                      aria-invalid={Boolean(fieldErrors.newPassword)}
                      aria-describedby={fieldErrors.newPassword ? 'reset-password-new-error' : undefined}
                      value={form.newPassword}
                      onChange={handleChange}
                    />
                    {fieldErrors.newPassword && (
                      <span className="mvp-form__field-error" id="reset-password-new-error" role="alert">
                        {fieldErrors.newPassword}
                      </span>
                    )}
                  </div>
                  <div className="mvp-form__field">
                    <label htmlFor="reset-password-confirm">새 비밀번호 확인</label>
                    <input
                      id="reset-password-confirm"
                      name="confirmPassword"
                      type="password"
                      placeholder="새 비밀번호를 다시 입력해 주세요"
                      autoComplete="new-password"
                      required
                      minLength={8}
                      maxLength={72}
                      aria-invalid={Boolean(fieldErrors.confirmPassword)}
                      aria-describedby={fieldErrors.confirmPassword ? 'reset-password-confirm-error' : undefined}
                      value={form.confirmPassword}
                      onChange={handleChange}
                    />
                    {fieldErrors.confirmPassword && (
                      <span className="mvp-form__field-error" id="reset-password-confirm-error" role="alert">
                        {fieldErrors.confirmPassword}
                      </span>
                    )}
                  </div>
                </div>

                {message && <p className="mvp-form__message" role="alert">{message}</p>}

                <Button fullWidth type="submit" disabled={isSubmitting}>
                  {isSubmitting ? '변경 중...' : '비밀번호 변경하기'}
                </Button>
              </form>

              <p className="mvp-form__footer mvp-auth__signup-link">
                <Link to="/login">로그인으로 돌아가기</Link>
              </p>
            </>
          )}
        </main>
      </MobileShell>
    </div>
  )
}

export default ResetPasswordPage
