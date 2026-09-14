import StatusPanel from './StatusPanel'

type LoadingStateProps = {
  message?: string
}

/**
 * 기존 호출부 호환을 위해 props 를 그대로 유지합니다.
 * 표현은 공통 StatusPanel 로 위임합니다.
 */
function LoadingState({
  message = '불러오는 중입니다.',
}: LoadingStateProps) {
  return <StatusPanel variant="loading" title={message} />
}

export default LoadingState
