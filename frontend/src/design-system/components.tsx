import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from 'react'
import navCalendarIcon from '../assets/nav-calendar.svg'
import navGuideIcon from '../assets/nav-guide.svg'
import navMenuIcon from '../assets/nav-menu.svg'
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
  if (item === '도지') {
    return (
      <span className="bottom-nav__doji" aria-hidden="true">
        <DoseyMascot variant="nav" />
      </span>
    )
  }

  if (item === '홈') {
    return <span className="bottom-nav__home" aria-hidden="true">⌂</span>
  }

  const icon = item === '일정' ? navCalendarIcon : item === '가이드' ? navGuideIcon : navMenuIcon

  return (
    <span
      className="bottom-nav__icon"
      aria-hidden="true"
      style={{ '--bottom-nav-icon': `url(${icon})` } as CSSProperties}
    />
  )
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
  headerAction,
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
  headerAction?: ReactNode
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
          {headerAction && <div className="app-topbar__action">{headerAction}</div>}
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
              <span>{item}</span>
            </button>
          ))}
        </nav>
      )}
    </div>
  )
}
