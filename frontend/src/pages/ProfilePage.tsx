import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { requestAccountWithdrawal } from '../api/auth'
import ProfileConsents from '../features/profile/ProfileConsents'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import {
  getCurrentUser,
  updateCurrentUser,
  type UpdateCurrentUserRequest,
  type UserGender,
  type CurrentUser,
} from '../api/users'
import { Button, Card, MobileShell } from '../design-system/components'
import StatusPanel from '../components/StatusPanel'
import '../design-system/prototype.css'
import './MvpPages.css'
import './ProfilePage.css'

type ProfileForm = {
  name: string
  email: string
  phone_number: string
  birthday: string
  /** '' 는 미선택(null 전송) 을 뜻한다. */
  gender: '' | UserGender
}

type FieldErrors = Partial<Record<keyof ProfileForm, string>>

const EMPTY_FORM: ProfileForm = {
  name: '',
  email: '',
  phone_number: '',
  birthday: '',
  gender: '',
}

/** 서버 값 → 폼 값. null 은 빈 문자열(미입력)로 둔다. */
function formFromUser(user: CurrentUser): ProfileForm {
  return {
    name: user.name,
    email: user.email,
    phone_number: user.phone_number ?? '',
    birthday: user.birthday ?? '',
    gender: user.gender ?? '',
  }
}

/**
 * 바뀐 필드만 담는다.
 * - 값이 그대로면 키를 넣지 않는다(omitted → 변경 없음).
 * - 비웠으면 null 을 넣는다(초기화).
 */
function buildUpdatePayload(
  user: CurrentUser,
  form: ProfileForm,
): UpdateCurrentUserRequest {
  const payload: UpdateCurrentUserRequest = {}

  const name = form.name.trim()
  if (name !== user.name) payload.name = name

  const email = form.email.trim()
  if (email !== user.email) payload.email = email

  // 서버 값이 undefined 로 들어와도 null 과 같은 "미입력"으로 취급한다.
  // 입력값을 임의로 가공하지 않는다. 숫자 외 문자는 Backend 422 로 판정한다.
  const phoneNumber = form.phone_number.trim() || null
  if (phoneNumber !== (user.phone_number ?? null)) payload.phone_number = phoneNumber

  const birthday = form.birthday.trim() || null
  if (birthday !== (user.birthday ?? null)) payload.birthday = birthday

  const gender = form.gender || null
  if (gender !== (user.gender ?? null)) payload.gender = gender

  return payload
}
const AUTH_ERROR_CODES = new Set(['UNAUTHORIZED', 'INVALID_TOKEN', 'EXPIRED_TOKEN'])
const STALE_AUTH_ERROR_CODES = new Set(['INVALID_TOKEN', 'EXPIRED_TOKEN'])
const ACCOUNT_WITHDRAWAL_COMPLETED_DETAIL = '회원탈퇴가 완료되었습니다.'
const ACCOUNT_WITHDRAWAL_FAILED_DETAIL =
  '탈퇴 요청 처리에 실패했습니다. 관리자 확인이 필요합니다.'

