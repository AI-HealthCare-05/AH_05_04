type LoadingStateProps = {
  message?: string
}

function LoadingState({
  message = '불러오는 중입니다.',
}: LoadingStateProps) {
  return (
    <div role="status" aria-live="polite">
      <p>{message}</p>
    </div>
  )
}

export default LoadingState
