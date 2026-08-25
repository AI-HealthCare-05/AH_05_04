import { useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { login } from '../api/auth'
import { ApiError } from '../api/client'
import { Button, MobileShell } from '../design-system/components'
import '../design-system/prototype.css'
import './MvpPages.css'

function LoginPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState({ email: '', password: '' })
  const [message, setMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target
    setForm((prev) => ({ ...prev, [name]: value }))
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    try {
      setIsSubmitting(true)
      setMessage('')
      const response = await login(form)
      localStorage.setItem('access_token', response.access_token)
      navigate('/')
    } catch (error) {
      setMessage(
        error instanceof ApiError ? error.message : '로그인에 실패했습니다.',
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
            <h1 className="mvp-page__title">다시 만나서 반가워요</h1>
            <p className="mvp-page__description">
              로그인하고 Dosey 도지에서 복약 관리를 이어가세요.
            </p>
          </header>

          <form className="mvp-form mvp-auth__form" onSubmit={handleSubmit}>
            <label className="mvp-form__field">
              이메일
              <input name="email" type="email" placeholder="가입한 이메일을 입력해 주세요" autoComplete="email" required value={form.email} onChange={handleChange} />
            </label>
            <label className="mvp-form__field">
              비밀번호
              <input name="password" type="password" placeholder="비밀번호를 입력해 주세요" autoComplete="current-password" required value={form.password} onChange={handleChange} />
            </label>

            {message && <p className="mvp-form__message" role="alert">{message}</p>}

            <Button fullWidth type="submit" disabled={isSubmitting}>
              {isSubmitting ? '로그인 중...' : '로그인'}
            </Button>
          </form>

          <div className="notice attention mvp-auth__notice">
            로그인 후 본인의 처방전과 복약 정보를 확인할 수 있어요.
          </div>

          <p className="mvp-form__footer">
            계정이 없다면 <Link to="/signup">회원가입</Link>
          </p>
        </main>
      </MobileShell>
    </div>
  )
}

export default LoginPage
