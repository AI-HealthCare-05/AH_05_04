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
    gender: 'MALE',
    birth_date: '',
    phone_number: '',
  })
  const [message, setMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleChange = (
    event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => {
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
              의료정보는 본인 확인과 동의 후 안전하게 관리합니다.
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
            <label className="mvp-form__field">
              성별
              <select name="gender" value={form.gender} onChange={handleChange}>
                <option value="MALE">남성</option>
                <option value="FEMALE">여성</option>
              </select>
            </label>
            <label className="mvp-form__field">
              생년월일
              <input name="birth_date" type="date" required value={form.birth_date} onChange={handleChange} />
            </label>
            <label className="mvp-form__field">
              휴대전화
              <input name="phone_number" type="tel" placeholder="010-1234-5678" required value={form.phone_number} onChange={handleChange} />
            </label>

            <div className="notice">
              서비스 이용약관, 개인정보 수집·이용, 민감정보 처리에 필수 동의합니다.
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
