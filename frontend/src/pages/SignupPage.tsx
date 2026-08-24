import { useState } from 'react'
import { signup } from '../api/auth'

function SignupPage() {
  const [form, setForm] = useState({
    email: '',
    password: '',
    name: '',
  })

  const [message, setMessage] = useState('')

  const handleChange = (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const { name, value } = event.target

    setForm((prev) => ({
      ...prev,
      [name]: value,
    }))
  }

  const handleSubmit = async (
    event: React.FormEvent<HTMLFormElement>,
  ) => {
    event.preventDefault()

    try {
      const response = await signup(form)
      setMessage(response.detail)
    } catch {
      setMessage('회원가입에 실패했습니다.')
    }
  }

  return (
    <main>
      <h1>회원가입</h1>

      <form onSubmit={handleSubmit}>
        <input
          name="email"
          type="email"
          placeholder="이메일"
          value={form.email}
          onChange={handleChange}
        />

        <input
          name="password"
          type="password"
          placeholder="비밀번호"
          value={form.password}
          onChange={handleChange}
        />
        <p>8자 이상, 대문자·소문자·숫자·특수문자를 각각 1개 이상 포함해 주세요.</p>

        <input
          name="name"
          placeholder="이름"
          value={form.name}
          onChange={handleChange}
        />

        <button type="submit">
          가입하기
        </button>
      </form>

      {message && <p>{message}</p>}
    </main>
  )
}

export default SignupPage
