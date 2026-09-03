import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { logout } from '../src/api/auth'
import { ApiError } from '../src/api/client'
import {
  getCurrentUser,
  updateCurrentUser,
  type CurrentUser,
} from '../src/api/users'
import ProfilePage from '../src/pages/ProfilePage'

vi.mock('../src/api/users', () => ({
  getCurrentUser: vi.fn(),
  updateCurrentUser: vi.fn(),
}))

vi.mock('../src/api/auth', () => ({
  logout: vi.fn(),
}))

const CURRENT_USER: CurrentUser = {
  id: '00000000-0000-4000-8000-000000000097',
  name: '테스트 사용자',
  email: 'profile@example.com',
  phone_number: '010-0000-0097',
  birthday: '1997-09-07',
  gender: 'FEMALE',
  created_at: '2026-08-27T00:00:00Z',
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function renderProfile() {
  return render(
    <MemoryRouter initialEntries={['/profile']}>
      <Routes>
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/login" element={<div>로그인 화면</div>} />
        <Route path="/" element={<div>홈 화면</div>} />
        <Route path="/guides" element={<div>가이드 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function openEditForm() {
  await screen.findByText(CURRENT_USER.email)
  fireEvent.click(screen.getByRole('button', { name: '이름·이메일 수정' }))
}

beforeEach(() => {
  localStorage.setItem('access_token', 'fixture-token')
  vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
  vi.mocked(updateCurrentUser).mockResolvedValue(CURRENT_USER)
  vi.mocked(logout).mockResolvedValue({ detail: '로그아웃되었습니다.' })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('내 정보 조회', () => {
  it('GET users/me 정상 응답으로 본인 정보를 표시한다', async () => {
    renderProfile()

    expect(await screen.findByText(CURRENT_USER.email)).toBeTruthy()
    expect(screen.getByText(CURRENT_USER.phone_number!)).toBeTruthy()
    expect(screen.getByText(CURRENT_USER.birthday!)).toBeTruthy()
    expect(screen.getByText('여성')).toBeTruthy()
    expect(getCurrentUser).toHaveBeenCalledTimes(1)
  })

  it('공통 Navigation의 Menu active, 일정 disabled, 기존 route 이동을 유지한다', async () => {
    const firstRender = renderProfile()

    await screen.findByText(CURRENT_USER.email)
    expect(screen.getByRole('button', { name: '메뉴' }).getAttribute('aria-current')).toBe(
      'page',
    )
    expect(screen.getByRole('button', { name: '일정 (준비 중)' })).toHaveProperty(
      'disabled',
      true,
    )
    fireEvent.click(screen.getByRole('button', { name: '가이드' }))
    expect(screen.getByText('가이드 화면')).toBeTruthy()

    firstRender.unmount()
    renderProfile()
    await screen.findByText(CURRENT_USER.email)
    fireEvent.click(screen.getByRole('button', { name: '홈' }))
    expect(screen.getByText('홈 화면')).toBeTruthy()
  })

  it('nullable 필드를 조회 실패가 아닌 미입력으로 표시한다', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue({
      ...CURRENT_USER,
      phone_number: null,
      birthday: null,
      gender: null,
    })
    renderProfile()

    expect(await screen.findAllByText('미입력')).toHaveLength(3)
    expect(screen.queryByText('내 정보를 불러올 수 없어요')).toBeNull()
  })

  it('GET 완료 전 loading 상태를 표시한다', () => {
    vi.mocked(getCurrentUser).mockReturnValue(new Promise(() => undefined))
    renderProfile()

    expect(screen.getByRole('status').textContent).toContain('불러오는 중')
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
  })

  it('GET 실패를 빈 상태로 숨기지 않고 재시도한다', async () => {
    vi.mocked(getCurrentUser)
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce(CURRENT_USER)
    renderProfile()

    expect((await screen.findByRole('alert')).textContent).toContain('불러오지 못했습니다')
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))

    expect(await screen.findByText(CURRENT_USER.email)).toBeTruthy()
    expect(getCurrentUser).toHaveBeenCalledTimes(2)
  })
})

describe('내 정보 수정', () => {
  it('PATCH 성공 응답의 최신 값으로 화면을 갱신한다', async () => {
    const updatedUser = {
      ...CURRENT_USER,
      name: '변경 사용자',
      email: 'updated@example.com',
    }
    vi.mocked(updateCurrentUser).mockResolvedValue(updatedUser)
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('이름'), {
      target: { value: '변경 사용자' },
    })
    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'updated@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect(await screen.findByText('내 정보가 저장되었습니다.')).toBeTruthy()
    expect(screen.getByText('updated@example.com')).toBeTruthy()
    expect(updateCurrentUser).toHaveBeenCalledWith({
      name: '변경 사용자',
      email: 'updated@example.com',
    })
  })

  it('validation 오류를 해당 field와 연결하고 첫 오류로 focus를 이동한다', async () => {
    renderProfile()
    await openEditForm()

    const nameInput = screen.getByLabelText('이름')
    fireEvent.change(nameInput, { target: { value: '한' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    const error = screen.getByText('이름은 2자 이상 20자 이하로 입력해 주세요.')
    expect(nameInput.getAttribute('aria-describedby')).toBe(error.id)
    expect(nameInput.getAttribute('aria-invalid')).toBe('true')
    await waitFor(() => expect(document.activeElement).toBe(nameInput))
    expect(updateCurrentUser).not.toHaveBeenCalled()
  })

  it('계약된 409 CONFLICT detail을 email field 오류로 표시한다', async () => {
    vi.mocked(updateCurrentUser).mockRejectedValue(
      new ApiError(409, 'conflict fixture', 'CONFLICT', [
        { field: 'email', reason: 'ALREADY_EXISTS' },
      ]),
    )
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'duplicate@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    const error = await screen.findByText('이미 사용 중인 이메일입니다.')
    expect(screen.getByLabelText('이메일').getAttribute('aria-describedby')).toBe(error.id)
  })

  it('계약된 422 VALIDATION_FAILED detail을 해당 field 오류로 표시한다', async () => {
    vi.mocked(updateCurrentUser).mockRejectedValue(
      new ApiError(422, 'validation fixture', 'VALIDATION_FAILED', [
        { field: 'name', reason: 'INVALID_FORMAT' },
      ]),
    )
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('이름'), {
      target: { value: '유효한 입력' },
    })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    const error = await screen.findByText('이름을 확인해 주세요.')
    expect(screen.getByLabelText('이름').getAttribute('aria-describedby')).toBe(error.id)
  })

  it('PATCH 네트워크 실패 후 사용자의 미저장 입력을 유지한다', async () => {
    vi.mocked(updateCurrentUser).mockRejectedValue(new Error('network unavailable'))
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('이름'), {
      target: { value: '미저장 사용자' },
    })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect((await screen.findByRole('alert')).textContent).toContain('입력값을 유지')
    expect((screen.getByLabelText('이름') as HTMLInputElement).value).toBe('미저장 사용자')
  })

  it('저장 중 입력과 버튼을 disabled하고 중복 제출을 막는다', async () => {
    const pending = deferred<CurrentUser>()
    vi.mocked(updateCurrentUser).mockReturnValue(pending.promise)
    renderProfile()
    await openEditForm()

    const form = screen.getByLabelText('이름').closest('form')!
    fireEvent.submit(form)
    fireEvent.submit(form)

    expect(updateCurrentUser).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '저장 중...' })).toHaveProperty('disabled', true)
    expect(screen.getByLabelText('이름')).toHaveProperty('disabled', true)

    pending.resolve(CURRENT_USER)
    expect(await screen.findByText('내 정보가 저장되었습니다.')).toBeTruthy()
  })
})

