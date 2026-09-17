import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ApiError } from '../src/api/client'
import { requestAccountWithdrawal } from '../src/api/auth'
import {
  getUserConsents,
  grantUserConsent,
  withdrawUserConsent,
  CONSENT_PURPOSES,
  type UserConsent,
} from '../src/api/userConsents'
import {
  getCurrentUser,
  updateCurrentUser,
  type CurrentUser,
} from '../src/api/users'
import ProfilePage from '../src/pages/ProfilePage'
import StartPage from '../src/pages/StartPage'

vi.mock('../src/api/users', () => ({
  getCurrentUser: vi.fn(),
  updateCurrentUser: vi.fn(),
}))
vi.mock('../src/api/auth', () => ({
  requestAccountWithdrawal: vi.fn(),
}))
vi.mock('../src/api/userConsents', () => ({
  getUserConsents: vi.fn(),
  grantUserConsent: vi.fn(),
  withdrawUserConsent: vi.fn(),
  CONSENT_PURPOSES: ['OCR', 'GUIDE', 'CHAT', 'NOTIFICATION'],
}))

const CONSENTS: UserConsent[] = CONSENT_PURPOSES.map((purpose) => ({
  purpose, status: 'GRANTED', is_granted: true,
  current_policy_version: `${purpose}-server-v3`, policy_version: `${purpose}-server-v3`,
  granted_at: '2026-09-14T00:00:00Z', withdrawn_at: null, updated_at: '2026-09-14T00:00:00Z',
}))
const LABELS = ['처방전 외부 처리', '복약 가이드', '복약 챗봇', '알림']

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
        <Route path="/menu" element={<div>메뉴 화면</div>} />
        <Route path="/login" element={<div>로그인 화면</div>} />
        <Route path="/start" element={<StartPage />} />
        <Route path="/" element={<div>홈 화면</div>} />
        <Route path="/guides" element={<div>가이드 화면</div>} />
        <Route path="/chat" element={<div>도지 화면</div>} />
        <Route path="/schedule" element={<div>복약 일정 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function openEditForm() {
  await screen.findByText(CURRENT_USER.email)
  fireEvent.click(screen.getByRole('button', { name: '사용자 정보 수정' }))
}

async function openWithdrawalForm() {
  await screen.findByText(CURRENT_USER.email)
  fireEvent.click(screen.getByRole('button', { name: '회원탈퇴' }))
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  localStorage.setItem('access_token', 'fixture-token')
  vi.mocked(getCurrentUser).mockResolvedValue(CURRENT_USER)
  vi.mocked(updateCurrentUser).mockResolvedValue(CURRENT_USER)
  vi.mocked(requestAccountWithdrawal).mockResolvedValue({ detail: '회원탈퇴가 완료되었습니다.' })
  vi.mocked(getUserConsents).mockResolvedValue({ data: CONSENTS })
  vi.mocked(grantUserConsent).mockImplementation(async (purpose, version) => ({
    data: {
      ...CONSENTS.find((item) => item.purpose === purpose)!,
      policy_version: version,
      status: 'GRANTED',
      is_granted: true,
      granted_at: '2026-09-17T00:00:00Z',
      withdrawn_at: null,
    },
  }))
  vi.mocked(withdrawUserConsent).mockImplementation(async (purpose, version) => ({
    data: { ...CONSENTS.find((item) => item.purpose === purpose)!, policy_version: version,
      status: 'WITHDRAWN', is_granted: false, withdrawn_at: '2026-09-15T00:00:00Z' },
  }))
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
    expect(screen.queryByText('수정 가능')).toBeNull()
    expect(
      screen.getByText('휴대폰 번호, 생년월일, 성별은 선택 입력이에요.'),
    ).toBeTruthy()
    expect(getCurrentUser).toHaveBeenCalledTimes(1)
  })

  it('공통 Navigation의 Menu active와 production 일정 route, 기존 route 이동을 유지한다', async () => {
    const firstRender = renderProfile()

    await screen.findByText(CURRENT_USER.email)
    expect(screen.getByRole('button', { name: '메뉴' }).getAttribute('aria-current')).toBe(
      'page',
    )
    expect(screen.getByRole('button', { name: '일정' })).toHaveProperty('disabled', false)
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

describe('인증과 재접근', () => {
  it('GET 401/session expired 시 토큰과 민감정보를 지우고 로그인으로 이동한다', async () => {
    sessionStorage.setItem('dosey_chat_session:fixture-prescription', 'fixture-session')
    vi.mocked(getCurrentUser).mockRejectedValue(
      new ApiError(401, 'expired fixture', 'EXPIRED_TOKEN'),
    )
    renderProfile()

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem('dosey_chat_session:fixture-prescription')).toBeNull()
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

describe('회원탈퇴 요청', () => {
  it('최종 확인 전에는 CTA를 비활성화하고 API를 호출하지 않는다', async () => {
    renderProfile()
    await openWithdrawalForm()

    const passwordInput = screen.getByLabelText('현재 비밀번호')
    fireEvent.change(passwordInput, { target: { value: 'Password1!' } })

    expect(passwordInput.getAttribute('autocomplete')).toBe('current-password')
    expect(screen.getByRole('button', { name: '회원탈퇴 요청' })).toHaveProperty(
      'disabled',
      true,
    )
    expect(requestAccountWithdrawal).not.toHaveBeenCalled()
  })

  it('성공 응답 후에만 인증·사용자 세션을 정리하고 완료 상태를 표시한다', async () => {
    sessionStorage.setItem('dosey_chat_session:fixture-prescription', 'fixture-session')
    sessionStorage.setItem('dosey_ocr_job_recovery:v1', 'fixture-ocr-recovery')
    localStorage.setItem('dosey_web_push_binding:v1', 'fixture-push-binding')
    sessionStorage.setItem('unrelated-session', 'keep-me')
    renderProfile()
    await openWithdrawalForm()

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), {
      target: { value: 'Password1!' },
    })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: '회원탈퇴 요청' }))

    expect(await screen.findByText('회원탈퇴가 완료되었습니다.')).toBeTruthy()
    expect(requestAccountWithdrawal).toHaveBeenCalledWith('Password1!', 'fixture-token')
    expect(requestAccountWithdrawal).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem('dosey_chat_session:fixture-prescription')).toBeNull()
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).toBeNull()
    expect(localStorage.getItem('dosey_web_push_binding:v1')).toBeNull()
    expect(sessionStorage.getItem('unrelated-session')).toBe('keep-me')
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
    expect(screen.getByText(/삭제·보존은 서비스 정책에 따라 처리됩니다/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '시작 화면으로 이동' }))
    expect(screen.getByRole('heading', { name: '처방과 일정을 쉽게 살펴봐요.' })).toBeTruthy()
  })

  it('제출 중 입력과 CTA를 비활성화하고 중복 요청을 막는다', async () => {
    const pending = deferred<{ detail: string }>()
    vi.mocked(requestAccountWithdrawal).mockReturnValue(pending.promise)
    renderProfile()
    await openWithdrawalForm()

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), {
      target: { value: 'Password1!' },
    })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    const form = screen.getByLabelText('현재 비밀번호').closest('form')!
    fireEvent.submit(form)
    fireEvent.submit(form)

    expect(requestAccountWithdrawal).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '요청 중...' })).toHaveProperty('disabled', true)
    expect(screen.getByLabelText('현재 비밀번호')).toHaveProperty('disabled', true)

    pending.resolve({ detail: '회원탈퇴가 완료되었습니다.' })
    expect(await screen.findByText('회원탈퇴가 완료되었습니다.')).toBeTruthy()
  })

  it('실패 detail 200 응답은 완료 화면으로 소비하지 않는다', async () => {
    vi.mocked(requestAccountWithdrawal).mockResolvedValue({
      detail: '탈퇴 요청 처리에 실패했습니다. 관리자 확인이 필요합니다.',
    })
    sessionStorage.setItem('dosey_chat_session:fixture-prescription', 'fixture-session')
    sessionStorage.setItem('dosey_ocr_job_recovery:v1', 'fixture-ocr-recovery')
    localStorage.setItem('dosey_web_push_binding:v1', 'fixture-push-binding')
    renderProfile()
    await openWithdrawalForm()

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), {
      target: { value: 'Password1!' },
    })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: '회원탈퇴 요청' }))

    expect(await screen.findByText('회원탈퇴 처리를 완료하지 못했어요.')).toBeTruthy()
    expect(screen.getByText('관리자 확인이 필요합니다. 완료 화면으로 이동하지 않습니다.')).toBeTruthy()
    expect(screen.queryByText('회원탈퇴가 완료되었습니다.')).toBeNull()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem('dosey_chat_session:fixture-prescription')).toBeNull()
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).toBeNull()
    expect(localStorage.getItem('dosey_web_push_binding:v1')).toBeNull()
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
  })

  it('알 수 없는 200 detail은 완료 화면으로 소비하지 않는다', async () => {
    vi.mocked(requestAccountWithdrawal).mockResolvedValue({ detail: 'unknown fixture detail' })
    renderProfile()
    await openWithdrawalForm()

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), {
      target: { value: 'Password1!' },
    })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: '회원탈퇴 요청' }))

    expect(await screen.findByText('회원탈퇴 요청을 접수하지 못했어요.')).toBeTruthy()
    expect(screen.queryByText('회원탈퇴가 완료되었습니다.')).toBeNull()
    expect(localStorage.getItem('access_token')).toBe('fixture-token')
  })

  it('잘못된 비밀번호는 field 오류로 표시하고 인증 상태를 유지한다', async () => {
    vi.mocked(requestAccountWithdrawal).mockRejectedValue(
      new ApiError(401, 'raw credential fixture', 'UNAUTHORIZED'),
    )
    renderProfile()
    await openWithdrawalForm()

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), {
      target: { value: 'WrongPass1!' },
    })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: '회원탈퇴 요청' }))

    const error = await screen.findByText('비밀번호가 올바르지 않습니다.')
    expect(screen.getByLabelText('현재 비밀번호').getAttribute('aria-describedby')).toBe(error.id)
    expect(localStorage.getItem('access_token')).toBe('fixture-token')
    expect(screen.queryByText('회원탈퇴가 완료되었습니다.')).toBeNull()
  })

  it('INVALID_TOKEN 401은 인증 상태와 사용자 정보를 정리하고 로그인으로 이동한다', async () => {
    sessionStorage.setItem('dosey_chat_session:fixture-prescription', 'fixture-session')
    vi.mocked(requestAccountWithdrawal).mockRejectedValue(
      new ApiError(401, 'raw expired fixture', 'INVALID_TOKEN'),
    )
    renderProfile()
    await openWithdrawalForm()

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), {
      target: { value: 'Password1!' },
    })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: '회원탈퇴 요청' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem('dosey_chat_session:fixture-prescription')).toBeNull()
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
  })

  it('제출 직전 local token이 사라지면 API를 호출하지 않고 인증 만료로 처리한다', async () => {
    renderProfile()
    await openWithdrawalForm()

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), {
      target: { value: 'Password1!' },
    })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    localStorage.removeItem('access_token')
    fireEvent.click(screen.getByRole('button', { name: '회원탈퇴 요청' }))

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(requestAccountWithdrawal).not.toHaveBeenCalled()
    expect(screen.queryByText('비밀번호가 올바르지 않습니다.')).toBeNull()
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
  })

  it('요청 중 다른 보호 화면으로 이동해도 성공 응답 후 접수 완료 화면으로 전환한다', async () => {
    const pending = deferred<{ detail: string }>()
    vi.mocked(requestAccountWithdrawal).mockReturnValue(pending.promise)
    renderProfile()
    await openWithdrawalForm()

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), {
      target: { value: 'Password1!' },
    })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: '회원탈퇴 요청' }))
    fireEvent.click(screen.getByRole('button', { name: '홈' }))
    expect(screen.getByText('홈 화면')).toBeTruthy()

    pending.resolve({ detail: '회원탈퇴가 완료되었습니다.' })

    expect(await screen.findByText('회원탈퇴가 완료되었습니다.')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(screen.queryByText('홈 화면')).toBeNull()
    expect(screen.queryByText(CURRENT_USER.email)).toBeNull()
  })

  it('503 Gate OFF는 처리 불가 상태를 표시하고 인증·입력을 유지한다', async () => {
    vi.mocked(requestAccountWithdrawal).mockRejectedValue(
      new ApiError(503, 'raw gate fixture', 'SERVICE_UNAVAILABLE', [
        {
          field: 'account_withdrawal',
          reason: 'ACCOUNT_WITHDRAWAL_REQUEST_DISABLED',
        },
      ]),
    )
    renderProfile()
    await openWithdrawalForm()

    const passwordInput = screen.getByLabelText('현재 비밀번호')
    fireEvent.change(passwordInput, { target: { value: 'Password1!' } })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: '회원탈퇴 요청' }))

    expect(await screen.findByText('회원탈퇴 요청을 현재 처리할 수 없어요.')).toBeTruthy()
    expect((passwordInput as HTMLInputElement).value).toBe('Password1!')
    expect(localStorage.getItem('access_token')).toBe('fixture-token')
    expect(screen.queryByText('회원탈퇴가 완료되었습니다.')).toBeNull()
  })

  it.each([
    ['network', new Error('network unavailable')],
    ['server', new ApiError(500, 'raw server fixture', 'INTERNAL_SERVER_ERROR')],
  ])('%s 실패는 재시도 안내와 인증 상태를 유지한다', async (_label, failure) => {
    vi.mocked(requestAccountWithdrawal).mockRejectedValue(failure)
    renderProfile()
    await openWithdrawalForm()

    fireEvent.change(screen.getByLabelText('현재 비밀번호'), {
      target: { value: 'Password1!' },
    })
    fireEvent.click(
      screen.getByLabelText(
        '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: '회원탈퇴 요청' }))

    expect(await screen.findByText('회원탈퇴 요청을 접수하지 못했어요.')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBe('fixture-token')
    expect(screen.queryByText('raw server fixture')).toBeNull()
    expect(screen.queryByText('회원탈퇴가 완료되었습니다.')).toBeNull()
  })
})


describe('목적별 동의 관리', () => {
  it('미동의 상태에서 현재 정책 버전으로 다시 동의할 수 있다', async () => {
    vi.mocked(getUserConsents).mockResolvedValue({
     data: CONSENTS.map((item) =>
        item.purpose === 'OCR'
          ? {
              ...item,
              status: null,
              policy_version: null,
              is_granted: false,
              granted_at: null,
            }
           : item,
      ),
    })

    renderProfile()

    const button = await screen.findByRole('button', {
      name: '처방전 외부 처리 동의하기',
    })

    fireEvent.click(button)

    expect(
      await screen.findByText('처방전 외부 처리 동의를 저장했습니다.'),
    ).toBeTruthy()

    expect(grantUserConsent).toHaveBeenCalledWith(
      'OCR',
      'OCR-server-v3',
    )

    expect(
      screen.getAllByText('현재 동의한 상태입니다.'),
    ).toHaveLength(4)

    expect(
      screen.getByRole('button', {
        name: '처방전 외부 처리 동의 철회',
      }),
    ).toBeTruthy()
  })

  it('철회 상태에서도 다시 동의할 수 있다', async () => {
    vi.mocked(getUserConsents).mockResolvedValue({
      data: CONSENTS.map((item) =>
        item.purpose === 'GUIDE'
          ? {
              ...item,
              status: 'WITHDRAWN',
              is_granted: false,
            }
          : item,
      ),
    })

    renderProfile()

    fireEvent.click(
      await screen.findByRole('button', {
        name: '복약 가이드 동의하기',
      }),
    )

    expect(grantUserConsent).toHaveBeenCalledWith(
      'GUIDE',
      'GUIDE-server-v3',
    )

    expect(
      await screen.findByText('복약 가이드 동의를 저장했습니다.'),
    ).toBeTruthy()
  })
  it('재동의 정책 버전 불일치 시 전용 안내를 표시한다', async () => {
    vi.mocked(getUserConsents).mockResolvedValue({
      data: CONSENTS.map((item) =>
        item.purpose === 'OCR'
          ? {
              ...item,
              status: null,
              policy_version: null,
              is_granted: false,
              granted_at: null,
            }
          : item,
      ),
    })

    vi.mocked(grantUserConsent).mockRejectedValueOnce(
      new ApiError(
        422,
        'raw',
        'VALIDATION_FAILED',
        [
          {
            field: 'policy_version',
            reason: 'POLICY_VERSION_MISMATCH',
          },
        ],
      ),
    )

    renderProfile()

    fireEvent.click(
      await screen.findByRole('button', {
        name: '처방전 외부 처리 동의하기',
      }),
    )

    expect(
      await screen.findByText(
        '동의 정책이 변경되었습니다. 동의 상태를 다시 확인한 뒤 다시 동의해 주세요.',
      ),
    ).toBeTruthy()

    expect(screen.queryByText('raw')).toBeNull()

    expect(
      screen.getByText('동의 상태를 확인할 수 없습니다.'),
    ).toBeTruthy()
  })

  it('재동의 일반 실패 시 성공으로 표시하지 않고 해당 목적을 확인 불가 상태로 전환한다', async () => {
    vi.mocked(getUserConsents).mockResolvedValue({
      data: CONSENTS.map((item) =>
        item.purpose === 'GUIDE'
          ? {
              ...item,
              status: 'WITHDRAWN',
              is_granted: false,
            }
          : item,
      ),
    })

    vi.mocked(grantUserConsent).mockRejectedValueOnce(
      new Error('response lost'),
    )

    renderProfile()

    fireEvent.click(
      await screen.findByRole('button', {
        name: '복약 가이드 동의하기',
      }),
    )

    expect(
      await screen.findByText(
        '동의 저장 결과를 확인할 수 없어요. 현재 상태를 다시 확인해 주세요.',
      ),
    ).toBeTruthy()

    expect(
      screen.queryByText('복약 가이드 동의를 저장했습니다.'),
    ).toBeNull()

    expect(
      screen.getByText('동의 상태를 확인할 수 없습니다.'),
    ).toBeTruthy()
  })

  it('재동의 PUT 401에서 세션을 정리하고 로그인으로 이동한다', async () => {
    vi.mocked(getUserConsents).mockResolvedValue({
      data: CONSENTS.map((item) =>
        item.purpose === 'NOTIFICATION'
          ? {
              ...item,
              status: 'WITHDRAWN',
              is_granted: false,
            }
          : item,
      ),
    })

    vi.mocked(grantUserConsent).mockRejectedValueOnce(
      new ApiError(401, 'expired', 'EXPIRED_TOKEN'),
    )

    renderProfile()

    fireEvent.click(
      await screen.findByRole('button', {
        name: '알림 동의하기',
      }),
    )

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('구버전 GRANTED이지만 is_granted=false이면 현재 정책 버전으로 다시 동의할 수 있다', async () => {
    vi.mocked(getUserConsents).mockResolvedValue({
      data: CONSENTS.map((item) =>
        item.purpose === 'CHAT'
          ? {
              ...item,
              status: 'GRANTED',
              policy_version: 'old',
              is_granted: false,
            }
          : item,
      ),
    })

    renderProfile()

    fireEvent.click(
      await screen.findByRole('button', {
        name: '복약 챗봇 동의하기',
      }),
    )

    expect(grantUserConsent).toHaveBeenCalledWith(
      'CHAT',
      'CHAT-server-v3',
    )

    expect(
      await screen.findByText('복약 챗봇 동의를 저장했습니다.'),
    ).toBeTruthy()

    expect(
      screen.getAllByText('현재 동의한 상태입니다.'),
    ).toHaveLength(4)
  })
  it.each(CONSENT_PURPOSES)('%s만 서버 버전으로 철회하고 나머지 목적을 유지한다', async (purpose) => {
    renderProfile()
    await screen.findAllByText('현재 동의한 상태입니다.')
    const label = LABELS[CONSENT_PURPOSES.indexOf(purpose)]
    fireEvent.click(screen.getByRole('button', { name: `${label} 동의 철회` }))
    expect(await screen.findByText('철회한 상태입니다.')).toBeTruthy()
    expect(screen.getAllByText('현재 동의한 상태입니다.')).toHaveLength(3)
    expect(withdrawUserConsent).toHaveBeenCalledWith(purpose, `${purpose}-server-v3`)
  })

  it('미동의·철회·구버전 동의를 구분하며 서버 is_granted를 표시한다', async () => {
    vi.mocked(getUserConsents).mockResolvedValue({ data: [
      { ...CONSENTS[0], status: null, policy_version: null, is_granted: false, granted_at: null },
      { ...CONSENTS[1], status: 'WITHDRAWN', is_granted: false },
      { ...CONSENTS[2], policy_version: 'old', is_granted: false }, CONSENTS[3],
    ] })
    renderProfile()
    expect(await screen.findByText('동의한 내역이 없습니다.')).toBeTruthy()
    expect(screen.getByText('철회한 상태입니다.')).toBeTruthy()
    expect(screen.getByText('현재 유효한 동의가 없습니다.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '처방전 외부 처리 동의 철회' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '복약 챗봇 동의 철회' }))
    await waitFor(() => expect(withdrawUserConsent).toHaveBeenCalledWith('CHAT', 'CHAT-server-v3'))
  })

  it('OCR 정책 미설정에서도 저장된 버전으로 철회한다', async () => {
    vi.mocked(getUserConsents).mockResolvedValue({ data: CONSENTS.map((item) => ({
      ...item, current_policy_version: '', is_granted: false,
    })) })
    renderProfile()
    fireEvent.click(await screen.findByRole('button', { name: '처방전 외부 처리 동의 철회' }))
    expect(await screen.findByText('철회한 상태입니다.')).toBeTruthy()
    expect(withdrawUserConsent).toHaveBeenCalledWith('OCR', 'OCR-server-v3')
    expect(screen.queryByRole('button', { name: '복약 가이드 동의 철회' })).toBeNull()
  })

  it('조회 실패 시 버전을 추정하지 않고 재조회한 뒤 철회한다', async () => {
    vi.mocked(getUserConsents).mockRejectedValueOnce(new Error('private raw error'))
    renderProfile()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.queryByText('private raw error')).toBeNull()
    expect(withdrawUserConsent).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '동의 상태 다시 확인' }))
    expect(await screen.findAllByText('현재 동의한 상태입니다.')).toHaveLength(4)
  })

  it.each([
    new Error('response lost'),
    new ApiError(422, 'raw', 'VALIDATION_FAILED', [{ field: 'policy_version', reason: 'POLICY_VERSION_MISMATCH' }]),
    new ApiError(503, 'raw', 'CONSENT_POLICY_UNAVAILABLE'),
  ])('철회 실패를 성공으로 표시하지 않고 최신 조회값으로 복구한다: %s', async (error) => {
    vi.mocked(withdrawUserConsent).mockRejectedValueOnce(error)
    renderProfile()
    fireEvent.click(await screen.findByRole('button', { name: '복약 가이드 동의 철회' }))
    await screen.findByRole('alert')
    expect(screen.queryByText('철회한 상태입니다.')).toBeNull()
    expect(screen.getByText('동의 상태를 확인할 수 없습니다.')).toBeTruthy()
    expect(screen.getAllByText('현재 동의한 상태입니다.')).toHaveLength(3)
    vi.mocked(getUserConsents).mockResolvedValue({ data: CONSENTS.map((item) => ({ ...item, current_policy_version: 'latest-server-version' })) })
    fireEvent.click(screen.getByRole('button', { name: '동의 상태 다시 확인' }))
    fireEvent.click(await screen.findByRole('button', { name: '복약 가이드 동의 철회' }))
    await screen.findByText('철회한 상태입니다.')
    expect(withdrawUserConsent).toHaveBeenLastCalledWith('GUIDE', 'latest-server-version')
  })

  it('처리 중 중복 요청과 조회 경합을 막는다', async () => {
    const pending = deferred<{ data: UserConsent }>()
    vi.mocked(withdrawUserConsent).mockReturnValueOnce(pending.promise)
    renderProfile()
    const button = await screen.findByRole('button', { name: '알림 동의 철회' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(withdrawUserConsent).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '동의 상태 다시 확인' })).toHaveProperty('disabled', true)
    pending.resolve({ data: { ...CONSENTS[3], status: 'WITHDRAWN', is_granted: false } })
    await screen.findByText('철회한 상태입니다.')
  })

  it.each(['GET', 'PUT'])('%s 401에서 세션을 정리하고 로그인으로 이동한다', async (method) => {
    const error = new ApiError(401, 'expired', 'EXPIRED_TOKEN')
    if (method === 'GET') vi.mocked(getUserConsents).mockRejectedValueOnce(error)
    else vi.mocked(withdrawUserConsent).mockRejectedValueOnce(error)
    renderProfile()
    if (method === 'PUT') fireEvent.click(await screen.findByRole('button', { name: '알림 동의 철회' }))
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
  })
})

