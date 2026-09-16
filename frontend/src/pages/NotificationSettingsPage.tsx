import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MobileShell } from '../design-system/components'
import {
  disableWebPush,
  enableWebPush,
  getWebPushState,
  hasStoredWebPushBinding,
  type WebPushState,
} from '../features/push/webPush'
import '../design-system/prototype.css'
import './MvpPages.css'
import './NotificationSettingsPage.css'

const STATE_COPY: Record<WebPushState, { title: string; detail: string }> = {
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
  subscription_failed: {
    title: '알림 연결을 완료하지 못했어요',
    detail: '네트워크와 브라우저 설정을 확인한 뒤 다시 시도해 주세요.',
  },
}

function NotificationSettingsPage() {
  const navigate = useNavigate()
  const [state, setState] = useState<WebPushState | null>(null)
  const [isUpdating, setIsUpdating] = useState(false)

  const refreshState = useCallback(() => {
    void getWebPushState().then(setState)
  }, [])

  useEffect(() => {
    refreshState()
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') refreshState()
    }
    window.addEventListener('focus', refreshState)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      window.removeEventListener('focus', refreshState)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [refreshState])

  const handleEnable = async () => {
    if (isUpdating) return
    setIsUpdating(true)
    setState(await enableWebPush())
    setIsUpdating(false)
  }

  const handleDisable = async () => {
    if (isUpdating) return
    setIsUpdating(true)
    try {
      await disableWebPush()
      setState('unrequested')
    } catch {
      setState('subscription_failed')
    } finally {
      setIsUpdating(false)
    }
  }

  const handleRecovery = () => {
    if (Notification.permission === 'granted') {
      void handleEnable()
      return
    }
    void handleDisable()
  }

  const copy = state ? STATE_COPY[state] : null
  const hasPendingBindingCleanup = state === 'subscription_failed' && hasStoredWebPushBinding()
  const canEnable = state !== null && !['unsupported', 'denied', 'granted'].includes(state) && !hasPendingBindingCleanup

  return (
    <div className="mvp-page mvp-notification-settings-page">
      <MobileShell
        title="알림 설정"
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
        <main className="app-scroll mvp-page__content mvp-notification-settings">
          <header>
            <h2 className="mvp-page__title">Push 알림</h2>
            <p className="mvp-page__description">
              복약 알림이 도착했다는 사실만 알려 드려요. 자세한 내용은 로그인 후 앱에서 확인해요.
            </p>
          </header>

          <section className="mvp-notification-settings__status" aria-live="polite" aria-busy={state === null}>
            <h3>{copy?.title ?? '알림 상태를 확인하고 있어요'}</h3>
            <p>{copy?.detail ?? '잠시만 기다려 주세요.'}</p>
          </section>

          {state === 'granted' || hasPendingBindingCleanup ? (
            <button type="button" className="mvp-notification-settings__secondary" disabled={isUpdating} onClick={hasPendingBindingCleanup ? handleRecovery : () => void handleDisable()}>
              {isUpdating ? '알림 연결 확인 중...' : hasPendingBindingCleanup ? '연결 다시 시도' : '알림 끄기'}
            </button>
          ) : (
            <button type="button" className="mvp-notification-settings__primary" disabled={!canEnable || isUpdating} onClick={() => void handleEnable()}>
              {isUpdating ? '알림 연결 중...' : '알림 켜기'}
            </button>
          )}

          <button type="button" className="mvp-notification-settings__fallback" onClick={() => navigate('/notifications')}>
            앱 안의 알림 목록 보기
          </button>

          <p className="mvp-notification-settings__notice">
            Push 수신이나 알림 클릭만으로 읽음 또는 복약 완료 상태가 바뀌지 않아요.
          </p>
        </main>
      </MobileShell>
    </div>
  )
}

export default NotificationSettingsPage
