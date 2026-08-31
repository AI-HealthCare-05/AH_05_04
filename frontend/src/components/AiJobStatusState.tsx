import { Button } from '../design-system/components'
import type { AiJobPresentation, AiJobViewStatus } from '../features/ai-jobs/jobState'
import './AiJobStatusState.css'

type AiJobVisualStatus =
  | Exclude<AiJobViewStatus, 'COMPLETED'>
  | 'RECONNECT_RECOVERY'
  | 'REQUEST_ERROR'
  | 'POLL_TIMEOUT'

type AiJobStatusStateProps = {
  status: AiJobVisualStatus
  presentation: AiJobPresentation
  onAction?: () => void
  actionDisabled?: boolean
}

type AiJobVisual = {
  indicator: string
  steps?: readonly string[]
  footer: string
}

const progressVisuals: Partial<Record<AiJobVisualStatus, AiJobVisual>> = {
  PENDING: {
    indicator: '•••',
    steps: ['작업 요청 접수 완료', '처리 시작 준비 중', '준비가 끝나면 자동 시작'],
    footer: '다른 화면으로 이동해도 준비는 계속돼요.',
  },
  PROCESSING: {
    indicator: '▱',
    steps: ['처방전 업로드 완료', '처방전 내용 읽는 중', '읽은 처방정보 정리 예정'],
    footer: '정리가 끝나면 결과 화면으로 자동으로 이동해요.',
  },
  RETRY_WAIT: {
    indicator: '↻',
    steps: ['일시적인 문제 확인', '자동 재시도 준비 중', '잠시 후 처리 자동 재개'],
    footer: '자동으로 다시 시작되므로 잠시 기다려 주세요.',
  },
  RECONNECT_RECOVERY: {
    indicator: '↻',
    steps: ['이전 작업 찾는 중', '현재 진행 상태 확인 중', '확인 후 이어서 진행'],
    footer: '새 작업을 시작하지 않고 이전 진행 상태를 이어가요.',
  },
  FAILED: {
    indicator: '!',
    footer: '문제가 계속되면 잠시 후 다시 확인해 주세요.',
  },
  STALE: {
    indicator: '↻',
    footer: '최신 상태를 확인해 주세요.',
  },
  REQUEST_ERROR: {
    indicator: '!',
    footer: '민감한 정보와 내부 권한·오류 정보는 표시하지 않아요.',
  },
  POLL_TIMEOUT: {
    indicator: '!',
    footer: '문제가 계속되면 잠시 후 다시 확인해 주세요.',
  },
}

function AiJobStatusState({
  status,
  presentation,
  onAction,
  actionDisabled = false,
}: AiJobStatusStateProps) {
  const isAlert =
    status === 'FAILED' ||
    status === 'REQUEST_ERROR' ||
    presentation.tone === 'error'
  const isProgress =
    status === 'PENDING' ||
    status === 'PROCESSING' ||
    status === 'RETRY_WAIT' ||
    status === 'RECONNECT_RECOVERY'
  const visual = progressVisuals[status] ?? progressVisuals.REQUEST_ERROR
  const primaryAction = status === 'STALE'

  return (
    <section
      className={`ai-job-state ai-job-state--${presentation.tone.toLowerCase()}`}
      role={isAlert ? 'alert' : 'status'}
      aria-live={isAlert ? 'assertive' : 'polite'}
      aria-busy={isProgress || undefined}
      data-job-status={status}
    >
      <div className="ai-job-state__document" aria-hidden="true">
        <span className={`ai-job-state__indicator ai-job-state__indicator--${status.toLowerCase()}`}>
          {visual?.indicator}
        </span>
      </div>
      <h1 className="ai-job-state__title">{presentation.title}</h1>
      <p className="ai-job-state__description">{presentation.description}</p>

      {visual?.steps && (
        <ul className="ai-job-state__steps" aria-label="작업 진행 상태">
          {visual.steps.map((step) => (
            <li key={step}>
              <span aria-hidden="true" />
              <strong>{step}</strong>
            </li>
          ))}
        </ul>
      )}

      {presentation.actionLabel && (
        <Button
          variant={primaryAction ? 'primary' : 'secondary'}
          fullWidth
          className={`ai-job-state__action ${primaryAction ? 'ai-job-state__action--primary' : 'ai-job-state__action--inline'}`}
          disabled={actionDisabled}
          onClick={onAction}
        >
          {!primaryAction && <span className="ai-job-state__step-marker" aria-hidden="true" />}
          <span>{presentation.actionLabel}</span>
        </Button>
      )}

      {visual?.footer && <p className="ai-job-state__footer">{visual.footer}</p>}

      {actionDisabled && (
        <p className="ai-job-state__unavailable">이 기능은 준비 중입니다.</p>
      )}
    </section>
  )
}

export default AiJobStatusState
