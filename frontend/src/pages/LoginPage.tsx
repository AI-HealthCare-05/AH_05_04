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
    <div className="mvp-page">
      <MobileShell title="Dosey 도지" onBack={() => navigate('/')} hideNavigation>
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav">
          <p className="mvp-page__eyebrow">다시 만나서 반가워요</p>
          <h1 className="mvp-page__title">도지에 로그인해 주세요</h1>
          <p className="mvp-page__description">
            확인한 처방전과 복약 가이드를 이어서 볼 수 있어요.
          </p>

          <form className="mvp-form" onSubmit={handleSubmit}>
            <label className="mvp-form__field">
              이메일
              <input name="email" type="email" placeholder="hello@example.com" autoComplete="email" required value={form.email} onChange={handleChange} />
            </label>
            <label className="mvp-form__field">
              비밀번호
              <input name="password" type="password" placeholder="비밀번호 입력" autoComplete="current-password" required value={form.password} onChange={handleChange} />
            </label>

            {message && <p className="mvp-form__message" role="alert">{message}</p>}

            <Button fullWidth type="submit" disabled={isSubmitting}>
              {isSubmitting ? '로그인 중...' : '로그인'}
            </Button>
          </form>

          <p className="mvp-form__footer">
            처음이신가요? <Link to="/signup">회원가입</Link>
          </p>
        </main>
      </MobileShell>
    </div>
  )
}

export default LoginPage
