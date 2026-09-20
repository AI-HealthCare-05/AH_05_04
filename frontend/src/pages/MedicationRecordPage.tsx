import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  getMedicationDay,
  getOccurrenceMedication,
  type MedicationDayResponse,
  type MedicationOccurrenceData,
} from '../api/medicationSchedules'
import { Button, Card, MobileShell } from '../design-system/components'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import bellIcon from '../assets/icon-bell-notification.svg'
import {
  buildRecordWeek,
  formatKstTime,
  formatLocalDate,
  isRecordReadOnly,
  isValidLocalDate,
  kstToday,
  medicationDescription,
  occurrenceStateLabel,
  type RecordMedicationDetail,
} from './medicationRecordView'
import '../design-system/prototype.css'
import './MvpPages.css'
import './MedicationRecordPage.css'

export type MedicationRecordPageServices = {
  getMedicationDay: typeof getMedicationDay
  getOccurrenceMedication: typeof getOccurrenceMedication
}

const defaultServices: MedicationRecordPageServices = {
  getMedicationDay,
  getOccurrenceMedication,
}

type LoadFailure = 'AUTH' | 'NOT_FOUND' | 'UNKNOWN'

function classifyLoadFailure(error: unknown): LoadFailure {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return 'AUTH'
    if (error.status === 404) return 'NOT_FOUND'
  }
  return 'UNKNOWN'
}

function failureCopy(failure: LoadFailure): { title: string; body: string } {
  if (failure === 'AUTH') {
    return {
      title: '다시 로그인해 주세요',
      body: '로그인 정보가 만료됐어요.',
    }
  }
  if (failure === 'NOT_FOUND') {
    return {
      title: '복약 기록을 찾을 수 없어요',
      body: '선택한 날짜의 기록을 확인할 수 없어요.',
    }
  }
  return {
    title: '복약 기록을 불러오지 못했어요',
    body: '잠시 후 다시 시도해 주세요.',
  }
}

