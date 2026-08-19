type ErrorStateProps = {
  message?: string
  onRetry?: () => void
}

function ErrorState({
  message = '처리 중 오류가 발생했습니다.',
  onRetry,
}: ErrorStateProps) {
  return (
    <div role="alert">
      <p>{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          다시 시도
        </button>
      )}
    </div>
  )
}

export default ErrorState
