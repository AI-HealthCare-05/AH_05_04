import type { ButtonHTMLAttributes, ReactNode } from 'react'

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
  hideNavigation = false,
  activeNavigation = '홈',
  onNavigate,
}: {
  title?: string
  children: ReactNode
  onBack?: () => void
  hideNavigation?: boolean
  activeNavigation?: '홈' | '일정' | '가이드' | '메뉴'
  onNavigate?: (item: '홈' | '일정' | '가이드' | '메뉴') => void
}) {
  return (
    <div className="mobile-app">
      <header className="app-topbar">
        {onBack && (
          <button className="icon-button" type="button" onClick={onBack} aria-label="이전 화면">
            ‹
          </button>
        )}
        <span className="brand-mark" aria-hidden="true">◌</span>
        <h1>{title ?? 'Dosey 도지'}</h1>
      </header>
      {children}
      {!hideNavigation && (
        <nav className="bottom-nav" aria-label="주요 메뉴">
          {(['홈', '일정', '가이드', '메뉴'] as const).map((item) => (
            <button key={item} type="button" aria-current={item === activeNavigation ? 'page' : undefined} onClick={() => onNavigate?.(item)}>
              {item}
            </button>
          ))}
        </nav>
      )}
    </div>
  )
}
