import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  getUnconfirmedCheckins,
  isUnconfirmedCursorNotFoundError,
  type UnconfirmedCheckinItem,
} from '../api/medicationCheckinBacklog'
import {
  createCheckinIdempotencyKey,
  isCheckinConflictError,
  isCheckinRevisionConflictError,
  isCheckinValidationError,
  isOccurrenceNotFoundError,
  putMedicationCheckin,
  type MedicationCheckinUserStatus,
  type PutMedicationCheckinInput,
} from '../api/medicationCheckins'
import {
  getMedicationOccurrenceMedication,
  type MedicationOccurrenceMedicationData,
} from '../api/medicationOccurrences'
import {
  resolveLogicalMutationAttempt,
  type LogicalMutationAttempt,
} from '../api/logicalMutationAttempt'
import StatusPanel from '../components/StatusPanel'
import { Button, Card, MobileShell } from '../design-system/components'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import '../design-system/prototype.css'
import './MvpPages.css'
import './UnconfirmedCheckinsPage.css'

const PAGE_LIMIT = 20

type BacklogFailure = 'NETWORK' | 'SERVER' | 'NOT_FOUND'
type CheckinAttempt = LogicalMutationAttempt<'CHECKIN_PUT', PutMedicationCheckinInput>

export type UnconfirmedCheckinsPageServices = {
  getUnconfirmedCheckins: typeof getUnconfirmedCheckins
  getMedicationOccurrenceMedication: typeof getMedicationOccurrenceMedication
  putMedicationCheckin: typeof putMedicationCheckin
  createCheckinIdempotencyKey: typeof createCheckinIdempotencyKey
}

const defaultServices: UnconfirmedCheckinsPageServices = {
  getUnconfirmedCheckins,
  getMedicationOccurrenceMedication,
  putMedicationCheckin,
  createCheckinIdempotencyKey,
}

