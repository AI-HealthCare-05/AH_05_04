import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { signup } from '../api/auth'
import { ApiError } from '../api/client'
import { Button, MobileShell } from '../design-system/components'
import '../design-system/prototype.css'
import './MvpPages.css'

function SignupPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    email: '',
    password: '',
    name: '',
  })
  const [message, setMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target
    setForm((prev) => ({ ...prev, [name]: value }))
  }

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    try {
      setIsSubmitting(true)
      setMessage('')
      await signup(form)
      navigate('/login')
    } catch (error) {
      setMessage(
        error instanceof ApiError ? error.message : '회원가입에 실패했습니다.',
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="mvp-page mvp-auth-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate('/start')}
        backPlacement="content"
        hideNavigation
      >
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-auth">
          <header className="mvp-auth__intro">
            <h1 className="mvp-page__title">Dosey 도지를 시작해 볼까요?</h1>
            <p className="mvp-page__description">
              이름과 이메일로 Dosey 계정을 만들 수 있어요.
            </p>
          </header>

          <form className="mvp-form mvp-auth__form" onSubmit={handleSubmit}>
            <label className="mvp-form__field">
              이름
              <input name="name" placeholder="홍길동" required value={form.name} onChange={handleChange} />
            </label>
            <label className="mvp-form__field">
              이메일
              <input name="email" type="email" placeholder="hello@example.com" autoComplete="email" required value={form.email} onChange={handleChange} />
            </label>
            <label className="mvp-form__field">
              비밀번호
              <input name="password" type="password" placeholder="8자 이상 입력" autoComplete="new-password" required value={form.password} onChange={handleChange} />
            </label>
            <p className="mvp-form__help">
              8자 이상, 대문자·소문자·숫자·특수문자를 각각 1개 이상 포함해 주세요.
            </p>
            <div className="notice">
              서비스 이용약관과 개인정보 수집·이용 내용을 확인해 주세요.
            </div>

            {message && <p className="mvp-form__message" role="alert">{message}</p>}

            <Button fullWidth type="submit" disabled={isSubmitting}>
              {isSubmitting ? '가입 중...' : '가입 완료'}
            </Button>
          </form>

          <p className="mvp-form__footer">
            이미 계정이 있나요? <Link to="/login">로그인</Link>
          </p>
        </main>
      </MobileShell>
    </div>
  )
}

export default SignupPage
