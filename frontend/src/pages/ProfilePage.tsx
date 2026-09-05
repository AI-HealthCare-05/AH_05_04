import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { logout } from '../api/auth'
import { ApiError } from '../api/client'
import {
  getCurrentUser,
  updateCurrentUser,
  type CurrentUser,
} from '../api/users'
import { Button, Card, MobileShell } from '../design-system/components'
import '../design-system/prototype.css'
import './MvpPages.css'
import './ProfilePage.css'

type ProfileForm = {
  name: string
  email: string
}

type FieldErrors = Partial<Record<keyof ProfileForm, string>>

const EMPTY_FORM: ProfileForm = { name: '', email: '' }
const AUTH_ERROR_CODES = new Set(['UNAUTHORIZED', 'INVALID_TOKEN', 'EXPIRED_TOKEN'])

function isAuthenticationError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 401 || AUTH_ERROR_CODES.has(error.code))
  )
}

function validateForm(form: ProfileForm): FieldErrors {
  const errors: FieldErrors = {}
  const name = form.name.trim()
  const email = form.email.trim()

  if (name.length < 2 || name.length > 20) {
    errors.name = '이름은 2자 이상 20자 이하로 입력해 주세요.'
  }

  if (!email) {
    errors.email = '이메일을 입력해 주세요.'
  } else if (email.length > 40 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    errors.email = '올바른 이메일 주소를 40자 이하로 입력해 주세요.'
  }

  return errors
}

function serverFieldErrors(error: ApiError): FieldErrors {
  const errors: FieldErrors = {}

  for (const detail of error.details) {
    if (detail.field === 'email') {
      errors.email =
        detail.reason === 'ALREADY_EXISTS'
          ? '이미 사용 중인 이메일입니다.'
          : '이메일을 확인해 주세요.'
    }

    if (detail.field === 'name') {
      errors.name = '이름을 확인해 주세요.'
    }
  }

  return errors
}

function nullableValue(value: string | null): string {
  return value?.trim() || '미입력'
}

function genderLabel(gender: CurrentUser['gender']): string {
  if (gender === 'MALE') return '남성'
  if (gender === 'FEMALE') return '여성'
  return '미입력'
}

