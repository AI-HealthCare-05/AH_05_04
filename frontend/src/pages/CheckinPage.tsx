import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  getMedicationDay,
  getOccurrenceMedication,
  type MedicationCheckinSnapshot,
  type MedicationOccurrenceData,
  type MedicationOccurrenceMedicationResponse,
} from '../api/medicationSchedules'
import {
  createCheckinIdempotencyKey,
  isCheckinBeforeScheduledAtError,
  isCheckinConflictError,
  isCheckinRevisionConflictError,
  isCheckinValidationError,
  isOccurrenceNotFoundError,
  putMedicationCheckin,
  type PutMedicationCheckinInput,
} from '../api/medicationCheckins'
import {
  resolveLogicalMutationAttempt,
  type LogicalMutationAttempt,
} from '../api/logicalMutationAttempt'
import { Button, Card, MobileShell } from '../design-system/components'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import {
  CHECKIN_GROUP_STATE_LABEL,
  findGroupForOccurrence,
  getGroupProgress,
  groupOccurrencesByScheduledAt,
  isBeforeScheduledAt,
  selectionFromCheckin,
  type CheckinGroup,
  type CheckinSelection,
} from './checkinGroupView'
import '../design-system/prototype.css'
import './MvpPages.css'
import './CheckinPage.css'
import checkinMedicationCapsuleIcon from '../assets/checkin-medication-capsule.svg'
import checkinMedicationCapsuleDetailIcon from '../assets/checkin-medication-capsule-detail.svg'

const KST_TIME_ZONE = 'Asia/Seoul'
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

type MedicationDetail = MedicationOccurrenceMedicationResponse['data']
type LoadFailure = 'AUTH' | 'NOT_FOUND' | 'NETWORK' | 'SERVER'

export type CheckinPageServices = {
  getMedicationDay: typeof getMedicationDay
  getOccurrenceMedication: typeof getOccurrenceMedication
  putMedicationCheckin: typeof putMedicationCheckin
  createCheckinIdempotencyKey: typeof createCheckinIdempotencyKey
}

const defaultServices: CheckinPageServices = {
  getMedicationDay,
  getOccurrenceMedication,
  putMedicationCheckin,
  createCheckinIdempotencyKey,
}

function kstToday(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: KST_TIME_ZONE }).format(
    new Date(),
  )
}

function isValidLocalDate(value: string | null): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
}


function formatKstTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '--:--'
  return new Intl.DateTimeFormat('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: KST_TIME_ZONE,
  }).format(date)
}


function classifyLoadFailure(error: unknown): LoadFailure {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return 'AUTH'
    if (error.status === 404) return 'NOT_FOUND'
    if (error.status >= 500) return 'SERVER'
  }
  return 'NETWORK'
}

function failureCopy(failure: LoadFailure): { title: string; body: string } {
  if (failure === 'AUTH') {
    return { title: '다시 로그인해 주세요', body: '로그인 정보가 만료됐어요.' }
  }
  if (failure === 'NOT_FOUND') {
    return {
      title: '복약 정보를 찾을 수 없어요',
      body: '일정에서 다시 선택해 주세요.',
    }
  }
  return {
    title: '복약 정보를 불러오지 못했어요',
    body: '잠시 후 다시 시도해 주세요.',
  }
}

function formatCheckinSummaryDate(value: string): string {
  const [, month, day] = value.split('-').map(Number)
  const label = `${month}월 ${day}일`

  return value === kstToday() ? `${label} · 오늘` : label
}

