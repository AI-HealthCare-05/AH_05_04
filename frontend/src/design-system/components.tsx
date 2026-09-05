import type { ButtonHTMLAttributes, ReactNode } from 'react'
import calendarIcon from '../assets/icon-calendar-schedule.svg'
import guideIcon from '../assets/icon-book-open-guide.svg'
import menuIcon from '../assets/icon-menu.svg'
import { DoseyMascot } from './DoseyMascot'

export type MainNavigationItem = '홈' | '일정' | '도지' | '가이드' | '메뉴'

const mainNavigationItems: readonly MainNavigationItem[] = [
  '홈',
  '일정',
  '도지',
  '가이드',
  '메뉴',
]

function NavigationIcon({ item }: { item: MainNavigationItem }) {
  if (item === '홈') {
    return (
      <svg
        className="bottom-nav__icon"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M4 10.4 12 4l8 6.4V20h-5.25v-5.5h-5.5V20H4v-9.6Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    )
  }

  if (item === '도지') {
    return (
      <span className="bottom-nav__doji-icon" aria-hidden="true">
        <DoseyMascot variant="navigation" />
      </span>
    )
  }

  const icon = item === '일정' ? calendarIcon : item === '가이드' ? guideIcon : menuIcon
  return <img className="bottom-nav__icon" src={icon} alt="" aria-hidden="true" />
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost'
  fullWidth?: boolean
}

export function Button({
  variant = 'primary',
  fullWidth = false,
  className = '',
  type = 'button',
  ...props
}: ButtonProps) {
  const variantClass = variant === 'primary' ? '' : variant

  return (
    <button
      type={type}
      className={`ds-button ${variantClass} ${fullWidth ? 'full-width' : ''} ${className}`.trim()}
      {...props}
    />
  )
}

export function Card({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return <section className={`ds-card ${className}`.trim()}>{children}</section>
}

export function StatusBadge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'neutral' | 'attention'
}) {
  return (
    <span className={`status-badge ${tone === 'attention' ? 'attention' : ''}`}>
      {children}
    </span>
  )
}

export function FieldReviewRow({
  label,
  value,
  status,
  needsReview = false,
}: {
  label: string
  value: string
  status: string
  needsReview?: boolean
}) {
  return (
    <div className={`field-review-row ${needsReview ? 'needs-review' : ''}`}>
      <span className="field-review-label">{label}</span>
      <span className="field-review-value">{value}</span>
      <StatusBadge tone={needsReview ? 'attention' : 'neutral'}>
        {status}
      </StatusBadge>
    </div>
  )
}

export function MobileShell({
  title,
  children,
  onBack,
  brandMark,
  backPlacement = 'topbar',
  hideHeader = false,
  hideNavigation = false,
  activeNavigation = '홈',
  disabledNavigation = [],
  onNavigate,
}: {
  title?: string
  children: ReactNode
  onBack?: () => void
  brandMark?: ReactNode
  backPlacement?: 'topbar' | 'content'
  hideHeader?: boolean
  hideNavigation?: boolean
  activeNavigation?: MainNavigationItem
  disabledNavigation?: readonly MainNavigationItem[]
  onNavigate?: (item: MainNavigationItem) => void
}) {
  return (
    <div className="mobile-app">
      {!hideHeader && (
        <header
          className={`app-topbar ${onBack ? 'app-topbar--with-back' : ''} app-topbar--${backPlacement}-back`}
        >
          {onBack && (
          <button className="icon-button" type="button" onClick={onBack} aria-label="이전 화면">
            <span className="chevron-icon" aria-hidden="true" />
          </button>
          )}
          {brandMark ?? (
            <span className="brand-mark" aria-hidden="true">
              <span className="brand-mark__ring" />
            </span>
          )}
          <h1>{title ?? 'Dosey 도지'}</h1>
        </header>
      )}
      {children}
      {!hideNavigation && (
        <nav className="bottom-nav" aria-label="주요 메뉴">
          {mainNavigationItems.map((item) => (
            <button
              key={item}
              type="button"
              aria-current={item === activeNavigation ? 'page' : undefined}
              aria-label={disabledNavigation.includes(item) ? `${item} (준비 중)` : item}
              disabled={disabledNavigation.includes(item)}
              onClick={() => onNavigate?.(item)}
            >
              <NavigationIcon item={item} />
              <span className="bottom-nav__label">{item}</span>
            </button>
          ))}
        </nav>
      )}
    </div>
  )
}
