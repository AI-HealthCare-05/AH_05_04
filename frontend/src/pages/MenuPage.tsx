import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { logout } from '../api/auth'
import notificationIcon from '../assets/icon-bell-notification.svg'
import { MobileShell } from '../design-system/components'
import '../design-system/prototype.css'
import './MvpPages.css'
import './MenuPage.css'

const ICONS = {
  person: 'data:image/svg+xml;base64,PHN2ZyBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJub25lIiBvdmVyZmxvdz0idmlzaWJsZSIgc3R5bGU9ImRpc3BsYXk6IGJsb2NrOyIgd2lkdGg9IjI0IiBoZWlnaHQ9IjI0IiB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxnIGlkPSJJY29uICYjMTk0OyYjMTgzOyBQZXJzb24iPgo8cGF0aCBpZD0iVmVjdG9yIiBkPSJNMjAgMjFDMjAgMTguODc4MyAxOS4xNTcxIDE2Ljg0MzQgMTcuNjU2OSAxNS4zNDMxQzE2LjE1NjYgMTMuODQyOSAxNC4xMjE3IDEzIDEyIDEzQzkuODc4MjcgMTMgNy44NDM0NCAxMy44NDI5IDYuMzQzMTUgMTUuMzQzMUM0Ljg0Mjg1IDE2Ljg0MzQgNCAxOC44NzgzIDQgMjEiIHN0cm9rZT0iIzE2NzdFQSIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KPHBhdGggaWQ9IlZlY3Rvcl8yIiBkPSJNMTIgMTFDMTQuMjA5MSAxMSAxNiA5LjIwOTE0IDE2IDdDMTYgNC43OTA4NiAxNC4yMDkxIDMgMTIgM0M5Ljc5MDg2IDMgOCA0Ljc5MDg2IDggN0M4IDkuMjA5MTQgOS43OTA4NiAxMSAxMiAxMVoiIHN0cm9rZT0iIzE2NzdFQSIgc3Ryb2tlLXdpZHRoPSIyIi8+CjwvZz4KPC9zdmc+Cg==',
  clipboard: 'data:image/svg+xml;base64,PHN2ZyBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJub25lIiBvdmVyZmxvdz0idmlzaWJsZSIgc3R5bGU9ImRpc3BsYXk6IGJsb2NrOyIgd2lkdGg9IjI0IiBoZWlnaHQ9IjI0IiB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxnIGlkPSJJY29uICYjMTk0OyYjMTgzOyBDbGlwYm9hcmQiPgo8cGF0aCBpZD0iVmVjdG9yIiBkPSJNMTcgNEg3QzUuODk1NDMgNCA1IDQuODk1NDMgNSA2VjE5QzUgMjAuMTA0NiA1Ljg5NTQzIDIxIDcgMjFIMTdDMTguMTA0NiAyMSAxOSAyMC4xMDQ2IDE5IDE5VjZDMTkgNC44OTU0MyAxOC4xMDQ2IDQgMTcgNFoiIHN0cm9rZT0iIzE2NzdFQSIgc3Ryb2tlLXdpZHRoPSIyIi8+CjxwYXRoIGlkPSJWZWN0b3JfMiIgZD0iTTkgNFYyLjhIMTVWNE05IDlIMTVNOSAxM0gxNU05IDE3SDEzIiBzdHJva2U9IiMxNjc3RUEiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+CjwvZz4KPC9zdmc+Cg==',
  report: 'data:image/svg+xml;base64,PHN2ZyBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJub25lIiBvdmVyZmxvdz0idmlzaWJsZSIgc3R5bGU9ImRpc3BsYXk6IGJsb2NrOyIgd2lkdGg9IjI0IiBoZWlnaHQ9IjI0IiB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxnIGlkPSJJY29uICYjMTk0OyYjMTgzOyBSZXBvcnQgY2hhcnQiPgo8cGF0aCBpZD0iVmVjdG9yIiBkPSJNNSAyMFYxMU0xMiAyMFY0TTE5IDIwVjEzIiBzdHJva2U9IiMxNjc3RUEiIHN0cm9rZS13aWR0aD0iMi40IiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KPHBhdGggaWQ9IlZlY3Rvcl8yIiBkPSJNMyAyMEgyMSIgc3Ryb2tlPSIjMTY3N0VBIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lYXA9InJvdW5kIi8+CjwvZz4KPC9zdmc+Cg==',
  bell: 'data:image/svg+xml;base64,PHN2ZyBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJub25lIiBvdmVyZmxvdz0idmlzaWJsZSIgc3R5bGU9ImRpc3BsYXk6IGJsb2NrOyIgd2lkdGg9IjI0IiBoZWlnaHQ9IjI0IiB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxnIGlkPSJJY29uICYjMTk0OyYjMTgzOyBCZWxsIHNldHRpbmdzIj4KPHBhdGggaWQ9IlZlY3RvciIgZD0iTTE4IDhDMTggNi40MDg3IDE3LjM2NzkgNC44ODI1OCAxNi4yNDI2IDMuNzU3MzZDMTUuMTE3NCAyLjYzMjE0IDEzLjU5MTMgMiAxMiAyQzEwLjQwODcgMiA4Ljg4MjU4IDIuNjMyMTQgNy43NTczNiAzLjc1NzM2QzYuNjMyMTQgNC44ODI1OCA2IDYuNDA4NyA2IDhDNiAxNSAzIDE1IDMgMTdIMjFDMjEgMTUgMTggMTUgMTggOFoiIHN0cm9rZT0iIzE2NzdFQSIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KPHBhdGggaWQ9IlZlY3Rvcl8yIiBkPSJNMTAgMjFIMTQiIHN0cm9rZT0iIzE2NzdFQSIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KPC9nPgo8L3N2Zz4K',
  logout: 'data:image/svg+xml;base64,PHN2ZyBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJub25lIiBvdmVyZmxvdz0idmlzaWJsZSIgc3R5bGU9ImRpc3BsYXk6IGJsb2NrOyIgd2lkdGg9IjI0IiBoZWlnaHQ9IjI0IiB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxnIGlkPSJJY29uICYjMTk0OyYjMTgzOyBMb2dvdXQiPgo8cGF0aCBpZD0iVmVjdG9yIiBkPSJNMTAgNUg1VjE5SDEwTTE0IDE2TDE4IDEyTDE0IDhNMTggMTJIOSIgc3Ryb2tlPSIjNTI2MDczIiBzdHJva2Utd2lkdGg9IjIuMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+CjwvZz4KPC9zdmc+Cg==',
  chevron: 'data:image/svg+xml;base64,PHN2ZyBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJub25lIiBvdmVyZmxvdz0idmlzaWJsZSIgc3R5bGU9ImRpc3BsYXk6IGJsb2NrOyIgd2lkdGg9IjI0IiBoZWlnaHQ9IjI0IiB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxnIGlkPSJDaGV2cm9uICYjMTk0OyYjMTgzOyAmIzIzNjsmIzEzMDsmIzE3MjsmIzIzNjsmIzE1NDsmIzE2OTsmIzIzNjsmIzE1ODsmIzE0NDsgJiMyMzY7JiMxNjA7JiMxNDk7JiMyMzU7JiMxNzk7JiMxODA7Ij4KPHBhdGggaWQ9IlZlY3RvciIgZD0iTTkgNUwxNiAxMkw5IDE5IiBzdHJva2U9IiM2NDc0OEIiIHN0cm9rZS13aWR0aD0iMi4yIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KPC9nPgo8L3N2Zz4K',
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
    logout().catch(() => undefined)
    localStorage.removeItem('access_token')
    navigate('/', { replace: true })
  }

  return (
    <div className="mvp-page mvp-menu-page">
      <MobileShell
        title="Dosey 도지"
        headerAction={
          <button
            className="mvp-menu__notification"
            type="button"
            aria-label="알림 (준비 중)"
            disabled
          >
            <img src={notificationIcon} alt="" aria-hidden="true" />
            <span aria-hidden="true" />
          </button>
        }
        activeNavigation="메뉴"
        disabledNavigation={['일정']}
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
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
              <MenuRow icon="clipboard" label="복약 기록" disabled />
              <MenuRow icon="report" label="복약 리포트" detail="7일 · 30일" disabled />
            </div>
          </section>

          <section className="mvp-menu__section" aria-labelledby="menu-settings-title">
            <h3 id="menu-settings-title">설정</h3>
            <div className="mvp-menu__card">
              <MenuRow icon="bell" label="알림 설정" disabled />
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