function useNow(intervalMs = 30_000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

function CheckinShell({
  children,
  onBack,
}: {
  children: React.ReactNode
  onBack?: () => void
}) {
  const navigate = useNavigate()
  return (
    <MobileShell
      title="Dosey 도지"
      activeNavigation="일정"
      onBack={onBack}
    onNavigate={(item) => {
        if (item === '홈') navigate('/')
        if (item === '일정') navigate('/schedule')
        if (item === '도지') navigate('/chat')
        if (item === '가이드') navigate('/guides')
        if (item === '메뉴') navigate('/menu')
      }}
    >
      {children}
    </MobileShell>
  )
}

/* ------------------------------------------------------------------ *
 * PB-01 · 오늘의 복약 체크 요약
 * ------------------------------------------------------------------ */

export function CheckinSummaryPage({
  services = defaultServices,
}: {
  services?: CheckinPageServices
} = {}) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const requested = searchParams.get('date')
  const date = isValidLocalDate(requested) ? requested : kstToday()

  const [occurrences, setOccurrences] = useState<MedicationOccurrenceData[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(null)
  const [reloadVersion, setReloadVersion] = useState(0)
  const now = useNow()

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setIsLoading(true)
    setLoadFailure(null)

    async function load() {
      try {
        const day = await services.getMedicationDay(date, controller.signal)
        if (!active) return
        setOccurrences(
          day.data.occurrences.filter(
            (item) => item.scheduled_local_date === date,
          ),
        )
      } catch (error) {
        if (!controller.signal.aborted && active) {
          setOccurrences([])
          setLoadFailure(classifyLoadFailure(error))
        }
      } finally {
        if (active) setIsLoading(false)
      }
    }

    void load()
    return () => {
      active = false
      controller.abort()
    }
  }, [date, reloadVersion, services])

  const groups = useMemo(
    () => groupOccurrencesByScheduledAt(occurrences),
    [occurrences],
  )

  return (
    <div className="mvp-page checkin-page">
      <CheckinShell
      onBack={() =>
        navigate(`/schedule?date=${encodeURIComponent(date)}`)
      }
    >
        <main className="app-scroll checkin checkin--summary">
          <header className="checkin__intro">
            <h1>오늘의 복약 체크</h1>
            <p>{formatCheckinSummaryDate(date)}</p>
          </header>

          {isLoading && (
            <Card className="checkin__state">
              <div role="status">복약 정보를 불러오는 중입니다.</div>
            </Card>
          )}

          {!isLoading && loadFailure && (() => {
            const copy = failureCopy(loadFailure)
            return (
              <Card className="checkin__state">
                <h2>{copy.title}</h2>
                <p role="alert">{copy.body}</p>
                <Button
                  fullWidth
                  onClick={loadFailure === 'AUTH'
                    ? () => {
                        clearAuthenticatedSession()
                        navigate('/login', { replace: true })
                      }
                    : () => setReloadVersion((version) => version + 1)}
                >
                  {loadFailure === 'AUTH' ? '로그인하기' : '다시 시도'}
                </Button>
              </Card>
            )
          })()}

          {!isLoading && !loadFailure && (
            <>
              <section
                className="checkin__groups"
                aria-labelledby="checkin-groups-title"
              >
                <h2 id="checkin-groups-title">오늘 복용 시간</h2>
                <p className="checkin__groups-hint">
                  시간대를 눌러 약을 확인할 수 있어요.
                </p>

                {groups.length === 0 ? (
                  <Card className="checkin__state">
                    <p>이 날짜에 기록할 복약 일정이 없어요.</p>
                  </Card>
                ) : (
                  <ul className="checkin-group-list">
                    {groups.map((group) => {
                      const progress = getGroupProgress(group, now)
                      const target = group.occurrences[0]
                      return (
                        <li key={group.scheduledAt}>
                          <button
                            type="button"
                            className="checkin-group-card"
                            data-state={progress.state}
                            onClick={() =>
                              navigate(
                                `/schedule/occurrences/${target.occurrence_id}?date=${encodeURIComponent(date)}`,
                              )
                            }
                          >
                            <span className="checkin-group-card__ordinal">
                              {group.ordinal}회차
                            </span>
                            <span className="checkin-group-card__time">
                              {formatKstTime(group.scheduledAt)}
                            </span>
                          <span
                            className="checkin-group-card__medication-icon"
                            aria-hidden="true"
                          >
                            <img
                              src={checkinMedicationCapsuleDetailIcon}
                              alt=""
                              draggable={false}
                            />
                          </span>
                          {progress.state === 'COMPLETE' && (
                            <span
                              className="checkin-group-card__check"
                              aria-hidden="true"
                            >
                              ✓
                            </span>
                          )}
                            <span className="checkin-group-card__count">
                              {progress.completed}/{progress.total}
                            </span>
                            <span className="checkin-group-card__state">
                              {CHECKIN_GROUP_STATE_LABEL[progress.state]}
                            </span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </section>

              <button
                className="checkin__link-row"
                type="button"
                onClick={() =>
                  navigate(`/schedule?date=${encodeURIComponent(date)}`)
                }
              >
                <span>전체 일정 보기</span>
                <span aria-hidden="true">›</span>
              </button>

              <button
                className="checkin__link-row checkin__link-row--stacked"
                type="button"
                onClick={() => navigate('/report')}
              >
                <span className="checkin__link-title">복약 리포트</span>
                <span className="checkin__link-body">
                  최근 기록을 한눈에 확인해요
                </span>
              <span className="checkin__link-meta">
                7일 · 30일 기록 보기
              </span>
              </button>
            </>
          )}
        </main>
      </CheckinShell>
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * PB-02 / PB-03 / PB-04 / PB-05 · 시간 그룹 상세와 저장
 * ------------------------------------------------------------------ */

type SaveFailure = 'RETRYABLE' | 'CONFLICT' | 'BEFORE_SCHEDULED' | 'VALIDATION'

export function CheckinDetailPage({
  services = defaultServices,
}: {
  services?: CheckinPageServices
} = {}) {
  const navigate = useNavigate()
  const { occurrenceId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const date = searchParams.get('date')
  const validRoute = UUID_PATTERN.test(occurrenceId) && isValidLocalDate(date)

  const [group, setGroup] = useState<CheckinGroup | null>(null)
  const [medications, setMedications] = useState<
    Record<string, MedicationDetail | null>
  >({})
  const [dailyFrequencyByMedication, setDailyFrequencyByMedication] = useState<
    Record<string, number>
  >({})
  const [selections, setSelections] = useState<
    Record<string, CheckinSelection>
  >({})
  const [savedCheckins, setSavedCheckins] = useState<
    Record<string, MedicationCheckinSnapshot>
  >({})
  const [isLoading, setIsLoading] = useState(validRoute)
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(
    validRoute ? null : 'NOT_FOUND',
  )
  const [reloadVersion, setReloadVersion] = useState(0)
  const [isSaving, setIsSaving] = useState(false)
  const [saveFailure, setSaveFailure] = useState<SaveFailure | null>(null)
  const [saveMessage, setSaveMessage] = useState('')
  const [isComplete, setIsComplete] = useState(false)
  const [showSuccessFeedback, setShowSuccessFeedback] = useState(false)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const successConfirmRef = useRef<HTMLButtonElement>(null)
  // occurrence별 논리적 저장 시도. 재시도 시 멱등성 키를 보존한다.
  const attemptsRef = useRef<
    Record<string, LogicalMutationAttempt<'CHECKIN_PUT', PutMedicationCheckinInput>>
  >({})
  const now = useNow()

  const backRoute = isValidLocalDate(date)
    ? `/schedule?date=${encodeURIComponent(date)}`
    : '/schedule'

  useEffect(() => {
    if (!validRoute || !date) return undefined
    const controller = new AbortController()
    let active = true
    setIsLoading(true)
    setLoadFailure(null)

    async function load() {
      try {
        const day = await services.getMedicationDay(date!, controller.signal)
        if (!active) return

        const sameDay = day.data.occurrences.filter(
          (item) => item.scheduled_local_date === date,
        )
        const groups = groupOccurrencesByScheduledAt(sameDay)
        const matched = findGroupForOccurrence(groups, occurrenceId)
        if (!matched) {
          setGroup(null)
          setLoadFailure('NOT_FOUND')
          return
        }

        const details = await Promise.all(
          matched.occurrences.map(async (occurrence) => {
            const detail = await services.getOccurrenceMedication(
              occurrence.occurrence_id,
              controller.signal,
            )

            const medication = detail.data
            const matchesOccurrence =
              medication.occurrence_id === occurrence.occurrence_id &&
              medication.prescription_version_id ===
                occurrence.prescription_version_id &&
              medication.prescription_version_medication_id ===
                occurrence.prescription_version_medication_id

            if (!matchesOccurrence) {
              throw new Error('CHECKIN_MEDICATION_IDENTITY_MISMATCH')
            }

            return [occurrence.occurrence_id, medication] as const
          }),
        )
        if (!active) return

        const dailyFrequencies = Object.fromEntries(
          day.data.schedule_items.flatMap((item) => {
            const count = item.schedule?.local_times.length ?? 0
            return count > 0
              ? [[item.prescription_version_medication_id, count]]
              : []
          }),
        )

        setGroup(matched)
        setMedications(Object.fromEntries(details))
        setDailyFrequencyByMedication(dailyFrequencies)
        // 기존 기록은 선택 상태로 복원한다.
        setSelections(
          Object.fromEntries(
            matched.occurrences.flatMap((occurrence) => {
              const selection = selectionFromCheckin(occurrence.checkin)
              return selection ? [[occurrence.occurrence_id, selection]] : []
            }),
          ),
        )
        setSavedCheckins({})
        setIsComplete(false)
        setShowSuccessFeedback(false)
        setSaveFailure(null)
        setSaveMessage('')
        attemptsRef.current = {}
      } catch (error) {
        if (!controller.signal.aborted && active) {
          if (
            error instanceof Error &&
            error.message === 'CHECKIN_MEDICATION_IDENTITY_MISMATCH'
          ) {
            setGroup(null)
            setMedications({})
            setDailyFrequencyByMedication({})
            setLoadFailure('NOT_FOUND')
            return
          }
          setGroup(null)
          setLoadFailure(
            isOccurrenceNotFoundError(error)
              ? 'NOT_FOUND'
              : classifyLoadFailure(error),
          )
        }
      } finally {
        if (active) setIsLoading(false)
      }
    }

    void load()
    return () => {
      active = false
      controller.abort()
    }
  }, [date, occurrenceId, reloadVersion, services, validRoute])

  useEffect(() => {
    if (group) headingRef.current?.focus()
  }, [group])

  useEffect(() => {
    if (showSuccessFeedback) {
      successConfirmRef.current?.focus()
      return
    }

    if (isComplete) {
      headingRef.current?.focus()
    }
  }, [isComplete, showSuccessFeedback])

  const reload = useCallback(() => {
    setReloadVersion((version) => version + 1)
  }, [])

  const isBeforeScheduled = group
    ? isBeforeScheduledAt(group.scheduledAt, now)
    : false

  const targets = group?.occurrences ?? []
  const allSelected =
    targets.length > 0 &&
    targets.every((occurrence) => selections[occurrence.occurrence_id])
  const canSubmit = allSelected && !isBeforeScheduled && !isSaving

  const savedCount = Object.keys(savedCheckins).length

  const submit = async () => {
    if (!group || !canSubmit) return

    setIsSaving(true)
    setSaveFailure(null)
    setSaveMessage('')

    let failure: SaveFailure | null = null
    const succeeded: Record<string, MedicationCheckinSnapshot> = {
      ...savedCheckins,
    }

    for (const occurrence of group.occurrences) {
      // 이미 이번 화면에서 저장 성공한 건은 다시 보내지 않는다.
      if (succeeded[occurrence.occurrence_id]) continue

      const status = selections[occurrence.occurrence_id]
      if (!status) continue

      const requestPayload: PutMedicationCheckinInput = {
        status,
        expectedRevision: occurrence.checkin?.revision ?? 0,
      }
      const attempt = resolveLogicalMutationAttempt(
        attemptsRef.current[occurrence.occurrence_id] ?? null,
        'CHECKIN_PUT',
        occurrence.occurrence_id,
        requestPayload,
        requestPayload.expectedRevision,
        services.createCheckinIdempotencyKey,
      )
      attemptsRef.current[occurrence.occurrence_id] = attempt

      try {
        const response = await services.putMedicationCheckin(
          attempt.targetId,
          attempt.requestPayload,
          attempt.idempotencyKey,
        )
        succeeded[occurrence.occurrence_id] = response.data
        delete attemptsRef.current[occurrence.occurrence_id]
      } catch (error) {
        if (isCheckinBeforeScheduledAtError(error)) {
          failure = 'BEFORE_SCHEDULED'
        } else if (
          isCheckinRevisionConflictError(error) ||
          isCheckinConflictError(error)
        ) {
          failure = 'CONFLICT'
        } else if (isCheckinValidationError(error)) {
          failure = 'VALIDATION'
        } else if (error instanceof ApiError && error.status === 401) {
          clearAuthenticatedSession()
          navigate('/login', { replace: true })
          return
        } else {
          failure = 'RETRYABLE'
        }
        break
      }
    }

    setSavedCheckins(succeeded)
    setIsSaving(false)

    if (failure) {
      setSaveFailure(failure)
      if (failure === 'CONFLICT') {
        // 최신 상태를 다시 조회한다. 이전 선택을 자동 재제출하지 않는다.
        attemptsRef.current = {}
        setSaveMessage(
          '기록이 다른 곳에서 변경됐어요. 최신 상태를 확인한 뒤 다시 선택해 주세요.',
        )
        reload()
      } else if (failure === 'BEFORE_SCHEDULED') {
        setSaveMessage(
          `${formatKstTime(group.scheduledAt)}부터 복약 기록을 남길 수 있어요.`,
        )
      } else if (failure === 'VALIDATION') {
        setSaveMessage('선택한 복약 기록을 저장할 수 없어요. 상태를 확인해 주세요.')
      } else {
        setSaveMessage(
        '선택한 내용은 그대로 유지돼요.\n연결을 확인한 뒤 다시 시도해 주세요.',
      )
      }
      return
    }

    // 전체 성공일 때만 완료로 본다. 부분 저장은 완료로 표시하지 않는다.
  const completed = group.occurrences.every(
    (occurrence) => succeeded[occurrence.occurrence_id],
  )

  setIsComplete(completed)
  if (completed) setShowSuccessFeedback(true)
  }

  return (
    <div className="mvp-page checkin-page">
      <CheckinShell onBack={() => navigate(backRoute)}>
        <main className="app-scroll checkin checkin--detail">
          {isLoading && (
            <Card className="checkin__state">
              <div role="status">복약 정보를 불러오는 중입니다.</div>
            </Card>
          )}

          {!isLoading && loadFailure && (() => {
            const copy = failureCopy(loadFailure)
            const canRetry = loadFailure === 'NETWORK' || loadFailure === 'SERVER'
            return (
              <Card className="checkin__state">
                <h2>{copy.title}</h2>
                <p role="alert">{copy.body}</p>
                <Button
                  fullWidth
                  onClick={loadFailure === 'AUTH'
                    ? () => {
                        clearAuthenticatedSession()
                        navigate('/login', { replace: true })
                      }
                    : canRetry
                      ? reload
                      : () => navigate(backRoute)}
                >
                  {loadFailure === 'AUTH'
                    ? '로그인하기'
                    : canRetry
                      ? '다시 시도'
                      : '일정으로 돌아가기'}
                </Button>
              </Card>
            )
          })()}

          {!isLoading && group && !isComplete && (
            <>
              <header className="checkin__intro">
                <h1 ref={headingRef} tabIndex={-1}>
                  {formatKstTime(group.scheduledAt)} 약을 확인해 주세요
                </h1>
                <p>복용 시간이 같은 약 {targets.length}개</p>
              </header>

              <Card className="checkin__guide">
                <strong>약을 하나씩 확인해 주세요.</strong>
                <span>선택이 끝나면 아래 기록하기 버튼으로 저장해요.</span>
              </Card>

              {isBeforeScheduled && (
                <p className="checkin__readonly-note" role="status">
                  {formatKstTime(group.scheduledAt)}부터 복약 기록을 남길 수 있어요.
                </p>
              )}

              <section
                className="checkin__medications"
                aria-labelledby="checkin-medications-title"
              >
                <h2 id="checkin-medications-title">
                  {formatKstTime(group.scheduledAt)}에 확인할 약 {targets.length}개
                </h2>

                {targets.map((occurrence) => {
                  const medication = medications[occurrence.occurrence_id]
                  const selection = selections[occurrence.occurrence_id] ?? null
                  const isSaved = Boolean(savedCheckins[occurrence.occurrence_id])
                  return (
                    <article
                      key={occurrence.occurrence_id}
                      className="checkin-medication-card"
                    >
                      <div className="checkin-medication-card__summary">
                        <span
                          className="checkin-medication-card__icon"
                          aria-hidden="true"
                        >
                          <img
                            src={checkinMedicationCapsuleIcon}
                            alt=""
                            draggable={false}
                          />
                        </span>

                        <div className="checkin-medication-card__copy">
                          <h3>
                            {medication
                              ? [
                                  medication.medication_name,
                                  medication.strength_text,
                                ]
                                  .filter(Boolean)
                                  .join(' ')
                              : '약 정보를 확인할 수 없어요'}
                          </h3>

                          <p>
                            {medication
                              ? [
                                  medication.dose_value != null &&
                                  medication.dose_unit
                                    ? `${medication.dose_value}${medication.dose_unit}`
                                    : null,
                                  dailyFrequencyByMedication[
                                    occurrence.prescription_version_medication_id
                                  ]
                                    ? `하루 ${
                                        dailyFrequencyByMedication[
                                          occurrence.prescription_version_medication_id
                                        ]
                                      }회`
                                    : null,
                                  formatKstTime(occurrence.scheduled_at),
                                ]
                                  .filter(Boolean)
                                  .join(' · ')
                              : formatKstTime(occurrence.scheduled_at)}
                          </p>
                        </div>
                      </div>
                      <div
                        className="checkin-medication-card__choices"
                        role="group"
                        aria-label={`${medication?.medication_name ?? '약'} 복용 여부`}
                      >
                        <button
                          type="button"
                          className="checkin-choice"
                          aria-pressed={selection === 'TAKEN'}
                          disabled={isSaving || isBeforeScheduled || isSaved}
                          onClick={() =>
                            setSelections((current) => ({
                              ...current,
                              [occurrence.occurrence_id]: 'TAKEN',
                            }))
                          }
                        >
                          복용했어요
                        </button>
                        <button
                          type="button"
                          className="checkin-choice"
                          aria-pressed={selection === 'NOT_TAKEN'}
                          disabled={isSaving || isBeforeScheduled || isSaved}
                          onClick={() =>
                            setSelections((current) => ({
                              ...current,
                              [occurrence.occurrence_id]: 'NOT_TAKEN',
                            }))
                          }
                        >
                          복용하지 않았어요
                        </button>
                      </div>
                      {isSaved && (
                        <p className="checkin-medication-card__saved" role="status">
                          기록을 저장했어요.
                        </p>
                      )}
                    </article>
                  )
                })}
              </section>

              {saveFailure && (
                <Card className="checkin__error">
                  <strong>기록을 저장하지 못했어요</strong>
                  <p role="alert">{saveMessage}</p>
                </Card>
              )}



              <p className="checkin__bottom-helper">
                {allSelected
                  ? '두 약의 복용 여부를 모두 선택했어요.'
                  : '모든 약의 복용 여부를 선택하면 기록할 수 있어요.'}
              </p>

              <Button
                fullWidth
                className="checkin__submit"
                disabled={!canSubmit}
                onClick={() => void submit()}
              >
                {isSaving
                  ? '저장 중…'
                  : saveFailure === 'RETRYABLE' || saveFailure === 'VALIDATION'
                    ? '저장 다시 시도'
                    : '기록하기'}
              </Button>
            </>
          )}

          {!isLoading && group && isComplete && (
            <section className="checkin__complete" aria-labelledby="checkin-complete-title">
              <header className="checkin__intro">
                <h1 id="checkin-complete-title" tabIndex={-1} ref={headingRef}>
                  오늘의 복약 체크
                </h1>
                <p>{formatKstTime(group.scheduledAt)} 복약 기록</p>
              </header>

              <Card className="checkin__complete-card">
                <span className="checkin__complete-time">
                  {formatKstTime(group.scheduledAt)}
                </span>
                <strong role="status">
                  {savedCount}/{targets.length} 기록 완료
                </strong>
                <span>약 {savedCount}개의 복용 여부가 저장됐어요.</span>
              </Card>

              <button
                className="checkin__link-row"
                type="button"
                onClick={() => navigate(backRoute)}
              >
                <span>전체 일정 보기</span>
                <span aria-hidden="true">›</span>
              </button>
            </section>
          )}
        </main>
      {!isLoading && group && isComplete && showSuccessFeedback && (
        <div className="checkin-success-overlay">
          <section
            className="checkin-success-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="checkin-success-title"
          >
            <span
              className="checkin-success-dialog__check"
              aria-hidden="true"
            >
              ✓
            </span>

            <h2 id="checkin-success-title">
              {formatKstTime(group.scheduledAt)} 약 기록을
              <br />
              확인했어요!
            </h2>

            <button
              ref={successConfirmRef}
              type="button"
              className="ds-button checkin-success-dialog__confirm"
              onClick={() => setShowSuccessFeedback(false)}
              onKeyDown={(event) => {
                if (event.key === 'Tab') {
                  event.preventDefault()
                  successConfirmRef.current?.focus()
                }
              }}
            >
              확인
            </button>
          </section>
        </div>
      )}

      </CheckinShell>
    </div>
  )
}

export default CheckinDetailPage
