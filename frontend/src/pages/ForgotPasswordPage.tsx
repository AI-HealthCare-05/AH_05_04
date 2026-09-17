import { useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { requestPasswordReset } from '../api/auth'
import { ApiError } from '../api/client'
import { Button, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const NETWORK_ERROR_MESSAGE = '네트워크 연결을 확인하고 다시 시도해 주세요.'

function validateEmail(email: string): string | undefined {
  const trimmed = email.trim()
  if (!trimmed) return '이메일을 입력해 주세요.'
  if (trimmed.length > 40 || !EMAIL_PATTERN.test(trimmed)) {
    return '올바른 이메일 주소를 40자 이하로 입력해 주세요.'
  }
  return undefined
}

function ForgotPasswordPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [emailError, setEmailError] = useState<string | undefined>()
  const [message, setMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isSent, setIsSent] = useState(false)

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    setEmail(event.target.value)
    setEmailError(undefined)
    setMessage('')
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isSubmitting) return

    const validationError = validateEmail(email)
    if (validationError) {
      setEmailError(validationError)
      return
    }

    try {
      setIsSubmitting(true)
      setMessage('')
      // PD-206 결정 3: 계정 존재 여부를 노출하지 않기 위해 항상 같은 성공 화면을 보여준다.
      await requestPasswordReset(email.trim())
      setIsSent(true)
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : NETWORK_ERROR_MESSAGE)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="mvp-page mvp-auth-page mvp-forgot-password-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate('/login')}
        brandMark={<DoseyMascot variant="header" />}
        backPlacement="content"
        hideNavigation
      >
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-auth">
          {isSent ? (
            <>
              <header className="mvp-auth__intro">
                <h1 className="mvp-page__title">이메일을 확인해 주세요</h1>
                <p className="mvp-page__description">
                  가입한 이메일 주소로 비밀번호 재설정 안내를 보냈어요.
                  받은 메일의 링크를 눌러 새 비밀번호를 설정할 수 있어요.
                </p>
              </header>
              <div className="notice attention mvp-auth__notice">
                이메일이 오지 않았다면 가입하지 않은 주소이거나, 스팸함에 있을 수 있어요.
              </div>
              <Button fullWidth onClick={() => navigate('/login')}>
                로그인으로 돌아가기
              </Button>
            </>
          ) : (
            <>
              <header className="mvp-auth__intro">
                <h1 className="mvp-page__title">비밀번호를 잊으셨나요</h1>
                <p className="mvp-page__description">
                  가입한 이메일 주소를 입력하면 비밀번호 재설정 안내를 보내드려요.
                </p>
              </header>

              <form className="mvp-form mvp-auth__form" onSubmit={handleSubmit} noValidate>
                <div className="mvp-auth__fields">
                  <div className="mvp-form__field">
                    <label htmlFor="forgot-password-email">이메일</label>
                    <input
                      id="forgot-password-email"
                      name="email"
                      type="email"
                      placeholder="가입한 이메일을 입력해 주세요"
                      autoComplete="email"
                      required
                      maxLength={40}
                      aria-invalid={Boolean(emailError)}
                      aria-describedby={emailError ? 'forgot-password-email-error' : undefined}
                      value={email}
                      onChange={handleChange}
                    />
                    {emailError && (
                      <span
                        className="mvp-form__field-error"
                        id="forgot-password-email-error"
                        role="alert"
                      >
                        {emailError}
                      </span>
                    )}
                  </div>
                </div>

                {message && <p className="mvp-form__message" role="alert">{message}</p>}

                <Button fullWidth type="submit" disabled={isSubmitting}>
                  {isSubmitting ? '전송 중...' : '재설정 안내 받기'}
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

export default ForgotPasswordPage