type WithdrawalError = 'unavailable' | 'retryable' | ''

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

    if (detail.field === 'phone_number') {
      errors.phone_number =
        detail.reason === 'ALREADY_EXISTS'
          ? '이미 등록된 휴대폰 번호예요. 다른 번호를 입력해 주세요.'
          : '휴대폰 번호는 숫자만 입력해 주세요.'
    }

    if (detail.field === 'birthday') {
      errors.birthday = '생년월일을 확인해 주세요.'
    }

    if (detail.field === 'gender') {
      errors.gender = '성별을 확인해 주세요.'
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
  const [isWithdrawalOpen, setIsWithdrawalOpen] = useState(false)
  const [withdrawalPassword, setWithdrawalPassword] = useState('')
  const [withdrawalConfirmed, setWithdrawalConfirmed] = useState(false)
  const [withdrawalPasswordError, setWithdrawalPasswordError] = useState('')
  const [withdrawalConfirmationError, setWithdrawalConfirmationError] = useState('')
  const [withdrawalError, setWithdrawalError] = useState<WithdrawalError>('')
  const [isWithdrawing, setIsWithdrawing] = useState(false)
  const nameInputRef = useRef<HTMLInputElement>(null)
  const emailInputRef = useRef<HTMLInputElement>(null)
  const phoneInputRef = useRef<HTMLInputElement>(null)
  const birthdayInputRef = useRef<HTMLInputElement>(null)
  const genderSelectRef = useRef<HTMLSelectElement>(null)
  const withdrawalPasswordInputRef = useRef<HTMLInputElement>(null)

  const clearFeedback = useCallback(() => {
    setSaveError('')
    setSuccessMessage('')
  }, [])

  const expireSession = useCallback(() => {
    clearAuthenticatedSession()
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
      setForm(formFromUser(response))
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
    if (firstError === 'phone_number') phoneInputRef.current?.focus()
    if (firstError === 'birthday') birthdayInputRef.current?.focus()
    if (firstError === 'gender') genderSelectRef.current?.focus()
  }, [fieldErrors])

  useEffect(() => {
    if (isWithdrawalOpen) withdrawalPasswordInputRef.current?.focus()
  }, [isWithdrawalOpen])

  const handleChange = (
    event: ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => {
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
    setForm(formFromUser(user))
    setFieldErrors({})
    clearFeedback()
    setIsEditing(true)
  }

  const cancelEditing = () => {
    if (user) setForm(formFromUser(user))
    setFieldErrors({})
    setSaveError('')
    setIsEditing(false)
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isSaving || !user) return

    const validationErrors = validateForm(form)
    if (Object.keys(validationErrors).length > 0) {
      setFieldErrors(validationErrors)
      return
    }

    setIsSaving(true)
    setFieldErrors({})
    clearFeedback()

    try {
      // 바뀐 필드만 담는다. 바뀐 값이 없으면 빈 본문이며 Backend 는 기존 값을 그대로 돌려준다.
      // (기존 name/email 저장 동작과 중복 제출 방지 흐름을 그대로 유지하기 위해 요청을 생략하지 않는다.)
      const response = await updateCurrentUser(buildUpdatePayload(user, form))
      setUser(response)
      setForm(formFromUser(response))
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

  const closeWithdrawal = () => {
    if (isWithdrawing) return
    setIsWithdrawalOpen(false)
    setWithdrawalPassword('')
    setWithdrawalConfirmed(false)
    setWithdrawalPasswordError('')
    setWithdrawalConfirmationError('')
    setWithdrawalError('')
  }

  const handleWithdrawalSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isWithdrawing) return

    let hasValidationError = false
    if (withdrawalPassword.length < 8) {
      setWithdrawalPasswordError('현재 비밀번호를 8자 이상 입력해 주세요.')
      hasValidationError = true
    }
    if (!withdrawalConfirmed) {
      setWithdrawalConfirmationError('회원탈퇴 요청 전 최종 확인이 필요합니다.')
      hasValidationError = true
    }
    if (hasValidationError) return

    const accessToken = localStorage.getItem('access_token')
    if (!accessToken) {
      expireSession()
      return
    }

    setIsWithdrawing(true)
    setWithdrawalPasswordError('')
    setWithdrawalConfirmationError('')
    setWithdrawalError('')

    try {
      const withdrawalResponse = await requestAccountWithdrawal(
        withdrawalPassword,
        accessToken,
      )
      if (withdrawalResponse.detail === ACCOUNT_WITHDRAWAL_FAILED_DETAIL) {
        clearAuthenticatedSession()
        setUser(null)
        setWithdrawalPassword('')
        setWithdrawalConfirmed(false)
        setIsWithdrawalOpen(false)
        navigate('/start', {
          replace: true,
          state: { accountWithdrawalFailed: true },
        })
        return
      }

      if (withdrawalResponse.detail !== ACCOUNT_WITHDRAWAL_COMPLETED_DETAIL) {
        setWithdrawalError('retryable')
        return
      }

      clearAuthenticatedSession()
      setUser(null)
      setForm(EMPTY_FORM)
      setFieldErrors({})
      setLoadError('')
      setSaveError('')
      setSuccessMessage('')
      setWithdrawalPassword('')
      setWithdrawalConfirmed(false)
      setIsWithdrawalOpen(false)
      navigate('/start', {
        replace: true,
        state: { accountWithdrawalCompleted: true },
      })
    } catch (error) {
      if (
        error instanceof ApiError &&
        (STALE_AUTH_ERROR_CODES.has(error.code) ||
          (error.status === 401 && error.code !== 'UNAUTHORIZED'))
      ) {
        expireSession()
        return
      }

      if (
        error instanceof ApiError &&
        error.status === 401 &&
        error.code === 'UNAUTHORIZED'
      ) {
        setWithdrawalPasswordError('비밀번호가 올바르지 않습니다.')
        withdrawalPasswordInputRef.current?.focus()
      } else if (
        error instanceof ApiError &&
        error.status === 503 &&
        (error.code === 'SERVICE_UNAVAILABLE' ||
          error.details.some(
            (detail) => detail.reason === 'ACCOUNT_WITHDRAWAL_REQUEST_DISABLED',
          ))
      ) {
        setWithdrawalError('unavailable')
      } else if (
        error instanceof ApiError &&
        error.status === 422 &&
        error.code === 'VALIDATION_FAILED' &&
        error.details.some(
          (detail) =>
            detail.field === 'confirmed' &&
            detail.reason === 'CONFIRMATION_REQUIRED',
        )
      ) {
        setWithdrawalConfirmed(false)
        setWithdrawalConfirmationError('회원탈퇴 요청 전 최종 확인이 필요합니다.')
      } else {
        setWithdrawalError('retryable')
      }
    } finally {
      setIsWithdrawing(false)
    }
  }

  return (
    <div className="mvp-page mvp-profile-page">
      <MobileShell
        title="Dosey 도지"
        activeNavigation="메뉴"
        onBack={() => navigate('/menu')}
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '일정') navigate('/schedule')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
          if (item === '메뉴') navigate('/menu')
        }}
      >
        <main
          className={`app-scroll mvp-page__content mvp-profile${
            isEditing ? ' mvp-profile--editing' : ''
          }`}
        >
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
                <h2 className="mvp-page__title">{isEditing ? '사용자 정보 수정' : '사용자 정보'}</h2>
              </header>

              {successMessage && (
                <p className="mvp-profile__success" role="status" aria-live="polite">
                  {successMessage}
                </p>
              )}

              {isEditing ? (
                <Card className="mvp-profile__card mvp-profile__edit-card">
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

                    <div className="mvp-form__field">
                      <label htmlFor="profile-phone-number">휴대폰 번호</label>
                      <input
                        ref={phoneInputRef}
                        id="profile-phone-number"
                        name="phone_number"
                        type="tel"
                        inputMode="numeric"
                        autoComplete="tel"
                        value={form.phone_number}
                        onChange={handleChange}
                        disabled={isSaving}
                        aria-invalid={Boolean(fieldErrors.phone_number)}
                        aria-describedby={
                          fieldErrors.phone_number
                            ? 'profile-phone-number-error'
                            : 'profile-optional-help'
                        }
                      />
                      {fieldErrors.phone_number && (
                        <p
                          id="profile-phone-number-error"
                          className="mvp-profile__field-error"
                        >
                          {fieldErrors.phone_number}
                        </p>
                      )}
                    </div>

                    <div className="mvp-form__field">
                      <label htmlFor="profile-birthday">생년월일</label>
                      <input
                        ref={birthdayInputRef}
                        id="profile-birthday"
                        name="birthday"
                        type="date"
                        autoComplete="bday"
                        value={form.birthday}
                        onChange={handleChange}
                        disabled={isSaving}
                        aria-invalid={Boolean(fieldErrors.birthday)}
                        aria-describedby={
                          fieldErrors.birthday
                            ? 'profile-birthday-error'
                            : 'profile-optional-help'
                        }
                      />
                      {fieldErrors.birthday && (
                        <p id="profile-birthday-error" className="mvp-profile__field-error">
                          {fieldErrors.birthday}
                        </p>
                      )}
                    </div>

                    <p id="profile-optional-help" className="mvp-form__help">
                      비워 두면 미입력으로 저장돼요.
                    </p>

                    <div className="mvp-form__field">
                      <label htmlFor="profile-gender">성별</label>
                      <select
                        ref={genderSelectRef}
                        id="profile-gender"
                        name="gender"
                        value={form.gender}
                        onChange={handleChange}
                        disabled={isSaving}
                        aria-invalid={Boolean(fieldErrors.gender)}
                        aria-describedby={
                          fieldErrors.gender ? 'profile-gender-error' : 'profile-optional-help'
                        }
                      >
                        <option value="">미선택</option>
                        <option value="MALE">남성</option>
                        <option value="FEMALE">여성</option>
                      </select>
                      {fieldErrors.gender && (
                        <p id="profile-gender-error" className="mvp-profile__field-error">
                          {fieldErrors.gender}
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
                  <section className="mvp-profile__section" aria-labelledby="account-info-title">
                    <h3 id="account-info-title">계정 정보</h3>
                    <Card className="mvp-profile__card mvp-profile__account-card">
                      <dl className="mvp-profile__details mvp-profile__account-details">
                        <div>
                          <dt>이름</dt>
                          <dd>{user.name}</dd>
                        </div>
                        <div>
                          <dt>이메일</dt>
                          <dd>{user.email}</dd>
                        </div>
                      </dl>
                    </Card>
                  </section>

                  <section className="mvp-profile__section" aria-labelledby="basic-info-title">
                    <h3 id="basic-info-title">기본 정보</h3>
                    <Card className="mvp-profile__card">
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
                    </Card>
                    <p className="mvp-profile__readonly-note">
                      휴대폰 번호, 생년월일, 성별은 선택 입력이에요.
                    </p>
                    <Button fullWidth onClick={startEditing}>
                      사용자 정보 수정
                    </Button>
                  </section>

                  <ProfileConsents onSessionExpired={expireSession} />

                  <section className="mvp-profile__section" aria-labelledby="account-management-title">
                    <h3 id="account-management-title">계정 관리</h3>
                    {!isWithdrawalOpen ? (
                      <Button
                        fullWidth
                        variant="secondary"
                        className="mvp-profile__danger-button"
                        onClick={() => setIsWithdrawalOpen(true)}
                      >
                        회원탈퇴
                      </Button>
                    ) : (
                      <Card className="mvp-profile__card mvp-profile__withdrawal-card">
                        <div className="mvp-profile__withdrawal-intro">
                          <h4>회원탈퇴 요청</h4>
                          <ul>
                            <li>탈퇴 요청 후 현재 계정 이용과 기존 로그인 세션이 종료됩니다.</li>
                            <li>탈퇴 요청 후에는 다시 로그인할 수 없습니다.</li>
                            <li>개인정보와 건강정보의 삭제·보존은 서비스 정책에 따라 처리됩니다.</li>
                          </ul>
                        </div>

                        <form className="mvp-form" onSubmit={handleWithdrawalSubmit} noValidate>
                          <div className="mvp-form__field">
                            <label htmlFor="withdrawal-password">현재 비밀번호</label>
                            <input
                              ref={withdrawalPasswordInputRef}
                              id="withdrawal-password"
                              name="password"
                              type="password"
                              autoComplete="current-password"
                              minLength={8}
                              value={withdrawalPassword}
                              onChange={(event) => {
                                setWithdrawalPassword(event.target.value)
                                setWithdrawalPasswordError('')
                                setWithdrawalError('')
                              }}
                              disabled={isWithdrawing}
                              aria-invalid={Boolean(withdrawalPasswordError)}
                              aria-describedby={
                                withdrawalPasswordError ? 'withdrawal-password-error' : undefined
                              }
                            />
                            {withdrawalPasswordError && (
                              <p
                                id="withdrawal-password-error"
                                className="mvp-profile__field-error"
                                role="alert"
                              >
                                {withdrawalPasswordError}
                              </p>
                            )}
                          </div>

                          <label className="mvp-profile__withdrawal-confirmation">
                            <input
                              type="checkbox"
                              checked={withdrawalConfirmed}
                              onChange={(event) => {
                                setWithdrawalConfirmed(event.target.checked)
                                setWithdrawalConfirmationError('')
                                setWithdrawalError('')
                              }}
                              disabled={isWithdrawing}
                              aria-invalid={Boolean(withdrawalConfirmationError)}
                              aria-describedby={
                                withdrawalConfirmationError
                                  ? 'withdrawal-confirmation-error'
                                  : undefined
                              }
                            />
                            <span>
                              회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.
                            </span>
                          </label>
                          {withdrawalConfirmationError && (
                            <p
                              id="withdrawal-confirmation-error"
                              className="mvp-profile__field-error"
                              role="alert"
                            >
                              {withdrawalConfirmationError}
                            </p>
                          )}

                          {withdrawalError === 'unavailable' && (
                            <StatusPanel
                              variant="unavailable"
                              title="회원탈퇴 요청을 현재 처리할 수 없어요."
                              description="잠시 후 다시 시도해 주세요. 계정은 그대로 유지됩니다."
                            />
                          )}

                          {withdrawalError === 'retryable' && (
                            <StatusPanel
                              variant="error-retryable"
                              title="회원탈퇴 요청을 접수하지 못했어요."
                              description="입력한 내용을 확인한 뒤 다시 시도해 주세요. 계정은 그대로 유지됩니다."
                            />
                          )}

                          <div className="mvp-profile__actions">
                            <Button
                              variant="secondary"
                              type="button"
                              onClick={closeWithdrawal}
                              disabled={isWithdrawing}
                            >
                              취소
                            </Button>
                            <Button
                              type="submit"
                              className="mvp-profile__danger-button"
                              disabled={
                                isWithdrawing ||
                                !withdrawalConfirmed ||
                                withdrawalPassword.length === 0
                              }
                              aria-busy={isWithdrawing}
                            >
                              {isWithdrawing ? '요청 중...' : '회원탈퇴 요청'}
                            </Button>
                          </div>
                        </form>
                      </Card>
                    )}
                  </section>
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
