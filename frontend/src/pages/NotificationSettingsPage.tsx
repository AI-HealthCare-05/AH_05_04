import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  getUserConsents,
} from '../api/userConsents'
import {
  getMedicationDay,
  type MedicationScheduleItem,
} from '../api/medicationSchedules'
import { MobileShell } from '../design-system/components'
import '../design-system/prototype.css'
import './MvpPages.css'
import './NotificationSettingsPage.css'

const KST_TIME_ZONE = 'Asia/Seoul'

function kstToday(): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: KST_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())

  const value = Object.fromEntries(
    parts.map((part) => [part.type, part.value]),
  )

  return `${value.year}-${value.month}-${value.day}`
}

function periodLabel(time: string): string {
  const hour = Number(time.slice(0, 2))
  if (hour < 11) return '아침'
  if (hour < 17) return '점심'
  return '저녁'
}

function confirmedTimes(items: MedicationScheduleItem[]): string[] {
  const times = items.flatMap((item) => {
    if (
      item.schedule_item_status !== 'READY' ||
      item.schedule?.status !== 'ACTIVE'
    ) {
      return []
    }

    return item.schedule.local_times
  })

  return [...new Set(times)].sort()
}

function NotificationSettingsPage() {
  const navigate = useNavigate()
  const [times, setTimes] = useState<string[] | null>(null)
  const [notificationEnabled, setNotificationEnabled] = useState<boolean | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reloadVersion, setReloadVersion] = useState(0)

  useEffect(() => {
    const controller = new AbortController()

    setTimes(null)
    setNotificationEnabled(null)
    setLoadError(null)

    void Promise.all([
      getMedicationDay(kstToday(), controller.signal),
      getUserConsents(controller.signal),
    ])
      .then(([scheduleResponse, consentResponse]) => {
        setTimes(
          confirmedTimes(scheduleResponse.data.schedule_items),
        )

        const notificationConsent =
          consentResponse.data.find(
            (consent) => consent.purpose === 'NOTIFICATION',
          )

        setNotificationEnabled(
          notificationConsent?.is_granted ?? false,
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return

        if (error instanceof ApiError && error.status === 401) {
          setLoadError('로그인 정보를 다시 확인해 주세요.')
          return
        }

        setLoadError('알림 설정을 불러오지 못했어요.')
      })

    return () => controller.abort()
  }, [reloadVersion])


  return (
    <div className="mvp-page mvp-notification-settings-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate('/menu')}
        activeNavigation="메뉴"
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '일정') navigate('/schedule')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
          if (item === '메뉴') navigate('/menu')
        }}
      >
        <main className="app-scroll mvp-notification-settings">
          <h2 className="mvp-notification-settings__title">
            알림 설정
          </h2>

          <section className="mvp-notification-settings__info">
            <p>
              확정한 복약 일정에 맞춰<br />
              Dosey에서 알려드려요.
            </p>
          </section>

          <h3 className="mvp-notification-settings__section-title">
            복약 알림
          </h3>

          <section className="mvp-notification-settings__reminder-status">
            <strong>앱 내 알림</strong>

            <span
              className={`mvp-notification-settings__toggle ${
                notificationEnabled === true ? 'is-on' : ''
              }`}
              aria-label={
                notificationEnabled === true
                  ? '앱 내 알림 동의 상태 켜짐'
                  : '앱 내 알림 동의 상태 꺼짐'
              }
              role="img"
            >
              <span />
            </span>
          </section>

          <h3 className="mvp-notification-settings__section-title">
            알림받을 일정
          </h3>

          {times === null && loadError === null && (
            <section
              className="mvp-notification-settings__schedule-state"
              role="status"
            >
              복약 일정을 확인하고 있어요.
            </section>
          )}

          {loadError && (
            <section
              className="mvp-notification-settings__schedule-state"
              role="alert"
            >
              <p>{loadError}</p>
              <button
                type="button"
                onClick={() =>
                  setReloadVersion((value) => value + 1)
                }
              >
                다시 시도
              </button>
            </section>
          )}

          {times && times.length > 0 && (
            <section
              className="mvp-notification-settings__schedule-list"
              aria-label="확정된 복약 일정"
            >
              {times.map((time) => (
                <div
                  className="mvp-notification-settings__schedule-row"
                  key={time}
                >
                  <strong>{periodLabel(time)}</strong>
                  <span>{time}</span>
                </div>
              ))}
            </section>
          )}

          {times?.length === 0 && (
            <section className="mvp-notification-settings__schedule-state">
              확정된 복약 일정이 없어요.
            </section>
          )}

          <button
            type="button"
            className="mvp-notification-settings__schedule-action"
            onClick={() => navigate('/schedule')}
          >
            <span>복약 일정 수정하기</span>
            <span aria-hidden="true">›</span>
          </button>

          <p className="mvp-notification-settings__boundary">
            현재는 확정된 복약 일정만 보여드려요.
          </p>
        </main>
      </MobileShell>
    </div>
  )
}

export default NotificationSettingsPage
