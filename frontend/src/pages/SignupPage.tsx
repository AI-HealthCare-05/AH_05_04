import { useState } from 'react'
import { signup } from '../api/auth'

function SignupPage() {
  const [form, setForm] = useState({
    email: '',
    password: '',
    name: '',
    gender: 'MALE',
    birth_date: '',
    phone_number: '',
  })

  const [message, setMessage] = useState('')

  const handleChange = (
    event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
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

        <select
          name="gender"
          value={form.gender}
          onChange={handleChange}
        >
          <option value="MALE">남성</option>
          <option value="FEMALE">여성</option>
        </select>

        <input
          name="birth_date"
          type="date"
          value={form.birth_date}
          onChange={handleChange}
        />

        <input
          name="phone_number"
          type="tel"
          placeholder="010-1234-5678"
          value={form.phone_number}
          onChange={handleChange}
        />
        <p>010-1234-5678, 01012345678 또는 +821012345678 형식으로 입력해 주세요.</p>

        <button type="submit">
          가입하기
        </button>
      </form>

      {message && <p>{message}</p>}
    </main>
  )
}

export default SignupPage
