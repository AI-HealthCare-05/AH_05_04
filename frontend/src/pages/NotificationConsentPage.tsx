import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { createGuide } from '../api/guides'
import { ApiError } from '../api/client'
import { enableWebPush } from '../features/push/webPush'
import { Button, MobileShell } from '../design-system/components'
import bellIcon from '../assets/icon-bell-notification.svg'
import '../design-system/prototype.css'
import './MvpPages.css'
import './NotificationConsentPage.css'

type NotificationConsentLocationState = {
  prescriptionId?: unknown
}

function getPrescriptionId(state: unknown): string | null {
  if (typeof state !== 'object' || state === null) return null
  const prescriptionId = (state as NotificationConsentLocationState).prescriptionId
  return typeof prescriptionId === 'string' && prescriptionId.length > 0 ? prescriptionId : null
}

function NotificationConsentPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const prescriptionId = getPrescriptionId(location.state)

  const [isProcessing, setIsProcessing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!prescriptionId) {
    // 이 화면은 처방 확정 직후에만 들어와요. 새로고침 등으로 targetId를 잃으면
    // 검토 화면으로 돌려보내 처방 확정 상태부터 다시 확인하게 합니다.
    return <Navigate to="/prescriptions/review" replace />
  }

  const proceedToGuide = async (requestPush: boolean) => {
    if (isProcessing) return
    try {
      setIsProcessing(true)
      setError(null)
      if (requestPush) {
        // Push 권한 요청이나 구독 실패는 가이드 생성을 막지 않아요. 실제 수신 여부는
        // 알림 설정 화면에서 다시 확인할 수 있어요.
        await enableWebPush().catch(() => undefined)
      }
      const response = await createGuide(prescriptionId)
      navigate(`/guides/${response.data.guide_id}`, { replace: true })
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : '복약 가이드를 만드는 중 오류가 발생했습니다.',
      )
      setIsProcessing(false)
    }
  }

  return (
    <div className="mvp-page notification-consent-page">
      <MobileShell title="Dosey 도지" hideNavigation>
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav notification-consent">
          <span className="notification-consent__icon" aria-hidden="true">
            <img src={bellIcon} alt="" />
          </span>
          <h1 className="mvp-page__title">복약 알림을 받을까요?</h1>
          <p className="mvp-page__description">
            정해진 시간에 복용 알림을 보내드려요.
            알림은 나중에 설정에서 언제든 바꿀 수 있어요.
          </p>
          <ul className="notification-consent__points">
            <li>아침·점심·저녁 등 설정한 시간에 알림을 보내요</li>
            <li>기기 알림 권한 요청이 한 번 표시돼요</li>
          </ul>

          {error && <p className="mvp-form__message" role="alert">{error}</p>}

          <Button fullWidth disabled={isProcessing} onClick={() => void proceedToGuide(true)}>
            {isProcessing ? '준비 중...' : '알림 받기'}
          </Button>
          <button
            type="button"
            className="notification-consent__later"
            disabled={isProcessing}
            onClick={() => void proceedToGuide(false)}
          >
            나중에 하기
          </button>
        </main>
      </MobileShell>
    </div>
  )
}

export default NotificationConsentPage