function formatScheduledDate(value: string): string {
  const parsed = new Date(`${value}T12:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return '예정 날짜 확인 필요'
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'long',
    day: 'numeric',
    weekday: 'short',
    timeZone: 'UTC',
  }).format(parsed)
}

function formatScheduledTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '예정 시각 확인 필요'
  return new Intl.DateTimeFormat('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Seoul',
  }).format(parsed)
}

function medicationLabel(medication: MedicationOccurrenceMedicationData): string {
  const dose =
    medication.dose_value !== null && medication.dose_unit
      ? `${medication.dose_value}${medication.dose_unit}`
      : null
  return [medication.medication_name, medication.strength_text, dose]
    .filter(Boolean)
    .join(' · ')
}

function isMatchingHistoricalMedication(
  item: UnconfirmedCheckinItem,
  medication: MedicationOccurrenceMedicationData,
): boolean {
  return (
    medication.occurrence_id === item.occurrence_id &&
    medication.prescription_version_id === item.prescription_version_id &&
    medication.prescription_version_medication_id ===
      item.prescription_version_medication_id
  )
}

function classifyBacklogFailure(error: unknown): BacklogFailure {
  if (error instanceof ApiError) {
    if (error.status === 404) return 'NOT_FOUND'
    if (error.status >= 500) return 'SERVER'
  }
  return 'NETWORK'
}

function backlogFailureCopy(failure: BacklogFailure) {
  if (failure === 'SERVER') {
    return {
      title: '미확인 기록을 잠시 불러오지 못했어요',
      description: '잠시 후 다시 시도해 주세요.',
    }
  }
  if (failure === 'NOT_FOUND') {
    return {
      title: '미확인 기록을 확인할 수 없어요',
      description: '요청한 기록이나 조회 위치는 다른 정보와 구분하지 않고 표시하지 않아요.',
    }
  }
  return {
    title: '인터넷 연결을 확인해 주세요',
    description: '연결이 복구되면 다시 시도할 수 있어요.',
  }
}

export function UnconfirmedCheckinsPage({
  services = defaultServices,
}: {
  services?: UnconfirmedCheckinsPageServices
}) {
  const navigate = useNavigate()
  const [items, setItems] = useState<UnconfirmedCheckinItem[]>([])
  const [medications, setMedications] = useState<
    Record<string, MedicationOccurrenceMedicationData | null>
  >({})
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [loadFailure, setLoadFailure] = useState<BacklogFailure | null>(null)
  const [loadMoreFailure, setLoadMoreFailure] = useState(false)
  const [selected, setSelected] = useState<{
    occurrenceId: string
    status: MedicationCheckinUserStatus
  } | null>(null)
  const [savingOccurrenceId, setSavingOccurrenceId] = useState<string | null>(null)
  const [saveError, setSaveError] = useState('')
  const [notice, setNotice] = useState('')
  const checkinAttemptRef = useRef<CheckinAttempt | null>(null)

  const loadMedicationDetails = useCallback(async (
    pageItems: UnconfirmedCheckinItem[],
    signal?: AbortSignal,
  ) => {
    const entries = await Promise.all(
      pageItems.map(async (item) => {
        try {
          const response = await services.getMedicationOccurrenceMedication(
            item.occurrence_id,
            signal,
          )
          return [
            item.occurrence_id,
            isMatchingHistoricalMedication(item, response.data)
              ? response.data
              : null,
          ] as const
        } catch (error) {
          if (error instanceof ApiError && error.status === 401) throw error
          if (signal?.aborted) throw error
          return [item.occurrence_id, null] as const
        }
      }),
    )
    return Object.fromEntries(entries)
  }, [services])

  const handleAuthError = useCallback((error: unknown): boolean => {
    if (!(error instanceof ApiError) || error.status !== 401) return false
    clearAuthenticatedSession()
    navigate('/login', { replace: true })
    return true
  }, [navigate])

  const loadFirstPage = useCallback(async (signal?: AbortSignal) => {
    setIsLoading(true)
    setLoadFailure(null)
    setLoadMoreFailure(false)
    try {
      const response = await services.getUnconfirmedCheckins({
        limit: PAGE_LIMIT,
        signal,
      })
      const details = await loadMedicationDetails(response.data.items, signal)
      if (signal?.aborted) return null
      setItems(response.data.items)
      setMedications(details)
      setNextCursor(response.data.next_cursor)
      return response.data.items
    } catch (error) {
      if (signal?.aborted) return null
      if (handleAuthError(error)) return null
      setItems([])
      setMedications({})
      setNextCursor(null)
      setLoadFailure(classifyBacklogFailure(error))
      return null
    } finally {
      if (!signal?.aborted) setIsLoading(false)
    }
  }, [handleAuthError, loadMedicationDetails, services])

  const loadAllPages = useCallback(async () => {
    setIsLoading(true)
    setLoadFailure(null)
    setLoadMoreFailure(false)
    try {
      const allItems: UnconfirmedCheckinItem[] = []
      const seenCursors = new Set<string>()
      let cursor: string | undefined

      do {
        const response = await services.getUnconfirmedCheckins({
          limit: 100,
          cursor,
        })
        allItems.push(...response.data.items)
        cursor = response.data.next_cursor ?? undefined
        if (cursor && seenCursors.has(cursor)) {
          throw new Error('Repeated UNCONFIRMED cursor')
        }
        if (cursor) seenCursors.add(cursor)
      } while (cursor)

      const details = await loadMedicationDetails(allItems)
      setItems(allItems)
      setMedications(details)
      setNextCursor(null)
      return allItems
    } catch (error) {
      if (handleAuthError(error)) return null
      setItems([])
      setMedications({})
      setNextCursor(null)
      setLoadFailure(classifyBacklogFailure(error))
      return null
    } finally {
      setIsLoading(false)
    }
  }, [handleAuthError, loadMedicationDetails, services])

  useEffect(() => {
    const controller = new AbortController()
    void loadFirstPage(controller.signal)
    return () => controller.abort()
  }, [loadFirstPage])

  const loadMore = async () => {
    if (!nextCursor || isLoadingMore) return
    const cursor = nextCursor
    setIsLoadingMore(true)
    setLoadMoreFailure(false)
    try {
      const response = await services.getUnconfirmedCheckins({
        limit: PAGE_LIMIT,
        cursor,
      })
      const details = await loadMedicationDetails(response.data.items)
      setItems((current) => [...current, ...response.data.items])
      setMedications((current) => ({ ...current, ...details }))
      setNextCursor(response.data.next_cursor)
    } catch (error) {
      if (handleAuthError(error)) return
      if (isUnconfirmedCursorNotFoundError(error)) {
        const refreshed = await loadFirstPage()
        if (refreshed) {
          setNotice('목록이 변경되어 처음부터 다시 불러왔어요.')
        }
      } else {
        setLoadMoreFailure(true)
      }
    } finally {
      setIsLoadingMore(false)
    }
  }

  const submitCorrection = async (
    item: UnconfirmedCheckinItem,
    status: MedicationCheckinUserStatus,
  ) => {
    if (savingOccurrenceId || isLoadingMore) return
    const requestPayload: PutMedicationCheckinInput = {
      status,
      expectedRevision: item.revision,
    }
    const attempt = resolveLogicalMutationAttempt(
      checkinAttemptRef.current,
      'CHECKIN_PUT',
      item.occurrence_id,
      requestPayload,
      item.revision,
      services.createCheckinIdempotencyKey,
    )
    checkinAttemptRef.current = attempt
    setSelected({ occurrenceId: item.occurrence_id, status })
    setSavingOccurrenceId(item.occurrence_id)
    setSaveError('')
    setNotice('')

    try {
      await services.putMedicationCheckin(
        attempt.targetId,
        attempt.requestPayload,
        attempt.idempotencyKey,
      )
      checkinAttemptRef.current = null
      setSelected(null)
      const refreshed = await loadFirstPage()
      if (refreshed) setNotice('복약 기록을 저장하고 목록을 새로 불러왔어요.')
    } catch (error) {
      if (handleAuthError(error)) {
        checkinAttemptRef.current = null
        return
      }
      if (isCheckinRevisionConflictError(error)) {
        checkinAttemptRef.current = null
        setSelected(null)
        const refreshed = await loadAllPages()
        if (refreshed) {
          setNotice(
            refreshed.some((candidate) => candidate.occurrence_id === item.occurrence_id)
              ? '기록이 다른 곳에서 변경됐어요. 최신 상태에서 다시 선택해 주세요.'
              : '이 기록은 다른 곳에서 이미 보완되어 목록에서 제외됐어요.',
          )
        }
      } else if (isOccurrenceNotFoundError(error)) {
        checkinAttemptRef.current = null
        setSelected(null)
        const refreshed = await loadFirstPage()
        if (refreshed) setNotice('기록 상태가 변경되어 목록을 새로 불러왔어요.')
      } else if (isCheckinValidationError(error)) {
        setSaveError('이 선택을 저장할 수 없어요. 상태를 확인한 뒤 다시 시도해 주세요.')
      } else if (isCheckinConflictError(error)) {
        checkinAttemptRef.current = null
        setSelected(null)
        const refreshed = await loadFirstPage()
        if (refreshed) setNotice('기록 상태가 변경되어 목록을 새로 불러왔어요.')
      } else if (error instanceof ApiError && error.status >= 500) {
        setSaveError('기록을 저장하지 못했어요. 선택한 내용은 그대로 두었어요.')
      } else {
        setSaveError('연결을 확인한 뒤 다시 시도해 주세요. 선택한 내용은 그대로 두었어요.')
      }
    } finally {
      setSavingOccurrenceId(null)
    }
  }

  const failureCopy = loadFailure ? backlogFailureCopy(loadFailure) : null

  return (
    <div className="mvp-page unconfirmed-page">
      <MobileShell
        title="Dosey 도지"
        activeNavigation="일정"
        onBack={() => navigate('/schedule')}
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '일정') navigate('/schedule')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
          if (item === '메뉴') navigate('/menu')
        }}
      >
        <main className="app-scroll unconfirmed-page__content">
          <header className="unconfirmed-page__intro">
            <p>복약 기록 확인</p>
            <h1>확인하지 못한 복약 기록이 있어요</h1>
            <span>실제로 어떻게 복용하셨는지 알려주세요.</span>
          </header>

          {notice && <p className="unconfirmed-page__notice" role="status">{notice}</p>}

          {isLoading && (
            <StatusPanel
              variant="loading"
              title="미확인 기록을 불러오는 중이에요"
            />
          )}

          {!isLoading && failureCopy && (
            <StatusPanel
              variant="error-retryable"
              title={failureCopy.title}
              description={failureCopy.description}
              onRetry={() => void loadFirstPage()}
            />
          )}

          {!isLoading && !loadFailure && items.length === 0 && (
            <StatusPanel
              variant="empty"
              title="확인할 미확인 기록이 없어요"
              description="새로운 미확인 기록이 생기면 이 화면에서 다시 확인할 수 있어요."
            />
          )}

          {!isLoading && !loadFailure && items.length > 0 && (
            <section className="unconfirmed-page__list" aria-labelledby="unconfirmed-list-title">
              <div className="unconfirmed-page__list-heading">
                <h2 id="unconfirmed-list-title">미확인 기록</h2>
                <span>{items.length}건 표시</span>
              </div>
              {items.map((item) => {
                const medication = medications[item.occurrence_id]
                const selectedStatus = selected?.occurrenceId === item.occurrence_id
                  ? selected.status
                  : null
                const isSaving = savingOccurrenceId === item.occurrence_id
                const isUnavailable = medication === null

                return (
                  <Card className="unconfirmed-card" key={item.checkin_id}>
                    <p className="unconfirmed-card__date">
                      {formatScheduledDate(item.scheduled_local_date)} · {formatScheduledTime(item.scheduled_at)}
                    </p>
                    {isUnavailable ? (
                      <StatusPanel
                        className="unconfirmed-card__medication-error"
                        variant="error-final"
                        title="원래 약 정보를 확인하지 못했어요"
                        description="현재 처방의 약으로 바꾸어 표시하지 않아요. 목록을 다시 불러와 주세요."
                        action={(
                          <Button
                            variant="ghost"
                            disabled={isLoadingMore || Boolean(savingOccurrenceId)}
                            onClick={() => void loadFirstPage()}
                          >
                            약 정보 다시 불러오기
                          </Button>
                        )}
                      />
                    ) : (
                      <div className="unconfirmed-card__medication">
                        <span aria-hidden="true">◐</span>
                        <div>
                          <strong>{medicationLabel(medication)}</strong>
                          <small>예정 당시 처방 기준</small>
                        </div>
                      </div>
                    )}

                    <fieldset
                      disabled={
                        isUnavailable ||
                        isLoadingMore ||
                        Boolean(savingOccurrenceId)
                      }
                    >
                      <legend>실제로 어떻게 복용하셨나요?</legend>
                      <button
                        type="button"
                        className={selectedStatus === 'TAKEN' ? 'is-selected' : ''}
                        aria-pressed={selectedStatus === 'TAKEN'}
                        onClick={() => void submitCorrection(item, 'TAKEN')}
                      >
                        <span className="unconfirmed-card__choice-icon" aria-hidden="true">✓</span>
                        <span><strong>복용했어요</strong><small>복용한 기록으로 정정해요</small></span>
                      </button>
                      <button
                        type="button"
                        className={selectedStatus === 'NOT_TAKEN' ? 'is-selected' : ''}
                        aria-pressed={selectedStatus === 'NOT_TAKEN'}
                        onClick={() => void submitCorrection(item, 'NOT_TAKEN')}
                      >
                        <span className="unconfirmed-card__choice-icon is-not-taken" aria-hidden="true">−</span>
                        <span><strong>복용하지 않았어요</strong><small>복용하지 않은 기록으로 정정해요</small></span>
                      </button>
                    </fieldset>

                    {isSaving && (
                      <p className="unconfirmed-card__saving" role="status">선택한 기록을 저장하는 중이에요.</p>
                    )}
                    {selected?.occurrenceId === item.occurrence_id && saveError && (
                      <div className="unconfirmed-card__save-error" role="alert">
                        <strong>기록을 저장하지 못했어요</strong>
                        <p>{saveError}</p>
                        <Button
                          fullWidth
                          disabled={Boolean(savingOccurrenceId)}
                          onClick={() => void submitCorrection(item, selected.status)}
                        >
                          다시 저장하기
                        </Button>
                      </div>
                    )}
                  </Card>
                )
              })}

              {nextCursor && (
                <div className="unconfirmed-page__more">
                  {loadMoreFailure && (
                    <p role="alert">다음 기록을 불러오지 못했어요. 현재 목록은 그대로 유지돼요.</p>
                  )}
                  <Button
                    fullWidth
                    variant="secondary"
                    disabled={isLoadingMore || Boolean(savingOccurrenceId)}
                    onClick={() => void loadMore()}
                  >
                    {isLoadingMore ? '더 불러오는 중…' : '미확인 기록 더 보기'}
                  </Button>
                </div>
              )}
            </section>
          )}

          <Button
            className="unconfirmed-page__later"
            fullWidth
            variant="ghost"
            disabled={Boolean(savingOccurrenceId)}
            onClick={() => navigate('/schedule')}
          >
            지금은 확인하기 어려워요
          </Button>
        </main>
      </MobileShell>
    </div>
  )
}

export default UnconfirmedCheckinsPage
