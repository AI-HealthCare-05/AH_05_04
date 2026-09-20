import { useCallback, useEffect, useState } from 'react'
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
import {
  disableWebPush,
  enableWebPush,
  getWebPushLaunchContext,
  getWebPushState,
  hasStoredWebPushBinding,
  type WebPushLaunchContext,
  type WebPushState,
} from '../features/push/webPush'
import '../design-system/prototype.css'
import './MvpPages.css'
import './NotificationSettingsPage.css'

const KST_TIME_ZONE = 'Asia/Seoul'

const PUSH_STATE_COPY: Record<WebPushState, { title: string; detail: string }> = {
  unsupported: {
    title: '이 브라우저에서는 Push 알림을 사용할 수 없어요',
    detail: '앱 안의 알림 목록과 복약 기록은 계속 사용할 수 있어요.',
  },
  unrequested: {
    title: 'Push 알림을 사용하지 않고 있어요',
    detail: '아래 버튼을 직접 누를 때만 브라우저가 알림 권한을 요청해요.',
  },
  granted: {
    title: 'Push 알림이 켜져 있어요',
    detail: '잠금 화면에는 약명이나 용량 없이 일반 문구만 표시해요.',
  },
  denied: {
    title: '브라우저에서 알림이 차단되어 있어요',
    detail: '반복해서 권한을 요청하지 않아요. 브라우저 설정에서 권한을 변경할 수 있어요.',
  },
  revoked: {
    title: '이전에 사용하던 알림 연결이 해제되었어요',
    detail: '브라우저 권한과 구독을 확인한 뒤 알림을 다시 켜 주세요.',
  },
  config_unavailable: {
    title: 'Push 알림 설정이 아직 준비되지 않았어요',
    detail: '관리자 설정이 완료되면 다시 시도해 주세요. 앱 안의 알림 목록은 계속 사용할 수 있어요.',
  },
  subscription_failed: {
    title: '알림 연결을 완료하지 못했어요',
    detail: '네트워크와 브라우저 설정을 확인한 뒤 다시 시도해 주세요.',
  },
}

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

  const [launchContext] = useState<WebPushLaunchContext>(
    () => getWebPushLaunchContext(),
  )
  const [pushState, setPushState] = useState<WebPushState | null>(null)
  const [isPushUpdating, setIsPushUpdating] = useState(false)
  const needsIOSInstall = launchContext === 'ios-browser'

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

  const refreshPushState = useCallback(() => {
    if (needsIOSInstall) return
    void getWebPushState().then(setPushState)
  }, [needsIOSInstall])

  useEffect(() => {
    refreshPushState()

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshPushState()
      }
    }

    window.addEventListener('focus', refreshPushState)
    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      window.removeEventListener('focus', refreshPushState)
      document.removeEventListener(
        'visibilitychange',
        handleVisibilityChange,
      )
    }
  }, [refreshPushState])

  const handleEnablePush = async () => {
    if (isPushUpdating) return

    setIsPushUpdating(true)
    setPushState(await enableWebPush())
    setIsPushUpdating(false)
  }

  const handleDisablePush = async () => {
    if (isPushUpdating) return

    setIsPushUpdating(true)

    try {
      await disableWebPush()
      setPushState('unrequested')
    } catch {
      setPushState('subscription_failed')
    } finally {
      setIsPushUpdating(false)
    }
  }

  const handlePushRecovery = () => {
    if (
      typeof Notification !== 'undefined' &&
      Notification.permission === 'granted'
    ) {
      void handleEnablePush()
      return
    }

    void handleDisablePush()
  }

  const pushCopy = needsIOSInstall
    ? {
        title: '홈 화면에 추가해 주세요',
        detail:
          'Safari의 공유 버튼을 누른 뒤 ‘홈 화면에 추가’를 선택하세요. 설치한 Dosey 도지를 홈 화면에서 열면 알림을 켤 수 있어요.',
      }
    : pushState
      ? PUSH_STATE_COPY[pushState]
      : null

  const hasPendingBindingCleanup =
    pushState === 'subscription_failed' &&
    hasStoredWebPushBinding()

  const canEnablePush =
    pushState !== null &&
    !['unsupported', 'denied', 'granted'].includes(pushState) &&
    !hasPendingBindingCleanup


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

          <h3 className="mvp-notification-settings__section-title">
            기기 Push 알림
          </h3>

          <section
            className="mvp-notification-settings__push-status"
            aria-live="polite"
            aria-busy={!needsIOSInstall && pushState === null}
          >
            <h4>
              {pushCopy?.title ?? '알림 상태를 확인하고 있어요'}
            </h4>
            <p>{pushCopy?.detail ?? '잠시만 기다려 주세요.'}</p>
          </section>

          {!needsIOSInstall && (
            pushState === 'granted' || hasPendingBindingCleanup ? (
              <button
                type="button"
                className="mvp-notification-settings__push-secondary"
                disabled={isPushUpdating}
                onClick={
                  hasPendingBindingCleanup
                    ? handlePushRecovery
                    : () => void handleDisablePush()
                }
              >
                {isPushUpdating
                  ? '알림 연결 확인 중...'
                  : hasPendingBindingCleanup
                    ? '연결 다시 시도'
                    : '알림 끄기'}
              </button>
            ) : (
              <button
                type="button"
                className="mvp-notification-settings__push-primary"
                disabled={!canEnablePush || isPushUpdating}
                onClick={() => void handleEnablePush()}
              >
                {isPushUpdating ? '알림 연결 중...' : '알림 켜기'}
              </button>
            )
          )}

          <button
            type="button"
            className="mvp-notification-settings__push-fallback"
            onClick={() => navigate('/notifications')}
          >
            앱 안의 알림 목록 보기
          </button>

          <p className="mvp-notification-settings__push-notice">
            Push 수신이나 알림 클릭만으로 읽음 또는 복약 완료 상태가
            바뀌지 않아요.
          </p>
        </main>
      </MobileShell>
    </div>
  )
}

export default NotificationSettingsPage
