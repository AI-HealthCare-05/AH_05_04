import type { ReactNode } from 'react'
import './StatusPanel.css'

/**
 * 화면 공통 상태 패널.
 *
 * Schedule / Notification / Report 등 신규 화면이 재사용하는 최소 공통 UI입니다.
 *
 * 설계 원칙:
 * - Backend error code·domain enum을 여기서 추정하지 않습니다. 호출자가 자신의 도메인
 *   오류를 판별해 variant 와 message 를 정해서 넘깁니다.
 * - Backend 원문 오류(detail, stack, code)를 받지 않습니다. 사용자에게 보여줄 문장만
 *   받습니다.
 * - AI Job 전용 상태는 AiJobStatusState 가 계속 담당합니다. 여기에 합치지 않습니다.
 * - 색상만으로 상태를 구분하지 않습니다. variant 마다 기호와 한글 라벨을 함께 표시합니다.
 */
export type StatusPanelVariant =
  /** 데이터를 불러오는 중 */
  | 'loading'
  /** 요청은 성공했으나 보여줄 내용이 없음 */
  | 'empty'
  /** 원인을 특정할 수 없는 일반 오류. 재시도 안내 없음 */
  | 'error'
  /** 다시 시도하면 해결될 수 있는 오류 */
  | 'error-retryable'
  /** 재시도로는 해결되지 않는 오류. 다른 행동이 필요 */
  | 'error-final'
  /** 네트워크 단절·서버 일시 장애 등 사용자 잘못이 아닌 중립 상태 */
  | 'unavailable'

type StatusPanelProps = {
  variant: StatusPanelVariant
  /** 제목. 한 문장으로 짧게. */
  title: string
  /** 보조 설명. 사용자가 할 수 있는 다음 행동을 적습니다. */
  description?: string
  /** variant 가 error-retryable 일 때만 동작합니다. */
  onRetry?: () => void
  retryLabel?: string
  /** 재시도 외의 다음 행동(예: 처방전 등록하러 가기). */
  action?: ReactNode
  className?: string
}

const VARIANT_LABEL: Record<StatusPanelVariant, string> = {
  loading: '불러오는 중',
  empty: '내용 없음',
  error: '오류',
  'error-retryable': '오류',
  'error-final': '오류',
  unavailable: '일시적인 문제',
}

/** 색맹·저시력 사용자를 위해 색 외에 형태로도 구분되는 기호입니다. */
const VARIANT_GLYPH: Record<StatusPanelVariant, string> = {
  loading: '⋯',
  empty: '□',
  error: '!',
  'error-retryable': '!',
  'error-final': '!',
  unavailable: '~',
}

function isErrorVariant(variant: StatusPanelVariant): boolean {
  return (
    variant === 'error' ||
    variant === 'error-retryable' ||
    variant === 'error-final'
  )
}

export function StatusPanel({
  variant,
  title,
  description,
  onRetry,
  retryLabel = '다시 시도',
  action,
  className,
}: StatusPanelProps) {
  // 오류는 즉시 읽어야 하므로 alert, 나머지는 흐름을 끊지 않도록 status 로 둡니다.
  const isError = isErrorVariant(variant)
  const showRetry = variant === 'error-retryable' && Boolean(onRetry)

  return (
    <div
      className={`status-panel status-panel--${variant}${className ? ` ${className}` : ''}`}
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
    >
      <p className="status-panel__label">
        <span className="status-panel__glyph" aria-hidden="true">
          {VARIANT_GLYPH[variant]}
        </span>
        {VARIANT_LABEL[variant]}
      </p>
      <p className="status-panel__title">{title}</p>
      {description && <p className="status-panel__description">{description}</p>}
      {(showRetry || action) && (
        <div className="status-panel__actions">
          {showRetry && (
            <button
              type="button"
              className="status-panel__retry"
              onClick={onRetry}
            >
              {retryLabel}
            </button>
          )}
          {action}
        </div>
      )}
    </div>
  )
}

export default StatusPanel
