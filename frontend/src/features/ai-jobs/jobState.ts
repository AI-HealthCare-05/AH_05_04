import { ApiError } from '../../api/client'

export type AiJobViewStatus =
  | 'PENDING'
  | 'PROCESSING'
  | 'RETRY_WAIT'
  | 'COMPLETED'
  | 'FAILED'
  | 'STALE'

export type AiJobFailureCode =
  | 'TIMEOUT'
  | 'DEPENDENCY_UNAVAILABLE'
  | 'INVALID_INPUT'
  | 'UNSUPPORTED_SCHEMA'
  | 'SAFETY_VALIDATION_FAILED'
  | 'RETRY_EXHAUSTED'
  | 'INTERNAL_ERROR'

export type AiJobPresentationTone = 'progress' | 'error' | 'attention'

export type AiJobPresentation = {
  title: string
  description: string
  actionLabel?: string
  tone: AiJobPresentationTone
}

export function isTerminalJobStatus(status: AiJobViewStatus): boolean {
  return status === 'COMPLETED' || status === 'FAILED' || status === 'STALE'
}

const failurePresentations: Record<AiJobFailureCode, AiJobPresentation> = {
  TIMEOUT: {
    title: '작업 시간이 너무 길어졌어요',
    description: '처리를 완료하지 못했습니다. 이전 화면에서 다시 시도해 주세요.',
    actionLabel: '이전 화면으로 돌아가기',
    tone: 'error',
  },
  DEPENDENCY_UNAVAILABLE: {
    title: '연결된 서비스가 응답하지 않아요',
    description: '잠시 후 이전 화면에서 다시 시도해 주세요.',
    actionLabel: '이전 화면으로 돌아가기',
    tone: 'error',
  },
  INVALID_INPUT: {
    title: '입력 내용을 확인해 주세요',
    description: '처리할 수 없는 입력이 포함되어 있습니다.',
    actionLabel: '이전 화면으로 돌아가기',
    tone: 'error',
  },
  UNSUPPORTED_SCHEMA: {
    title: '지원하지 않는 형식이에요',
    description: '입력 형식을 확인한 뒤 다시 시도해 주세요.',
    actionLabel: '이전 화면으로 돌아가기',
    tone: 'error',
  },
  SAFETY_VALIDATION_FAILED: {
    title: '안전하게 결과를 제공할 수 없어요',
    description: '입력 내용을 확인하고 필요한 경우 전문가에게 문의해 주세요.',
    actionLabel: '이전 화면으로 돌아가기',
    tone: 'error',
  },
  RETRY_EXHAUSTED: {
    title: '여러 번 시도했지만 완료하지 못했어요',
    description: '잠시 후 이전 화면에서 다시 시도해 주세요.',
    actionLabel: '이전 화면으로 돌아가기',
    tone: 'error',
  },
  INTERNAL_ERROR: {
    title: '작업을 완료하지 못했어요',
    description: '일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.',
    actionLabel: '이전 화면으로 돌아가기',
    tone: 'error',
  },
}

const unknownFailurePresentation: AiJobPresentation = {
  title: '작업을 완료하지 못했어요',
  description: '문제가 계속되면 잠시 후 다시 시도해 주세요.',
  actionLabel: '이전 화면으로 돌아가기',
  tone: 'error',
}

export function getJobFailurePresentation(
  code: string | null | undefined,
): AiJobPresentation {
  if (code && code in failurePresentations) {
    return failurePresentations[code as AiJobFailureCode]
  }

  return unknownFailurePresentation
}

export function getJobStatusPresentation(
  status: Exclude<AiJobViewStatus, 'COMPLETED' | 'FAILED'>,
): AiJobPresentation {
  switch (status) {
    case 'PENDING':
      return {
        title: '처리를 준비하고 있어요',
        description: '작업을 시작할 때까지 잠시만 기다려 주세요.',
        tone: 'progress',
      }
    case 'PROCESSING':
      return {
        title: '처방정보를 확인하고 있어요',
        description: '파일 업로드 → 글자 인식 → 복약정보 구조화',
        tone: 'progress',
      }
    case 'RETRY_WAIT':
      return {
        title: '잠시 후 자동으로 다시 시도할게요',
        description: '현재 작업은 안전하게 대기 중입니다.',
        tone: 'attention',
      }
    case 'STALE':
      return {
        title: '최신 정보 확인이 필요해요',
        description: '새로운 기준으로 다시 확인하는 기능을 준비하고 있습니다.',
        actionLabel: '최신 정보 확인하기',
        tone: 'attention',
      }
  }
}

export function getPollingTimeoutPresentation(): AiJobPresentation {
  return {
    title: '처방전 확인 시간이 길어지고 있어요',
    description: '상태 확인을 잠시 멈췄습니다. 잠시 후 다시 시도해 주세요.',
    actionLabel: '처방전 등록 화면으로 돌아가기',
    tone: 'attention',
  }
}

export function getJobRequestErrorPresentation(error: unknown): AiJobPresentation {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return {
          title: '로그인이 필요해요',
          description: '다시 로그인한 뒤 작업을 확인해 주세요.',
          actionLabel: '로그인하기',
          tone: 'error',
        }
      case 403:
        return {
          title: '이 작업을 확인할 수 없어요',
          description: '현재 계정으로 요청한 작업에 접근할 수 없습니다.',
          actionLabel: '이전 화면으로 돌아가기',
          tone: 'error',
        }
      case 404:
        return {
          title: '작업을 찾을 수 없어요',
          description: '요청한 작업을 확인할 수 없습니다.',
          actionLabel: '이전 화면으로 돌아가기',
          tone: 'error',
        }
      case 409:
        return {
          title: '작업 상태가 변경되었어요',
          description: '현재 상태를 다시 확인한 뒤 시도해 주세요.',
          actionLabel: '이전 화면으로 돌아가기',
          tone: 'attention',
        }
      default:
        if (error.status >= 500) {
          return {
            title: '서버 응답이 원활하지 않아요',
            description: '잠시 후 다시 시도해 주세요.',
            actionLabel: '이전 화면으로 돌아가기',
            tone: 'error',
          }
        }
    }
  }

  if (error instanceof TypeError) {
    return {
      title: '네트워크 연결을 확인해 주세요',
      description: '인터넷 연결을 확인한 뒤 다시 시도해 주세요.',
      actionLabel: '이전 화면으로 돌아가기',
      tone: 'error',
    }
  }

  return {
    title: '작업 상태를 확인하지 못했어요',
    description: '잠시 후 다시 시도해 주세요.',
    actionLabel: '이전 화면으로 돌아가기',
    tone: 'error',
  }
}