function ProfilePage() {
  const navigate = useNavigate()
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [form, setForm] = useState<ProfileForm>(EMPTY_FORM)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [loadError, setLoadError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [successMessage, setSuccessMessage] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [isEditing, setIsEditing] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const nameInputRef = useRef<HTMLInputElement>(null)
  const emailInputRef = useRef<HTMLInputElement>(null)

  const clearFeedback = useCallback(() => {
    setSaveError('')
    setSuccessMessage('')
  }, [])

  const expireSession = useCallback(() => {
    localStorage.removeItem('access_token')
    setUser(null)
    setForm(EMPTY_FORM)
    setFieldErrors({})
    setLoadError('')
    setSaveError('')
    setSuccessMessage('')
    navigate('/login', { replace: true })
  }, [navigate])

  const loadProfile = useCallback(async () => {
    if (!localStorage.getItem('access_token')) {
      expireSession()
      return
    }

    setIsLoading(true)
    setLoadError('')
    setSuccessMessage('')

    try {
      const response = await getCurrentUser()
      setUser(response)
      setForm({ name: response.name, email: response.email })
    } catch (error) {
      if (isAuthenticationError(error)) {
        expireSession()
        return
      }

      setUser(null)
      setLoadError('내 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.')
    } finally {
      setIsLoading(false)
    }
  }, [expireSession])

  useEffect(() => {
    void loadProfile()
  }, [loadProfile])

  useEffect(() => {
    const firstError = Object.keys(fieldErrors)[0] as keyof ProfileForm | undefined
    if (firstError === 'name') nameInputRef.current?.focus()
    if (firstError === 'email') emailInputRef.current?.focus()
  }, [fieldErrors])

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const field = event.target.name as keyof ProfileForm
    setForm((current) => ({ ...current, [field]: event.target.value }))
    setFieldErrors((current) => {
      const nextErrors = { ...current }
      delete nextErrors[field]
      return nextErrors
    })
    clearFeedback()
  }

  const startEditing = () => {
    if (!user) return
    setForm({ name: user.name, email: user.email })
    setFieldErrors({})
    clearFeedback()
    setIsEditing(true)
  }

  const cancelEditing = () => {
    if (user) setForm({ name: user.name, email: user.email })
    setFieldErrors({})
    setSaveError('')
    setIsEditing(false)
  }

  const handleLogout = () => {
    if (isLoggingOut) return

    setIsLoggingOut(true)
    clearFeedback()

    // 계정 생명주기 계약(PD-206): 서버 요청이 실패하거나 응답 없이 계속 pending이어도
    // 로컬 자격증명을 먼저 제거합니다. await하면 요청이 끝내 settle되지 않는 경우(네트워크
    // 행, 서버 무응답) expireSession()이 영원히 호출되지 않으므로, 서버 호출은
    // fire-and-forget으로 분리하고 로컬 세션 정리는 이 요청의 완료를 기다리지 않고
    // 즉시 실행합니다. logout()이 Authorization 헤더에 쓸 access_token을 이 호출 시점에
    // 이미 읽으므로, expireSession()이 뒤이어 그 값을 지워도 요청에는 영향이 없습니다.
    logout().catch(() => {
      // 네트워크 오류·5xx 등 서버 로그아웃 요청이 실패해도 로컬 세션은 이미 제거됩니다.
    })

    expireSession()
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isSaving) return

    const validationErrors = validateForm(form)
    if (Object.keys(validationErrors).length > 0) {
      setFieldErrors(validationErrors)
      return
    }

    setIsSaving(true)
    setFieldErrors({})
    clearFeedback()

    try {
      const response = await updateCurrentUser({
        name: form.name.trim(),
        email: form.email.trim(),
      })
      setUser(response)
      setForm({ name: response.name, email: response.email })
      setIsEditing(false)
      setSuccessMessage('내 정보가 저장되었습니다.')
    } catch (error) {
      if (isAuthenticationError(error)) {
        expireSession()
        return
      }

      if (
        error instanceof ApiError &&
        ((error.status === 422 && error.code === 'VALIDATION_FAILED') ||
          (error.status === 409 && error.code === 'CONFLICT'))
      ) {
        const errors = serverFieldErrors(error)
        if (Object.keys(errors).length > 0) {
          setFieldErrors(errors)
        } else {
          setSaveError('입력값을 확인해 주세요.')
        }
      } else {
        setSaveError('내 정보를 저장하지 못했습니다. 입력값을 유지한 채 다시 시도해 주세요.')
      }
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="mvp-page mvp-profile-page">
      <MobileShell
        title="내 정보"
        activeNavigation="메뉴"
        disabledNavigation={['일정']}
        onBack={() => navigate('/')}
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
          if (item === '메뉴') navigate('/profile')
        }}
      >
        <main className="app-scroll mvp-page__content mvp-profile">
          {isLoading && (
            <Card className="mvp-profile__state-card">
              <div role="status" aria-live="polite">
                <span className="mvp-profile__spinner" aria-hidden="true" />
                <p>내 정보를 불러오는 중입니다.</p>
              </div>
            </Card>
          )}

          {!isLoading && loadError && (
            <Card className="mvp-profile__state-card" aria-live="assertive">
              <div role="alert">
                <h2>내 정보를 불러올 수 없어요</h2>
                <p>{loadError}</p>
              </div>
              <Button fullWidth onClick={() => void loadProfile()}>
                다시 시도
              </Button>
            </Card>
          )}

          {!isLoading && user && (
            <>
              <header className="mvp-profile__intro">
                <p className="mvp-page__eyebrow">ACCOUNT</p>
                <h2 className="mvp-page__title">내 정보</h2>
                <p className="mvp-page__description">
                  계정에 등록된 이름과 이메일을 확인하고 수정할 수 있어요.
                </p>
              </header>

              {successMessage && (
                <p className="mvp-profile__success" role="status" aria-live="polite">
                  {successMessage}
                </p>
              )}

              {isEditing ? (
                <Card className="mvp-profile__card">
                  <form className="mvp-form" onSubmit={handleSubmit} noValidate>
                    <div className="mvp-form__field">
                      <label htmlFor="profile-name">이름</label>
                      <input
                        ref={nameInputRef}
                        id="profile-name"
                        name="name"
                        type="text"
                        autoComplete="name"
                        minLength={2}
                        maxLength={20}
                        value={form.name}
                        onChange={handleChange}
                        disabled={isSaving}
                        aria-invalid={Boolean(fieldErrors.name)}
                        aria-describedby={fieldErrors.name ? 'profile-name-error' : undefined}
                      />
                      {fieldErrors.name && (
                        <p id="profile-name-error" className="mvp-profile__field-error">
                          {fieldErrors.name}
                        </p>
                      )}
                    </div>

                    <div className="mvp-form__field">
                      <label htmlFor="profile-email">이메일</label>
                      <input
                        ref={emailInputRef}
                        id="profile-email"
                        name="email"
                        type="email"
                        autoComplete="email"
                        maxLength={40}
                        value={form.email}
                        onChange={handleChange}
                        disabled={isSaving}
                        aria-invalid={Boolean(fieldErrors.email)}
                        aria-describedby={fieldErrors.email ? 'profile-email-error' : undefined}
                      />
                      {fieldErrors.email && (
                        <p id="profile-email-error" className="mvp-profile__field-error">
                          {fieldErrors.email}
                        </p>
                      )}
                    </div>

                    {saveError && (
                      <p className="mvp-form__message" role="alert">
                        {saveError}
                      </p>
                    )}

                    <div className="mvp-profile__actions">
                      <Button
                        variant="secondary"
                        type="button"
                        onClick={cancelEditing}
                        disabled={isSaving}
                      >
                        취소
                      </Button>
                      <Button type="submit" disabled={isSaving} aria-busy={isSaving}>
                        {isSaving ? '저장 중...' : '저장'}
                      </Button>
                    </div>
                  </form>
                </Card>
              ) : (
                <>
                  <Card className="mvp-profile__card">
                    <div className="mvp-profile__identity">
                      <span aria-hidden="true">{user.name.slice(0, 1)}</span>
                      <div>
                        <strong>{user.name}</strong>
                        <small>{user.email}</small>
                      </div>
                    </div>
                    <Button fullWidth variant="secondary" onClick={startEditing}>
                      이름·이메일 수정
                    </Button>
                  </Card>

                  <Card className="mvp-profile__card">
                    <h3>기본 정보</h3>
                    <dl className="mvp-profile__details">
                      <div>
                        <dt>휴대폰 번호</dt>
                        <dd>{nullableValue(user.phone_number)}</dd>
                      </div>
                      <div>
                        <dt>생년월일</dt>
                        <dd>{nullableValue(user.birthday)}</dd>
                      </div>
                      <div>
                        <dt>성별</dt>
                        <dd>{genderLabel(user.gender)}</dd>
                      </div>
                    </dl>
                    <p className="mvp-profile__readonly-note">
                      이 정보는 현재 화면에서 수정할 수 없습니다.
                    </p>
                  </Card>

                  <Button
                    fullWidth
                    variant="ghost"
                    onClick={() => void handleLogout()}
                    disabled={isLoggingOut}
                    aria-busy={isLoggingOut}
                  >
                    {isLoggingOut ? '로그아웃 중...' : '로그아웃'}
                  </Button>
                </>
              )}
            </>
          )}
        </main>
      </MobileShell>
    </div>
  )
}

export default ProfilePage
