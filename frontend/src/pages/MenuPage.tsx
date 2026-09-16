import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { logout } from '../api/auth'
import personIcon from '../assets/menu-person.svg'
import clipboardIcon from '../assets/menu-clipboard.svg'
import reportIcon from '../assets/menu-report.svg'
import bellIcon from '../assets/menu-bell.svg'
import logoutIcon from '../assets/menu-logout.svg'
import chevronIcon from '../assets/menu-chevron.svg'
import notificationIcon from '../assets/icon-bell-notification.svg'
import { MobileShell } from '../design-system/components'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import { beginWebPushLogoutCleanup } from '../features/push/webPush'
import '../design-system/prototype.css'
import './MvpPages.css'
import './MenuPage.css'

const ICONS = {
  person: personIcon,
  clipboard: clipboardIcon,
  report: reportIcon,
  bell: bellIcon,
  logout: logoutIcon,
  chevron: chevronIcon,
} as const

type MenuRowProps = {
  icon: keyof typeof ICONS
  label: string
  detail?: string
  disabled?: boolean
  onClick?: () => void
}

function MenuRow({ icon, label, detail, disabled = false, onClick }: MenuRowProps) {
  return (
    <button
      className="mvp-menu__row"
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-label={disabled ? `${label} (준비 중)` : label}
    >
      <span className={`mvp-menu__icon ${icon === 'logout' ? 'is-neutral' : ''}`}>
        <img src={ICONS[icon]} alt="" width="24" height="24" />
      </span>
      <span className="mvp-menu__copy">
        <strong>{label}</strong>
        {detail && <small>{detail}</small>}
      </span>
      <img className="mvp-menu__chevron" src={ICONS.chevron} alt="" width="24" height="24" />
    </button>
  )
}

function MenuPage() {
  const navigate = useNavigate()
  const [isLoggingOut, setIsLoggingOut] = useState(false)

  const handleLogout = () => {
    if (isLoggingOut) return
    setIsLoggingOut(true)
    beginWebPushLogoutCleanup()
    logout().catch(() => undefined)
    clearAuthenticatedSession()
    navigate('/start', { replace: true })
  }

  return (
    <div className="mvp-page mvp-menu-page">
      <MobileShell
        title="Dosey 도지"
        headerAction={
          <button
            className="mvp-menu__notification"
            type="button"
            aria-label="알림"
            onClick={() => navigate('/notifications')}
          >
            <img src={notificationIcon} alt="" aria-hidden="true" />
          </button>
        }
        activeNavigation="메뉴"
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '일정') navigate('/schedule')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
        }}
      >
        <main className="app-scroll mvp-page__content mvp-menu">
          <h2 className="mvp-page__title">메뉴</h2>

          <section className="mvp-menu__section" aria-labelledby="menu-user-title">
            <h3 id="menu-user-title">내 정보</h3>
            <div className="mvp-menu__card">
              <MenuRow icon="person" label="사용자 정보" onClick={() => navigate('/profile')} />
            </div>
          </section>

          <section className="mvp-menu__section" aria-labelledby="menu-medication-title">
            <h3 id="menu-medication-title">복약 관리</h3>
            <div className="mvp-menu__card">
              <MenuRow icon="clipboard" label="복약 일정" onClick={() => navigate('/schedule')} />
              <MenuRow icon="report" label="복약 리포트" detail="7일 · 30일" onClick={() => navigate('/report')} />
            </div>
          </section>

          <section className="mvp-menu__section" aria-labelledby="menu-settings-title">
            <h3 id="menu-settings-title">설정</h3>
            <div className="mvp-menu__card">
              <MenuRow icon="bell" label="알림 설정" onClick={() => navigate('/settings/notifications')} />
            </div>
          </section>

          <section className="mvp-menu__section" aria-labelledby="menu-account-title">
            <h3 id="menu-account-title">계정</h3>
            <div className="mvp-menu__card">
              <MenuRow icon="logout" label={isLoggingOut ? '로그아웃 중...' : '로그아웃'} onClick={handleLogout} />
            </div>
          </section>
        </main>
      </MobileShell>
    </div>
  )
}

export default MenuPage
