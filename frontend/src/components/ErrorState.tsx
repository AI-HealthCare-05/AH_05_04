import StatusPanel from './StatusPanel'

type ErrorStateProps = {
  message?: string
  onRetry?: () => void
}

/**
 * 기존 호출부 호환을 위해 props 를 그대로 유지합니다.
 * onRetry 가 있으면 재시도 가능한 오류로, 없으면 일반 오류로 표현합니다.
 */
function ErrorState({
  message = '처리 중 오류가 발생했습니다.',
  onRetry,
}: ErrorStateProps) {
  return onRetry ? (
    <StatusPanel variant="error-retryable" title={message} onRetry={onRetry} />
  ) : (
    <StatusPanel variant="error" title={message} />
  )
}

export default ErrorState
