import { Button } from '../design-system/components'
import type { AiJobPresentation, AiJobViewStatus } from '../features/ai-jobs/jobState'
import './AiJobStatusState.css'

type AiJobStatusStateProps = {
  status: Exclude<AiJobViewStatus, 'COMPLETED'> | 'REQUEST_ERROR' | 'POLL_TIMEOUT'
  presentation: AiJobPresentation
  onAction?: () => void
  actionDisabled?: boolean
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
  const isProgress = status === 'PENDING' || status === 'PROCESSING' || status === 'RETRY_WAIT'

  return (
    <section
      className={`ai-job-state ai-job-state--${presentation.tone.toLowerCase()}`}
      role={isAlert ? 'alert' : 'status'}
      aria-live={isAlert ? 'assertive' : 'polite'}
      aria-busy={isProgress || undefined}
      data-job-status={status}
    >
      <div className="ai-job-state__document" aria-hidden="true">
        <span />
      </div>
      <h1 className="ai-job-state__title">{presentation.title}</h1>
      <p className="ai-job-state__description">{presentation.description}</p>

      {isProgress && (
        <div className="ai-job-state__progress" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
      )}

      {presentation.actionLabel && (
        <Button
          fullWidth
          className="ai-job-state__action"
          disabled={actionDisabled}
          onClick={onAction}
        >
          {presentation.actionLabel}
        </Button>
      )}

      {actionDisabled && (
        <p className="ai-job-state__unavailable">이 기능은 준비 중입니다.</p>
      )}
    </section>
  )
}

export default AiJobStatusState