export function MedicationRecordPage({
  services = defaultServices,
}: {
  services?: MedicationRecordPageServices
} = {}) {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedDate = searchParams.get('date')
  const selectedDate = isValidLocalDate(requestedDate) ? requestedDate : kstToday()

  const [occurrences, setOccurrences] = useState<MedicationOccurrenceData[]>([])
  const [medications, setMedications] = useState<
    Record<string, RecordMedicationDetail | null>
  >({})
  const [isLoading, setIsLoading] = useState(true)
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(null)
  const [reloadVersion, setReloadVersion] = useState(0)

  // 예정 시각이 지나면 read-only가 풀리도록 주기적으로 갱신한다.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    let isDisposed = false
    const controller = new AbortController()

    setIsLoading(true)
    setLoadFailure(null)
    setOccurrences([])
    setMedications({})

    const load = async () => {
      try {
        const response: MedicationDayResponse = await services.getMedicationDay(
          selectedDate,
          controller.signal,
        )
        if (isDisposed) return

        const dayOccurrences = response.data.occurrences
          .filter((occurrence) => occurrence.scheduled_local_date === selectedDate)
          .slice()
          .sort((left, right) =>
            left.scheduled_at.localeCompare(right.scheduled_at),
          )
        setOccurrences(dayOccurrences)

        // 약 정보는 기존 occurrence medication 계약을 그대로 쓴다.
        const details = await Promise.all(
          dayOccurrences.map(async (occurrence) => {
            try {
              const detail = await services.getOccurrenceMedication(
                occurrence.occurrence_id,
                controller.signal,
              )
              return [occurrence.occurrence_id, detail.data] as const
            } catch {
              // 약 정보 실패가 기록 목록 전체를 막지 않는다.
              return [occurrence.occurrence_id, null] as const
            }
          }),
        )
        if (isDisposed) return
        setMedications(Object.fromEntries(details))
      } catch (error) {
        if (isDisposed || controller.signal.aborted) return
        setLoadFailure(classifyLoadFailure(error))
      } finally {
        if (!isDisposed) setIsLoading(false)
      }
    }

    void load()

    return () => {
      isDisposed = true
      controller.abort()
    }
  }, [reloadVersion, selectedDate, services])

  const goToLogin = () => {
    clearAuthenticatedSession()
    navigate('/login', { replace: true })
  }

  const weekCells = buildRecordWeek(selectedDate, kstToday())
  const visibleOccurrences = occurrences.filter(
    (occurrence) => occurrence.status !== 'CANCELLED',
  )

  return (
    <div className="mvp-page medication-record-page">
      <MobileShell
        title="Dosey 도지"
        activeNavigation="일정"
        onBack={() => navigate('/menu')}
        headerAction={
          <button
            className="medication-record__notification"
            type="button"
            aria-label="알림"
            onClick={() => navigate('/notifications')}
          >
            <img src={bellIcon} alt="" aria-hidden="true" />
          </button>
        }
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '일정') navigate('/schedule')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
          if (item === '메뉴') navigate('/menu')
        }}
      >
        <main className="app-scroll medication-record">
          <header className="medication-record__intro">
            <h1>복약 기록</h1>
            <div className="medication-record-week">
              <p className="medication-record-week__selected" aria-live="polite">
                {formatLocalDate(selectedDate)}
              </p>
              <div
                className="medication-record-week__days"
                role="group"
                aria-label="날짜 선택"
              >
                {weekCells.map((cell) => (
                  <button
                    key={cell.date}
                    type="button"
                    className="medication-record-week__day"
                    aria-pressed={cell.isSelected}
                    aria-current={cell.isToday ? 'date' : undefined}
                    disabled={cell.isFuture}
                    aria-label={`${formatLocalDate(cell.date)}${cell.isToday ? ' 오늘' : ''}`}
                    onClick={() => setSearchParams({ date: cell.date })}
                  >
                    <span className="medication-record-week__weekday">
                      {cell.weekday}
                    </span>
                    <span className="medication-record-week__date">
                      {cell.dayOfMonth}
                    </span>
                    {cell.isToday && (
                      <span className="medication-record-week__today">오늘</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          </header>

          {isLoading && (
            <Card className="medication-record__state" aria-live="polite">
              <div role="status">복약 기록을 불러오는 중입니다.</div>
            </Card>
          )}

          {!isLoading && loadFailure && (() => {
            const copy = failureCopy(loadFailure)
            return (
              <Card className="medication-record__state">
                <h2>{copy.title}</h2>
                <p role="alert">{copy.body}</p>
                <Button
                  fullWidth
                  onClick={loadFailure === 'AUTH'
                    ? goToLogin
                    : () => setReloadVersion((version) => version + 1)}
                >
                  {loadFailure === 'AUTH' ? '로그인하기' : '다시 시도'}
                </Button>
              </Card>
            )
          })()}

          {!isLoading && !loadFailure && (
            <section
              className="medication-record__list"
              aria-labelledby="medication-record-list-title"
            >
              <h2 id="medication-record-list-title">
                {formatLocalDate(selectedDate)} 복약 기록
              </h2>

              {visibleOccurrences.length === 0 ? (
                <Card className="medication-record__empty">
                  <p>이 날짜에 표시할 복약 기록이 없어요.</p>
                </Card>
              ) : visibleOccurrences.map((occurrence) => {
                const medication = medications[occurrence.occurrence_id]
                const readOnly = isRecordReadOnly(occurrence, now)
                // 기존 Check-in 화면(CHECKIN-01)으로 진입한다.
                // 기록된 결과의 약별 상세/수정(RECORD-02)은 후속 구현 대상이다.
                const route = `/schedule/occurrences/${occurrence.occurrence_id}?date=${encodeURIComponent(occurrence.scheduled_local_date)}`
                return (
                  <article
                    key={occurrence.occurrence_id}
                    className={`medication-record-card${readOnly ? ' medication-record-card--readonly' : ''}`}
                  >
                    <div className="medication-record-card__body">
                      <strong className="medication-record-card__time">
                        {formatKstTime(occurrence.scheduled_at)}
                      </strong>
                      <span className="medication-record-card__medication">
                        {medication
                          ? medicationDescription(medication)
                          : '약 정보를 확인할 수 없어요'}
                      </span>
                      <span
                        className={`medication-record-card__state medication-record-card__state--${occurrence.checkin?.status?.toLowerCase() ?? occurrence.status.toLowerCase()}`}
                      >
                        {occurrenceStateLabel(occurrence)}
                      </span>
                      {readOnly && (
                        <span className="medication-record-card__readonly-note">
                          {formatKstTime(occurrence.scheduled_at)}부터 복약 기록을 남길 수 있어요.
                        </span>
                      )}
                    </div>
                    {!readOnly && (
                      <>
                        <span
                          className="medication-record-card__chevron"
                          aria-hidden="true"
                        />
                        <Button
                          fullWidth
                          className="medication-record-card__action"
                          onClick={() => navigate(route)}
                        >
                          {occurrence.checkin ? '복약 기록 수정하기' : '복용 여부 기록하기'}
                        </Button>
                      </>
                    )}
                  </article>
                )
              })}
            </section>
          )}
        </main>
      </MobileShell>
    </div>
  )
}

export default MedicationRecordPage