describe('로그아웃', () => {
  it('logout API 성공 후 토큰을 지우고 로그인 화면으로 이동한다', async () => {
    renderProfile()
    await screen.findByText(CURRENT_USER.email)

    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(logout).toHaveBeenCalledTimes(1)
  })

  it('logout API가 네트워크 오류로 실패해도 로컬 자격증명을 지우고 로그인 화면으로 이동한다', async () => {
    vi.mocked(logout).mockRejectedValue(new Error('network unavailable'))
    renderProfile()
    await screen.findByText(CURRENT_USER.email)

    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('logout API가 5xx로 실패해도 로컬 자격증명을 지우고 로그인 화면으로 이동한다', async () => {
    vi.mocked(logout).mockRejectedValue(new ApiError(500, 'server fixture', 'INTERNAL_SERVER_ERROR'))
    renderProfile()
    await screen.findByText(CURRENT_USER.email)

    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('로그아웃 처리 중 중복 클릭해도 logout API를 한 번만 호출한다', async () => {
    const pending = deferred<{ detail: string }>()
    vi.mocked(logout).mockReturnValue(pending.promise)
    renderProfile()
    await screen.findByText(CURRENT_USER.email)

    const logoutButton = screen.getByRole('button', { name: '로그아웃' })
    fireEvent.click(logoutButton)
    fireEvent.click(logoutButton)
    fireEvent.click(logoutButton)

    expect(logout).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '로그아웃 중...' })).toHaveProperty('disabled', true)

    pending.resolve({ detail: '로그아웃되었습니다.' })
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
  })

  it('로그아웃 후 보호된 route를 다시 렌더링하면 API 호출 없이 로그인 화면으로 보낸다', async () => {
    const rendered = renderProfile()
    await screen.findByText(CURRENT_USER.email)

    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }))
    expect(await screen.findByText('로그인 화면')).toBeTruthy()

    vi.mocked(getCurrentUser).mockClear()
    rendered.unmount()
    renderProfile()

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(getCurrentUser).not.toHaveBeenCalled()
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
  })
})

describe('인증과 재접근', () => {
  it('GET 401/session expired 시 토큰과 민감정보를 지우고 로그인으로 이동한다', async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(
      new ApiError(401, 'expired fixture', 'EXPIRED_TOKEN'),
    )
    renderProfile()

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
  })

  it('PATCH 중 401 시 기존 조회 정보까지 숨기고 로그인으로 이동한다', async () => {
    vi.mocked(updateCurrentUser).mockRejectedValue(
      new ApiError(401, 'invalid fixture', 'INVALID_TOKEN'),
    )
    renderProfile()
    await openEditForm()
    fireEvent.change(screen.getByLabelText('이메일'), {
      target: { value: 'unsaved@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
    expect(screen.queryByDisplayValue('unsaved@example.com')).toBeNull()
  })

  it('새로고침에 해당하는 재마운트에서 최신 서버값을 다시 조회한다', async () => {
    vi.mocked(getCurrentUser)
      .mockResolvedValueOnce(CURRENT_USER)
      .mockResolvedValueOnce({
        ...CURRENT_USER,
        name: '서버 최신 사용자',
        email: 'latest@example.com',
      })

    const firstRender = renderProfile()
    expect(await screen.findByText(CURRENT_USER.email)).toBeTruthy()
    firstRender.unmount()

    renderProfile()
    expect(await screen.findByText('latest@example.com')).toBeTruthy()
    expect(getCurrentUser).toHaveBeenCalledTimes(2)
  })

  it('token 없이 직접 route 접근하면 API를 호출하거나 민감정보를 노출하지 않는다', async () => {
    localStorage.removeItem('access_token')
    renderProfile()

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(getCurrentUser).not.toHaveBeenCalled()
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
  })
})