describe('#691 기본정보 선택 입력', () => {
  it('바뀐 필드만 보내고 손대지 않은 필드는 생략한다', async () => {
    vi.mocked(updateCurrentUser).mockResolvedValue({
      ...CURRENT_USER,
      gender: 'MALE',
    })
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('성별'), { target: { value: 'MALE' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect(await screen.findByText('내 정보가 저장되었습니다.')).toBeTruthy()
    expect(updateCurrentUser).toHaveBeenCalledWith({ gender: 'MALE' })
  })

  it('휴대폰 번호는 입력값을 가공하지 않고 그대로 보낸다', async () => {
    vi.mocked(updateCurrentUser).mockResolvedValue({
      ...CURRENT_USER,
      phone_number: '01012345678',
    })
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('휴대폰 번호'), {
      target: { value: '01012345678' },
    })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect(await screen.findByText('내 정보가 저장되었습니다.')).toBeTruthy()
    expect(updateCurrentUser).toHaveBeenCalledWith({ phone_number: '01012345678' })
  })

  it('생년월일을 수정하면 YYYY-MM-DD 로 보낸다', async () => {
    vi.mocked(updateCurrentUser).mockResolvedValue({
      ...CURRENT_USER,
      birthday: '1990-01-02',
    })
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('생년월일'), {
      target: { value: '1990-01-02' },
    })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect(await screen.findByText('내 정보가 저장되었습니다.')).toBeTruthy()
    expect(updateCurrentUser).toHaveBeenCalledWith({ birthday: '1990-01-02' })
  })

  it('값을 비우고 저장하면 null 을 보내 미입력으로 되돌린다', async () => {
    vi.mocked(updateCurrentUser).mockResolvedValue({
      ...CURRENT_USER,
      phone_number: null,
      birthday: null,
      gender: null,
    })
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('휴대폰 번호'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('생년월일'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('성별'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect(await screen.findByText('내 정보가 저장되었습니다.')).toBeTruthy()
    expect(updateCurrentUser).toHaveBeenCalledWith({
      phone_number: null,
      birthday: null,
      gender: null,
    })
    expect(screen.getAllByText('미입력').length).toBeGreaterThan(0)
  })

  it('휴대폰 번호 409 ALREADY_EXISTS 를 해당 field 오류로 표시하고 입력을 유지한다', async () => {
    vi.mocked(updateCurrentUser).mockRejectedValue(
      new ApiError(409, 'conflict', 'CONFLICT', [
        { field: 'phone_number', reason: 'ALREADY_EXISTS' },
      ]),
    )
    renderProfile()
    await openEditForm()

    const phoneInput = screen.getByLabelText('휴대폰 번호')
    fireEvent.change(phoneInput, { target: { value: '01099998888' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect(
      await screen.findByText('이미 등록된 휴대폰 번호예요. 다른 번호를 입력해 주세요.'),
    ).toBeTruthy()
    expect(screen.getByLabelText('휴대폰 번호')).toHaveProperty('value', '01099998888')
    expect(document.activeElement).toBe(screen.getByLabelText('휴대폰 번호'))
  })

  it('휴대폰 번호 422 는 raw message 대신 안내 문구로 표시한다', async () => {
    vi.mocked(updateCurrentUser).mockRejectedValue(
      new ApiError(422, 'phone_number must contain digits only', 'VALIDATION_FAILED', [
        { field: 'phone_number', reason: 'INVALID' },
      ]),
    )
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('휴대폰 번호'), { target: { value: '010abc' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect(await screen.findByText('휴대폰 번호는 010으로 시작하는 11자리 숫자로 입력해 주세요.')).toBeTruthy()
    expect(screen.queryByText(/digits only/)).toBeNull()
  })

  it('5xx 에서는 입력값을 유지하고 재시도를 안내한다', async () => {
    vi.mocked(updateCurrentUser).mockRejectedValue(
      new ApiError(500, 'server error', 'INTERNAL_SERVER_ERROR'),
    )
    renderProfile()
    await openEditForm()

    fireEvent.change(screen.getByLabelText('성별'), { target: { value: 'MALE' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))

    expect(
      await screen.findByText(
        '내 정보를 저장하지 못했습니다. 입력값을 유지한 채 다시 시도해 주세요.',
      ),
    ).toBeTruthy()
    expect(screen.getByLabelText('성별')).toHaveProperty('value', 'MALE')
  })
})
