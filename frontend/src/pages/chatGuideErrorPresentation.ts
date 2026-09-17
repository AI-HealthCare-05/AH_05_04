import { ApiError } from '../api/client'

export type ChatGuideErrorAction =
  | 'CONSENT_SETTINGS'
  | 'RELOAD'
  | 'RETRY'

export type ChatGuideErrorPresentation = {
  title: string
  helper?: string
  action: ChatGuideErrorAction
}

type FallbackMessages = {
  unauthorized: string
  notFound: string
  server: string
  network: string
  unknown: string
}

const codedPresentations: Record<string, ChatGuideErrorPresentation> = {
  CONSENT_REQUIRED: {
    title: '이 기능을 이용하려면 동의가 필요해요.',
    helper: '동의 설정을 확인한 뒤 다시 이용해 주세요.',
    action: 'CONSENT_SETTINGS',
  },
  PRESCRIPTION_VERSION_STALE: {
    title: '처방 정보가 변경되었어요.',
    helper: '최신 처방 정보를 다시 불러온 뒤 이용해 주세요.',
    action: 'RELOAD',
  },
  CONSENT_POLICY_UNAVAILABLE: {
    title: '동의 안내를 준비하고 있어요. 잠시 후 다시 시도해 주세요.',
    action: 'RETRY',
  },
}

export function hasChatGuideErrorPresentation(error: unknown) {
  return (
    error instanceof ApiError &&
    Object.hasOwn(codedPresentations, error.code)
  )
}

export function getChatGuideErrorPresentation(
  error: unknown,
  fallback: FallbackMessages,
): ChatGuideErrorPresentation {
  if (error instanceof ApiError) {
    if (hasChatGuideErrorPresentation(error)) {
      return codedPresentations[error.code]
    }

    if (error.status === 401) {
      return { title: fallback.unauthorized, action: 'RELOAD' }
    }
    if (error.status === 404) {
      return { title: fallback.notFound, action: 'RELOAD' }
    }
    if (error.status >= 500) {
      return { title: fallback.server, action: 'RELOAD' }
    }
  }

  if (error instanceof TypeError) {
    return { title: fallback.network, action: 'RELOAD' }
  }

  return { title: fallback.unknown, action: 'RELOAD' }
}
