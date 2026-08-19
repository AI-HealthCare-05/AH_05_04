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
          placeholder="전화번호"
          value={form.phone_number}
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
